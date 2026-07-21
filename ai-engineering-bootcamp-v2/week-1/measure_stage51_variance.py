#!/usr/bin/env python3
"""
Stage 5.3 — re-measure after subject enum + provenance stamp.

Same metrics as 5.1/5.2A plus subject stability. Fixture expectations unchanged.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from conflicts import detect_disagreements_from_ledger
from extract import build_ledger
from provider import ModelProvider, compute_cost_usd
from schemas import Child, Source
from test_all_stages import (
    HEALTH_FIXTURE_PATH,
    HISTORY_FIXTURE_PATH,
    NO_CONFLICT_FIXTURE_PATH,
    _load_fixture,
    _score_ledger_facts,
    score_conflicts,
)

WORKDIR = Path(__file__).resolve().parent
N_RUNS = 5
FIXTURES = (
    ("history", HISTORY_FIXTURE_PATH),
    ("health", HEALTH_FIXTURE_PATH),
    ("no_conflict", NO_CONFLICT_FIXTURE_PATH),
)


def _mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _fact_key(f) -> str:
    """Identity for temporality tracking across runs (ignore fact id)."""

    return "|".join(
        [
            f.source_id,
            f.subject,
            f.predicate,
            f.qualifier or "",
            f.assertion,
            f.value,
        ]
    )


def run_fixture_once(
    label: str,
    path: Path,
    provider: ModelProvider,
) -> dict:
    fixture = _load_fixture(path)
    child = Child.model_validate(fixture["child"])
    sources = [Source.model_validate(s) for s in fixture["sources"]]
    model = fixture.get("model") or "gpt-4o-mini"
    ledger, tokens_by_source, pt, ct, review, subj_review, gap, timelines = build_ledger(
        provider, child=child, sources=sources, model=model
    )
    cost = compute_cost_usd(model, pt, ct)
    tokens = sum(tokens_by_source.values())

    expected_facts = fixture.get("expected_ledger_facts") or []
    found, missed = _score_ledger_facts(ledger.facts, expected_facts)
    fact_recall = (len(found) / len(expected_facts)) if expected_facts else 1.0

    conflicts, variance, _, _, _ = detect_disagreements_from_ledger(ledger)
    expected_c = fixture.get("expected_conflicts") or []
    c_found, c_missed, c_fp = score_conflicts(conflicts, expected_c)
    tp, fn, fp = len(c_found), len(c_missed), len(c_fp)
    c_prec = tp / (tp + fp) if (tp + fp) else 1.0
    c_rec = tp / len(expected_c) if expected_c else 1.0

    # Named checks matching stage0 harness thresholds (no helper side-effects).
    check_results: dict[str, bool] = {
        f"{label}.conflict_recall": (c_rec == 1.0) if expected_c else True,
        f"{label}.conflict_precision": c_prec == 1.0,
        f"{label}.fact_recall": (fact_recall == 1.0) if expected_facts else True,
    }

    preds_by_source: dict[str, set[str]] = defaultdict(set)
    temp_by_key: dict[str, str] = {}
    for f in ledger.facts:
        if f.source_id in ("request", "computed"):
            continue
        preds_by_source[f.source_id].add(f.predicate)
        temp_by_key[_fact_key(f)] = f.temporality

    return {
        "label": label,
        "path": path.name,
        "tokens": tokens,
        "cost_usd": cost,
        "fact_recall": fact_recall,
        "conflict_precision": c_prec,
        "conflict_recall": c_rec,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "conflicts": [
            {
                "topic": c.topic,
                "predicate": c.predicate,
                "qualifier": c.qualifier,
                "sources": sorted({v.source_id for v in c.versions}),
                "values": sorted({(v.assertion, v.value) for v in c.versions}),
            }
            for c in conflicts
        ],
        "missed_conflicts": c_missed,
        "false_positives": [
            getattr(x, "topic", None) or (x.get("topic") if isinstance(x, dict) else str(x))
            for x in c_fp
        ],
        "preds_by_source": {k: sorted(v) for k, v in preds_by_source.items()},
        "temp_by_key": temp_by_key,
        "facts": [
            {
                "source_id": f.source_id,
                "subject": f.subject,
                "predicate": f.predicate,
                "qualifier": f.qualifier,
                "value": f.value,
                "temporality": f.temporality,
                "as_of_date": f.as_of_date,
                "assertion": f.assertion,
            }
            for f in ledger.facts
            if f.source_id not in ("request", "computed")
        ],
        "review": review,
        "check_results": check_results,
    }


def main() -> int:
    load_dotenv(WORKDIR / ".env")
    provider = ModelProvider()

    print("=" * 72)
    print(f"Stage 5.3 variance — {N_RUNS} runs × {len(FIXTURES)} fixtures")
    print("Subject enum + provenance stamp; EXTRACT_TEMPERATURE=0.")
    print("=" * 72)

    all_runs: list[list[dict]] = []
    total_tokens = 0
    total_cost = 0.0
    check_fail_counts: dict[str, int] = defaultdict(int)
    check_seen: set[str] = set()

    for run_i in range(1, N_RUNS + 1):
        print(f"\n######## RUN {run_i}/{N_RUNS} ########")
        run_rows: list[dict] = []
        for label, path in FIXTURES:
            print(f"\n--- run {run_i} · {label} ({path.name}) ---")
            row = run_fixture_once(label, path, provider)
            run_rows.append(row)
            total_tokens += row["tokens"]
            total_cost += row["cost_usd"]
            print(
                f"  fact_R={row['fact_recall']:.2f}  "
                f"conflict P={row['conflict_precision']:.2f} R={row['conflict_recall']:.2f}  "
                f"TP={row['tp']} FN={row['fn']} FP={row['fp']}  "
                f"tokens={row['tokens']} cost={row['cost_usd']:.6f}"
            )
            for c in row["conflicts"]:
                print(f"    conflict: {c['topic']} values={c['values']} sources={c['sources']}")
            for m in row["missed_conflicts"]:
                print(f"    missed: {m.get('topic') if isinstance(m, dict) else m}")
            for fp in row["false_positives"]:
                print(f"    false_positive: {fp}")
            for name, passed in row["check_results"].items():
                check_seen.add(name)
                if not passed:
                    check_fail_counts[name] += 1
        all_runs.append(run_rows)

    # ---- Aggregate per fixture ----
    print("\n" + "=" * 72)
    print("AGGREGATE — fact recall & conflict P/R (min / mean / max)")
    print("=" * 72)

    for fi, (label, path) in enumerate(FIXTURES):
        rows = [all_runs[r][fi] for r in range(N_RUNS)]
        fr = [r["fact_recall"] for r in rows]
        cp = [r["conflict_precision"] for r in rows]
        cr = [r["conflict_recall"] for r in rows]
        print(f"\n### {label} ({path.name})")
        print(
            f"  fact_recall:          min={min(fr):.2f}  mean={_mean(fr):.2f}  max={max(fr):.2f}"
        )
        print(
            f"  conflict_precision:   min={min(cp):.2f}  mean={_mean(cp):.2f}  max={max(cp):.2f}"
        )
        print(
            f"  conflict_recall:      min={min(cr):.2f}  mean={_mean(cr):.2f}  max={max(cr):.2f}"
        )

        # Predicate name stability
        print("  predicate names by source (union across runs; * = unstable):")
        all_sources = sorted({sid for r in rows for sid in r["preds_by_source"]})
        for sid in all_sources:
            per_run = [set(r["preds_by_source"].get(sid, [])) for r in rows]
            union = sorted(set().union(*per_run)) if per_run else []
            # Names that appear in some but not all runs where the source produced facts
            intersection = set.intersection(*per_run) if all(per_run) else set()
            unstable = sorted(set(union) - intersection) if per_run else []
            # Also: predicates present in only a subset of runs
            presence = {
                p: sum(1 for s in per_run if p in s) for p in union
            }
            varying = sorted(p for p, n in presence.items() if 0 < n < N_RUNS)
            mark = " *" if varying else ""
            print(f"    {sid}{mark}: {union}")
            if varying:
                for p in varying:
                    print(f"      ~ {p}: present in {presence[p]}/{N_RUNS} runs")

        # Temporality stability — group by (source, predicate, qualifier) ignoring value drift
        print("  temporality stability (by source/predicate/qualifier):")
        temp_obs: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            for f in r["facts"]:
                key = f"{f['source_id']}|{f['predicate']}|{f['qualifier'] or ''}"
                temp_obs[key].add(f["temporality"])
        unstable_temp = {k: sorted(v) for k, v in temp_obs.items() if len(v) > 1}
        stable_n = sum(1 for v in temp_obs.values() if len(v) == 1)
        print(f"    keys observed={len(temp_obs)}  stable={stable_n}  unstable={len(unstable_temp)}")
        for k, temps in sorted(unstable_temp.items()):
            print(f"      UNSTABLE {k}: {temps}")

        print("  subject stability (non-provenance facts must be canonical):")
        from predicates import CANONICAL_SUBJECTS, is_provenance_predicate

        bad_subjects = []
        subj_by_pred: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            for f in r["facts"]:
                if is_provenance_predicate(f["predicate"]):
                    continue
                subj_by_pred[f"{f['source_id']}|{f['predicate']}"].add(f["subject"])
                if f["subject"] not in CANONICAL_SUBJECTS:
                    bad_subjects.append(f)
        print(f"    non-canonical subject occurrences: {len(bad_subjects)}")
        unstable_subj = {k: sorted(v) for k, v in subj_by_pred.items() if len(v) > 1}
        print(f"    subject-stable keys={len(subj_by_pred) - len(unstable_subj)}  unstable={len(unstable_subj)}")
        for k, subs in sorted(unstable_subj.items())[:15]:
            print(f"      UNSTABLE {k}: {subs}")
        # Sample subjects for key conflict predicates
        for want in ("legal_name", "allergy_status", "private_tutoring"):
            samples = [
                (f["source_id"], f["subject"], f["value"])
                for r in rows[:1]
                for f in r["facts"]
                if f["predicate"] == want
            ]
            if samples:
                print(f"    sample {want}: {samples}")

        # Also value-keyed temporality flips (same claim, different temporality)
        value_temp: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            for key, temp in r["temp_by_key"].items():
                value_temp[key].add(temp)
        value_flips = {k: sorted(v) for k, v in value_temp.items() if len(v) > 1}
        if value_flips:
            print(f"  same-claim temporality flips: {len(value_flips)}")
            for k, temps in sorted(value_flips.items())[:20]:
                print(f"      {k}: {temps}")
        else:
            print("  same-claim temporality flips: 0")

        # allergy_status / health_plan spotlight (health fixture)
        if label == "health":
            print("  spotlight — allergy_status & health_plan_status per run:")
            for ri, r in enumerate(rows, 1):
                allergy = [
                    f
                    for f in r["facts"]
                    if f["predicate"] == "allergy_status"
                ]
                plans = [
                    f
                    for f in r["facts"]
                    if f["predicate"] == "health_plan_status"
                ]
                names = [
                    f
                    for f in r["facts"]
                    if f["predicate"] == "legal_name" and f["source_id"] == "iep-health-2025"
                ]
                print(
                    f"    run {ri}: iep_legal_name={len(names)}  "
                    f"allergy={[ (a['source_id'], a['value'], a['temporality'], a['as_of_date']) for a in allergy ]}  "
                    f"plan={[ (p['source_id'], p['value'], p['temporality'], p['as_of_date']) for p in plans ]}  "
                    f"conflicts={[c['topic'] for c in r['conflicts']]}"
                )

        if label == "no_conflict":
            print("  spotlight — dob facts per run:")
            for ri, r in enumerate(rows, 1):
                dobs = [f for f in r["facts"] if f["predicate"] == "dob"]
                print(
                    f"    run {ri}: dob={[(d['source_id'], d['value'], d['temporality']) for d in dobs]}  "
                    f"FP={r['false_positives']}"
                )

    # ---- Check stability ----
    print("\n" + "=" * 72)
    print("CHECK STABILITY (threshold failures across 5 runs)")
    print("=" * 72)
    always_fail = []
    sometimes = []
    always_pass = []
    for name in sorted(check_seen):
        fails = check_fail_counts.get(name, 0)
        if fails == N_RUNS:
            always_fail.append(name)
            print(f"  FAIL all {N_RUNS}: {name}")
        elif fails == 0:
            always_pass.append(name)
            print(f"  PASS all {N_RUNS}: {name}")
        else:
            sometimes.append((name, fails))
            print(f"  FAIL {fails}/{N_RUNS}: {name}")

    print("\n" + "=" * 72)
    print("COST")
    print("=" * 72)
    print(f"  total_tokens={total_tokens}")
    print(f"  total_cost_usd={round(total_cost, 6)}")
    print(f"  mean_cost_per_run_usd={round(total_cost / N_RUNS, 6)}")

    out = WORKDIR / "measure_stage53_variance_results.json"
    out.write_text(
        json.dumps(
            {
                "stage": "5.3",
                "extract_temperature": 0.0,
                "n_runs": N_RUNS,
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 6),
                "always_fail": always_fail,
                "sometimes_fail": sometimes,
                "always_pass": always_pass,
                "runs": all_runs,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n  wrote {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
