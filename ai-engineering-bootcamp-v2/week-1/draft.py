"""Section drafting from a settled ledger + must-mention conflicts."""

from __future__ import annotations

import json
from pathlib import Path

from conflicts import compute_timelines
from draft_validators import (
    build_citation_review_items,
    build_conflict_review_items,
    build_variance_review_items,
    validate_conflicts_mentioned,
    validate_entailment,
    validate_fact_id_trace,
    validate_temporal_framing,
    validate_terminology_flags,
)
from provider import DRAFT_TEMPERATURE, ModelProvider, compute_cost_usd
from schemas import (
    Conflict,
    Disagreement,
    DraftProseOutput,
    DraftRequest,
    DraftResponse,
    Fact,
    Ledger,
    ReportSection,
    ReviewItem,
    ReviewQueue,
    SectionName,
    SourcedFact,
    Timeline,
)
from validators import compute_age_years, validate_age_consistency

_DIR = Path(__file__).resolve().parent
DRAFT_SYSTEM_PROMPT = (_DIR / "draft_prompt.md").read_text(encoding="utf-8")


def section_has_supporting_facts(ledger: Ledger, section: SectionName) -> bool:
    """
    Conditional sections: no supporting facts → do not draft a thin section.

    For history, any non-empty ledger is sufficient. Future sections may filter
    by predicate/life_stage.
    """

    if section == "history":
        return len(ledger.facts) > 0
    return len(ledger.facts) > 0


def _fact_dict(f: Fact) -> dict:
    return {
        "fact_id": f.id,
        "subject": f.subject,
        "predicate": f.predicate,
        "qualifier": f.qualifier,
        "value": f.value,
        "value_text": f.value_text,
        "assertion": f.assertion,
        "source_id": f.source_id,
        "source_date": f.source_date,
        "as_of_date": f.as_of_date,
        "reporter": f.reporter,
        "life_stage": f.life_stage,
        "grade": f.grade,
        "temporality": f.temporality,
        "confidence": f.confidence,
        "derivation": f.derivation,
        "inherits_dispute": f.inherits_dispute,
    }


def _timeline_payload(timelines: list[Timeline]) -> list[dict]:
    return [
        {
            "subject": t.subject,
            "predicate": t.predicate,
            "qualifier": t.qualifier,
            "topic": t.topic,
            "entries": [
                {
                    "fact_id": e.fact_id,
                    "value": e.value,
                    "value_text": e.value_text,
                    "as_of_date": e.as_of_date,
                    "source_id": e.source_id,
                    "source_date": e.source_date,
                    "assertion": e.assertion,
                    "is_latest": e.is_latest,
                }
                for e in t.entries
            ],
        }
        for t in timelines
    ]


def _disagreement_payload(items: list[Disagreement]) -> list[dict]:
    return [
        {
            "topic": d.topic,
            "subject": d.subject,
            "predicate": d.predicate,
            "qualifier": d.qualifier,
            "predicate_class": d.predicate_class,
            "versions": [
                {
                    "fact_id": v.fact_id,
                    "source_id": v.source_id,
                    "source_date": v.source_date,
                    "reporter": v.reporter,
                    "value": v.value,
                    "value_text": v.value_text,
                    "assertion": v.assertion,
                }
                for v in d.versions
            ],
        }
        for d in items
    ]


def _draft_user_payload(body: DraftRequest, *, timeline_shaped: bool = True) -> str:
    """
    Build the drafter user packet.

    timeline_shaped=True (default): durable facts flat; as_of facts as ordered timelines.
    timeline_shaped=False: flat facts list (Stage 4.6 shape) — for A/B chronology check.
    """

    source_labels = {s.id: {"label": s.label, "date": s.date} for s in body.ledger.sources}
    packet: dict = {
        "section": body.section,
        "child": body.ledger.child.model_dump(),
        "stale_as_of_days": body.stale_as_of_days,
        "source_labels": source_labels,
        "must_mention_conflicts": _disagreement_payload(body.conflicts),
        "variance": _disagreement_payload(body.variance),
    }
    if timeline_shaped:
        durable = [_fact_dict(f) for f in body.ledger.facts if f.temporality == "durable"]
        timelines = compute_timelines(body.ledger.facts)
        packet["durable_facts"] = durable
        packet["timelines"] = _timeline_payload(timelines)
        packet["note"] = (
            "durable_facts are atemporal. timelines are as_of progressions "
            "ordered by as_of_date — use them for chronological narrative. "
            "Cite fact_id from either list."
        )
    else:
        packet["facts"] = [_fact_dict(f) for f in body.ledger.facts]
    return json.dumps(packet, indent=2)


def _disagreement_to_report_conflict(d: Disagreement, ledger: Ledger) -> Conflict:
    by_id = {f.id: f for f in ledger.facts}
    versions: list[SourcedFact] = []
    for v in d.versions:
        fact = by_id.get(v.fact_id)
        versions.append(
            SourcedFact(
                statement=v.value_text or v.value,
                fact_id=v.fact_id,
                source_id=v.source_id,
                source_date=v.source_date,
                life_stage=fact.life_stage if fact else "current",
                reporter=v.reporter,
            )
        )
    return Conflict(topic=d.topic, versions=versions)


def _output_to_report_section(
    output: DraftProseOutput,
    *,
    section: SectionName,
    ledger: Ledger,
    conflicts: list[Disagreement],
) -> ReportSection:
    by_id = {f.id: f for f in ledger.facts}
    facts: list[SourcedFact] = []
    for stmt in output.statements:
        fact = by_id[stmt.fact_id]
        facts.append(
            SourcedFact(
                statement=stmt.statement,
                fact_id=fact.id,
                source_id=fact.source_id,
                source_date=fact.source_date,
                life_stage=fact.life_stage,
                reporter=fact.reporter,
            )
        )
    return ReportSection(
        section=section,
        prose=output.prose,
        facts=facts,
        conflicts=[_disagreement_to_report_conflict(c, ledger) for c in conflicts],
        coverage=output.coverage or sorted({f.life_stage for f in facts}),
    )


def draft_section(
    provider: ModelProvider,
    body: DraftRequest,
    *,
    timeline_shaped: bool = True,
) -> DraftResponse:
    """
    Draft prose from settled facts/conflicts; build review work queue.

    timeline_shaped: when True, as_of facts are supplied as ordered timelines
    (default). Set False for flat-facts A/B chronology comparison.

    Returns without model spend when the section cannot be populated.
    """

    model = body.model or "gpt-4o-mini"
    entailment_model = body.entailment_model or "gpt-4o-mini"
    tokens_by_stage: dict[str, int] = {"draft": 0, "entailment": 0}

    if not section_has_supporting_facts(body.ledger, body.section):
        review = ReviewQueue(
            items=[
                ReviewItem(
                    kind="section_empty",
                    summary=(
                        f"Section {body.section!r} cannot be populated — "
                        "no supporting facts in the ledger. This is a legitimate "
                        "outcome when sources for this section were not collected."
                    ),
                    requires_decision=False,
                )
            ]
        )
        return DraftResponse(
            section_populated=False,
            empty_reason=f"No ledger facts for section {body.section!r}",
            answer=None,
            review=review,
            unverified_citations=[],
            failed_citation_attempts=[],
            tokens_used=0,
            tokens_by_stage=tokens_by_stage,
            model=model,
            latency_ms=0,
            cost_usd=0.0,
            age_years_expected=compute_age_years(
                body.ledger.child.dob, body.ledger.child.evaluation_date
            ),
        )

    result = provider.complete_structured(
        model=model,
        system=DRAFT_SYSTEM_PROMPT,
        user=_draft_user_payload(body, timeline_shaped=timeline_shaped),
        schema=DraftProseOutput,
        temperature=DRAFT_TEMPERATURE,
    )
    output = result.data
    assert isinstance(output, DraftProseOutput)
    tokens_by_stage["draft"] = result.total_tokens

    # Fact_id trace: unknown ids → secondary gap signal (not a hard fail by themselves).
    # Empty statements with non-empty prose remains a hard failure.
    trace_errors, failed_citations = validate_fact_id_trace(output, body.ledger)
    hard = [e for e in trace_errors if "statements list is empty" in e]
    if hard:
        raise ValueError("Draft fact_id trace failed: " + "; ".join(hard[:5]))

    if failed_citations:
        by_id = {f.id: f for f in body.ledger.facts}
        output = output.model_copy(
            update={
                "statements": [s for s in output.statements if s.fact_id in by_id],
            }
        )
        if not output.statements and output.prose.strip():
            raise ValueError(
                "Draft fact_id trace failed: all statements cited unknown fact_ids; "
                + "; ".join(trace_errors[:3])
            )

    section = _output_to_report_section(
        output,
        section=body.section,
        ledger=body.ledger,
        conflicts=body.conflicts,
    )

    expected_age = validate_age_consistency(
        section,
        dob=body.ledger.child.dob,
        evaluation_date=body.ledger.child.evaluation_date,
        ledger=body.ledger,
    )

    review_items: list[ReviewItem] = []
    review_items.extend(build_conflict_review_items(body.conflicts))
    review_items.extend(build_variance_review_items(body.variance))
    review_items.extend(validate_conflicts_mentioned(output.prose, body.conflicts))
    review_items.extend(validate_terminology_flags(output.prose))
    review_items.extend(
        validate_temporal_framing(
            output,
            body.ledger,
            evaluation_date=body.ledger.child.evaluation_date,
            stale_as_of_days=body.stale_as_of_days,
        )
    )
    review_items.extend(build_citation_review_items(output.unverified_citations))

    entail_items, e_total, e_prompt, e_completion = validate_entailment(
        provider,
        model=entailment_model,
        output=output,
        ledger=body.ledger,
    )
    review_items.extend(entail_items)
    tokens_by_stage["entailment"] = e_total

    tokens_used = tokens_by_stage["draft"] + tokens_by_stage["entailment"]
    cost_usd = compute_cost_usd(
        model, result.prompt_tokens, result.completion_tokens
    ) + compute_cost_usd(entailment_model, e_prompt, e_completion)

    return DraftResponse(
        section_populated=True,
        empty_reason=None,
        answer=section,
        review=ReviewQueue(items=review_items),
        unverified_citations=list(output.unverified_citations),
        failed_citation_attempts=failed_citations,
        tokens_used=tokens_used,
        tokens_by_stage=tokens_by_stage,
        model=model,
        latency_ms=0,  # filled by caller
        cost_usd=round(cost_usd, 6),
        age_years_expected=expected_age,
    )
