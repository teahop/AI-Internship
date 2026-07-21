"""Deterministic section coverage / gap report — no model call.

Each section declares the predicates (and optional life stages / source types)
it consumes. Compare declared vs present in the ledger. Absence is described,
never graded as failure.

Freshness (absent / stale / current) applies only to as_of predicates.
Durable predicates are excluded explicitly — they are never stale.
"""

from __future__ import annotations

from dataclasses import dataclass

from conflicts import compute_timelines
from derived import COMPUTED_SOURCE_ID, REQUEST_SOURCE_ID, is_derived_fact
from predicates import get_predicate
from schemas import (
    GapReport,
    Ledger,
    LifeStage,
    PredicateFreshness,
    SectionCoverage,
    SectionName,
    SourceType,
)
from validators import parse_iso_date


@dataclass(frozen=True, slots=True)
class SectionDeclaration:
    """What a section expects from the case packet — structural, not clinical rules."""

    section: SectionName
    predicates: frozenset[str]
    life_stages: tuple[LifeStage, ...]
    source_types: frozenset[SourceType]


# Background & History consumes identity, birth/dev, health, and school predicates.
# Extend when new sections are added — do not encode clinical detection here.
HISTORY_PREDICATES = frozenset(
    {
        "legal_name",
        "dob",
        "age_years",
        "grade",
        "retention_year",
        "pregnancy_course",
        "birth_term",
        "birth_delivery",
        "nicu",
        "walked_age_months",
        "first_words_age_months",
        "two_word_phrases_age_months",
        "developmental_history",
        "developmental_concern_onset",
        "preschool_experience_impression",
        "allergy_status",
        "medications",
        "hospitalizations",
        "sleep",
        "attendance",
        "iep_status",
        "plan_504_status",
        "special_education_eligibility",
        "intervention_tier",
        "private_tutoring",
        "reading_fluency",
        "spelling",
        "written_expression",
        "math_computation",
        "phonics_progress",
        "inattention_rating",
        "behavioral_concern",
        "anxiety_impression",
        "homework_completion_impression",
    }
)

HISTORY_LIFE_STAGES: tuple[LifeStage, ...] = (
    "birth",
    "infancy",
    "preschool",
    "school-age",
    "current",
)

HISTORY_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {"parent", "school", "teacher", "assessment"}
)

SECTION_DECLARATIONS: dict[SectionName, SectionDeclaration] = {
    "history": SectionDeclaration(
        section="history",
        predicates=HISTORY_PREDICATES,
        life_stages=HISTORY_LIFE_STAGES,
        source_types=HISTORY_SOURCE_TYPES,
    ),
}

# ---------------------------------------------------------------------------
# Staleness thresholds (days before evaluation_date).
# Conservative default; override per predicate without changing logic.
# Open clinical question — fill in with Molly later.
# ---------------------------------------------------------------------------

DEFAULT_STALE_DAYS = 365

# Per-predicate overrides (only as_of predicates; durable never consulted).
PREDICATE_STALE_DAYS: dict[str, int] = {
    # Placeholders — clinician to confirm. Structure only; values are conservative.
    "reading_fluency": 365,
    "spelling": 365,
    "written_expression": 365,
    "math_computation": 365,
    "phonics_progress": 365,
    "grade": 365,
    "age_years": 365,
    "attendance": 180,
    "iep_status": 365,
    "plan_504_status": 365,
    "special_education_eligibility": 365,
    "intervention_tier": 365,
    "medications": 180,
    "sleep": 180,
    "inattention_rating": 365,
    "behavioral_concern": 365,
    "anxiety_impression": 365,
    "homework_completion_impression": 365,
    "classroom_engagement_impression": 365,
}


def stale_threshold_days(predicate: str) -> int:
    return PREDICATE_STALE_DAYS.get(predicate, DEFAULT_STALE_DAYS)


def _is_durable_predicate(predicate: str) -> bool:
    """
    Durable predicates are excluded from freshness entirely.

    Prefer vocabulary default_temporality; unknown predicates are treated as
    as_of for freshness purposes (conservative — may surface as absent/stale).
    """

    spec = get_predicate(predicate)
    if spec is None:
        return False
    return spec.default_temporality == "durable"


def _document_facts(ledger: Ledger):
    """Facts from real sources — exclude request/computed synthetic rows."""

    for fact in ledger.facts:
        if is_derived_fact(fact):
            continue
        if fact.source_id in {REQUEST_SOURCE_ID, COMPUTED_SOURCE_ID}:
            continue
        yield fact


def _freshness_for_section(
    ledger: Ledger,
    decl: SectionDeclaration,
) -> list[PredicateFreshness]:
    """
    Per declared as_of predicate: absent / stale / current.

    Durable predicates are skipped (never stale). Uses the latest timeline entry
    for that predicate (any qualifier) against evaluation_date.
    """

    evaluation_date = ledger.child.evaluation_date
    timelines = compute_timelines(ledger.facts)
    # Latest entry per predicate (qualifier-agnostic for section-level freshness).
    latest_by_pred: dict[str, tuple[str, str | None, str]] = {}
    for tl in timelines:
        if not tl.entries:
            continue
        latest = next((e for e in reversed(tl.entries) if e.is_latest), tl.entries[-1])
        prev = latest_by_pred.get(tl.predicate)
        if prev is None or latest.as_of_date > prev[0]:
            latest_by_pred[tl.predicate] = (
                latest.as_of_date,
                latest.fact_id,
                tl.qualifier,
            )

    # Also count presence of any as_of fact for the predicate (including single entries).
    as_of_present = {
        f.predicate
        for f in ledger.facts
        if f.temporality == "as_of" and f.predicate in decl.predicates
    }

    rows: list[PredicateFreshness] = []
    for predicate in sorted(decl.predicates):
        if _is_durable_predicate(predicate):
            continue
        threshold = stale_threshold_days(predicate)
        if predicate not in as_of_present and predicate not in latest_by_pred:
            rows.append(
                PredicateFreshness(
                    predicate=predicate,
                    state="absent",
                    latest_as_of_date=None,
                    threshold_days=threshold,
                    fact_id=None,
                )
            )
            continue
        latest_date, fact_id, qual = latest_by_pred.get(
            predicate, (None, None, None)
        )
        if latest_date is None:
            rows.append(
                PredicateFreshness(
                    predicate=predicate,
                    state="absent",
                    threshold_days=threshold,
                )
            )
            continue
        age_days = (parse_iso_date(evaluation_date) - parse_iso_date(latest_date)).days
        state = "stale" if age_days > threshold else "current"
        rows.append(
            PredicateFreshness(
                predicate=predicate,
                qualifier=qual,
                state=state,  # type: ignore[arg-type]
                latest_as_of_date=latest_date,
                threshold_days=threshold,
                fact_id=fact_id,
            )
        )
    return rows


def coverage_for_section(ledger: Ledger, section: SectionName) -> SectionCoverage:
    decl = SECTION_DECLARATIONS.get(section)
    if decl is None:
        return SectionCoverage(
            section=section,
            available=False,
            predicates_covered=[],
            predicates_missing=[],
            predicate_freshness=[],
            life_stages_empty=[],
            source_types_present=[],
            source_types_missing=[],
        )

    doc_facts = list(_document_facts(ledger))
    present_predicates = {f.predicate for f in ledger.facts if f.predicate in decl.predicates}

    covered = sorted(p for p in decl.predicates if p in present_predicates)
    missing = sorted(p for p in decl.predicates if p not in present_predicates)

    # Life-stage and source-type gaps are about gathering — synthetic rows do not fill them.
    stages_with_facts = {f.life_stage for f in doc_facts}
    empty_stages = [s for s in decl.life_stages if s not in stages_with_facts]

    present_types = {s.type for s in ledger.sources}
    types_missing = sorted(t for t in decl.source_types if t not in present_types)
    types_present = sorted(t for t in decl.source_types if t in present_types)

    # Unavailable only when no document-sourced facts exist for the section.
    available = len(doc_facts) > 0

    return SectionCoverage(
        section=section,
        available=available,
        predicates_covered=covered,
        predicates_missing=missing,
        predicate_freshness=_freshness_for_section(ledger, decl),
        life_stages_empty=empty_stages,  # type: ignore[arg-type]
        source_types_present=types_present,  # type: ignore[arg-type]
        source_types_missing=types_missing,  # type: ignore[arg-type]
    )


def build_gap_report(
    ledger: Ledger,
    *,
    sections: list[SectionName] | None = None,
) -> GapReport:
    names = sections or list(SECTION_DECLARATIONS.keys())
    return GapReport(sections=[coverage_for_section(ledger, name) for name in names])
