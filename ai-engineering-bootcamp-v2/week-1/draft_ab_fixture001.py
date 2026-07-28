#!/usr/bin/env python3
"""
Opt-in Draft A/B on fixture_001 — diagnose the traceability-enumeration hypothesis.

Same ledger, two system prompts:
  A — current draft_prompt.md (per-claim statements/fact_id required)
  B — same prompt with that contract relaxed (flowing multi-fact sentences allowed;
      statements optional / coarse)

Prose only for human side-by-side reading. No scores, no prompt file mutation.

Usage
-----
  RUN_DRAFT_AB=1 python draft_ab_fixture001.py

Caches the extracted ledger at fixtures/fixture_001/_ab_ledger_cache.json so a
re-run only re-drafts. Delete that file to force a fresh extract.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from conflicts import detect_disagreements_from_ledger
from draft import DRAFT_SYSTEM_PROMPT, _draft_user_payload
from draft_fixture001_scale import _build_fixture_001_ledger
from provider import DRAFT_TEMPERATURE, ModelProvider, compute_cost_usd
from schemas import DraftRequest, DraftStatement, Ledger, LifeStage, UnverifiedCitation
from test_all_stages import FIXTURE_001_MANIFEST_PATH

WORKDIR = Path(__file__).resolve().parent
CACHE_PATH = WORKDIR / "fixtures" / "fixture_001" / "_ab_ledger_cache.json"
OUT_PATH = WORKDIR / "draft_ab_fixture001_prose.md"

RELAXED_PROMPT = """# Drafting prompt — Background & History (prose only)

You write the **Background & History** narrative for a Licensed Educational Psychologist.
She reviews, edits, and signs. You never have final authority.

## Settled input (no discretion)

You receive **durable facts** (atemporal) and **timelines** (as_of progressions already
ordered by `as_of_date`), plus **must-mention conflicts** detected upstream. You do **not**
decide which facts exist or which conflicts are real. Your only job is prose.

Timelines are one chronological lens — not the whole case. Durable facts (birth history,
milestones, diagnoses) have no timeline and must still be used when relevant.

## Output

Return `DraftProseRelaxedOutput`:
- `prose` — paste-ready narrative
- `statements` — optional; may be empty or coarse. Do **not** enumerate one fact per sentence.
- `unverified_citations` — education-code / public legal citations only (see carve-out)
- `coverage` — life stages represented

## Hard rules

1. **Ground prose in the ledger; do not invent.** Use only facts present in `durable_facts`
   or timeline entries. Do not invent clinical, developmental, or biographical claims.
   You are **not** required to list every claim in `statements` or attach a `fact_id` to
   every sentence. Prefer flowing, multi-fact sentences over one-fact-per-sentence
   enumeration.
2. **Must-mention conflicts.** Every item in `must_mention_conflicts` must appear in `prose`
   neutrally (both sides). Do not resolve, rank, or pick a winner. Do not bury a conflict
   as a soft aside — state both versions clearly.
3. **Variance.** If `variance` is provided (rater/informant differences), present as comparison
   when relevant — not as an error.
4. **Ledger facts only in prose.** Ages, DOB, grade, and other predicates must come from the
   ledger. There is no administrative-framing exemption and no invented biographical statement.
   **Current age:** when prose states the child's **current** age, use the derived
   `age_years` row (`source_id: computed`, derivation `dob + evaluation_date`). Do not
   use a historical source age (e.g. "age 8" from an old IEP) as the current age.
   You do **not** need a per-sentence `statements` entry for age or any other claim.
5. **Chronology from timelines.** Follow each timeline's date order when narrating that
   predicate. Present tense is reserved for the latest entry (`is_latest: true`); earlier
   entries must be framed historically ("as of [date]…", "the [year] file stated…").
6. **Tone — write in Molly's voice.** Derived from her signed reports (evidence in
   `Molly_Voice_Profile.md`). Tone only — it never overrides the reliability rules above
   (ledger grounding, must-mention conflicts, chronology). Never invent warmth, implication,
   or certainty to hit the voice.

   - **Narrative register.** Flowing, chronological prose — not clipped clinical notes.
     Paragraphs are topically tight (one theme each: pregnancy, milestones, health, schooling);
     sentences within them may be medium-to-long and detail-dense. Retain concrete specifics
     from the ledger verbatim — dates, weights, ages in months, provider names, doses — rather
     than rounding or generalizing. Refer to the child by first name.
   - **First person for evaluator actions.** Use first person for steps the evaluator took —
     "I observed," "I administered," "the assessment revealed" — not "this evaluator" or
     "Mrs. Harrison administered." The student stays the grammatical subject for the majority
     of the narrative; reserve first person for direct observation, test administration, and
     clinical synthesis, and don't overuse it. Pairs with active voice ("The student struggled
     to decode unfamiliar words," not "Difficulty was experienced by the student").
   - **Strengths-based, not deficit-based.** Default to strengths-based, collaborative phrasing
     over deficit-based, negative phrasing wherever both are equally supported by the facts;
     lead with the child's strengths and close on a forward-looking, support-oriented note.
     This is a framing preference, not license to soften or omit a real finding — strengths
     stated must still come from ledger facts.
   - **Accessible plain language.** The parent and student read these reports. Gloss an
     unavoidable technical term in everyday words (a parenthetical plain equivalent); keep
     psychometric labels and any statutory language precise. Accessibility applies to
     interpretation, not to the numbers or the law.
   - **Calibrated certainty — match the hedge to the uncertainty.** Use *may / could / suggests*
     for implications; *is consistent with* or *the pattern of results indicates* for findings
     that converge across sources; *by report / reportedly* for second-hand claims; an explicit
     snapshot / "minimal estimate" caveat for test-level limits. Do not launder a hedged source
     fact into a flat assertion, and do not manufacture confidence the ledger doesn't support.
   - **Temporal bounding.** Findings and status read as current-not-permanent ("at this time,"
     "a snapshot of current functioning"), because children are always changing. Consistent
     with the chronology rule (5) above.
   - **Variance is comparison, not error** (reinforces rule 3 / spec §9.2). Name each rater and
     set their views in parallel clauses (*while / whereas / however*); keep the divergence
     visible; never average multiple raters into one smoothed statement.
   - **Non-judgmental toward schools and prior evaluators.** State any disagreement factually
     and neutrally; do not editorialize against a district or a previous report.
   - Cite by source label + date in prose where helpful.

   Terminology substitutions (e.g. "Very Low" not "Extremely Low") are enforced deterministically
   by `terminology.py`, not by this prompt — do not duplicate that list here.

   **Scope note.** This prompt drafts **Background & History**. Section-specific registers not
   used here — the findings triad (instrument → result → real-world impact), recommendation
   sample scripts, verbatim "About test scores" explainer blocks, and IEE discrepancy analysis —
   live in `Molly_Voice_Profile.md` (§2.2, §2.4, §11, Appendix A). Load them if/when this prompt
   is reused for those sections; do not inline them here.

## Ed-code carve-out (public legal authority only)

You may include California Education Code (or similar public statute/regulation) citations
that are not in the ledger. Put each in `unverified_citations` with `unverified: true`.
These never become ledger facts.

**Allowed:** public legal / regulatory citations.
**Forbidden to invent:** anything clinical, developmental, historical, or about the child.

## Synthetic data only

Treat names as fake.
"""


class DraftProseRelaxedOutput(BaseModel):
    """Relaxed A/B variant — statements optional; prose is the product."""

    prose: str
    statements: list[DraftStatement] = Field(
        default_factory=list,
        description=(
            "Optional. May be empty or coarse (paragraph-level). "
            "Do not enumerate one fact per sentence."
        ),
    )
    unverified_citations: list[UnverifiedCitation] = Field(default_factory=list)
    coverage: list[LifeStage] = Field(default_factory=list)


class DraftProseStrictOutput(BaseModel):
    """Mirror of production DraftProseOutput — kept local so A/B doesn't import mutables."""

    prose: str
    statements: list[DraftStatement] = Field(
        description=(
            "Every substantive claim covered by some entry with real ledger "
            "fact_ids. An entry may span clauses/sentences and carry several ids."
        ),
    )
    unverified_citations: list[UnverifiedCitation] = Field(default_factory=list)
    coverage: list[LifeStage] = Field(default_factory=list)


def _load_or_build_ledger(provider: ModelProvider):
    if CACHE_PATH.exists():
        print(f"  loading cached ledger: {CACHE_PATH}")
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        ledger = Ledger.model_validate(data["ledger"])
        model = data.get("model") or "gpt-4o-mini"
        print(f"  cache: facts={len(ledger.facts)} sources={len(ledger.sources)}")
        return ledger, model

    print("  extracting fixture_001 ledger (no cache)…")
    ledger, model, tokens_by_source, pt, ct, extract_failures = _build_fixture_001_ledger(
        provider
    )
    extract_cost = compute_cost_usd(model, pt, ct)
    print(
        f"  extract: facts={len(ledger.facts)} sources={len(ledger.sources)} "
        f"failures={len(extract_failures)} tokens={sum(tokens_by_source.values())} "
        f"cost_usd={round(extract_cost, 6)}"
    )
    CACHE_PATH.write_text(
        json.dumps(
            {"model": model, "ledger": ledger.model_dump(mode="json")},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  wrote cache: {CACHE_PATH}")
    return ledger, model


def _draft_once(
    provider: ModelProvider,
    *,
    label: Literal["A", "B"],
    system: str,
    schema: type[BaseModel],
    body: DraftRequest,
) -> tuple[str, int, float]:
    print(f"  drafting variant {label} (temp={DRAFT_TEMPERATURE})…")
    t0 = time.perf_counter()
    result = provider.complete_structured(
        model=body.model or "gpt-4o-mini",
        system=system,
        user=_draft_user_payload(body, timeline_shaped=True),
        schema=schema,
        temperature=DRAFT_TEMPERATURE,
    )
    elapsed = time.perf_counter() - t0
    output = result.data
    prose = getattr(output, "prose", "") or ""
    cost = compute_cost_usd(
        body.model or "gpt-4o-mini", result.prompt_tokens, result.completion_tokens
    )
    n_stmt = len(getattr(output, "statements", []) or [])
    print(
        f"  {label}: tokens={result.total_tokens} cost_usd={round(cost, 6)} "
        f"statements={n_stmt} elapsed_s={elapsed:.1f} prose_chars={len(prose)}"
    )
    return prose, result.total_tokens, cost


def _write_side_by_side(prose_a: str, prose_b: str) -> None:
    body = f"""# Draft A/B — fixture_001 Background & History

**Date:** 2026-07-27
**Hypothesis under test:** the per-sentence `statements`/`fact_id` traceability contract
forces one-fact-per-sentence enumeration.
**Controls:** same ledger, same model, `DRAFT_TEMPERATURE=1.0`. Only the system prompt
(+ schema description for statements) differs. No scores.

---

## Variant A — current `draft_prompt.md`

*(Every substantive claim → `statements` entry with `fact_id`.)*

{prose_a.strip()}

---

## Variant B — per-sentence statements/fact_id relaxed

*(Ledger-grounded prose still required; statements optional/coarse; multi-fact sentences encouraged.)*

{prose_b.strip()}
"""
    OUT_PATH.write_text(body + "\n", encoding="utf-8")
    print(f"  wrote {OUT_PATH}")


def main() -> int:
    if os.environ.get("RUN_DRAFT_AB", "").strip() not in {"1", "true", "yes"}:
        print(
            "Refusing to run: set RUN_DRAFT_AB=1 to opt into the live "
            "fixture_001 Draft A/B (extract+two drafts; expensive)."
        )
        return 2

    load_dotenv(WORKDIR / ".env")
    provider = ModelProvider()

    print("=" * 72)
    print("Draft A/B — fixture_001 (traceability contract)")
    print(f"manifest={FIXTURE_001_MANIFEST_PATH}")
    print("=" * 72)

    t0 = time.perf_counter()
    try:
        ledger, model = _load_or_build_ledger(provider)
    except ValidationError as exc:
        print(f"FAIL  ledger build: {exc}")
        return 1

    conflicts, variance, timelines, _, _ = detect_disagreements_from_ledger(ledger)
    print(
        f"  conflicts={len(conflicts)} variance={len(variance)} "
        f"timelines={len(timelines)}"
    )

    body = DraftRequest(
        confirm_synthetic=True,
        section="history",
        ledger=ledger,
        conflicts=conflicts,
        variance=variance,
        model=model,
        entailment_model="gpt-4o-mini",
    )

    prose_a, _, _ = _draft_once(
        provider,
        label="A",
        system=DRAFT_SYSTEM_PROMPT,
        schema=DraftProseStrictOutput,
        body=body,
    )
    prose_b, _, _ = _draft_once(
        provider,
        label="B",
        system=RELAXED_PROMPT,
        schema=DraftProseRelaxedOutput,
        body=body,
    )

    _write_side_by_side(prose_a, prose_b)
    print(f"DONE  elapsed_s={time.perf_counter() - t0:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
