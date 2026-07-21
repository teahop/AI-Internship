"""Draft validators: entailment, temporal framing, terminology, conflict mention, fact_id trace."""

from __future__ import annotations

import json
import re

from provider import ENTAILMENT_TEMPERATURE, ModelProvider
from schemas import (
    Disagreement,
    DraftProseOutput,
    EntailmentJudgment,
    FailedCitationAttempt,
    Ledger,
    ReviewItem,
    Source,
)
from terminology import find_terminology_violations
from validators import parse_iso_date
from derived import is_derived_fact
from predicates import PREDICATES

# Present-tense framing for as_of claims (generic — no clinical topics).
_PRESENT_TENSE = re.compile(
    r"\b("
    r"is|are|currently|presently|now|remains?|continues?\s+to|"
    r"attends?|enrolled|in\s+grade|in\s+the\s+\d"
    r")\b",
    re.IGNORECASE,
)

_HISTORICAL_FRAME = re.compile(
    r"\b("
    r"as\s+of|at\s+the\s+time|in\s+\d{4}|dated|formerly|"
    r"was|were|stated|indicated|listed|recorded|noted|per\s+the\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)


def days_between(earlier: str, later: str) -> int:
    return (parse_iso_date(later) - parse_iso_date(earlier)).days


def validate_fact_id_trace(
    output: DraftProseOutput,
    ledger: Ledger,
) -> tuple[list[str], list[FailedCitationAttempt]]:
    """
    Every statement.fact_id must exist on the ledger.

    Unknown ids become FailedCitationAttempt (secondary gap signal) and errors.
    """

    by_id = {f.id: f for f in ledger.facts}
    errors: list[str] = []
    failed: list[FailedCitationAttempt] = []
    if not output.statements and output.prose.strip():
        errors.append("prose is non-empty but statements list is empty")
    for stmt in output.statements:
        if stmt.fact_id not in by_id:
            errors.append(
                f"unknown fact_id={stmt.fact_id!r} for statement={stmt.statement[:80]!r}"
            )
            failed.append(
                FailedCitationAttempt(
                    fact_id=stmt.fact_id,
                    statement=stmt.statement,
                    predicate_hint=_predicate_hint(stmt.statement),
                )
            )
    return errors, failed


def _predicate_hint(statement: str) -> str | None:
    """Structural: known predicate token appearing in the statement, if any."""

    lower = statement.lower()
    hits = [name for name in PREDICATES if name.replace("_", " ") in lower or name in lower]
    return hits[0] if hits else None


def validate_conflicts_mentioned(
    prose: str,
    conflicts: list[Disagreement],
) -> list[ReviewItem]:
    """
    Each must-mention conflict must be detectable in prose (both sides' substance).

    Generic: uses value_text / value tokens from the disagreement versions — no topic list.
    """

    items: list[ReviewItem] = []
    prose_l = prose.lower()
    for conflict in conflicts:
        missing_sides = 0
        for version in conflict.versions:
            needles = [
                t
                for t in (
                    version.value.lower().strip(),
                    *(w for w in re.findall(r"[a-z0-9]{4,}", (version.value_text or "").lower())),
                )
                if t and len(t) >= 3
            ]
            # Require at least one distinctive token from this version.
            if not needles:
                continue
            if not any(n in prose_l for n in needles[:6]):
                missing_sides += 1
        if missing_sides > 0:
            items.append(
                ReviewItem(
                    kind="conflict_not_mentioned",
                    summary=(
                        f"Must-mention conflict {conflict.topic!r} not clearly "
                        f"surfaced with both sides in prose"
                    ),
                    conflict_topic=conflict.topic,
                    requires_decision=True,
                )
            )
    return items


def validate_terminology_flags(prose: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for banned, preferred in find_terminology_violations(prose):
        items.append(
            ReviewItem(
                kind="terminology",
                summary=f"Replace {banned!r} with preferred {preferred!r}",
                banned_term=banned,
                preferred_term=preferred,
                requires_decision=True,
            )
        )
    return items


def validate_temporal_framing(
    output: DraftProseOutput,
    ledger: Ledger,
    *,
    evaluation_date: str,
    stale_as_of_days: int = 365,
) -> list[ReviewItem]:
    """
    Present-tense prose must cite the latest entry on each as_of timeline.

    Citing a superseded (earlier as_of_date) entry in the present tense is a
    validation failure. Historical framing of earlier entries is allowed.

    stale_as_of_days is retained for callers but no longer gates the check —
    timeline position is the sole criterion.
    """

    from conflicts import latest_as_of_fact_ids

    _ = evaluation_date, stale_as_of_days  # API compat; timeline position is authoritative
    by_id = {f.id: f for f in ledger.facts}
    latest_ids = latest_as_of_fact_ids(ledger.facts)
    items: list[ReviewItem] = []
    for stmt in output.statements:
        fact = by_id.get(stmt.fact_id)
        if fact is None or fact.temporality != "as_of":
            continue
        if fact.id in latest_ids:
            continue
        window = stmt.statement
        if _PRESENT_TENSE.search(window) and not _HISTORICAL_FRAME.search(window):
            items.append(
                ReviewItem(
                    kind="temporal_framing",
                    summary=(
                        f"as_of fact {fact.id} (as_of_date={fact.as_of_date}) is "
                        f"superseded on its timeline but framed in present tense: "
                        f"{stmt.statement[:100]!r}"
                    ),
                    fact_id=fact.id,
                    requires_decision=True,
                )
            )
    return items


def check_entailment_one(
    provider: ModelProvider,
    *,
    model: str,
    source: Source,
    statement: str,
) -> tuple[bool, str, int, int, int]:
    """
    One cheap model call: does this source text support this statement?

    Topic-agnostic — no clinical vocabulary in the instruction.
    Returns (supported, rationale, total_tokens, prompt_tokens, completion_tokens).
    """

    system = (
        "You judge whether a source document supports a claim. "
        "Answer only via the schema. "
        "supported=true only if the source text entails the claim "
        "(including explicit denials when the claim is a negative finding). "
        "Silence, deferral, or omission in the source does not support a positive claim. "
        "Do not use outside knowledge."
    )
    user = json.dumps(
        {
            "source": {
                "id": source.id,
                "date": source.date,
                "label": source.label,
                "content": source.content,
            },
            "claim": statement,
        },
        indent=2,
    )
    result = provider.complete_structured(
        model=model,
        system=system,
        user=user,
        schema=EntailmentJudgment,
        temperature=ENTAILMENT_TEMPERATURE,
    )
    judgment = result.data
    assert isinstance(judgment, EntailmentJudgment)
    return (
        judgment.supported,
        judgment.rationale,
        result.total_tokens,
        result.prompt_tokens,
        result.completion_tokens,
    )


def validate_entailment(
    provider: ModelProvider,
    *,
    model: str,
    output: DraftProseOutput,
    ledger: Ledger,
) -> tuple[list[ReviewItem], int, int, int]:
    """
    Generic attribution: for each draft statement, ask whether the cited source supports it.

    Derived facts (source_id=computed / derivation set) are skipped — recomputation
    covers them. Returns (review_items, total, prompt, completion tokens).
    """

    by_id = {f.id: f for f in ledger.facts}
    by_source = {s.id: s for s in ledger.sources}
    items: list[ReviewItem] = []
    total = prompt_tok = completion_tok = 0

    # Deduplicate by (fact_id, statement) to avoid repeat calls.
    seen: set[tuple[str, str]] = set()
    for stmt in output.statements:
        key = (stmt.fact_id, stmt.statement.strip())
        if key in seen:
            continue
        seen.add(key)
        fact = by_id.get(stmt.fact_id)
        if fact is None:
            continue
        if is_derived_fact(fact):
            continue
        source = by_source.get(fact.source_id)
        if source is None:
            items.append(
                ReviewItem(
                    kind="entailment_failure",
                    summary=f"No source for fact {fact.id}",
                    fact_id=fact.id,
                )
            )
            continue
        supported, rationale, t, p, c = check_entailment_one(
            provider, model=model, source=source, statement=stmt.statement
        )
        total += t
        prompt_tok += p
        completion_tok += c
        if not supported:
            items.append(
                ReviewItem(
                    kind="entailment_failure",
                    summary=(
                        f"Source {source.id} does not support statement for {fact.id}: "
                        f"{rationale[:160]}"
                    ),
                    fact_id=fact.id,
                    requires_decision=True,
                )
            )
    return items, total, prompt_tok, completion_tok


def build_conflict_review_items(conflicts: list[Disagreement]) -> list[ReviewItem]:
    """Conflicts always enter the review work queue as decision items."""

    return [
        ReviewItem(
            kind="conflict",
            summary=(
                f"Resolve or document judgment on {c.topic}: "
                + " vs ".join(f"{v.value!r} ({v.source_id})" for v in c.versions)
            ),
            conflict_topic=c.topic,
            requires_decision=True,
        )
        for c in conflicts
    ]


def build_variance_review_items(variance: list[Disagreement]) -> list[ReviewItem]:
    return [
        ReviewItem(
            kind="variance",
            summary=(
                f"Informant variance on {v.topic}: "
                + " vs ".join(f"{x.value!r} ({x.source_id})" for x in v.versions)
            ),
            conflict_topic=v.topic,
            requires_decision=False,
        )
        for v in variance
    ]


def build_citation_review_items(citations) -> list[ReviewItem]:
    return [
        ReviewItem(
            kind="unverified_citation",
            summary=f"Confirm unverified {c.citation_type} citation: {c.text}",
            citation_text=c.text,
            requires_decision=True,
        )
        for c in citations
    ]
