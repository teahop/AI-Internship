"""Typed I/O for Molly's Background & History draft — provenance in, attribution out.

Ledger Fact/Ledger are the extraction spine (Stages 2+). ReportSection remains
the /draft (and current /ask) narrative output — do not delete it.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from predicates import ExtractPredicateName, ExtractSubjectName, UNREGISTERED_PREDICATE


LifeStage = Literal["birth", "infancy", "preschool", "school-age", "current"]
SourceType = Literal[
    "assessment",
    "school",
    "parent",
    "teacher",
    "observation",
    "prior_eval",
    "other",
]
SectionName = Literal["history"]
Temporality = Literal["durable", "as_of"]
FactConfidence = Literal["stated", "hedged"]
FactAssertion = Literal["asserted", "denied"]
FreshnessState = Literal["absent", "stale", "current"]


class Child(BaseModel):
    """Identity for age math only — use initials / synthetic IDs, never real names here."""

    initials: str = Field(description="Synthetic initials only, e.g. J.D.")
    dob: str = Field(description="ISO date YYYY-MM-DD")
    evaluation_date: str = Field(description="ISO date YYYY-MM-DD of this evaluation")


class Source(BaseModel):
    """One dated input artifact — the provenance spine."""

    id: str = Field(description="Stable id referenced by SourcedFact.source_id / Fact.source_id")
    type: SourceType
    date: str = Field(description="ISO date YYYY-MM-DD when this source was created")
    label: str = Field(description="Short human label, e.g. Parent developmental history")
    content: str = Field(description="Full text / notes from this source")


class Fact(BaseModel):
    """
    One normalized claim in the case ledger.

    Conflict detection groups on (subject, predicate, qualifier). durable facts
    compare directly; as_of facts form a timeline by as_of_date (same-date
    disagreement only). Prefer predicates from predicates.py; unknown
    predicates must be flagged for review (needs_predicate_review), not silently
    accepted.

    subject is a canonical entity id (child, mother, father, school, or a source
    id for provenance) — never a display name.

    assertion is asserted | denied only. Non-assertion (silence, deferral, omission)
    produces no row — there is no not_stated value.

    grade is independent of age and life_stage — never infer one from another.
    """

    id: str = Field(description='Stable id, e.g. "f_001"')
    subject: str = Field(
        description=(
            'Who/what the claim is about, e.g. "child", "mother", "school". '
            "Provenance predicates (e.g. defers_to) take a source id as subject."
        ),
    )
    predicate: str = Field(
        description=(
            "Normalized claim type (e.g. birth_term, walked_age_months, "
            "allergy_status, legal_name, reading_fluency). See predicates.py."
        ),
    )
    value: str = Field(description="Normalized value used for comparison")
    value_text: str = Field(description="The claim in the source's own words")
    qualifier: str | None = Field(
        default=None,
        description=(
            "What the predicate is about when it can apply to more than one thing "
            "(e.g. peanuts, dairy). Null when the predicate admits only one subject-matter. "
            "Part of the conflict grouping key with subject + predicate."
        ),
    )
    assertion: FactAssertion = Field(
        description=(
            "asserted = positive claim; denied = explicit negative finding. "
            "Silence/deferral/omission → no fact row."
        ),
    )
    source_id: str = Field(description="Must be an id from the ledger sources list")
    source_date: str = Field(
        description="Must equal the matching input source's date (YYYY-MM-DD)"
    )
    as_of_date: str | None = Field(
        default=None,
        description=(
            "Date the claim is about (YYYY-MM-DD). Defaults to source_date. "
            "Differs when a later document summarizes an earlier dated claim "
            "(e.g. 2026 note citing a 2024 IEP grade)."
        ),
    )
    reporter: str | None = Field(
        default=None,
        description=(
            "Who/what the source text attributes this claim to. "
            "Never inferred — only when the source text states it."
        ),
    )
    life_stage: LifeStage
    grade: str | None = Field(
        default=None,
        description=(
            "Grade level at the time of the claim, if stated. "
            "Never infer from age. Aggressively as_of when used as a fact."
        ),
    )
    temporality: Temporality = Field(
        description=(
            "durable = stays true across evaluations (e.g. birth_term); "
            "as_of = true only at as_of_date (e.g. reading_fluency, grade, age_years)"
        ),
    )
    confidence: FactConfidence = Field(
        description="stated = asserted outright; hedged = qualified in the source",
    )
    derivation: str | None = Field(
        default=None,
        description=(
            "Null for extracted facts. For derived facts (source_id='computed'), "
            "names the inputs, e.g. 'dob + evaluation_date'. Provenance is the "
            "recomputation, not source text."
        ),
    )
    inherits_dispute: bool = Field(
        default=False,
        description=(
            "True when a derived fact's inputs are themselves disputed "
            "(e.g. age_years when dob has a record conflict). Still computed; "
            "never silently treated as settled."
        ),
    )

    @model_validator(mode="after")
    def _default_as_of_date(self) -> Self:
        if not self.as_of_date:
            self.as_of_date = self.source_date
        return self


class Ledger(BaseModel):
    """
    Case fact ledger returned to the caller — never persisted by this service
    (spec §4). Carries sources so downstream stages can check entailment.
    """

    child: Child
    ledger_version: str
    built_at: str = Field(description="ISO-8601 timestamp when this ledger was built")
    sources: list[Source] = Field(
        description="Input sources carried for entailment checks downstream",
    )
    facts: list[Fact]


class SourcedFact(BaseModel):
    """One claim in the draft, tied to where it came from and when."""

    statement: str
    fact_id: str | None = Field(
        default=None,
        description="Ledger Fact.id this statement traces to (required for /draft output)",
    )
    source_id: str = Field(description="Must be an id from the input sources list")
    source_date: str = Field(
        description="Must equal the matching input source's date (YYYY-MM-DD)"
    )
    life_stage: LifeStage
    reporter: str | None = Field(
        default=None,
        description=(
            "Who/what the source text attributes this claim to "
            "(e.g. school nurse, IEP document, father interview). "
            "Must match the source content — never invent a reporter."
        ),
    )


class Conflict(BaseModel):
    """Disagreement between sources — surfaced, not silently resolved."""

    topic: str
    versions: list[SourcedFact] = Field(min_length=2)


class ReportSection(BaseModel):
    """Structured history draft Molly can review, cite-check, and sign (/draft output)."""

    section: SectionName
    prose: str = Field(description="Paste-ready Background & History narrative")
    facts: list[SourcedFact] = Field(description="Every claim, attributed")
    conflicts: list[Conflict] = Field(
        default_factory=list,
        description="Conflicts between sources, left unresolved for clinician judgment",
    )
    coverage: list[LifeStage] = Field(
        description="Life stages represented in this draft (expect birth-to-present)",
    )


class ExtractedFactDraft(BaseModel):
    """Model output for one atomic fact from a single source (ids stamped server-side)."""

    subject: ExtractSubjectName = Field(
        default=ExtractSubjectName.child,
        description=(
            "Canonical entity: child | mother | father | school. "
            "Default child for student claims. Provenance predicates ignore this — "
            "server stamps the extracting source id."
        ),
    )
    predicate: ExtractPredicateName = Field(
        description=(
            "Registered predicate from the vocabulary, or __unregistered__ when "
            "proposing a new name via proposed_predicate."
        ),
    )
    proposed_predicate: str | None = Field(
        default=None,
        description=(
            "Required snake_case name when predicate is __unregistered__; "
            "null when using a registered predicate."
        ),
    )
    value: str = Field(
        min_length=1,
        description="Normalized comparison value — must be a non-empty stated claim",
    )
    value_text: str = Field(min_length=1, description="The claim in the source's own words")
    qualifier: str | None = None
    assertion: FactAssertion = "asserted"
    reporter: str | None = None
    life_stage: LifeStage
    grade: str | None = None
    confidence: FactConfidence
    as_of_date: str | None = Field(
        default=None,
        description=(
            "YYYY-MM-DD when the source names an explicit temporal anchor; "
            "null → server stamps source_date"
        ),
    )

    @field_validator("value")
    @classmethod
    def value_must_exist(cls, v: str) -> str:
        text = (v or "").strip()
        if not text or text.lower() == "null":
            raise ValueError("value must be a non-empty stated claim (not empty/null)")
        return text

    @model_validator(mode="after")
    def unregistered_requires_proposal(self) -> Self:
        pred = self.predicate.value if isinstance(self.predicate, ExtractPredicateName) else str(self.predicate)
        if pred == UNREGISTERED_PREDICATE:
            proposed = (self.proposed_predicate or "").strip()
            if not proposed:
                raise ValueError("proposed_predicate required when predicate is __unregistered__")
            self.proposed_predicate = proposed
        return self


class SourceExtraction(BaseModel):
    """Structured extraction result for exactly one source document."""

    facts: list[ExtractedFactDraft] = Field(default_factory=list)


class ExtractRequest(BaseModel):
    """
    Domain request for /extract.

    confirm_synthetic must be true — this OpenAI build never accepts real cases.
    """

    confirm_synthetic: Literal[True] = Field(
        description="Must be true. Refuses real PHI/PII cases; OpenAI runtime is synthetic-only.",
    )
    child: Child
    sources: list[Source] = Field(min_length=1)
    model: str | None = None


class PredicateFreshness(BaseModel):
    """
    Freshness of one declared as_of predicate for a section.

    Durable predicates are never included — they cannot be stale.
    """

    predicate: str
    qualifier: str | None = None
    state: FreshnessState
    latest_as_of_date: str | None = None
    threshold_days: int | None = Field(
        default=None,
        description="Days before evaluation_date at which latest entry becomes stale",
    )
    fact_id: str | None = Field(
        default=None,
        description="Latest timeline entry fact_id when present",
    )


class SectionCoverage(BaseModel):
    """
    Deterministic gap report for one section — set arithmetic over the ledger.

    Describes what exists and what is missing. Does not grade the case.
    Absence of supporting sources → available=False (not a coverage failure).
    """

    section: SectionName
    available: bool = Field(
        description="False when the section has no supporting document facts",
    )
    predicates_covered: list[str] = Field(default_factory=list)
    predicates_missing: list[str] = Field(default_factory=list)
    predicate_freshness: list[PredicateFreshness] = Field(
        default_factory=list,
        description=(
            "Per declared as_of predicate: absent / stale / current. "
            "Durable predicates are excluded — they are never stale."
        ),
    )
    life_stages_empty: list[LifeStage] = Field(
        default_factory=list,
        description="Declared life stages with no facts in the ledger",
    )
    source_types_present: list[SourceType] = Field(default_factory=list)
    source_types_missing: list[SourceType] = Field(
        default_factory=list,
        description="Declared source types not represented in this case packet",
    )


class GapReport(BaseModel):
    """Coverage deliverable returned from /extract while gathering is underway."""

    sections: list[SectionCoverage] = Field(default_factory=list)


class FailedCitationAttempt(BaseModel):
    """
    Secondary gap signal: drafter cited a fact_id that is not on the ledger.

    May be a real gap or an averted hallucination — attempt alone cannot distinguish.
    """

    fact_id: str
    statement: str
    predicate_hint: str | None = Field(
        default=None,
        description="Known predicate token found in the statement, if any",
    )


class ExtractResponse(BaseModel):
    """Ledger plus cost metadata. Nothing is persisted (spec §4)."""

    ledger: Ledger
    gap_report: GapReport = Field(
        default_factory=GapReport,
        description="Deterministic coverage gaps — actionable while gathering continues",
    )
    timelines: list[Timeline] = Field(
        default_factory=list,
        description=(
            "Computed as_of view (subject+predicate+qualifier, sorted by as_of_date). "
            "Not stored — regenerated from the ledger on each request."
        ),
    )
    tokens_used: int
    model: str
    latency_ms: int
    cost_usd: float
    tokens_by_source: dict[str, int] = Field(
        description="Total tokens per source id (one model call each)",
    )
    predicates_for_review: list[str] = Field(
        default_factory=list,
        description="Predicates emitted outside the vocabulary — flag for review",
    )
    subjects_for_review: list[str] = Field(
        default_factory=list,
        description="Subjects outside the canonical entity list — flag for review",
    )


class DisagreementVersion(BaseModel):
    """One side of a disagreement — never ranked or resolved."""

    fact_id: str
    source_id: str
    source_date: str
    as_of_date: str | None = None
    reporter: str | None = None
    value: str
    value_text: str
    assertion: FactAssertion


class TimelineEntry(BaseModel):
    """One point on an as_of predicate timeline."""

    fact_id: str
    as_of_date: str
    source_id: str
    source_date: str
    value: str
    value_text: str
    assertion: FactAssertion
    is_latest: bool = False


class Timeline(BaseModel):
    """
    as_of facts for one subject+predicate+qualifier, ordered by as_of_date.

    Different values at different dates are expected change — not conflicts.
    """

    subject: str
    predicate: str
    qualifier: str | None = None
    predicate_class: Literal["record", "perspectival"]
    topic: str
    entries: list[TimelineEntry] = Field(default_factory=list)


class Disagreement(BaseModel):
    """
    Disagreement among facts sharing subject + predicate + qualifier.

    record → conflicts bucket; perspectival → variance bucket.
    """

    subject: str
    predicate: str
    qualifier: str | None = None
    predicate_class: Literal["record", "perspectival"]
    topic: str = Field(description="Display label: predicate or predicate:qualifier")
    versions: list[DisagreementVersion] = Field(min_length=2)


class ConflictsRequest(BaseModel):
    """Ledger in — confirm_synthetic required. No model call."""

    confirm_synthetic: Literal[True] = Field(
        description="Must be true. Refuses real PHI/PII cases; OpenAI runtime is synthetic-only.",
    )
    ledger: Ledger


class ConflictsResponse(BaseModel):
    """
    Deterministic disagreements + as_of timelines. Nothing persisted.

    conflicts — record predicates (a source is wrong; surface for resolution)
    variance  — perspectival predicates (informants differ; present as comparison)
    timelines — as_of progressions (different dates = expected change)
    """

    conflicts: list[Disagreement] = Field(default_factory=list)
    variance: list[Disagreement] = Field(default_factory=list)
    timelines: list[Timeline] = Field(default_factory=list)
    predicates_for_review: list[str] = Field(default_factory=list)
    subjects_for_review: list[str] = Field(default_factory=list)


class UnverifiedCitation(BaseModel):
    """
    Public legal/regulatory citation generated without a case source (ed-code carve-out).

    Never enters the ledger as a sourced fact. Requires clinician confirmation.
    """

    text: str = Field(description="Citation text as it appears in prose")
    citation_type: Literal["education_code", "regulation", "statute", "other_legal"] = (
        "education_code"
    )
    unverified: Literal[True] = True
    note: str | None = None


class ReviewItem(BaseModel):
    """One work-queue item for clinician review — not a prose footnote."""

    kind: Literal[
        "conflict",
        "variance",
        "unverified_citation",
        "terminology",
        "entailment_failure",
        "temporal_framing",
        "section_empty",
        "conflict_not_mentioned",
    ]
    summary: str
    requires_decision: bool = True
    conflict_topic: str | None = None
    fact_id: str | None = None
    banned_term: str | None = None
    preferred_term: str | None = None
    citation_text: str | None = None


class ReviewQueue(BaseModel):
    """Structured review surface — work queue, not a warning banner."""

    items: list[ReviewItem] = Field(default_factory=list)


class DraftStatement(BaseModel):
    """One prose claim traced to a ledger fact."""

    statement: str
    fact_id: str


class DraftProseOutput(BaseModel):
    """Model output for /draft — prose only; facts/conflicts are settled input."""

    prose: str
    statements: list[DraftStatement] = Field(
        description="Every substantive claim with its ledger fact_id",
    )
    unverified_citations: list[UnverifiedCitation] = Field(
        default_factory=list,
        description="Ed-code / public legal citations only; never clinical claims",
    )
    coverage: list[LifeStage] = Field(default_factory=list)


class EntailmentJudgment(BaseModel):
    """Topic-agnostic support check: does this source text entail this claim?"""

    supported: bool
    rationale: str = Field(description="Brief reason; no outside knowledge")


class DraftRequest(BaseModel):
    """
    Ledger + detected conflicts → section prose.

    confirm_synthetic required. Drafter has no discretion over which facts/conflicts exist.
    """

    confirm_synthetic: Literal[True] = Field(
        description="Must be true. Refuses real PHI/PII cases; OpenAI runtime is synthetic-only.",
    )
    section: SectionName = "history"
    ledger: Ledger
    conflicts: list[Disagreement] = Field(
        default_factory=list,
        description="Record disagreements from /conflicts — must-mention in prose",
    )
    variance: list[Disagreement] = Field(
        default_factory=list,
        description="Perspectival disagreements — present as comparison, not error",
    )
    model: str | None = None
    entailment_model: str | None = Field(
        default="gpt-4o-mini",
        description="Cheap model for per-fact entailment checks",
    )
    stale_as_of_days: int = Field(
        default=365,
        ge=0,
        description=(
            "as_of facts older than this many days before evaluation_date "
            "must not be framed in the present tense. Confirm threshold with Molly."
        ),
    )


class DraftResponse(BaseModel):
    """Draft plus review work queue. Nothing persisted (spec §4)."""

    section_populated: bool
    empty_reason: str | None = None
    answer: ReportSection | None = None
    review: ReviewQueue = Field(default_factory=ReviewQueue)
    unverified_citations: list[UnverifiedCitation] = Field(default_factory=list)
    failed_citation_attempts: list[FailedCitationAttempt] = Field(
        default_factory=list,
        description="Secondary coverage signal — citations to missing fact_ids",
    )
    tokens_used: int
    tokens_by_stage: dict[str, int] = Field(
        default_factory=dict,
        description="Token totals by stage, e.g. draft / entailment",
    )
    model: str
    latency_ms: int
    cost_usd: float
    age_years_expected: int | None = None


class AskRequest(BaseModel):
    """
    Domain request for /ask.

    confirm_synthetic must be true — this OpenAI build never accepts real cases.
    """

    confirm_synthetic: Literal[True] = Field(
        description="Must be true. Refuses real PHI/PII cases; OpenAI runtime is synthetic-only.",
    )
    section: SectionName = "history"
    child: Child
    sources: list[Source] = Field(min_length=1)
    model: str | None = None
    force_bad_age: bool = False  # Plant a wrong age on attempt 0 to exercise the validator.


class AskResponse(BaseModel):
    """Typed response: section draft + cost/latency metadata."""

    answer: ReportSection
    tokens_used: int
    model: str
    latency_ms: int
    cost_usd: float
    age_years_expected: int = Field(
        description="Age recomputed from dob + evaluation_date (ground truth for the validator)",
    )


class IngestRequest(BaseModel):
    """
    Classify one raw document for user confirmation before it becomes a Source.

    Never applied silently — caller must confirm type/date/label.
    """

    confirm_synthetic: Literal[True] = Field(
        description="Must be true. Refuses real PHI/PII cases; OpenAI runtime is synthetic-only.",
    )
    content: str = Field(min_length=1, description="Raw document text")
    model: str | None = Field(
        default="gpt-4o-mini",
        description="Cheap classification model",
    )


class IngestSuggestion(BaseModel):
    """Proposed Source metadata — user must confirm before use."""

    source_type: SourceType
    source_date: str = Field(description="ISO date YYYY-MM-DD guessed from the document")
    label: str = Field(description="Short human label for this source")


class IngestResponse(BaseModel):
    """Classification suggestion + cost. Nothing persisted; nothing applied silently."""

    suggestion: IngestSuggestion
    confirm_required: Literal[True] = True
    note: str = (
        "Confirm source_type, source_date, and label before adding to the case. "
        "A wrong date is a provenance failure."
    )
    tokens_used: int
    model: str
    latency_ms: int
    cost_usd: float
