"""
Predicate vocabulary for Background & History ledger facts.

Extraction selects from this vocabulary via structured-output enum (plus an
`__unregistered__` escape with proposed_predicate). Unknown proposals route to
predicates_for_review — never silently treated as known.

Predicate class (required for every entry) is what Stage 3 conflict detection
uses instead of domain keywords:

  record        — one true value exists; disagreement means a source is wrong
                  (legal_name, dob, birth_term, allergy_status, grade, …)
  perspectival  — multiple valid viewpoints; disagreement is clinical signal
                  (rating scales, behavioral concerns, interview impressions)

Molly's questionnaire battery (BASC, ASRS, SRS, ABAS, SSIS-SEL) is given to
parent, student, and teacher by design. Rater disagreement on perspectival
predicates must never become a conflict (spec §9.2).

Temporality is a property of the predicate (default_temporality), stamped
server-side after extraction — not chosen per instance by the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

PredicateClass = Literal["record", "perspectival"]
TemporalityHint = Literal["durable", "as_of"]

# Escape valve for gap discovery — must stay outside PREDICATES.
UNREGISTERED_PREDICATE = "__unregistered__"


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    """One entry in the Background & History predicate vocabulary."""

    name: str
    predicate_class: PredicateClass
    description: str
    default_temporality: TemporalityHint
    notes: str = ""
    takes_qualifier: bool = False


def _p(
    name: str,
    predicate_class: PredicateClass,
    description: str,
    default_temporality: TemporalityHint,
    notes: str = "",
    *,
    takes_qualifier: bool = False,
) -> PredicateSpec:
    return PredicateSpec(
        name=name,
        predicate_class=predicate_class,
        description=description,
        default_temporality=default_temporality,
        notes=notes,
        takes_qualifier=takes_qualifier,
    )


# ---------------------------------------------------------------------------
# Vocabulary — start with what Background & History needs. Add entries as
# extraction surfaces stable claim types; do not invent clinical topics in
# validators or prompts to compensate for a missing predicate.
# ---------------------------------------------------------------------------

PREDICATE_VOCABULARY: tuple[PredicateSpec, ...] = (
    # --- provenance (subject is a source id, not the child) ---
    _p(
        "defers_to",
        "record",
        "Source defers clinical/content detail to other named sources or records.",
        "as_of",
        "Subject must be the deferring source's id. Preserves deferral without inventing clinical facts.",
    ),
    # --- identity / demographics (record) ---
    _p(
        "legal_name",
        "record",
        "Legal or documented student name as stated on a source.",
        "durable",
        "Header vs body name mismatches are record conflicts.",
    ),
    _p(
        "dob",
        "record",
        "Date of birth as stated in a source (ISO when possible).",
        "durable",
    ),
    _p(
        "age_years",
        "record",
        "Age in whole years as stated in a source at that source's date.",
        "as_of",
        "Never infer from grade. Historical ages are as_of the source date.",
    ),
    _p(
        "grade",
        "record",
        "Grade placement as stated (K, 1, 2, …).",
        "as_of",
        "Never infer from age. Aggressively as_of — old grades do not imply current placement.",
    ),
    _p(
        "retention_year",
        "record",
        "Grade level or school year in which retention occurred.",
        "durable",
        "Retention is a historical event; grade-for-age atypicality alone is not a conflict.",
    ),
    # --- birth / early development (record, durable) ---
    _p(
        "pregnancy_course",
        "record",
        "Pregnancy course (e.g. uncomplicated, complications noted).",
        "durable",
    ),
    _p(
        "birth_term",
        "record",
        "Gestational term at birth (full-term, preterm, weeks if stated).",
        "durable",
    ),
    _p(
        "birth_delivery",
        "record",
        "Delivery type (vaginal, cesarean, etc.).",
        "durable",
    ),
    _p(
        "nicu",
        "record",
        "Whether NICU stay occurred (and duration if stated).",
        "durable",
    ),
    _p(
        "walked_age_months",
        "record",
        "Age in months when child walked independently.",
        "durable",
    ),
    _p(
        "first_words_age_months",
        "record",
        "Age in months of first words.",
        "durable",
    ),
    _p(
        "two_word_phrases_age_months",
        "record",
        "Age in months of two-word phrases.",
        "durable",
    ),
    _p(
        "developmental_history",
        "record",
        "Overall developmental history characterization when explicitly stated (e.g. typical).",
        "durable",
        "Not for concern-onset timing — use developmental_concern_onset for when concerns began.",
    ),
    # --- health (record) ---
    _p(
        "allergy_status",
        "record",
        "Allergy classification or presence (known, undiagnosed, none, etc.).",
        "durable",
        "Put the substance in qualifier (peanuts, dairy). Do not mint peanut_allergy_status.",
        takes_qualifier=True,
    ),
    _p(
        "allergy_substance",
        "record",
        "Named allergen when stated without a status classification.",
        "durable",
        "Prefer allergy_status + qualifier when both substance and status appear.",
    ),
    _p(
        "epinephrine_available",
        "record",
        "Whether epinephrine auto-injector is available / where kept.",
        "as_of",
        takes_qualifier=True,
    ),
    _p(
        "health_plan_status",
        "record",
        "Individual health plan status (draft, active/on file, none).",
        "as_of",
    ),
    _p(
        "medications",
        "record",
        "Daily or prescribed medications as stated (including none).",
        "as_of",
        takes_qualifier=True,
    ),
    _p(
        "hospitalizations",
        "record",
        "Major medical hospitalizations (including none).",
        "durable",
    ),
    _p(
        "sleep",
        "record",
        "Sleep quality/pattern as stated.",
        "as_of",
        "If framed as a rater impression rather than a factual report, prefer a perspectival predicate.",
    ),
    # --- school / services (record) ---
    _p(
        "attendance",
        "record",
        "Attendance pattern (regular, chronic absence, etc.).",
        "as_of",
    ),
    _p(
        "iep_status",
        "record",
        "Whether an IEP is in place.",
        "as_of",
    ),
    _p(
        "plan_504_status",
        "record",
        "Whether a 504 plan is in place.",
        "as_of",
    ),
    _p(
        "special_education_eligibility",
        "record",
        "Formal special education eligibility status.",
        "as_of",
    ),
    _p(
        "intervention_tier",
        "record",
        "MTSS/RTI tier or named school intervention in place.",
        "as_of",
        takes_qualifier=True,
    ),
    _p(
        "private_tutoring",
        "record",
        "Whether private tutoring occurred (and when/grade if stated).",
        "durable",
        "Omission in a school log vs positive parent report can be a record conflict.",
    ),
    _p(
        "behavioral_referral",
        "record",
        "Whether behavioral referrals occurred in a stated period.",
        "as_of",
    ),
    _p(
        "referral_reason",
        "record",
        "Stated reason the child was referred for this evaluation "
        "(e.g. learning concerns, attention concerns).",
        "as_of",
        "Use for referral purpose/reason only — not for classroom behavior "
        "or in-session testing demeanor.",
    ),
    # --- academic skill levels ---
    _p(
        "reading_fluency",
        "record",
        "Reading fluency level relative to peers or grade expectations.",
        "as_of",
    ),
    _p(
        "spelling",
        "record",
        "Spelling skill level as stated.",
        "as_of",
    ),
    _p(
        "written_expression",
        "record",
        "Written expression skill level as stated.",
        "as_of",
    ),
    _p(
        "math_computation",
        "record",
        "Math computation skill level as stated.",
        "as_of",
    ),
    _p(
        "phonics_progress",
        "record",
        "Phonics / letter-sound progress as stated.",
        "as_of",
    ),
    # --- perspectival ---
    _p(
        "inattention_rating",
        "perspectival",
        "Rater severity for inattention/distractibility (scale score or qualitative).",
        "as_of",
        "Parent vs teacher disagreement is expected clinical variance, not a conflict.",
    ),
    _p(
        "hyperactivity_rating",
        "perspectival",
        "Rater severity for hyperactivity/impulsivity.",
        "as_of",
    ),
    _p(
        "behavioral_concern",
        "perspectival",
        "Ongoing problem behavior outside a structured test session — "
        "classroom conduct, aggression, noncompliance, or home behavior concerns "
        "described by a rater/interview.",
        "as_of",
        "Do NOT use for in-session testing demeanor (cooperative, attentive during WISC) — "
        "that is testing_impression. Do NOT use for ordinary on-task engagement in class — "
        "that is classroom_engagement_impression.",
        takes_qualifier=True,
    ),
    _p(
        "anxiety_impression",
        "perspectival",
        "Anxiety or emotional impression from a rater/observer.",
        "as_of",
    ),
    _p(
        "homework_completion_impression",
        "perspectival",
        "Homework completion impression from a rater.",
        "as_of",
    ),
    _p(
        "classroom_engagement_impression",
        "perspectival",
        "Day-to-day attention, on-task behavior, or engagement during ordinary "
        "classroom instruction (not a formal test battery).",
        "as_of",
        "Teacher notes about attentive/distracted in class go here. "
        "Examiner notes about cooperation during standardized testing → testing_impression. "
        "Problem behaviors (aggression, defiance) → behavioral_concern.",
    ),
    _p(
        "testing_impression",
        "perspectival",
        "Examiner/observer impression of the child's demeanor during a formal "
        "testing or assessment session (e.g. cooperative, fatigued, anxious in session).",
        "as_of",
        "Only for in-session testing behavior — not classroom engagement or ongoing "
        "behavioral concerns at home/school.",
    ),
    _p(
        "developmental_concern_onset",
        "perspectival",
        "When/how a reporter describes concerns beginning (timing of onset).",
        "durable",
        "Only for onset timing — not for 'development was typical' (use developmental_history).",
    ),
    _p(
        "preschool_experience_impression",
        "perspectival",
        "Reporter impression of preschool experience.",
        "durable",
    ),
    _p(
        "interview_impression",
        "perspectival",
        "General interview/examiner impression not captured by a narrower predicate.",
        "as_of",
    ),
)

PREDICATES: dict[str, PredicateSpec] = {p.name: p for p in PREDICATE_VOCABULARY}

if len(PREDICATES) != len(PREDICATE_VOCABULARY):
    raise RuntimeError("Duplicate predicate names in PREDICATE_VOCABULARY")

REGISTERED_PREDICATE_NAMES: tuple[str, ...] = tuple(p.name for p in PREDICATE_VOCABULARY)

# Closed enum for structured extraction output (registered names + escape valve).
# Enum *member names* cannot start with "_"; the *value* remains __unregistered__.
_EXTRACT_PREDICATE_MEMBERS = {name: name for name in REGISTERED_PREDICATE_NAMES}
_EXTRACT_PREDICATE_MEMBERS["unregistered"] = UNREGISTERED_PREDICATE
ExtractPredicateName = Enum(
    "ExtractPredicateName",
    _EXTRACT_PREDICATE_MEMBERS,
    type=str,
)


def temporality_for_predicate(name: str) -> TemporalityHint:
    """
    Authoritative temporality from the vocabulary.

    Unregistered / unknown predicates default to as_of so they do not falsely
    collide as durable conflicts before review.
    """

    spec = PREDICATES.get(name)
    if spec is None:
        return "as_of"
    return spec.default_temporality


# ---------------------------------------------------------------------------
# Canonical subjects — entity identifiers, never display names.
#
# Names (Justin, Jason, …) are values of legal_name. Using a name as subject
# would split one child into two entities when the name itself is disputed,
# silently breaking grouping for every other predicate between those sources.
#
# Provenance predicates (defers_to) use a source id as subject — those are
# accepted when the id is in the case's source list (checked at review time).
# ---------------------------------------------------------------------------

CANONICAL_SUBJECTS: frozenset[str] = frozenset(
    {
        "child",
        "mother",
        "father",
        "school",
    }
)

# Provenance predicates describe the *source document*, not a person/entity.
# Subject is stamped server-side as the source being extracted — never model-chosen.
PROVENANCE_PREDICATES: frozenset[str] = frozenset(
    name for name, spec in PREDICATES.items() if name == "defers_to"
)

# Closed enum for structured extraction — person/entity subjects only (no source ids).
ExtractSubjectName = Enum(
    "ExtractSubjectName",
    {name: name for name in sorted(CANONICAL_SUBJECTS)},
    type=str,
)


def is_provenance_predicate(name: str) -> bool:
    return name in PROVENANCE_PREDICATES


def is_known_predicate(name: str) -> bool:
    return name in PREDICATES


def get_predicate(name: str) -> PredicateSpec | None:
    return PREDICATES.get(name)


def predicate_class_of(name: str) -> PredicateClass | None:
    """Return record/perspectival for known predicates; None if unknown."""

    spec = PREDICATES.get(name)
    return spec.predicate_class if spec else None


def needs_predicate_review(name: str) -> bool:
    """
    True when extraction emitted a predicate outside the vocabulary.

    Unknown predicates must be flagged for review — never silently accepted as
    known, or conflict grouping by subject+predicate becomes unreliable.
    The __unregistered__ sentinel itself is never a stored predicate name.
    """

    if name == UNREGISTERED_PREDICATE:
        return True
    return name not in PREDICATES


def needs_subject_review(subject: str, *, known_source_ids: set[str] | None = None) -> bool:
    """
    True when subject is not a canonical entity and not a known source id.

    Source ids are valid subjects only for provenance facts (defers_to), stamped
    server-side — never emitted by the model enum.
    """

    s = (subject or "").strip()
    if not s:
        return True
    if s in CANONICAL_SUBJECTS:
        return False
    if known_source_ids and s in known_source_ids:
        return False
    return True


def resolve_predicate(name: str) -> tuple[PredicateSpec | None, bool]:
    """
    Look up a predicate name.

    Returns (spec, needs_review). When needs_review is True, spec is None and
    the caller must surface the unknown predicate for human review.
    """

    spec = PREDICATES.get(name)
    if spec is None:
        return None, True
    return spec, False


def fact_grouping_key(subject: str, predicate: str, qualifier: str | None) -> tuple[str, str, str | None]:
    """Conflict grouping key: subject + predicate + qualifier (normalized)."""

    q = qualifier.strip().lower() if qualifier and qualifier.strip() else None
    return (subject.strip(), predicate.strip(), q)
