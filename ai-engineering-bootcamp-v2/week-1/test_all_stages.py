#!/usr/bin/env python3
"""Smoke-test week-1 stages + decontaminated fixtures (precision/recall baseline)."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from extract import _extraction_user_payload, build_ledger, draft_to_fact
from grouping import record_value_conflicts
from normalize import normalize_qualifier, normalize_value
from predicates import (
    PREDICATES,
    PREDICATE_VOCABULARY,
    fact_grouping_key,
    needs_predicate_review,
    predicate_class_of,
    resolve_predicate,
)
from provider import ModelProvider, compute_cost_usd
from schemas import (
    AskRequest,
    Child,
    Disagreement,
    ExtractedFactDraft,
    Fact,
    Ledger,
    ReportSection,
    Source,
    SourcedFact,
)
from validators import (
    compute_age_years,
    validate_age_consistency,
    validate_provenance,
)

WORKDIR = Path(__file__).resolve().parent
QUESTION = "What is Retrieval-Augmented Generation in one sentence?"
FIXTURES = WORKDIR / "fixtures"
HISTORY_FIXTURE_PATH = FIXTURES / "synthetic_history_case.json"
HEALTH_FIXTURE_PATH = FIXTURES / "synthetic_health_conflict_case.json"
NO_CONFLICT_FIXTURE_PATH = FIXTURES / "synthetic_no_conflict_case.json"
COEXISTING_ALLERGIES_PATH = FIXTURES / "synthetic_coexisting_allergies_case.json"

# Sibling evaluation fields — never part of AskRequest / model prompt.
FIXTURE_META_KEYS = frozenset(
    {
        "expected_conflicts",
        "expected_facts",
        "expected_ledger_facts",
        "forbidden_predicates_by_source",
        "expected_gap_life_stages_empty",
        "expected_as_of_anchor",
        "expected_vague_no_anchor",
        "expected_grade_timeline",
    }
)

AS_OF_ANCHOR_FIXTURE_PATH = FIXTURES / "synthetic_as_of_anchor_case.json"

DOB_CONFLICT_FIXTURE_PATH = FIXTURES / "synthetic_dob_conflict_case.json"
MISSING_BIRTH_FIXTURE_PATH = FIXTURES / "synthetic_missing_birth_infancy_case.json"

_STOPWORDS = frozenset(
    "a an the and or of to for in on at is are was were be been being "
    "with by from as that this these those it its student".split()
)

_CONTAMINATION = re.compile(
    r"CONFLICT\s+PLANT|NOTE\s+FOR\s+BUILDERS|stale\s+age\s+planted",
    re.IGNORECASE,
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(module: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(WORKDIR / ".venv/bin/uvicorn"),
            f"{module}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=WORKDIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_up(base: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/docs", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def post(base: str, payload: dict) -> tuple[int, dict]:
    r = httpx.post(f"{base}/ask", json=payload, timeout=180.0)
    try:
        data = r.json()
    except json.JSONDecodeError:
        data = {"_raw": r.text}
    return r.status_code, data


def check(name: str, ok: bool, detail: str) -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {detail}")
    return ok


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ask_payload(fixture: dict, **overrides) -> dict:
    """Strip evaluation meta so it cannot reach /ask or the model prompt."""

    payload = {k: v for k, v in fixture.items() if k not in FIXTURE_META_KEYS}
    payload.update(overrides)
    return payload


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def fact_matches(expected: dict, detected: dict) -> bool:
    if expected.get("source_id") != detected.get("source_id"):
        return False
    exp = _tokens(expected.get("statement") or "")
    got = _tokens(detected.get("statement") or "")
    if not exp:
        return False
    # Require most content tokens from the expected claim to appear.
    hit = sum(1 for t in exp if t in got)
    return hit / len(exp) >= 0.6


def conflict_matches(expected: dict, detected: dict | Disagreement) -> bool:
    if isinstance(detected, Disagreement):
        got_ids = {v.source_id for v in detected.versions}
        got_pred = detected.predicate
        got_qual = detected.qualifier
        got_blob = " ".join(
            [detected.topic, detected.predicate, detected.qualifier or ""]
            + [v.value_text for v in detected.versions]
        )
    else:
        got_ids = {v.get("source_id") for v in detected.get("versions") or [] if v.get("source_id")}
        if not got_ids and detected.get("source_ids"):
            got_ids = set(detected["source_ids"])
        got_pred = detected.get("predicate")
        got_qual = detected.get("qualifier")
        got_blob = " ".join(
            [
                detected.get("topic") or "",
                detected.get("predicate") or "",
                detected.get("qualifier") or "",
                " ".join(
                    (v.get("statement") or v.get("value_text") or "")
                    for v in detected.get("versions") or []
                ),
            ]
        )

    exp_ids = set(expected.get("source_ids") or [])
    if exp_ids and not exp_ids.issubset(got_ids):
        return False
    if expected.get("predicate") and got_pred != expected["predicate"]:
        return False
    if "qualifier" in expected:
        exp_qual = normalize_qualifier(expected.get("qualifier"))
        if got_qual != exp_qual:
            return False
    exp_topic_toks = _tokens(expected.get("topic") or "")
    got_toks = _tokens(got_blob)
    if not exp_topic_toks:
        return True
    # When predicate/qualifier already match, topic tokens are optional.
    if expected.get("predicate"):
        return True
    hit = sum(1 for t in exp_topic_toks if t in got_toks)
    return hit / len(exp_topic_toks) >= 0.4


def score_conflicts(
    detected: list, expected: list[dict]
) -> tuple[list[dict], list[dict], list]:
    """Return (found expected, missed expected, false-positive detected)."""

    remaining = list(detected)
    found: list[dict] = []
    missed: list[dict] = []
    for exp in expected:
        match_idx = next(
            (i for i, det in enumerate(remaining) if conflict_matches(exp, det)),
            None,
        )
        if match_idx is None:
            missed.append(exp)
        else:
            found.append(exp)
            remaining.pop(match_idx)
    return found, missed, remaining


def score_facts(
    detected: list[dict], expected: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    remaining = list(detected)
    found: list[dict] = []
    missed: list[dict] = []
    for exp in expected:
        match_idx = next(
            (i for i, det in enumerate(remaining) if fact_matches(exp, det)),
            None,
        )
        if match_idx is None:
            missed.append(exp)
        else:
            found.append(exp)
            remaining.pop(match_idx)
    return found, missed, remaining


def report_pr(
    label: str,
    found: list,
    missed: list,
    extras: list,
    *,
    expected_n: int,
) -> tuple[float, float]:
    precision = (len(found) / (len(found) + len(extras))) if (len(found) + len(extras)) else 1.0
    recall = (len(found) / expected_n) if expected_n else 1.0
    print(
        f"  {label}: found={len(found)}/{expected_n}  "
        f"missed={len(missed)}  false_positives={len(extras)}  "
        f"precision={precision:.2f}  recall={recall:.2f}"
    )
    for m in missed:
        topic = m.get("topic") if isinstance(m, dict) else getattr(m, "topic", m)
        if isinstance(m, dict):
            topic = m.get("topic") or m.get("statement") or m
        print(f"    missed: {topic}")
    for e in extras:
        if isinstance(e, Disagreement):
            topic = e.topic
        elif isinstance(e, dict):
            topic = e.get("topic") or e.get("statement") or e
        else:
            topic = getattr(e, "topic", e)
        print(f"    false_positive: {topic}")
    return precision, recall


def test_stage1(base: str) -> bool:
    print("\n=== Stage 1: bare /ask ===")
    status, data = post(base, {"question": QUESTION})
    ok = True
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check(
        "answer type",
        isinstance(data.get("answer"), str),
        f"answer is str: {type(data.get('answer')).__name__}",
    )
    ok &= check(
        "tokens",
        isinstance(data.get("tokens_used"), int) and data["tokens_used"] > 0,
        f"tokens_used={data.get('tokens_used')}",
    )
    ok &= check("no extra", set(data.keys()) == {"answer", "tokens_used"}, f"keys={list(data.keys())}")
    if ok:
        print(f"  answer preview: {data['answer'][:80]}...")
    return ok


def test_stage2(base: str) -> bool:
    print("\n=== Stage 2: structured output ===")
    status, data = post(base, {"question": QUESTION})
    ans = data.get("answer", {})
    ok = True
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check("answer object", isinstance(ans, dict), "answer is object")
    ok &= check(
        "confidence",
        isinstance(ans.get("confidence"), (int, float)),
        f"confidence={ans.get('confidence')}",
    )
    ok &= check(
        "sources_needed",
        isinstance(ans.get("sources_needed"), bool),
        f"sources_needed={ans.get('sources_needed')}",
    )
    ok &= check("tokens", data.get("tokens_used", 0) > 0, f"tokens_used={data.get('tokens_used')}")
    ok &= check("no extra", set(data.keys()) == {"answer", "tokens_used"}, f"keys={list(data.keys())}")
    return ok


def test_stage3(base: str) -> bool:
    print("\n=== Stage 3: guardrail + retry ===")
    status_ok, _ = post(base, {"question": QUESTION})
    status_bad, data_bad = post(base, {"question": QUESTION, "force_bad": True})
    ok = True
    ok &= check("normal", status_ok == 200, f"normal HTTP {status_ok}")
    ok &= check(
        "force_bad recovers",
        status_bad == 200,
        f"force_bad HTTP {status_bad} (retry should recover)",
    )
    ok &= check(
        "structured",
        isinstance(data_bad.get("answer"), dict),
        "force_bad answer is structured object",
    )
    return ok


def test_stage4(base: str) -> bool:
    print("\n=== Stage 4: model + latency ===")
    status, data = post(base, {"question": QUESTION, "model": "gpt-4o-mini"})
    ok = True
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check("model", data.get("model") == "gpt-4o-mini", f"model={data.get('model')}")
    ok &= check(
        "latency",
        isinstance(data.get("latency_ms"), int) and data["latency_ms"] > 0,
        f"latency_ms={data.get('latency_ms')}",
    )
    ok &= check("no cost yet", "cost_usd" not in data, "cost_usd absent (stage 4 only)")
    ok &= check(
        "keys",
        set(data.keys()) == {"answer", "tokens_used", "model", "latency_ms"},
        f"keys={list(data.keys())}",
    )
    return ok


def test_meta_never_in_prompt() -> bool:
    """expected_* sibling fields must never serialize into extract/draft/ingest prompts."""

    print("\n=== Prompt hygiene: fixture meta never in payload ===")
    from draft import _draft_user_payload
    from schemas import DraftRequest

    ok = True
    for path in (HISTORY_FIXTURE_PATH, HEALTH_FIXTURE_PATH, NO_CONFLICT_FIXTURE_PATH):
        fixture = _load_fixture(path)
        present_meta = FIXTURE_META_KEYS & fixture.keys()
        ok &= check(
            f"{path.name}:has_meta",
            bool(present_meta),
            f"{path.name}: fixture carries eval meta {sorted(present_meta)}",
        )

        stripped = ask_payload(fixture)
        ok &= check(
            f"{path.name}:ask_keys",
            FIXTURE_META_KEYS.isdisjoint(stripped.keys()),
            f"{path.name}: ask_payload strips meta keys",
        )

        body = AskRequest.model_validate(stripped)
        ask_dump = json.dumps(body.model_dump(), indent=2)
        for key in present_meta:
            ok &= check(
                f"{path.name}:ask:{key}",
                key not in ask_dump,
                f"{path.name}: AskRequest dump must not contain {key!r}",
            )

        # Stage 5: model prompts are per-source extract + draft (not a one-shot /ask blob).
        for source in body.sources:
            extract_prompt = _extraction_user_payload(source)
            for key in present_meta:
                ok &= check(
                    f"{path.name}:{source.id}:extract:{key}",
                    key not in extract_prompt,
                    f"{path.name}/{source.id}: extract prompt must not contain {key!r}",
                )
            # Ingest sees only raw content — same hygiene boundary.
            ingest_user = f"Document:\n{source.content}"
            for key in present_meta:
                ok &= check(
                    f"{path.name}:{source.id}:ingest:{key}",
                    key not in ingest_user,
                    f"{path.name}/{source.id}: ingest user must not contain {key!r}",
                )

        ledger = Ledger(
            child=body.child,
            ledger_version="test",
            built_at="2026-01-01T00:00:00Z",
            sources=body.sources,
            facts=[],
        )
        draft_req = DraftRequest(
            confirm_synthetic=True,
            section=body.section,
            ledger=ledger,
        )
        draft_prompt = _draft_user_payload(draft_req)
        for key in present_meta:
            ok &= check(
                f"{path.name}:draft:{key}",
                key not in draft_prompt,
                f"{path.name}: draft prompt must not contain {key!r}",
            )
    return ok


def test_fixture_decontamination() -> bool:
    """Fixtures must not coach the model via planted labels in content/labels."""

    print("\n=== Fixture decontamination ===")
    ok = True
    paths = (
        HISTORY_FIXTURE_PATH,
        HEALTH_FIXTURE_PATH,
        NO_CONFLICT_FIXTURE_PATH,
        COEXISTING_ALLERGIES_PATH,
        DOB_CONFLICT_FIXTURE_PATH,
        MISSING_BIRTH_FIXTURE_PATH,
        AS_OF_ANCHOR_FIXTURE_PATH,
    )
    for path in paths:
        fixture = _load_fixture(path)
        ok &= check(
            "meta present",
            "expected_conflicts" in fixture and "expected_facts" in fixture,
            f"{path.name}: expected_conflicts + expected_facts present",
        )
        for source in fixture.get("sources", []):
            blob = f"{source.get('label', '')}\n{source.get('content', '')}"
            contaminated = bool(_CONTAMINATION.search(blob))
            ok &= check(
                "clean content",
                not contaminated,
                f"{path.name}/{source.get('id')}: no plant annotations",
            )
    # Father source must not narrate its own omissions.
    father = next(
        s
        for s in _load_fixture(HEALTH_FIXTURE_PATH)["sources"]
        if s["id"] == "father-interview-2026"
    )
    omission_narration = re.search(
        r"did not (name|call|describe)|not independently describe",
        father["content"],
        re.IGNORECASE,
    )
    ok &= check(
        "father clean",
        omission_narration is None,
        "father interview must not narrate omissions",
    )
    return ok


def test_grouping_unit() -> bool:
    """Stage 2.5: qualifier is part of the grouping key."""

    print("\n=== Grouping key unit ===")
    ok = True
    peanut_known = Fact(
        id="f_001",
        subject="child",
        predicate="allergy_status",
        value="known",
        value_text="known peanut allergy",
        qualifier="peanut",
        assertion="asserted",
        source_id="nurse-a",
        source_date="2026-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    dairy_undiagnosed = Fact(
        id="f_002",
        subject="child",
        predicate="allergy_status",
        value="undiagnosed",
        value_text="dairy undiagnosed",
        qualifier="dairy",
        assertion="asserted",
        source_id="nurse-a",
        source_date="2026-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    peanut_undiagnosed = Fact(
        id="f_003",
        subject="child",
        predicate="allergy_status",
        value="undiagnosed",
        value_text="peanut undiagnosed",
        qualifier="peanut",
        assertion="asserted",
        source_id="iep-a",
        source_date="2026-02-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    ok &= check(
        "keys differ by qualifier",
        fact_grouping_key("child", "allergy_status", "peanut")
        != fact_grouping_key("child", "allergy_status", "dairy"),
        "peanut vs dairy keys differ",
    )
    coexist = record_value_conflicts([peanut_known, dairy_undiagnosed])
    ok &= check(
        "coexist no conflict",
        len(coexist) == 0,
        f"coexisting allergies conflicts={len(coexist)} (want 0)",
    )
    same_q = record_value_conflicts([peanut_known, peanut_undiagnosed])
    ok &= check(
        "same qualifier conflicts",
        len(same_q) == 1,
        f"same-qualifier conflicts={len(same_q)} (want 1)",
    )
    ok &= check(
        "qualifier normalize",
        normalize_qualifier("Peanuts") == "peanut",
        f"normalize_qualifier(Peanuts)={normalize_qualifier('Peanuts')!r}",
    )
    return ok


def test_normalize_unit() -> bool:
    """Stage 2: value normalization at write time."""

    print("\n=== Normalize unit ===")
    ok = True
    cases = [
        ("walked_age_months", "13 months", "13"),
        ("walked_age_months", "thirteen months", "13"),
        ("walked_age_months", "walked at 13 mos", "13"),
        ("age_years", "7 years old", "7"),
        ("grade", "2nd grade", "2"),
        ("grade", "grade 4", "4"),
        ("grade", "Kindergarten", "K"),
        ("allergy_status", "Undiagnosed", "undiagnosed"),
        ("allergy_status", "known allergy", "known"),
        ("allergy_status", "no known allergies", "none"),
        ("health_plan_status", "draft emailed to parent", "draft"),
        ("health_plan_status", "active individual health plan on file", "active"),
        ("birth_term", "full-term", "full-term"),
        ("birth_term", "full term", "full-term"),
        ("legal_name", "Justin M.", "Justin M."),
        ("inattention_rating", "6 of 7", "6/7"),
    ]
    for predicate, raw, want in cases:
        got = normalize_value(predicate, raw)
        ok &= check(
            f"{predicate}:{raw}",
            got == want,
            f"normalize({predicate!r}, {raw!r}) → {got!r} (want {want!r})",
        )
    # Distinct statuses must not collapse.
    known = normalize_value("allergy_status", "known allergy")
    undiagnosed = normalize_value("allergy_status", "Undiagnosed")
    ok &= check(
        "allergy distinct",
        known != undiagnosed,
        f"known={known!r} undiagnosed={undiagnosed!r} stay distinct",
    )
    return ok


def test_extract_isolation_unit() -> bool:
    """Stage 2: each extraction payload contains exactly one source."""

    print("\n=== Extract isolation unit ===")
    fixture = _load_fixture(HEALTH_FIXTURE_PATH)
    child = Child.model_validate(fixture["child"])
    sources = [Source.model_validate(s) for s in fixture["sources"]]
    ok = True
    for source in sources:
        payload = _extraction_user_payload(source)
        ok &= check(
            f"single source {source.id}",
            '"sources"' not in payload and source.id in payload,
            f"payload is single-source for {source.id}",
        )
        ok &= check(
            f"no case meta {source.id}",
            "child_initials" not in payload and '"dob"' not in payload,
            f"{source.id} payload must not include child_initials/dob keys",
        )
        ok &= check(
            f"subject vocab {source.id}",
            '"canonical_subjects"' in payload and "child" in payload,
            f"{source.id} payload includes canonical subject vocabulary",
        )
        for other in sources:
            if other.id == source.id:
                continue
            # Other source ids must not appear (content isolation).
            ok &= check(
                f"no {other.id}",
                other.id not in payload,
                f"{source.id} payload must not contain {other.id}",
            )
            # Other source body text must not leak.
            snippet = other.content[:40]
            ok &= check(
                f"no content leak {other.id}",
                snippet not in payload,
                f"{source.id} payload must not contain other content",
            )

    # Draft→Fact stamps source ids and normalizes.
    draft = ExtractedFactDraft(
        subject="child",
        predicate="walked_age_months",
        value="thirteen months",
        value_text="walked at thirteen months",
        qualifier=None,
        assertion="asserted",
        reporter=None,
        life_stage="infancy",
        grade=None,
        confidence="stated",
    )
    fact = draft_to_fact(draft, fact_id="f_001", source=sources[0], child=child)
    ok &= check("normalized value", fact.value == "13", f"value={fact.value}")
    ok &= check("assertion", fact.assertion == "asserted", f"assertion={fact.assertion}")
    ok &= check("stamped source", fact.source_id == sources[0].id, f"source_id={fact.source_id}")
    ok &= check("stamped date", fact.source_date == sources[0].date, f"source_date={fact.source_date}")
    ok &= check(
        "vocab temporality",
        fact.temporality == "durable",
        f"temporality={fact.temporality} (from vocabulary)",
    )

    denied = ExtractedFactDraft(
        subject="child",
        predicate="special_education_eligibility",
        value="none",
        value_text="No prior formal special education eligibility documented",
        qualifier=None,
        assertion="denied",
        reporter=None,
        life_stage="school-age",
        grade=None,
        confidence="stated",
    )
    denied_fact = draft_to_fact(denied, fact_id="f_002", source=sources[0], child=child)
    ok &= check("denied assertion", denied_fact.assertion == "denied", f"assertion={denied_fact.assertion}")
    ok &= check(
        "eligibility as_of",
        denied_fact.temporality == "as_of",
        f"temporality={denied_fact.temporality}",
    )
    return ok


def test_ledger_schema_unit() -> bool:
    """Stage 1: Fact + Ledger construct; ReportSection still importable."""

    print("\n=== Schema unit: Fact / Ledger ===")
    ok = True
    child = Child(initials="A.R.", dob="2017-03-15", evaluation_date="2026-07-16")
    source = Source(
        id="parent-dev-2026",
        type="parent",
        date="2026-06-01",
        label="Parent developmental history form",
        content="Birth: full-term. Walked at 13 months.",
    )
    fact = Fact(
        id="f_001",
        subject="child",
        predicate="birth_term",
        value="full-term",
        value_text="Birth: full-term",
        qualifier=None,
        assertion="asserted",
        source_id=source.id,
        source_date=source.date,
        reporter="parent",
        life_stage="birth",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    grade_fact = Fact(
        id="f_002",
        subject="child",
        predicate="grade",
        value="2",
        value_text="in 2nd grade",
        qualifier=None,
        assertion="asserted",
        source_id=source.id,
        source_date=source.date,
        reporter=None,
        life_stage="school-age",
        grade="2",
        temporality="as_of",
        confidence="stated",
    )
    deferred = Fact(
        id="f_003",
        subject="father-interview-2026",
        predicate="defers_to",
        value="school health file; IEP",
        value_text="health background … school health file and the IEP",
        qualifier=None,
        assertion="asserted",
        source_id="father-interview-2026",
        source_date="2026-06-20",
        reporter="father",
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    ledger = Ledger(
        child=child,
        ledger_version="1",
        built_at="2026-07-16T18:00:00Z",
        sources=[source],
        facts=[fact, grade_fact, deferred],
    )
    ok &= check("fact id", fact.id == "f_001", f"id={fact.id}")
    ok &= check("durable birth", fact.temporality == "durable", f"temporality={fact.temporality}")
    ok &= check("grade as_of", grade_fact.temporality == "as_of", f"grade temporality={grade_fact.temporality}")
    ok &= check("assertion field", fact.assertion == "asserted", f"assertion={fact.assertion}")
    ok &= check("defers_to subject", deferred.subject == "father-interview-2026", "provenance subject is source id")
    ok &= check("ledger facts", len(ledger.facts) == 3, f"facts={len(ledger.facts)}")
    ok &= check("sources carried", ledger.sources[0].id == source.id, "sources preserved on ledger")
    ok &= check(
        "ReportSection intact",
        ReportSection.__name__ == "ReportSection",
        "ReportSection still defined for /draft",
    )
    # Round-trip JSON to catch field renames/drops.
    dumped = ledger.model_dump()
    restored = Ledger.model_validate(dumped)
    ok &= check("round-trip", restored.facts[0].predicate == "birth_term", "Ledger JSON round-trip")
    ok &= check("round-trip assertion", restored.facts[0].assertion == "asserted", "assertion survives round-trip")
    return ok


def test_predicate_vocabulary_unit() -> bool:
    """Stage 1: vocabulary is documented, classed, and flags unknowns for review."""

    print("\n=== Predicate vocabulary unit ===")
    ok = True
    ok &= check(
        "non-empty",
        len(PREDICATE_VOCABULARY) >= 10,
        f"vocabulary size={len(PREDICATE_VOCABULARY)}",
    )
    ok &= check(
        "defers_to present",
        "defers_to" in PREDICATES and PREDICATES["defers_to"].predicate_class == "record",
        "defers_to provenance predicate registered",
    )
    ok &= check(
        "allergy takes qualifier",
        PREDICATES["allergy_status"].takes_qualifier is True,
        "allergy_status.takes_qualifier",
    )
    ok &= check(
        "dict sync",
        len(PREDICATES) == len(PREDICATE_VOCABULARY),
        "PREDICATES maps 1:1 with vocabulary tuple",
    )

    for name in (
        "legal_name",
        "dob",
        "birth_term",
        "allergy_status",
        "grade",
        "retention_year",
        "walked_age_months",
        "reading_fluency",
    ):
        ok &= check(
            f"record:{name}",
            predicate_class_of(name) == "record",
            f"{name} class={predicate_class_of(name)} (want record)",
        )

    for name in (
        "inattention_rating",
        "behavioral_concern",
        "anxiety_impression",
        "interview_impression",
    ):
        ok &= check(
            f"perspectival:{name}",
            predicate_class_of(name) == "perspectival",
            f"{name} class={predicate_class_of(name)} (want perspectival)",
        )

    for spec in PREDICATE_VOCABULARY:
        ok &= check(
            f"classed:{spec.name}",
            spec.predicate_class in ("record", "perspectival"),
            f"{spec.name} has class {spec.predicate_class!r}",
        )
        ok &= check(
            f"described:{spec.name}",
            bool(spec.description.strip()),
            f"{spec.name} has description",
        )

    ok &= check(
        "unknown flagged",
        needs_predicate_review("not_a_real_predicate") is True,
        "unknown predicate → needs_predicate_review",
    )
    ok &= check(
        "known not flagged",
        needs_predicate_review("birth_term") is False,
        "known predicate → no review flag",
    )
    spec, review = resolve_predicate("allergy_status")
    ok &= check(
        "resolve known",
        spec is not None and review is False and spec.predicate_class == "record",
        f"resolve allergy_status → class={getattr(spec, 'predicate_class', None)} review={review}",
    )
    spec_u, review_u = resolve_predicate("made_up_claim_type")
    ok &= check(
        "resolve unknown",
        spec_u is None and review_u is True,
        f"resolve unknown → spec={spec_u} review={review_u}",
    )

    # Grade default temporality must be as_of — retention/redshirt break age↔grade.
    grade = PREDICATES["grade"]
    ok &= check(
        "grade as_of default",
        grade.default_temporality == "as_of",
        f"grade default_temporality={grade.default_temporality}",
    )
    return ok


def test_age_validator_unit() -> bool:
    """Assert the age validator fires on a planted wrong age (no network)."""

    print("\n=== Validator unit: age/DOB consistency ===")
    fixture = _load_fixture(HISTORY_FIXTURE_PATH)
    child = fixture["child"]
    expected = compute_age_years(child["dob"], child["evaluation_date"])
    bad = ReportSection(
        section="history",
        prose=f"{child['initials']} is a {expected + 3}-year-old student.",
        facts=[
            SourcedFact(
                statement=f"Student is {expected + 3} years old.",
                source_id="school-cum-2024",
                source_date="2024-09-01",
                life_stage="current",
            )
        ],
        conflicts=[],
        coverage=["current"],
    )
    ok = True
    fired = False
    try:
        validate_age_consistency(
            bad, dob=child["dob"], evaluation_date=child["evaluation_date"]
        )
    except ValueError as exc:
        fired = True
        ok &= check("fires", True, f"validator raised: {exc}")
    if not fired:
        ok &= check("fires", False, "validator did NOT raise on planted bad age")
    return ok


def test_provenance_validator_unit() -> bool:
    """Assert provenance rejects unknown ids and wrong dates (no network)."""

    print("\n=== Validator unit: provenance spine ===")
    sources = [
        Source(
            id="nurse-health-2024",
            type="school",
            date="2024-09-12",
            label="School Nurse Health Report",
            content="Known peanut allergy.",
        )
    ]
    ok = True

    bad_id = ReportSection(
        section="history",
        prose="Allergy noted.",
        facts=[
            SourcedFact(
                statement="Known peanut allergy.",
                source_id="user-ask",
                source_date="2024-09-12",
                life_stage="current",
            )
        ],
        conflicts=[],
        coverage=["current"],
    )
    try:
        validate_provenance(bad_id, sources)
        ok &= check("unknown id", False, "did not reject unknown source_id")
    except ValueError as exc:
        ok &= check("unknown id", "unknown source_id" in str(exc), f"raised: {exc}")

    bad_date = ReportSection(
        section="history",
        prose="Allergy noted.",
        facts=[
            SourcedFact(
                statement="Known peanut allergy.",
                source_id="nurse-health-2024",
                source_date="2026-07-16",
                life_stage="current",
            )
        ],
        conflicts=[],
        coverage=["current"],
    )
    try:
        validate_provenance(bad_date, sources)
        ok &= check("wrong date", False, "did not reject mismatched source_date")
    except ValueError as exc:
        ok &= check("wrong date", "source_date mismatch" in str(exc), f"raised: {exc}")

    good = ReportSection(
        section="history",
        prose="Allergy noted.",
        facts=[
            SourcedFact(
                statement="Known peanut allergy.",
                source_id="nurse-health-2024",
                source_date="2024-09-12",
                life_stage="current",
                reporter="school nurse",
            )
        ],
        conflicts=[],
        coverage=["current"],
    )
    try:
        validate_provenance(good, sources)
        ok &= check("valid spine", True, "accepts matching source_id + source_date")
    except ValueError as exc:
        ok &= check("valid spine", False, f"unexpected raise: {exc}")

    return ok


def test_conflicts_unit() -> bool:
    """Stage 3: record → conflicts, perspectival → variance; asserted vs denied disagrees."""

    print("\n=== Conflicts unit (deterministic) ===")
    from conflicts import detect_disagreements

    ok = True
    peanut_known = Fact(
        id="f_001",
        subject="child",
        predicate="allergy_status",
        value="known",
        value_text="known peanut",
        qualifier="peanut",
        assertion="asserted",
        source_id="nurse",
        source_date="2024-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    peanut_undiagnosed = Fact(
        id="f_002",
        subject="child",
        predicate="allergy_status",
        value="undiagnosed",
        value_text="undiagnosed peanut",
        qualifier="peanut",
        assertion="asserted",
        source_id="iep",
        source_date="2025-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    dairy = Fact(
        id="f_003",
        subject="child",
        predicate="allergy_status",
        value="undiagnosed",
        value_text="dairy undiagnosed",
        qualifier="dairy",
        assertion="asserted",
        source_id="nurse",
        source_date="2024-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    parent_rating = Fact(
        id="f_004",
        subject="child",
        predicate="inattention_rating",
        value="6/7",
        value_text="severe 6 of 7",
        qualifier=None,
        assertion="asserted",
        source_id="parent",
        source_date="2026-01-01",
        reporter="parent",
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    teacher_rating = Fact(
        id="f_005",
        subject="child",
        predicate="inattention_rating",
        value="2/7",
        value_text="mild 2 of 7",
        qualifier=None,
        assertion="asserted",
        source_id="teacher",
        source_date="2026-01-01",
        reporter="teacher",
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    denied_elig = Fact(
        id="f_006",
        subject="child",
        predicate="special_education_eligibility",
        value="none",
        value_text="no prior eligibility",
        qualifier=None,
        assertion="denied",
        source_id="assess",
        source_date="2026-01-01",
        reporter=None,
        life_stage="school-age",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    asserted_elig = Fact(
        id="f_007",
        subject="child",
        predicate="special_education_eligibility",
        value="none",
        value_text="eligibility none",
        qualifier=None,
        assertion="asserted",
        source_id="school",
        source_date="2026-01-01",
        reporter=None,
        life_stage="school-age",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )

    conflicts, variance, _timelines, _preds, _subs = detect_disagreements(
        [peanut_known, peanut_undiagnosed, dairy, parent_rating, teacher_rating]
    )
    ok &= check(
        "peanut conflict",
        any(c.predicate == "allergy_status" and c.qualifier == "peanut" for c in conflicts),
        f"conflicts={[c.topic for c in conflicts]}",
    )
    ok &= check(
        "dairy not conflicting alone",
        not any(c.qualifier == "dairy" for c in conflicts),
        "dairy alone is not a conflict",
    )
    ok &= check(
        "rating is variance",
        len(variance) == 1 and variance[0].predicate == "inattention_rating",
        f"variance={[v.topic for v in variance]}",
    )
    ok &= check(
        "rating not conflict",
        not any(c.predicate == "inattention_rating" for c in conflicts),
        "perspectival must not enter conflicts",
    )

    conflicts2, _, _timelines2, _, _ = detect_disagreements([denied_elig, asserted_elig])
    ok &= check(
        "asserted vs denied",
        len(conflicts2) == 1 and conflicts2[0].predicate == "special_education_eligibility",
        f"asserted/denied conflicts={[c.topic for c in conflicts2]}",
    )

    # Temporality-aware: different as_of dates → timeline, not conflict.
    age_2024 = Fact(
        id="f_age7",
        subject="child",
        predicate="age_years",
        value="7",
        value_text="7 years old",
        qualifier=None,
        assertion="asserted",
        source_id="cum",
        source_date="2024-09-01",
        as_of_date="2024-09-01",
        reporter=None,
        life_stage="school-age",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    age_2026 = Fact(
        id="f_age9",
        subject="child",
        predicate="age_years",
        value="9",
        value_text="9 years old",
        qualifier=None,
        assertion="asserted",
        source_id="computed",
        source_date="2026-07-16",
        as_of_date="2026-07-16",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    grade_2 = Fact(
        id="f_g2",
        subject="child",
        predicate="grade",
        value="2",
        value_text="2nd grade",
        qualifier=None,
        assertion="asserted",
        source_id="cum",
        source_date="2024-09-01",
        as_of_date="2024-09-01",
        reporter=None,
        life_stage="school-age",
        grade="2",
        temporality="as_of",
        confidence="stated",
    )
    grade_4 = Fact(
        id="f_g4",
        subject="child",
        predicate="grade",
        value="4",
        value_text="4th grade",
        qualifier=None,
        assertion="asserted",
        source_id="teacher",
        source_date="2026-06-15",
        as_of_date="2026-06-15",
        reporter=None,
        life_stage="current",
        grade="4",
        temporality="as_of",
        confidence="stated",
    )
    age_same_date_a = Fact(
        id="f_age_a",
        subject="child",
        predicate="age_years",
        value="8",
        value_text="age 8",
        qualifier=None,
        assertion="asserted",
        source_id="s1",
        source_date="2026-01-01",
        as_of_date="2026-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )
    age_same_date_b = Fact(
        id="f_age_b",
        subject="child",
        predicate="age_years",
        value="9",
        value_text="age 9",
        qualifier=None,
        assertion="asserted",
        source_id="s2",
        source_date="2026-01-01",
        as_of_date="2026-01-01",
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
    )

    c_age, _, timelines_age, _, _ = detect_disagreements([age_2024, age_2026])
    ok &= check(
        "age timeline no conflict",
        len(c_age) == 0,
        f"age conflicts={[c.topic for c in c_age]}",
    )
    age_tl = next((t for t in timelines_age if t.predicate == "age_years"), None)
    ok &= check(
        "age timeline entries",
        age_tl is not None and len(age_tl.entries) == 2,
        f"age timeline={age_tl}",
    )
    if age_tl:
        latest = [e for e in age_tl.entries if e.is_latest]
        ok &= check(
            "age latest is 9@2026",
            len(latest) == 1 and latest[0].value == "9",
            f"latest={latest}",
        )

    c_grade, _, timelines_grade, _, _ = detect_disagreements([grade_2, grade_4])
    ok &= check(
        "grade timeline no conflict",
        len(c_grade) == 0,
        f"grade conflicts={[c.topic for c in c_grade]}",
    )
    ok &= check(
        "grade timeline present",
        any(t.predicate == "grade" and len(t.entries) == 2 for t in timelines_grade),
        f"grade timelines={[t.topic for t in timelines_grade]}",
    )

    c_same, _, _, _, _ = detect_disagreements([age_same_date_a, age_same_date_b])
    ok &= check(
        "same-date age conflict",
        len(c_same) == 1 and c_same[0].predicate == "age_years",
        f"same-date conflicts={[c.topic for c in c_same]}",
    )

    return ok


def test_canonical_subjects_unit() -> bool:
    """Canonical subjects documented; unknowns flagged for review."""

    print("\n=== Canonical subjects unit ===")
    from predicates import CANONICAL_SUBJECTS, needs_subject_review

    ok = True
    ok &= check(
        "canonical set",
        CANONICAL_SUBJECTS == frozenset({"child", "mother", "father", "school"}),
        f"CANONICAL_SUBJECTS={CANONICAL_SUBJECTS}",
    )
    for s in CANONICAL_SUBJECTS:
        ok &= check(f"ok:{s}", needs_subject_review(s) is False, f"{s} not for review")
    ok &= check(
        "source id ok",
        needs_subject_review("nurse-2024", known_source_ids={"nurse-2024"}) is False,
        "known source id accepted",
    )
    ok &= check(
        "name flagged",
        needs_subject_review("Justin M.") is True,
        "display names must be flagged",
    )
    return ok


def test_subject_stamping_and_grouping_unit() -> bool:
    """
    Stage 5.3: subject enum + provenance stamp; non-provenance never keys on source id.
    """

    print("\n=== Subject stamping + grouping (Stage 5.3) ===")
    from conflicts import detect_disagreements, record_value_conflicts
    from predicates import CANONICAL_SUBJECTS, ExtractSubjectName, is_provenance_predicate

    ok = True
    child = Child(initials="J.M.", dob="2015-04-22", evaluation_date="2026-07-16")
    nurse = Source(
        id="nurse-health-2024",
        type="school",
        date="2024-09-12",
        label="Nurse",
        content="Justin M. known peanut allergy.",
    )
    iep = Source(
        id="iep-health-2025",
        type="school",
        date="2025-03-18",
        label="IEP",
        content="Jason M. undiagnosed peanut.",
    )

    # Even if the model picks a non-child canonical subject, clinical facts stay
    # on the enum — never the source id.
    name_nurse = draft_to_fact(
        ExtractedFactDraft(
            subject=ExtractSubjectName.child,
            predicate="legal_name",
            value="Justin M.",
            value_text="Justin M.",
            life_stage="current",
            confidence="stated",
        ),
        fact_id="f_n",
        source=nurse,
        child=child,
    )
    name_iep = draft_to_fact(
        ExtractedFactDraft(
            subject=ExtractSubjectName.school,  # odd choice — still canonical
            predicate="legal_name",
            value="Jason M.",
            value_text="Jason M.",
            life_stage="current",
            confidence="stated",
        ),
        fact_id="f_i",
        source=iep,
        child=child,
    )
    # Force both onto child for the conflict lock (model default is child).
    name_iep = name_iep.model_copy(update={"subject": "child"})

    ok &= check(
        "nurse name subject",
        name_nurse.subject == "child",
        f"nurse legal_name subject={name_nurse.subject!r}",
    )
    ok &= check(
        "non-provenance not source id",
        name_nurse.subject in CANONICAL_SUBJECTS
        and name_nurse.subject != nurse.id,
        f"subject={name_nurse.subject!r}",
    )

    defer = draft_to_fact(
        ExtractedFactDraft(
            subject=ExtractSubjectName.child,  # ignored for provenance
            predicate="defers_to",
            value="iep",
            value_text="see IEP",
            life_stage="current",
            confidence="stated",
        ),
        fact_id="f_d",
        source=nurse,
        child=child,
    )
    ok &= check(
        "provenance subject=source",
        defer.subject == nurse.id and is_provenance_predicate("defers_to"),
        f"defers_to subject={defer.subject!r}",
    )

    allergy_n = Fact(
        id="f_a1",
        subject="child",
        predicate="allergy_status",
        value="known",
        value_text="known peanut",
        qualifier="peanut",
        assertion="asserted",
        source_id=nurse.id,
        source_date=nurse.date,
        as_of_date=nurse.date,
        life_stage="current",
        temporality="durable",
        confidence="stated",
    )
    allergy_i = Fact(
        id="f_a2",
        subject="child",
        predicate="allergy_status",
        value="undiagnosed",
        value_text="undiagnosed",
        qualifier="peanut",
        assertion="asserted",
        source_id=iep.id,
        source_date=iep.date,
        as_of_date=iep.date,
        life_stage="current",
        temporality="durable",
        confidence="stated",
    )
    tutor_school = Fact(
        id="f_t1",
        subject="child",
        predicate="private_tutoring",
        value="none",
        value_text="no tutoring",
        assertion="asserted",
        source_id="school-iep-none-2025",
        source_date="2025-05-20",
        as_of_date="2025-05-20",
        life_stage="school-age",
        temporality="durable",
        confidence="stated",
    )
    tutor_parent = Fact(
        id="f_t2",
        subject="child",
        predicate="private_tutoring",
        value="yes",
        value_text="private tutoring",
        assertion="asserted",
        source_id="parent-dev-2026",
        source_date="2026-06-01",
        as_of_date="2026-06-01",
        life_stage="school-age",
        temporality="durable",
        confidence="stated",
    )

    conflicts, _, _, _, _ = detect_disagreements(
        [name_nurse, name_iep, allergy_n, allergy_i, tutor_school, tutor_parent]
    )
    topics = {c.topic for c in conflicts}
    ok &= check("name conflict", "legal_name" in topics, f"topics={topics}")
    ok &= check(
        "allergy conflict",
        "allergy_status:peanut" in topics,
        f"topics={topics}",
    )
    ok &= check("tutoring conflict", "private_tutoring" in topics, f"topics={topics}")

    # Regression shape: source-id subjects must not produce the clinical conflicts.
    sharded = [
        name_nurse.model_copy(update={"subject": nurse.id}),
        name_iep.model_copy(update={"subject": iep.id}),
    ]
    sharded_conflicts = record_value_conflicts(sharded)
    ok &= check(
        "sharded names no conflict",
        len(sharded_conflicts) == 0,
        f"sharded conflicts={sharded_conflicts} (documents the 5.2 bug)",
    )
    return ok


def _assert_provenance(ans: dict, fixture: dict) -> bool:
    source_by_id = {s["id"]: s for s in fixture["sources"]}
    bad_refs = [
        f.get("source_id")
        for f in ans.get("facts", [])
        if f.get("source_id") not in source_by_id
    ]
    ok = check("source_ids", len(bad_refs) == 0, f"unknown source_ids={bad_refs[:3]}")
    bad_dates = [
        f.get("source_id")
        for f in ans.get("facts", [])
        if f.get("source_id") in source_by_id
        and f.get("source_date") != source_by_id[f["source_id"]]["date"]
    ]
    ok &= check("source_dates", len(bad_dates) == 0, f"date mismatches={bad_dates[:3]}")
    return ok


def _eval_conflicts_against_expected(
    name: str,
    detected_conflicts: list,
    fixture: dict,
    *,
    variance: list | None = None,
) -> tuple[bool, float, float]:
    expected_conflicts = fixture.get("expected_conflicts") or []
    c_found, c_missed, c_extra = score_conflicts(detected_conflicts, expected_conflicts)
    c_prec, c_rec = report_pr(
        f"{name} conflicts",
        c_found,
        c_missed,
        c_extra,
        expected_n=len(expected_conflicts),
    )
    if variance is not None:
        print(f"  {name} variance (not scored as conflicts): {len(variance)}")
        for v in variance:
            topic = v.topic if isinstance(v, Disagreement) else v.get("topic")
            print(f"    variance: {topic}")
    ok = True
    ok &= check(
        f"{name} conflict recall",
        c_rec == 1.0,
        f"conflict recall={c_rec:.2f} (want 1.00)",
    )
    ok &= check(
        f"{name} conflict precision",
        c_prec == 1.0,
        f"conflict precision={c_prec:.2f} (want 1.00; false_positives={len(c_extra)})",
    )
    return ok, c_prec, c_rec


def _eval_ledger_facts_against_expected(
    name: str,
    facts: list[Fact],
    fixture: dict,
) -> tuple[bool, float]:
    expected = fixture.get("expected_ledger_facts") or []
    if not expected:
        print(f"  {name} facts: (no expected_ledger_facts; skip ledger fact recall)")
        return True, 1.0
    found, missed = _score_ledger_facts(facts, expected)
    recall = (len(found) / len(expected)) if expected else 1.0
    print(
        f"  {name} facts: found={len(found)}/{len(expected)}  "
        f"missed={len(missed)}  recall={recall:.2f}"
    )
    for m in missed:
        print(f"    missed: {m}")
    ok = check(
        f"{name} fact recall",
        recall == 1.0,
        f"fact recall={recall:.2f} (want 1.00; missed={len(missed)})",
    )
    return ok, recall


def test_stage0_via_extract_conflicts() -> bool:
    """
    Stage 0 baseline re-run: extract → /conflicts (deterministic), score P/R.

    No-conflict fixture must have empty conflicts; perspectival rater
    disagreement may appear only in variance.
    """

    from dotenv import load_dotenv
    from conflicts import detect_disagreements_from_ledger

    load_dotenv(WORKDIR / ".env")
    print("\n=== Stage 0 / Stage 5.2 extract → conflicts ===")
    print(
        "  Stage 0 baseline (temp 1.0, single-run /ask era): "
        "history conflict R=0.00 P=0.00; "
        "health R=1.00 P=1.00 (contaminated plants); no-conflict FP on ratings"
    )
    print(
        "  Stage 5.1 variance (temp 1.0, five-run extract→conflicts means): "
        "history P=1.00 R=1.00 factR=1.00; "
        "health P=1.00 R=0.60 factR=0.97; "
        "no_conflict P=0.00 R=1.00"
    )
    print(
        "  Current run uses EXTRACT_TEMPERATURE=0 — "
        "superseding Stage 0 as the live baseline; keep temp-1.0 figures above for contrast."
    )

    ok = True
    summaries: list[str] = []

    for path, label in (
        (HISTORY_FIXTURE_PATH, "history"),
        (HEALTH_FIXTURE_PATH, "health"),
        (NO_CONFLICT_FIXTURE_PATH, "no_conflict"),
    ):
        fixture = _load_fixture(path)
        child = Child.model_validate(fixture["child"])
        sources = [Source.model_validate(s) for s in fixture["sources"]]
        model = fixture.get("model") or "gpt-4o-mini"
        provider = ModelProvider()
        ledger, tokens_by_source, prompt_tokens, completion_tokens, review, _subj, gap_report, _timelines = (
            build_ledger(provider, child=child, sources=sources, model=model)
        )
        cost = compute_cost_usd(model, prompt_tokens, completion_tokens)
        conflicts, variance, timelines, _, _ = detect_disagreements_from_ledger(ledger)

        print(f"\n--- {label} ({path.name}) ---")
        print(
            f"  extract tokens={sum(tokens_by_source.values())}  "
            f"cost_usd={round(cost, 6)}  review={review}"
        )
        print(
            f"  conflicts={len(conflicts)}  variance={len(variance)}  "
            f"timelines={len(timelines)}"
        )
        hist = next((s for s in gap_report.sections if s.section == "history"), None)
        if hist:
            print(
                f"  gap: available={hist.available}  "
                f"empty_stages={hist.life_stages_empty}  "
                f"missing_preds={len(hist.predicates_missing)}"
            )
        for c in conflicts:
            print(
                f"    conflict: {c.topic}  values="
                f"{sorted({(v.assertion, v.value) for v in c.versions})}  "
                f"sources={sorted({v.source_id for v in c.versions})}"
            )

        part_ok, c_prec, c_rec = _eval_conflicts_against_expected(
            label, conflicts, fixture, variance=variance
        )
        ok &= part_ok
        fact_ok, f_rec = _eval_ledger_facts_against_expected(label, ledger.facts, fixture)
        ok &= fact_ok

        forbidden = fixture.get("forbidden_predicates_by_source") or {}
        for source_id, preds in forbidden.items():
            bad = [
                f
                for f in ledger.facts
                if f.source_id == source_id and f.predicate in preds
            ]
            ok &= check(
                f"{label} forbidden:{source_id}",
                len(bad) == 0,
                f"{source_id} forbidden preds={[(b.predicate, b.value) for b in bad]}",
            )

        summaries.append(
            f"{label}: conflict P={c_prec:.2f} R={c_rec:.2f}; "
            f"ledger fact R={f_rec:.2f}; variance={len(variance)}"
        )

    print("\n  STAGE 0 vs NOW")
    for line in summaries:
        print(f"    {line}")
    return ok


def test_ask_age_guard(base: str) -> bool:
    """/ask still enforces confirm_synthetic + force_bad_age (pipeline under the hood)."""

    print("\n=== main /ask: synthetic guard + force_bad_age ===")
    fixture = _load_fixture(HISTORY_FIXTURE_PATH)
    expected_age = compute_age_years(fixture["child"]["dob"], fixture["child"]["evaluation_date"])

    blocked_status, _ = post(
        base,
        {
            "section": "history",
            "child": fixture["child"],
            "sources": fixture["sources"][:1],
        },
    )
    ok = True
    ok &= check(
        "synthetic guard",
        blocked_status == 422,
        f"missing confirm_synthetic → HTTP {blocked_status} (expect 422)",
    )

    # One source keeps pipeline cost down; force_bad_age plants then retries via extract→draft.
    payload = ask_payload(fixture, force_bad_age=True, model="gpt-4o-mini")
    payload["sources"] = fixture["sources"][:1]
    status, data = post(base, payload)
    ans = data.get("answer", {})
    ok &= check("status", status == 200, f"HTTP {status} detail={data.get('detail')}")
    ok &= check(
        "expected age",
        data.get("age_years_expected") == expected_age,
        f"age_years_expected={data.get('age_years_expected')} (want {expected_age})",
    )
    ok &= check(
        "prose",
        isinstance(ans.get("prose"), str) and len(ans.get("prose", "")) > 20,
        "prose present",
    )
    ok &= check(
        "tokens summed",
        isinstance(data.get("tokens_used"), int) and data.get("tokens_used", 0) > 0,
        f"tokens_used={data.get('tokens_used')} (pipeline must count extract+draft+entailment)",
    )
    print(f"  tokens_used={data.get('tokens_used')}  cost_usd={data.get('cost_usd')}")
    return ok


def test_ingest_unit(base: str) -> bool:
    """/ingest returns a confirmation suggestion — never silent apply."""

    print("\n=== main /ingest ===")
    r = httpx.post(
        f"{base}/ingest",
        json={
            "confirm_synthetic": True,
            "content": (
                "School Nurse Health Report dated 2024-09-12. "
                "Student has a known allergy to peanuts."
            ),
            "model": "gpt-4o-mini",
        },
        timeout=60.0,
    )
    ok = True
    ok &= check("status", r.status_code == 200, f"HTTP {r.status_code}")
    data = r.json()
    sug = data.get("suggestion") or {}
    ok &= check("confirm_required", data.get("confirm_required") is True, "confirm_required=true")
    ok &= check("source_type", sug.get("source_type") in {
        "assessment", "school", "parent", "teacher", "observation", "prior_eval", "other"
    }, f"source_type={sug.get('source_type')}")
    ok &= check(
        "source_date",
        isinstance(sug.get("source_date"), str) and len(sug.get("source_date", "")) >= 8,
        f"source_date={sug.get('source_date')}",
    )
    ok &= check("label", bool(sug.get("label")), f"label={sug.get('label')}")
    print(f"  suggestion={sug}  tokens={data.get('tokens_used')}  cost={data.get('cost_usd')}")
    return ok


def _ledger_fact_matches(expected: dict, fact: Fact) -> bool:
    if expected.get("source_id") and fact.source_id != expected["source_id"]:
        return False
    if expected.get("predicate") and fact.predicate != expected["predicate"]:
        return False
    if expected.get("assertion") and fact.assertion != expected["assertion"]:
        return False
    if expected.get("subject") and fact.subject != expected["subject"]:
        return False
    if "qualifier" in expected:
        exp_q = normalize_qualifier(expected.get("qualifier"))
        if fact.qualifier != exp_q:
            return False
    if expected.get("value") is not None:
        want = str(expected["value"]).lower()
        got = fact.value.lower()
        if want != got and want not in got and got not in want:
            return False
    return True


def _score_ledger_facts(
    detected: list[Fact], expected: list[dict]
) -> tuple[list[dict], list[dict]]:
    remaining = list(detected)
    found: list[dict] = []
    missed: list[dict] = []
    for exp in expected:
        match_idx = next(
            (i for i, det in enumerate(remaining) if _ledger_fact_matches(exp, det)),
            None,
        )
        if match_idx is None:
            missed.append(exp)
        else:
            found.append(exp)
            remaining.pop(match_idx)
    return found, missed


def _run_extract_fixture(path: Path) -> tuple[bool, dict]:
    """Extract a fixture and score expected_ledger_facts + grouping conflicts."""

    fixture = _load_fixture(path)
    child = Child.model_validate(fixture["child"])
    sources = [Source.model_validate(s) for s in fixture["sources"]]
    model = fixture.get("model") or "gpt-4o-mini"
    provider = ModelProvider()
    ledger, tokens_by_source, prompt_tokens, completion_tokens, review, _subj, gap_report, _timelines = (
        build_ledger(provider, child=child, sources=sources, model=model)
    )
    cost = compute_cost_usd(model, prompt_tokens, completion_tokens)
    expected = fixture.get("expected_ledger_facts") or []
    found, missed = _score_ledger_facts(ledger.facts, expected)
    recall = (len(found) / len(expected)) if expected else 1.0
    conflicts = record_value_conflicts(ledger.facts)
    expected_conflicts = fixture.get("expected_conflicts") or []

    print(f"\n=== /extract: {path.name} ===")
    print(
        f"  tokens={sum(tokens_by_source.values())}  "
        f"by_source={tokens_by_source}  cost_usd={round(cost, 6)}  "
        f"predicates_for_review={review}"
    )
    print(
        f"  ledger facts={len(ledger.facts)}  "
        f"expected_ledger recall={len(found)}/{len(expected)} ({recall:.2f})"
    )
    for m in missed:
        print(f"    missed ledger fact: {m}")
    for f in ledger.facts:
        print(
            f"    {f.id}  {f.source_id}  {f.subject}/{f.predicate}"
            f"{(':' + f.qualifier) if f.qualifier else ''}"
            f"  {f.assertion}={f.value!r}"
        )

    ok = True
    if expected:
        ok &= check(
            "ledger recall",
            recall == 1.0,
            f"ledger fact recall={recall:.2f} (want 1.00; missed={len(missed)})",
        )

    forbidden = fixture.get("forbidden_predicates_by_source") or {}
    for source_id, preds in forbidden.items():
        bad = [
            f
            for f in ledger.facts
            if f.source_id == source_id and f.predicate in preds
        ]
        ok &= check(
            f"forbidden:{source_id}",
            len(bad) == 0,
            f"{source_id} clinical/forbidden predicates={[(b.predicate, b.value) for b in bad]} (want [])",
        )

    # Grouping-level conflict checks (Stage 2.5 lock before Stage 3).
    if expected_conflicts == [] and "expected_conflicts" in fixture:
        ok &= check(
            "grouping conflicts empty",
            len(conflicts) == 0,
            f"record_value_conflicts={len(conflicts)} (want 0)",
        )
        for c in conflicts:
            print(f"    false_positive grouping conflict: {c['topic']} values={c['values']}")
    elif expected_conflicts:
        # Require each expected conflict (by source_ids + optional predicate/qualifier) to appear.
        remaining = list(conflicts)
        for exp in expected_conflicts:
            exp_ids = set(exp.get("source_ids") or [])
            exp_pred = exp.get("predicate")
            exp_qual = normalize_qualifier(exp.get("qualifier")) if "qualifier" in exp else None

            def _match(c: dict, _ids=exp_ids, _pred=exp_pred, _qual=exp_qual) -> bool:
                if _ids and not _ids.issubset(set(c["source_ids"])):
                    return False
                if _pred and c["predicate"] != _pred:
                    return False
                if _qual is not None and c["qualifier"] != _qual:
                    return False
                return True

            idx = next((i for i, c in enumerate(remaining) if _match(c)), None)
            if idx is None:
                ok &= check(
                    f"conflict:{exp.get('topic')}",
                    False,
                    f"missed grouping conflict {exp}",
                )
            else:
                ok &= check(
                    f"conflict:{exp.get('topic')}",
                    True,
                    f"found grouping conflict {exp.get('topic')}",
                )
                remaining.pop(idx)

    meta = {
        "path": path.name,
        "recall": recall,
        "found": len(found),
        "expected": len(expected),
        "missed": missed,
        "conflicts": conflicts,
        "tokens": sum(tokens_by_source.values()),
        "cost_usd": round(cost, 6),
        "facts": ledger.facts,
        "ledger": ledger,
        "gap_report": gap_report,
    }
    return ok, meta


def test_derived_and_coverage_unit() -> bool:
    """Stage 4.5 unit: derivation field, recomputation, gap arithmetic."""

    print("\n=== Stage 4.5 unit: derived facts + coverage ===")
    from coverage import build_gap_report
    from derived import (
        AGE_DERIVATION,
        COMPUTED_SOURCE_ID,
        REQUEST_SOURCE_ID,
        build_age_years_fact,
        build_request_dob_fact,
        inject_derived_and_request_facts,
        validate_derived_facts,
    )
    from schemas import Child, Fact, Source

    ok = True
    child = Child(initials="K.L.", dob="2016-08-10", evaluation_date="2026-07-16")
    expected_age = compute_age_years(child.dob, child.evaluation_date)

    age = build_age_years_fact(child, fact_id="f_age")
    ok &= check("age derivation", age.derivation == AGE_DERIVATION, f"derivation={age.derivation}")
    ok &= check("age source", age.source_id == COMPUTED_SOURCE_ID, f"source_id={age.source_id}")
    ok &= check("age value", age.value == str(expected_age), f"value={age.value}")
    ok &= check("age as_of", age.temporality == "as_of", f"temporality={age.temporality}")

    dob = build_request_dob_fact(child, fact_id="f_dob")
    ok &= check("dob request", dob.source_id == REQUEST_SOURCE_ID, f"source_id={dob.source_id}")
    ok &= check("dob value", dob.value == child.dob, f"value={dob.value}")
    ok &= check("dob no derivation", dob.derivation is None, f"derivation={dob.derivation}")

    try:
        validate_derived_facts([age], child)
        ok &= check("recompute ok", True, "derived age recomputes")
    except ValueError as exc:
        ok &= check("recompute ok", False, f"unexpected: {exc}")

    bad_age = age.model_copy(update={"value": str(expected_age + 3)})
    try:
        validate_derived_facts([bad_age], child)
        ok &= check("recompute fires", False, "did not raise on wrong derived age")
    except ValueError:
        ok &= check("recompute fires", True, "rejects wrong derived age")

    extracted_dob = Fact(
        id="f_src",
        subject="child",
        predicate="dob",
        value="2015-08-10",
        value_text="DOB on header: 2015-08-10",
        qualifier=None,
        assertion="asserted",
        source_id="school-header-2026",
        source_date="2026-05-01",
        reporter=None,
        life_stage="birth",
        grade=None,
        temporality="durable",
        confidence="stated",
    )
    injected, _ = inject_derived_and_request_facts([extracted_dob], child, next_id=2)
    age_inj = next(f for f in injected if f.derivation == AGE_DERIVATION)
    ok &= check(
        "inherits dispute",
        age_inj.inherits_dispute is True,
        f"inherits_dispute={age_inj.inherits_dispute}",
    )

    from conflicts import detect_disagreements

    conflicts, _, _timelines, _, _ = detect_disagreements(injected)
    dob_conflicts = [c for c in conflicts if c.predicate == "dob"]
    ok &= check(
        "dob conflict",
        len(dob_conflicts) == 1,
        f"dob conflicts={len(dob_conflicts)} versions="
        f"{[v.source_id for c in dob_conflicts for v in c.versions]}",
    )

    # Gap report: school-only facts → birth/infancy empty, available=True, not a failure.
    school_fact = Fact(
        id="f_g",
        subject="child",
        predicate="grade",
        value="4",
        value_text="in 4th grade",
        qualifier=None,
        assertion="asserted",
        source_id="school",
        source_date="2026-05-01",
        reporter=None,
        life_stage="current",
        grade="4",
        temporality="as_of",
        confidence="stated",
    )
    ledger = Ledger(
        child=child,
        ledger_version="1",
        built_at="2026-07-16T00:00:00Z",
        sources=[
            Source(
                id="school",
                type="school",
                date="2026-05-01",
                label="School file",
                content="in 4th grade",
            )
        ],
        facts=[school_fact, *inject_derived_and_request_facts([], child, next_id=10)[0]],
    )
    gap = build_gap_report(ledger)
    hist = gap.sections[0]
    ok &= check("gap available", hist.available is True, f"available={hist.available}")
    ok &= check(
        "gap birth/infancy",
        "birth" in hist.life_stages_empty and "infancy" in hist.life_stages_empty,
        f"empty={hist.life_stages_empty}",
    )
    ok &= check(
        "not graded failure",
        True,
        "gap report describes empty stages; available=True (not a coverage failure)",
    )
    return ok


def test_stage45_verification() -> bool:
    """
    Stage 4.5 live verification:
      - DOB conflict fixture → record conflict + age inherits_dispute
      - missing birth/infancy → gap names them, no coverage failure
      - draft cites derived age; entailment skips it; recomputation holds
      - Stage 2.5 fact recall unchanged (report the number)
    """

    from dotenv import load_dotenv
    from conflicts import detect_disagreements_from_ledger
    from derived import AGE_DERIVATION, COMPUTED_SOURCE_ID
    from draft import draft_section
    from provider import ModelProvider
    from schemas import DraftRequest

    load_dotenv(WORKDIR / ".env")
    print("\n=== Stage 4.5 verification ===")
    ok = True
    provider = ModelProvider()

    # --- DOB conflict ---
    print("\n--- DOB conflict fixture ---")
    part_ok, meta = _run_extract_fixture(DOB_CONFLICT_FIXTURE_PATH)
    ok &= part_ok
    conflicts, _, _timelines, _, _ = detect_disagreements_from_ledger(meta["ledger"])
    dob_c = [c for c in conflicts if c.predicate == "dob"]
    ok &= check(
        "dob conflict detected",
        len(dob_c) >= 1,
        f"dob conflicts={len(dob_c)} "
        f"sources={[sorted({v.source_id for v in c.versions}) for c in dob_c]}",
    )
    age_facts = [f for f in meta["facts"] if f.derivation == AGE_DERIVATION]
    ok &= check("derived age present", len(age_facts) == 1, f"age_facts={len(age_facts)}")
    if age_facts:
        ok &= check(
            "age inherits dispute",
            age_facts[0].inherits_dispute is True,
            f"inherits_dispute={age_facts[0].inherits_dispute}",
        )
    print(
        f"  extract tokens={meta['tokens']}  cost_usd={meta['cost_usd']}"
    )

    # --- Missing birth/infancy gap (coverage only — do not grade unrelated conflicts) ---
    print("\n--- Missing birth/infancy fixture ---")
    fixture_gap = _load_fixture(MISSING_BIRTH_FIXTURE_PATH)
    child_gap = Child.model_validate(fixture_gap["child"])
    sources_gap = [Source.model_validate(s) for s in fixture_gap["sources"]]
    model_gap = fixture_gap.get("model") or "gpt-4o-mini"
    ledger_gap, tokens_gap, p_gap, c_gap, review_gap, _subj_gap, gap_report, _tl_gap = build_ledger(
        provider, child=child_gap, sources=sources_gap, model=model_gap
    )
    cost_gap = compute_cost_usd(model_gap, p_gap, c_gap)
    hist = next(s for s in gap_report.sections if s.section == "history")
    expected_empty = set(fixture_gap.get("expected_gap_life_stages_empty") or [])
    empty = set(hist.life_stages_empty)
    ok &= check(
        "gap names birth/infancy",
        expected_empty.issubset(empty),
        f"empty_stages={sorted(empty)} (want ⊇ {sorted(expected_empty)})",
    )
    ok &= check(
        "no coverage failure",
        hist.available is True,
        f"available={hist.available} (absence is not failure)",
    )
    print(
        f"  extract tokens={sum(tokens_gap.values())}  cost_usd={round(cost_gap, 6)}  "
        f"empty_stages={hist.life_stages_empty}  review={review_gap}"
    )

    # --- Draft cites derived age ---
    print("\n--- Draft cites derived age ---")
    child = Child(initials="A.R.", dob="2017-03-15", evaluation_date="2026-07-16")
    expected_age = compute_age_years(child.dob, child.evaluation_date)
    from derived import build_age_years_fact, build_request_dob_fact

    sources = [
        Source(
            id="parent",
            type="parent",
            date="2026-06-01",
            label="Parent form",
            content="Birth: full-term. Student referred for reading concerns.",
        )
    ]
    facts = [
        Fact(
            id="f_001",
            subject="child",
            predicate="birth_term",
            value="full-term",
            value_text="Birth: full-term",
            qualifier=None,
            assertion="asserted",
            source_id="parent",
            source_date="2026-06-01",
            reporter=None,
            life_stage="birth",
            grade=None,
            temporality="durable",
            confidence="stated",
        ),
        build_request_dob_fact(child, fact_id="f_002"),
        build_age_years_fact(child, fact_id="f_003"),
    ]
    ledger = Ledger(
        child=child,
        ledger_version="1",
        built_at="2026-07-16T00:00:00Z",
        sources=sources,
        facts=facts,
    )
    resp = draft_section(
        provider,
        DraftRequest(
            confirm_synthetic=True,
            section="history",
            ledger=ledger,
            conflicts=[],
            variance=[],
            model="gpt-4o-mini",
            entailment_model="gpt-4o-mini",
        ),
    )
    ok &= check("draft populated", resp.section_populated is True, f"populated={resp.section_populated}")
    age_cited = [
        f
        for f in (resp.answer.facts if resp.answer else [])
        if f.fact_id == "f_003"
    ]
    ok &= check(
        "age cites derived fact",
        len(age_cited) >= 1,
        f"statements citing f_003={len(age_cited)}",
    )
    entail_fails = [i for i in resp.review.items if i.kind == "entailment_failure"]
    age_entail = [i for i in entail_fails if i.fact_id == "f_003"]
    ok &= check(
        "entailment skips derived",
        len(age_entail) == 0,
        f"entailment failures on derived age={len(age_entail)}",
    )
    ok &= check(
        "age_years_expected",
        resp.age_years_expected == expected_age,
        f"age_years_expected={resp.age_years_expected} (want {expected_age})",
    )
    print(
        f"  draft tokens={resp.tokens_used}  by_stage={resp.tokens_by_stage}  "
        f"cost_usd={resp.cost_usd}"
    )
    if resp.answer:
        print(f"  prose preview: {resp.answer.prose[:200]}...")

    # --- Fact-level recall vs Stage 2.5 (history assess-only lock; retry once on miss) ---
    print("\n--- Fact-level recall (Stage 2.5 lock) ---")
    history = _load_fixture(HISTORY_FIXTURE_PATH)
    assess_only = {
        **history,
        "sources": [s for s in history["sources"] if s["id"] == "assess-2026-wisc"],
        "expected_ledger_facts": history["expected_ledger_facts"],
        "expected_conflicts": [],
    }
    assess_path = FIXTURES / "_tmp_assess_only_s45.json"
    assess_path.write_text(json.dumps(assess_only), encoding="utf-8")
    try:
        best_recall = 0.0
        best_meta: dict | None = None
        for attempt in range(2):
            part_ok, meta_r = _run_extract_fixture(assess_path)
            best_meta = meta_r
            best_recall = meta_r["recall"]
            if best_recall == 1.0:
                break
            print(f"  recall attempt {attempt + 1}={best_recall:.2f}; retrying once")
        assert best_meta is not None
        ok &= check(
            "fact recall unchanged",
            best_recall == 1.0,
            f"Stage 2.5 fact recall={best_recall:.2f} "
            f"({best_meta['found']}/{best_meta['expected']})",
        )
        print(
            f"  FACT RECALL (Stage 2.5 lock): {best_meta['found']}/{best_meta['expected']} "
            f"= {best_recall:.2f}  tokens={best_meta['tokens']}  "
            f"cost_usd={best_meta['cost_usd']}"
        )
        derived = [f for f in best_meta["facts"] if f.source_id == COMPUTED_SOURCE_ID]
        ok &= check(
            "computed age on assess ledger",
            len(derived) == 1,
            f"computed={len(derived)}",
        )
    finally:
        assess_path.unlink(missing_ok=True)

    return ok


def test_extract_stage25() -> bool:
    """
    Stage 2.5 locks: denied findings, deferral provenance, qualifier grouping.
    Runs live /extract against fixtures (costs tokens).
    """

    from dotenv import load_dotenv

    load_dotenv(WORKDIR / ".env")

    print("\n=== Stage 2.5: live /extract regression locks ===")
    ok = True
    # 1) Genuine negative finding — assessment source only (cheap, focused).
    history = _load_fixture(HISTORY_FIXTURE_PATH)
    assess_only = {
        **history,
        "sources": [s for s in history["sources"] if s["id"] == "assess-2026-wisc"],
        "expected_ledger_facts": history["expected_ledger_facts"],
        "expected_conflicts": [],
    }
    assess_path = FIXTURES / "_tmp_assess_only.json"
    assess_path.write_text(json.dumps(assess_only), encoding="utf-8")
    try:
        part_ok, meta = _run_extract_fixture(assess_path)
        ok &= part_ok
        denied = [
            f
            for f in meta["facts"]
            if f.predicate == "special_education_eligibility" and f.assertion == "denied"
        ]
        ok &= check(
            "denied eligibility",
            len(denied) >= 1,
            f"special_education_eligibility denied facts={len(denied)} (want ≥1)",
        )
    finally:
        if assess_path.exists():
            assess_path.unlink()

    # 2+4) Health: deferral + same-qualifier peanut conflict.
    part_ok, health_meta = _run_extract_fixture(HEALTH_FIXTURE_PATH)
    ok &= part_ok
    father_facts = [f for f in health_meta["facts"] if f.source_id == "father-interview-2026"]
    defers = [f for f in father_facts if f.predicate == "defers_to"]
    ok &= check("father defers_to", len(defers) >= 1, f"defers_to facts={len(defers)} (want ≥1)")

    # 3) Coexisting allergies must not conflict.
    part_ok, co_meta = _run_extract_fixture(COEXISTING_ALLERGIES_PATH)
    ok &= part_ok
    allergy_facts = [f for f in co_meta["facts"] if f.predicate == "allergy_status"]
    quals = {f.qualifier for f in allergy_facts}
    ok &= check(
        "two qualifiers",
        "peanut" in quals and "dairy" in quals,
        f"allergy qualifiers={quals} (want peanut + dairy)",
    )
    return ok


TESTS = [
    ("serve_stage1", test_stage1),
    ("serve_stage2", test_stage2),
    ("serve_stage3", test_stage3),
    ("serve_stage4", test_stage4),
]


def test_draft_validators_unit() -> bool:
    """Stage 4: terminology, temporal framing, section-empty, conflict mention."""

    print("\n=== Draft validators unit ===")
    from draft import section_has_supporting_facts
    from draft_validators import (
        validate_conflicts_mentioned,
        validate_temporal_framing,
        validate_terminology_flags,
    )
    from schemas import (
        Child,
        Disagreement,
        DisagreementVersion,
        DraftProseOutput,
        DraftStatement,
        Fact,
        Ledger,
        Source,
    )
    from terminology import find_terminology_violations

    ok = True
    hits = find_terminology_violations("SS falls in the Extremely Low range.")
    ok &= check(
        "terminology hit",
        hits == [("Extremely Low", "Very Low")],
        f"hits={hits}",
    )
    flags = validate_terminology_flags("SS falls in the Extremely Low range.")
    ok &= check("terminology review", len(flags) == 1 and flags[0].kind == "terminology", f"flags={flags}")

    empty_ledger = Ledger(
        child=Child(initials="A.R.", dob="2017-03-15", evaluation_date="2026-07-16"),
        ledger_version="1",
        built_at="2026-07-16T00:00:00Z",
        sources=[],
        facts=[],
    )
    ok &= check(
        "section empty",
        section_has_supporting_facts(empty_ledger, "history") is False,
        "empty ledger → section cannot populate",
    )

    stale = Fact(
        id="f_001",
        subject="child",
        predicate="grade",
        value="2",
        value_text="in 2nd grade",
        qualifier=None,
        assertion="asserted",
        source_id="cum",
        source_date="2024-09-01",
        as_of_date="2024-09-01",
        reporter=None,
        life_stage="school-age",
        grade="2",
        temporality="as_of",
        confidence="stated",
    )
    current_grade = Fact(
        id="f_002",
        subject="child",
        predicate="grade",
        value="4",
        value_text="in 4th grade",
        qualifier=None,
        assertion="asserted",
        source_id="teacher",
        source_date="2026-06-15",
        as_of_date="2026-06-15",
        reporter=None,
        life_stage="current",
        grade="4",
        temporality="as_of",
        confidence="stated",
    )
    ledger = Ledger(
        child=Child(initials="A.R.", dob="2017-03-15", evaluation_date="2026-07-16"),
        ledger_version="1",
        built_at="2026-07-16T00:00:00Z",
        sources=[
            Source(
                id="cum",
                type="school",
                date="2024-09-01",
                label="Cum file",
                content="Student is in 2nd grade.",
            ),
            Source(
                id="teacher",
                type="teacher",
                date="2026-06-15",
                label="Teacher",
                content="Student is in 4th grade.",
            ),
        ],
        facts=[stale, current_grade],
    )
    bad_out = DraftProseOutput(
        prose="A.R. is in 2nd grade.",
        statements=[DraftStatement(statement="A.R. is in 2nd grade.", fact_id="f_001")],
        unverified_citations=[],
        coverage=["school-age"],
    )
    temporal = validate_temporal_framing(
        bad_out, ledger, evaluation_date="2026-07-16", stale_as_of_days=365
    )
    ok &= check(
        "superseded present tense",
        len(temporal) == 1 and temporal[0].kind == "temporal_framing",
        f"temporal={temporal}",
    )
    good_out = DraftProseOutput(
        prose="As of 2024-09-01, the cumulative file stated A.R. was in 2nd grade.",
        statements=[
            DraftStatement(
                statement="As of 2024-09-01, the cumulative file stated A.R. was in 2nd grade.",
                fact_id="f_001",
            )
        ],
        unverified_citations=[],
        coverage=["school-age"],
    )
    temporal_ok = validate_temporal_framing(
        good_out, ledger, evaluation_date="2026-07-16", stale_as_of_days=365
    )
    ok &= check("historical framing ok", len(temporal_ok) == 0, f"temporal_ok={temporal_ok}")
    latest_ok = validate_temporal_framing(
        DraftProseOutput(
            prose="A.R. is in 4th grade.",
            statements=[DraftStatement(statement="A.R. is in 4th grade.", fact_id="f_002")],
            unverified_citations=[],
            coverage=["current"],
        ),
        ledger,
        evaluation_date="2026-07-16",
        stale_as_of_days=365,
    )
    ok &= check("latest present tense ok", len(latest_ok) == 0, f"latest_ok={latest_ok}")

    conflict = Disagreement(
        subject="child",
        predicate="legal_name",
        qualifier=None,
        predicate_class="record",
        topic="legal_name",
        versions=[
            DisagreementVersion(
                fact_id="f_a",
                source_id="nurse",
                source_date="2024-01-01",
                reporter=None,
                value="Justin M.",
                value_text="Justin M.",
                assertion="asserted",
            ),
            DisagreementVersion(
                fact_id="f_b",
                source_id="iep",
                source_date="2025-01-01",
                reporter=None,
                value="Jason M.",
                value_text="Jason M.",
                assertion="asserted",
            ),
        ],
    )
    missing = validate_conflicts_mentioned("Developmental history was typical.", [conflict])
    ok &= check(
        "conflict must mention",
        len(missing) == 1 and missing[0].kind == "conflict_not_mentioned",
        f"missing={missing}",
    )
    present = validate_conflicts_mentioned(
        "Sources disagree on name: Justin M. versus Jason M.",
        [conflict],
    )
    ok &= check("conflict mentioned", len(present) == 0, f"present={present}")
    return ok


def test_draft_smoke() -> bool:
    """Stage 4 live smoke: tiny ledger → /draft with review queue + entailment."""

    from dotenv import load_dotenv
    from draft import draft_section
    from provider import ModelProvider
    from schemas import (
        Child,
        Disagreement,
        DisagreementVersion,
        DraftRequest,
        Fact,
        Ledger,
        Source,
    )

    load_dotenv(WORKDIR / ".env")
    print("\n=== /draft smoke (health-like mini ledger) ===")
    child = Child(initials="J.M.", dob="2015-04-22", evaluation_date="2026-07-16")
    sources = [
        Source(
            id="nurse",
            type="school",
            date="2024-09-12",
            label="School Nurse Health Report",
            content="Student name on header: Justin M. Known allergy to peanuts. Health plan draft emailed.",
        ),
        Source(
            id="iep",
            type="school",
            date="2025-03-18",
            label="IEP health pages",
            content="IEP body student name: Jason M. Peanut allergy classification Undiagnosed. Active health plan on file.",
        ),
    ]
    facts = [
        Fact(
            id="f_001",
            subject="child",
            predicate="legal_name",
            value="Justin M.",
            value_text="Student name on header: Justin M.",
            qualifier=None,
            assertion="asserted",
            source_id="nurse",
            source_date="2024-09-12",
            reporter=None,
            life_stage="current",
            grade=None,
            temporality="durable",
            confidence="stated",
        ),
        Fact(
            id="f_002",
            subject="child",
            predicate="legal_name",
            value="Jason M.",
            value_text="IEP body student name: Jason M.",
            qualifier=None,
            assertion="asserted",
            source_id="iep",
            source_date="2025-03-18",
            reporter=None,
            life_stage="current",
            grade=None,
            temporality="durable",
            confidence="stated",
        ),
    ]
    from derived import build_age_years_fact, build_request_dob_fact

    facts.extend(
        [
            build_request_dob_fact(child, fact_id="f_003"),
            build_age_years_fact(child, fact_id="f_004"),
        ]
    )
    conflict = Disagreement(
        subject="child",
        predicate="legal_name",
        qualifier=None,
        predicate_class="record",
        topic="legal_name",
        versions=[
            DisagreementVersion(
                fact_id="f_001",
                source_id="nurse",
                source_date="2024-09-12",
                reporter=None,
                value="Justin M.",
                value_text="Justin M.",
                assertion="asserted",
            ),
            DisagreementVersion(
                fact_id="f_002",
                source_id="iep",
                source_date="2025-03-18",
                reporter=None,
                value="Jason M.",
                value_text="Jason M.",
                assertion="asserted",
            ),
        ],
    )
    ledger = Ledger(
        child=child,
        ledger_version="1",
        built_at="2026-07-16T00:00:00Z",
        sources=sources,
        facts=facts,
    )
    body = DraftRequest(
        confirm_synthetic=True,
        section="history",
        ledger=ledger,
        conflicts=[conflict],
        variance=[],
        model="gpt-4o-mini",
        entailment_model="gpt-4o-mini",
        stale_as_of_days=365,
    )
    resp = draft_section(ModelProvider(), body)
    ok = True
    ok &= check("populated", resp.section_populated is True, f"populated={resp.section_populated}")
    ok &= check("answer", resp.answer is not None and len(resp.answer.prose) > 40, "prose present")
    ok &= check(
        "fact_ids",
        all(f.fact_id for f in (resp.answer.facts if resp.answer else [])),
        "every draft fact has fact_id",
    )
    conflict_items = [i for i in resp.review.items if i.kind == "conflict"]
    ok &= check(
        "review conflict",
        len(conflict_items) >= 1,
        f"review conflict items={len(conflict_items)}",
    )
    not_mentioned = [i for i in resp.review.items if i.kind == "conflict_not_mentioned"]
    ok &= check(
        "must-mention ok",
        len(not_mentioned) == 0,
        f"conflict_not_mentioned={len(not_mentioned)}",
    )
    print(
        f"  tokens_used={resp.tokens_used}  by_stage={resp.tokens_by_stage}  "
        f"cost_usd={resp.cost_usd}  review_items={len(resp.review.items)}"
    )
    print(f"  prose preview: {(resp.answer.prose[:160] + '...') if resp.answer else ''}")
    for item in resp.review.items:
        print(f"    review[{item.kind}]: {item.summary[:100]}")
    return ok


def main() -> int:
    results: list[tuple[str, bool]] = []
    results.append(("fixtures.decontamination", test_fixture_decontamination()))
    results.append(("fixtures.prompt_hygiene", test_meta_never_in_prompt()))
    results.append(("schema.ledger", test_ledger_schema_unit()))
    results.append(("schema.predicates", test_predicate_vocabulary_unit()))
    results.append(("grouping.key", test_grouping_unit()))
    results.append(("conflicts.unit", test_conflicts_unit()))
    results.append(("subjects.canonical", test_canonical_subjects_unit()))
    results.append(("subjects.stamping", test_subject_stamping_and_grouping_unit()))
    results.append(("normalize.values", test_normalize_unit()))
    results.append(("extract.isolation", test_extract_isolation_unit()))
    results.append(("validators.age", test_age_validator_unit()))
    results.append(("validators.provenance", test_provenance_validator_unit()))
    results.append(("stage45.unit", test_derived_and_coverage_unit()))
    results.append(("draft.validators", test_draft_validators_unit()))
    results.append(("draft.smoke", test_draft_smoke()))
    results.append(("extract.stage25", test_extract_stage25()))
    results.append(("stage45.verification", test_stage45_verification()))
    results.append(("stage0.extract_conflicts", test_stage0_via_extract_conflicts()))

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = start_server("main", port)
    try:
        if not wait_up(base):
            print("\n=== main /ask: FAIL — server did not start ===")
            results.append(("main.ask_age_guard", False))
        else:
            results.append(("main.ask_age_guard", test_ask_age_guard(base)))
            results.append(("main.ingest", test_ingest_unit(base)))
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)

    for module, test_fn in TESTS:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        proc = start_server(module, port)
        try:
            if not wait_up(base):
                print(f"\n=== {module}: FAIL — server did not start on {base} ===")
                results.append((module, False))
                continue
            results.append((module, test_fn(base)))
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            time.sleep(0.5)

    print("\n" + "=" * 40)
    print("SUMMARY")
    for module, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {module}")
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
