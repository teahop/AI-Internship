#!/usr/bin/env python3
"""
Opt-in scale draft over the accumulated fixture_001 ledger.

Not part of the blocking ``test_all_stages.py`` suite — same posture as
``measure_stage51_variance.py`` (live, expensive, manual).

Why this exists
---------------
The blocking draft tests only exercise tiny ledgers. The UI ``/draft`` path on
fixture_001 is where a missing ``f_computed_age_years`` cite first 502'd.
This script builds the full per-file ledger (narrative extract only; score
reports are triage-skipped), runs conflicts, then drafts with the same
validation-retry budget as ``/draft`` / ``/ask``.

Usage
-----
  RUN_SCALE_DRAFT=1 python draft_fixture001_scale.py

Optional follow-ups (not built here)
------------------------------------
- Add a draft leg to ``measure_stage51_variance.py`` so extract→conflicts→draft
  is measured end-to-end, not just the first two stages.
- Dedicated hardening stage: retry + deterministic repair uniformly across
  ``/ingest``, ``/extract``, ``/draft`` (``/ask`` already retries) — see TJ note.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import ValidationError

from conflicts import detect_disagreements_from_ledger
from draft import draft_section
from extract import build_ledger, merge_ledger_with_extracted
from provider import ModelProvider, compute_cost_usd
from retries import VALIDATION_RETRY_ATTEMPTS, run_with_validation_retries
from schemas import DraftRequest
from test_all_stages import FIXTURE_001_MANIFEST_PATH, load_case_manifest

WORKDIR = Path(__file__).resolve().parent


def _build_fixture_001_ledger(provider: ModelProvider):
    man = json.loads(FIXTURE_001_MANIFEST_PATH.read_text(encoding="utf-8"))
    child, sources, _keys = load_case_manifest(FIXTURE_001_MANIFEST_PATH)
    model = man.get("model") or "gpt-4o-mini"

    ledger = None
    tokens_by_source: dict[str, int] = {}
    pt = ct = 0
    extract_failures: list[dict] = []
    for source in sources:
        try:
            ledger, toks, p_tok, c_tok, _rev, _subj, _gap, _tl = build_ledger(
                provider,
                child=child,
                sources=[source],
                model=model,
                prior_ledger=ledger,
            )
            tokens_by_source.update(toks)
            pt += p_tok
            ct += c_tok
        except ValidationError as exc:
            extract_failures.append(
                {
                    "source_id": source.id,
                    "doc_class": source.doc_class,
                    "error": str(exc).split("\n", 1)[0],
                }
            )
            ledger = merge_ledger_with_extracted(
                child=child,
                prior=ledger,
                new_sources=[source],
                new_facts_by_source={source.id: []},
            )
            tokens_by_source[source.id] = 0

    assert ledger is not None
    return ledger, model, tokens_by_source, pt, ct, extract_failures


def main() -> int:
    if os.environ.get("RUN_SCALE_DRAFT", "").strip() not in {"1", "true", "yes"}:
        print(
            "Refusing to run: set RUN_SCALE_DRAFT=1 to opt into the live "
            "fixture_001 scale draft (expensive; not part of test_all_stages)."
        )
        return 2

    load_dotenv(WORKDIR / ".env")
    provider = ModelProvider()

    print("=" * 72)
    print("fixture_001 scale draft (opt-in)")
    print(f"manifest={FIXTURE_001_MANIFEST_PATH}")
    print(f"draft retry budget={VALIDATION_RETRY_ATTEMPTS}")
    print("=" * 72)

    t0 = time.perf_counter()
    ledger, model, tokens_by_source, pt, ct, extract_failures = _build_fixture_001_ledger(
        provider
    )
    extract_cost = compute_cost_usd(model, pt, ct)
    extract_tokens = sum(tokens_by_source.values())
    narrative_n = sum(1 for s in ledger.sources if s.doc_class == "narrative")
    score_n = sum(1 for s in ledger.sources if s.doc_class == "score_report")
    print(
        f"  extract: sources={len(ledger.sources)} "
        f"(narrative={narrative_n} score_report={score_n}) "
        f"facts={len(ledger.facts)} failures={len(extract_failures)} "
        f"tokens={extract_tokens} cost_usd={round(extract_cost, 6)}"
    )
    for fail in extract_failures[:8]:
        print(f"    extract_fail {fail['source_id']}: {fail['error']}")

    conflicts, variance, timelines, _, _ = detect_disagreements_from_ledger(ledger)
    print(
        f"  conflicts={len(conflicts)} variance={len(variance)} "
        f"timelines={len(timelines)}"
    )

    draft_body = DraftRequest(
        confirm_synthetic=True,
        section="history",
        ledger=ledger,
        conflicts=conflicts,
        variance=variance,
        model=model,
        entailment_model="gpt-4o-mini",
    )

    draft_attempts = {"n": 0}

    def _attempt(_i: int):
        draft_attempts["n"] += 1
        print(f"  draft attempt {draft_attempts['n']}/{VALIDATION_RETRY_ATTEMPTS}…")
        return draft_section(provider, draft_body)

    try:
        resp = run_with_validation_retries(
            _attempt,
            max_attempts=VALIDATION_RETRY_ATTEMPTS,
            failure_prefix="Draft failed validation after retry",
        )
    except HTTPException as exc:
        elapsed = time.perf_counter() - t0
        print(f"FAIL  HTTP {exc.status_code}: {exc.detail}")
        print(f"  attempts={draft_attempts['n']}  elapsed_s={elapsed:.1f}")
        return 1

    elapsed = time.perf_counter() - t0
    age_cited = [
        f
        for f in (resp.answer.facts if resp.answer else [])
        if f.fact_id == "f_computed_age_years"
    ]
    print(
        f"PASS  populated={resp.section_populated} "
        f"attempts={draft_attempts['n']} "
        f"draft_tokens={resp.tokens_used} cost_usd={resp.cost_usd} "
        f"age_years_expected={resp.age_years_expected} "
        f"derived_age_cites={len(age_cited)} "
        f"review_items={len(resp.review.items)} "
        f"elapsed_s={elapsed:.1f}"
    )
    if resp.answer and resp.answer.prose:
        preview = resp.answer.prose[:240].replace("\n", " ")
        print(f"  prose preview: {preview}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
