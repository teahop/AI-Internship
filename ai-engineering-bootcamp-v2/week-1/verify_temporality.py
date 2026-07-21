#!/usr/bin/env python3
"""Stage 4.5b verification: temporality-aware grouping + history timelines."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from conflicts import detect_disagreements, detect_disagreements_from_ledger
from extract import build_ledger
from provider import ModelProvider, compute_cost_usd
from schemas import Child, Fact, Source

WORKDIR = Path(__file__).resolve().parent
HISTORY = WORKDIR / "fixtures" / "synthetic_history_case.json"


def check(ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {detail}")
    return ok


def main() -> int:
    load_dotenv(WORKDIR / ".env")
    ok = True
    print("=== Temporality-aware grouping verification ===\n")

    # Unit matrix (no network)
    age7 = Fact(
        id="a7",
        subject="child",
        predicate="age_years",
        value="7",
        value_text="7",
        assertion="asserted",
        source_id="cum",
        source_date="2024-09-01",
        as_of_date="2024-09-01",
        life_stage="school-age",
        temporality="as_of",
        confidence="stated",
    )
    age9 = Fact(
        id="a9",
        subject="child",
        predicate="age_years",
        value="9",
        value_text="9",
        assertion="asserted",
        source_id="computed",
        source_date="2026-07-16",
        as_of_date="2026-07-16",
        life_stage="current",
        temporality="as_of",
        confidence="stated",
    )
    g2 = Fact(
        id="g2",
        subject="child",
        predicate="grade",
        value="2",
        value_text="2",
        assertion="asserted",
        source_id="cum",
        source_date="2024-09-01",
        as_of_date="2024-09-01",
        life_stage="school-age",
        temporality="as_of",
        confidence="stated",
    )
    g4 = Fact(
        id="g4",
        subject="child",
        predicate="grade",
        value="4",
        value_text="4",
        assertion="asserted",
        source_id="t",
        source_date="2026-06-15",
        as_of_date="2026-06-15",
        life_stage="current",
        temporality="as_of",
        confidence="stated",
    )
    age_clash_a = age9.model_copy(
        update={"id": "x1", "value": "8", "source_id": "s1", "as_of_date": "2026-07-16", "source_date": "2026-07-16"}
    )
    age_clash_b = age9.model_copy(
        update={"id": "x2", "value": "9", "source_id": "s2", "as_of_date": "2026-07-16", "source_date": "2026-07-16"}
    )
    birth_a = Fact(
        id="b1",
        subject="child",
        predicate="birth_term",
        value="full-term",
        value_text="full-term",
        assertion="asserted",
        source_id="p",
        source_date="2026-01-01",
        life_stage="birth",
        temporality="durable",
        confidence="stated",
    )
    birth_b = Fact(
        id="b2",
        subject="child",
        predicate="birth_term",
        value="preterm",
        value_text="preterm",
        assertion="asserted",
        source_id="s",
        source_date="2025-01-01",
        life_stage="birth",
        temporality="durable",
        confidence="stated",
    )

    c, _, tl, _, _ = detect_disagreements([age7, age9])
    ok &= check(len(c) == 0, f"age 7@2024 vs 9@2026 → timeline, no conflict (conflicts={len(c)})")
    ok &= check(
        any(t.predicate == "age_years" and len(t.entries) == 2 for t in tl),
        "age timeline has 2 entries",
    )

    c, _, tl, _, _ = detect_disagreements([g2, g4])
    ok &= check(len(c) == 0, f"grade 2@2024 vs 4@2026 → timeline, no conflict (conflicts={len(c)})")

    c, _, _, _, _ = detect_disagreements([age_clash_a, age_clash_b])
    ok &= check(len(c) == 1, f"two ages same as_of_date → conflict (conflicts={len(c)})")

    c, _, _, _, _ = detect_disagreements([birth_a, birth_b])
    ok &= check(len(c) == 1, f"durable birth_term disagreement → conflict (conflicts={len(c)})")

    # Live history fixture
    print("\n--- History fixture extract → conflicts/timelines ---")
    fixture = json.loads(HISTORY.read_text())
    child = Child.model_validate(fixture["child"])
    sources = [Source.model_validate(s) for s in fixture["sources"]]
    model = fixture.get("model") or "gpt-4o-mini"
    ledger, toks, p, c_tok, review, subj_review, gap, timelines = build_ledger(
        ModelProvider(), child=child, sources=sources, model=model
    )
    conflicts, variance, timelines, _, _ = detect_disagreements_from_ledger(ledger)
    cost = compute_cost_usd(model, p, c_tok)

    # Fact recall (Stage 2.5 lock on assess-only expected fact)
    expected = fixture.get("expected_ledger_facts") or []
    from test_all_stages import _score_ledger_facts

    found, missed = _score_ledger_facts(ledger.facts, expected)
    recall = (len(found) / len(expected)) if expected else 1.0
    ok &= check(
        recall == 1.0,
        f"fact recall={recall:.2f} ({len(found)}/{len(expected)}; missed={missed})",
    )

    expected_conflicts = fixture.get("expected_conflicts") or []
    from test_all_stages import score_conflicts

    c_found, c_missed, c_extra = score_conflicts(conflicts, expected_conflicts)
    c_prec = (len(c_found) / (len(c_found) + len(c_extra))) if (c_found or c_extra) else 1.0
    c_rec = (len(c_found) / len(expected_conflicts)) if expected_conflicts else 1.0
    ok &= check(c_rec == 1.0, f"conflict recall={c_rec:.2f} (missed={c_missed})")
    print(
        f"  conflict precision={c_prec:.2f}  recall={c_rec:.2f}  "
        f"false_positives={len(c_extra)}"
    )
    for e in c_extra:
        topic = e.topic if hasattr(e, "topic") else e.get("topic")
        print(f"    false_positive: {topic}")

    age_conflict = [c for c in conflicts if c.predicate == "age_years"]
    grade_conflict = [c for c in conflicts if c.predicate == "grade"]
    ok &= check(len(age_conflict) == 0, f"no age_years conflict (got {len(age_conflict)})")
    ok &= check(len(grade_conflict) == 0, f"no grade conflict (got {len(grade_conflict)})")

    print(
        f"  tokens={sum(toks.values())}  cost_usd={round(cost, 6)}  "
        f"conflicts={len(conflicts)}  variance={len(variance)}  "
        f"timelines={len(timelines)}  pred_review={review}  subj_review={subj_review}"
    )
    print("\n  TIMELINES:")
    for t in timelines:
        if t.predicate in {"age_years", "grade"} or len(t.entries) > 1:
            parts = [
                f"{e.value}@{e.as_of_date}{'*' if e.is_latest else ''}"
                for e in t.entries
            ]
            print(f"    {t.topic}: {' → '.join(parts)}")

    print("\n" + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
