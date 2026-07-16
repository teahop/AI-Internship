"""Typed I/O for Molly's Background & History draft — provenance in, attribution out."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class Child(BaseModel):
    """Identity for age math only — use initials / synthetic IDs, never real names here."""

    initials: str = Field(description="Synthetic initials only, e.g. J.D.")
    dob: str = Field(description="ISO date YYYY-MM-DD")
    evaluation_date: str = Field(description="ISO date YYYY-MM-DD of this evaluation")


class Source(BaseModel):
    """One dated input artifact — the provenance spine."""

    id: str = Field(description="Stable id referenced by SourcedFact.source_id")
    type: SourceType
    date: str = Field(description="ISO date YYYY-MM-DD when this source was created")
    label: str = Field(description="Short human label, e.g. Parent developmental history")
    content: str = Field(description="Full text / notes from this source")


class SourcedFact(BaseModel):
    """One claim in the draft, tied to where it came from and when."""

    statement: str
    source_id: str
    source_date: str | None = None
    life_stage: LifeStage


class Conflict(BaseModel):
    """Disagreement between sources — surfaced, not silently resolved."""

    topic: str
    versions: list[SourcedFact] = Field(min_length=2)


class ReportSection(BaseModel):
    """Structured history draft Molly can review, cite-check, and sign."""

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
