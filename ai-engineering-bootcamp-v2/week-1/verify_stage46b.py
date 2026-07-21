#!/usr/bin/env python3
"""Stage 4.6b verification: as_of_date anchoring + tutoring fixture rewrite."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from conflicts import detect_disagreements_from_ledger
from extract import EXTRACT_SYSTEM_PROMPT, build_ledger
from provider import ModelProvider, compute_cost_usd
from schemas import Child, Source
from test_all_stages import (
    HISTORY_FIXTURE_PATH,
    _load_fixture,
    _score_ledger_facts,
    score_conflicts,
)

WORKDIR = Path(__file__).resolve().parent
ANCHOR_FIXTURE = WORKDIR / "fixtures" / "synthetic_as_of_anchor_case.json"

# Stage 4.6 baseline — multi-entry timelines on history (age_years, reading_fluency)
STAGE_46_MULTI_ENTRY_TIMELINES = 2


def check(ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {detail}")
    return ok


def _grade_timeline_order(timelines) -> list[str]:
    for t in timelines:
        if t.predicate == "grade":
            return [f"{e.value}@{e.as_of_date[:4]}" for e in t.entries]
    return []


def main() -> int:
    load_dotenv(WORKDIR / ".env")
    ok = True
    provider = ModelProvider()
    model = "gpt-4o-mini"

    print("=== Stage 4.6b verification ===\n")
    ok &= check(
        "as_of_date" in EXTRACT_SYSTEM_PROMPT and "Temporal anchoring" in EXTRACT_SYSTEM_PROMPT,
        "extract_prompt.md documents temporal anchoring",
    )

    # --- as_of anchor fixture ---
    print("\n--- as_of_date anchor fixture ---")
    anchor = _load_fixture(ANCHOR_FIXTURE)
    child = Child.model_validate(anchor["child"])
    sources = [Source.model_validate(s) for s in anchor["sources"]]
    ledger, toks, p, c_tok, review, subj, gap, timelines = build_ledger(
        provider, child=child, sources=sources, model=model
    )
    cost = compute_cost_usd(model, p, c_tok)
    conflicts, variance, timelines, _, _ = detect_disagreements_from_ledger(ledger)
    print(f"  tokens={sum(toks.values())}  cost_usd={round(cost, 6)}  timelines={len(timelines)}")

    exp_anchor = anchor["expected_as_of_anchor"]
    anchored = [
        f
        for f in ledger.facts
        if f.source_id == exp_anchor["source_id"]
        and f.predicate == exp_anchor["predicate"]
        and f.value == exp_anchor["value"]
    ]
    ok &= check(len(anchored) >= 1, f"grade@2024 fact extracted (found={len(anchored)})")
    if anchored:
        fact = anchored[0]
        ok &= check(
            fact.source_date == exp_anchor["source_date"],
            f"source_date={fact.source_date} (want {exp_anchor['source_date']})",
        )
        ok &= check(
            (fact.as_of_date or "").startswith(exp_anchor["as_of_date_year"]),
            f"as_of_date={fact.as_of_date} anchored to {exp_anchor['as_of_date_year']}",
        )
        ok &= check(
            fact.as_of_date != fact.source_date,
            f"as_of_date ({fact.as_of_date}) != source_date ({fact.source_date})",
        )

    exp_vague = anchor.get("expected_vague_no_anchor")
    if exp_vague:
        vague_facts = [
            f
            for f in ledger.facts
            if f.source_id == exp_vague["source_id"] and f.predicate == exp_vague["predicate"]
        ]
        if vague_facts:
            vf = vague_facts[0]
            ok &= check(
                vf.as_of_date == vf.source_date,
                f"vague 'last year' → as_of_date defaults to source_date "
                f"({vf.as_of_date} == {vf.source_date})",
            )
        else:
            ok &= check(
                False,
                f"no {exp_vague['predicate']} fact for vague-anchor check "
                f"(extraction may have skipped 'last year' claim)",
            )

    grade_order = _grade_timeline_order(timelines)
    print(f"  grade timeline: {' → '.join(grade_order)}")
    ok &= check(
        len(grade_order) >= 2 and grade_order[0].startswith("2@2024"),
        f"anchored grade 2@2024 is first on timeline (got {grade_order})",
    )
    if len(grade_order) >= 2:
        ok &= check(
            grade_order[-1].startswith("4@2026"),
            f"current grade 4@2026 is latest (got {grade_order})",
        )

    # --- History: tutoring conflict + timeline fragmentation ---
    print("\n--- History fixture (tutoring + timelines) ---")
    history = _load_fixture(HISTORY_FIXTURE_PATH)
    child_h = Child.model_validate(history["child"])
    sources_h = [Source.model_validate(s) for s in history["sources"]]
    ledger_h, toks_h, p_h, c_h, review_h, subj_h, gap_h, timelines_h = build_ledger(
        provider, child=child_h, sources=sources_h, model=model
    )
    cost_h = compute_cost_usd(model, p_h, c_h)
    conflicts_h, variance_h, timelines_h, _, _ = detect_disagreements_from_ledger(ledger_h)

    tutoring = [c for c in conflicts_h if c.predicate == "private_tutoring"]
    ok &= check(
        len(tutoring) == 1,
        f"private_tutoring conflict from asserted/denied pair (conflicts={len(tutoring)})",
    )
    if tutoring:
        versions = tutoring[0].versions
        assertions = {(v.assertion, v.value, v.source_id) for v in versions}
        print(f"    tutoring versions: {sorted(assertions)}")
        has_yes = any(v.value in {"yes", "true"} and v.source_id == "parent-dev-2026" for v in versions)
        has_no = any(
            v.assertion == "denied"
            or v.value in {"none", "no"}
            for v in versions
            if v.source_id == "school-iep-none-2025"
        )
        ok &= check(
            has_yes and has_no,
            "tutoring conflict: parent yes vs school denial/none",
        )

    expected_conflicts = history.get("expected_conflicts") or []
    c_found, c_missed, c_extra = score_conflicts(conflicts_h, expected_conflicts)
    c_prec = (len(c_found) / (len(c_found) + len(c_extra))) if (c_found or c_extra) else 1.0
    c_rec = (len(c_found) / len(expected_conflicts)) if expected_conflicts else 1.0
    ok &= check(c_rec == 1.0, f"conflict recall={c_rec:.2f} (missed={c_missed})")
    ok &= check(c_prec == 1.0, f"conflict precision={c_prec:.2f} (false_positives={len(c_extra)})")

    # Fact recall: assess-only lock
    assess_only = {
        **history,
        "sources": [s for s in history["sources"] if s["id"] == "assess-2026-wisc"],
        "expected_conflicts": [],
    }
    child_a = Child.model_validate(assess_only["child"])
    sources_a = [Source.model_validate(s) for s in assess_only["sources"]]
    best_recall = 0.0
    for attempt in range(2):
        ledger_a, _, p_a, c_a, _, _, _, _ = build_ledger(
            provider, child=child_a, sources=sources_a, model=model
        )
        found, missed = _score_ledger_facts(
            ledger_a.facts, assess_only.get("expected_ledger_facts") or []
        )
        expected_n = len(assess_only.get("expected_ledger_facts") or [])
        best_recall = (len(found) / expected_n) if expected_n else 1.0
        if best_recall == 1.0:
            break
    ok &= check(
        best_recall == 1.0,
        f"fact recall (assess-only lock)={best_recall:.2f}",
    )

    tl_count = len(timelines_h)
    multi_entry = [t for t in timelines_h if len(t.entries) > 1]
    ok &= check(
        len(multi_entry) >= STAGE_46_MULTI_ENTRY_TIMELINES,
        f"multi-entry timelines={len(multi_entry)} "
        f"(Stage 4.6 baseline>={STAGE_46_MULTI_ENTRY_TIMELINES}; no fragmentation)",
    )
    # Same key progressions must remain single timelines, not split shards.
    age_tl = next((t for t in timelines_h if t.predicate == "age_years"), None)
    read_tl = next((t for t in timelines_h if t.predicate == "reading_fluency"), None)
    ok &= check(
        age_tl is not None and len(age_tl.entries) == 2,
        f"age_years still one 2-entry timeline (got {len(age_tl.entries) if age_tl else 0})",
    )
    ok &= check(
        read_tl is not None and len(read_tl.entries) >= 2,
        f"reading_fluency still one multi-entry timeline (got {len(read_tl.entries) if read_tl else 0})",
    )
    print(
        f"  tokens={sum(toks_h.values())}  cost_usd={round(cost_h, 6)}  "
        f"conflicts={len(conflicts_h)}  timelines={tl_count}  "
        f"fact_recall={best_recall:.2f}  conflict_P/R={c_prec:.2f}/{c_rec:.2f}"
    )
    print("\n  TIMELINES (multi-entry):")
    for t in timelines_h:
        if len(t.entries) > 1 or t.predicate in {"age_years", "grade", "private_tutoring"}:
            parts = [
                f"{e.value}@{e.as_of_date}{'*' if e.is_latest else ''}"
                for e in t.entries
            ]
            print(f"    {t.topic}: {' → '.join(parts)}")

    print("\n" + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
