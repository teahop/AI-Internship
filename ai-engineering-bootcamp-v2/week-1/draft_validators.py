"""Draft validators: entailment, temporal framing, terminology, conflict mention, fact_id trace."""

from __future__ import annotations

import json
import re

from provider import ENTAILMENT_TEMPERATURE, ModelProvider
from schemas import (
    Disagreement,
    DraftBlock,
    DraftBlockKind,
    DraftProseOutput,
    EntailmentJudgment,
    Fact,
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


def filter_blocks_by_trigger(
    blocks: list[DraftBlock],
    ledger: Ledger,
) -> list[DraftBlock]:
    """
    Drop blocks whose trigger predicate has no facts in the ledger.

    trigger=None always stays (always-present blocks such as school experience).
    That is layout-doc §7 degrade — paragraph count falls out of the evidence.
    """

    present = {f.predicate for f in ledger.facts}
    return [b for b in blocks if b.trigger is None or b.trigger in present]


def render_prose_from_blocks(blocks: list[DraftBlock]) -> str:
    """Deterministic consumer-facing prose from typed blocks — label order preserved."""

    parts: list[str] = []
    for block in blocks:
        if block.kind == DraftBlockKind.PROSE:
            body = (block.prose or "").strip()
            if body:
                parts.append(f"**{block.label}:** {body}")
            else:
                parts.append(f"**{block.label}:**")
        elif block.kind == DraftBlockKind.TABLE:
            parts.append(_render_table_block(block))
    return "\n\n".join(parts)


def _render_table_block(block: DraftBlock) -> str:
    table = block.table
    assert table is not None
    lines = [f"**{block.label}:**", "", f"*{table.title}*", ""]
    lines.append("| " + " | ".join(table.columns) + " |")
    lines.append("| " + " | ".join("---" for _ in table.columns) + " |")
    for row in table.rows:
        lines.append("| " + " | ".join(cell.text for cell in row) + " |")
    return "\n".join(lines)


def validate_draft_blocks(output: DraftProseOutput, ledger: Ledger) -> list[str]:
    """
    Structural checks on typed blocks before trigger filtering / prose render.

    Empty `blocks` is valid (legacy prose-authored path). Schema validators already
    enforce cell blank rules and row width; this catches cross-block invariants.
    """

    del ledger  # reserved for future ledger-aware block checks
    errors: list[str] = []
    for block in output.blocks:
        if block.kind == DraftBlockKind.TABLE and block.table is not None:
            width = len(block.table.columns)
            for i, row in enumerate(block.table.rows):
                if len(row) != width:
                    errors.append(
                        f"block {block.label!r} row {i} has {len(row)} cells; "
                        f"expected {width}"
                    )
                for j, cell in enumerate(row):
                    if cell.text.strip().upper() in {"N/A", "NA", "N.A."}:
                        errors.append(
                            f"block {block.label!r} cell [{i},{j}] uses filler "
                            f"{cell.text!r} — blank cells must be empty text"
                        )
                    if cell.text == "" and cell.fact_ids:
                        errors.append(
                            f"block {block.label!r} cell [{i},{j}] is blank but "
                            f"carries fact_ids"
                        )
                    if cell.text != "" and not cell.fact_ids:
                        errors.append(
                            f"block {block.label!r} cell [{i},{j}] has text but "
                            f"no fact_ids"
                        )
        if block.kind == DraftBlockKind.PROSE and block.prose is None:
            errors.append(f"prose block {block.label!r} missing prose body")
    return errors


def facts_needing_valence_judgment(facts: list[Fact]) -> list[Fact]:
    """
    Facts whose valence was not grounded in a document heading.

    `source_section is None` means the cell needed Molly's judgment rather than
    the document's (layout doc §5). Identifiable seam for a future review-queue
    rule — do not invent ReviewItems here until that rule is specced.
    """

    return [f for f in facts if f.source_section is None]


def days_between(earlier: str, later: str) -> int:
    return (parse_iso_date(later) - parse_iso_date(earlier)).days


def validate_fact_id_trace(
    output: DraftProseOutput,
    ledger: Ledger,
) -> tuple[list[str], list[FailedCitationAttempt]]:
    """
    Every id in statement.fact_ids must exist on the ledger.

    Unknown ids become FailedCitationAttempt (secondary gap signal) and errors.
    """

    by_id = {f.id: f for f in ledger.facts}
    errors: list[str] = []
    failed: list[FailedCitationAttempt] = []
    if not output.statements and output.prose.strip():
        errors.append("prose is non-empty but statements list is empty")
    for stmt in output.statements:
        for fact_id in stmt.fact_ids:
            if fact_id not in by_id:
                errors.append(
                    f"unknown fact_id={fact_id!r} for statement={stmt.statement[:80]!r}"
                )
                failed.append(
                    FailedCitationAttempt(
                        fact_id=fact_id,
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
        window = stmt.statement
        if not (
            _PRESENT_TENSE.search(window) and not _HISTORICAL_FRAME.search(window)
        ):
            continue
        for fact_id in stmt.fact_ids:
            fact = by_id.get(fact_id)
            if fact is None or fact.temporality != "as_of":
                continue
            if fact.id in latest_ids:
                continue
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
        for fact_id in stmt.fact_ids:
            key = (fact_id, stmt.statement.strip())
            if key in seen:
                continue
            seen.add(key)
            fact = by_id.get(fact_id)
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
