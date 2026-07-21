"""Per-source ledger extraction — one model call per source, no cross-document view."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from coverage import build_gap_report
from conflicts import compute_timelines
from derived import inject_derived_and_request_facts
from normalize import normalize_qualifier, normalize_value
from predicates import (
    CANONICAL_SUBJECTS,
    PREDICATE_VOCABULARY,
    UNREGISTERED_PREDICATE,
    ExtractPredicateName,
    is_provenance_predicate,
    needs_predicate_review,
    needs_subject_review,
    temporality_for_predicate,
)
from provider import EXTRACT_TEMPERATURE, ModelProvider
from schemas import (
    Child,
    ExtractedFactDraft,
    Fact,
    FactAssertion,
    GapReport,
    Ledger,
    Source,
    SourceExtraction,
    Temporality,
    Timeline,
)

_DIR = Path(__file__).resolve().parent
_PROMPT_TEMPLATE = (_DIR / "extract_prompt.md").read_text(encoding="utf-8")

LEDGER_VERSION = "1"


def _predicate_list_for_prompt() -> str:
    lines: list[str] = []
    for spec in PREDICATE_VOCABULARY:
        qual = "; takes qualifier" if spec.takes_qualifier else ""
        lines.append(
            f"- `{spec.name}` ({spec.predicate_class}, {spec.default_temporality}{qual}): "
            f"{spec.description}"
        )
        if spec.notes:
            lines.append(f"  note: {spec.notes}")
    return "\n".join(lines)


def build_extract_system_prompt() -> str:
    return _PROMPT_TEMPLATE.replace("{{PREDICATE_LIST}}", _predicate_list_for_prompt())


EXTRACT_SYSTEM_PROMPT = build_extract_system_prompt()


def _extraction_user_payload(source: Source) -> str:
    """
    Serialize exactly one source plus subject vocabulary.

    Vocabulary (canonical subject names) is not case data — no dob, initials,
    or evaluation_date. Keeps entity keys available without leaking identity.
    """

    packet = {
        "canonical_subjects": sorted(CANONICAL_SUBJECTS),
        "source": {
            "id": source.id,
            "type": source.type,
            "date": source.date,
            "label": source.label,
            "content": source.content,
        },
    }
    return json.dumps(packet, indent=2)


def _resolve_predicate_name(draft: ExtractedFactDraft) -> str:
    pred = (
        draft.predicate.value
        if isinstance(draft.predicate, ExtractPredicateName)
        else str(draft.predicate)
    )
    if pred == UNREGISTERED_PREDICATE:
        proposed = (draft.proposed_predicate or "").strip()
        return proposed or "unspecified_proposed_predicate"
    return pred


def _finalize_temporality(predicate: str) -> Temporality:
    return temporality_for_predicate(predicate)


def _finalize_assertion(draft: ExtractedFactDraft) -> FactAssertion:
    return draft.assertion if draft.assertion in ("asserted", "denied") else "asserted"


def _finalize_as_of_date(draft: ExtractedFactDraft, source: Source) -> str:
    """
    Use model as_of_date when the source text contains an explicit anchor; otherwise
    source.date. Blocks aggressive inference from vague relative time ('last year').
    """

    proposed = (draft.as_of_date or "").strip() or source.date
    if proposed == source.date:
        return source.date

    # Anchor evidence may live in the claim wording or the source body
    # ("Per the 2024 IEP…" often sits outside a short value_text).
    blob = f"{draft.value_text or ''} {draft.value or ''} {source.content or ''}"
    if proposed in blob:
        return proposed

    # Explicit four-digit year in anchor must appear in the source/claim text.
    year = proposed[:4]
    if year.isdigit() and re.search(rf"\b{year}\b", blob):
        return proposed

    return source.date


def _finalize_subject(draft: ExtractedFactDraft, source: Source, predicate: str) -> str:
    """
    Provenance predicates → extracting source id (model cannot choose).
    Everything else → canonical enum subject (default child).
    """

    if is_provenance_predicate(predicate):
        return source.id
    raw = draft.subject
    if hasattr(raw, "value"):
        return str(raw.value)
    subject = (str(raw) if raw is not None else "").strip()
    return subject if subject in CANONICAL_SUBJECTS else "child"


def draft_to_fact(
    draft: ExtractedFactDraft,
    *,
    fact_id: str,
    source: Source,
    child: Child,
) -> Fact:
    del child  # Subject no longer needs initials for canonicalization.
    predicate = _resolve_predicate_name(draft)
    value = normalize_value(predicate, draft.value, draft.value_text)
    if not value or value.strip().lower() == "null":
        raise ValueError(f"Refusing fact with empty/null value for predicate={predicate!r}")
    grade = draft.grade
    if grade:
        grade = normalize_value("grade", grade, grade)
    reporter = draft.reporter.strip() if draft.reporter and draft.reporter.strip() else None
    qualifier = normalize_qualifier(draft.qualifier)
    as_of = _finalize_as_of_date(draft, source)
    subject = _finalize_subject(draft, source, predicate)

    # Structural lock: non-provenance facts must never key on a source id.
    if not is_provenance_predicate(predicate) and subject not in CANONICAL_SUBJECTS:
        raise ValueError(
            f"Non-provenance fact subject must be canonical, got {subject!r} "
            f"for predicate={predicate!r}"
        )

    return Fact(
        id=fact_id,
        subject=subject,
        predicate=predicate,
        value=value,
        value_text=draft.value_text.strip(),
        qualifier=qualifier,
        assertion=_finalize_assertion(draft),
        source_id=source.id,
        source_date=source.date,
        as_of_date=as_of,
        reporter=reporter,
        life_stage=draft.life_stage,
        grade=grade,
        temporality=_finalize_temporality(predicate),
        confidence=draft.confidence,
        derivation=None,
        inherits_dispute=False,
    )


def extract_source_facts(
    provider: ModelProvider,
    *,
    child: Child,
    source: Source,
    model: str,
) -> tuple[list[ExtractedFactDraft], int, int, int]:
    del child  # Case metadata must not enter the extraction prompt.
    result = provider.complete_structured(
        model=model,
        system=EXTRACT_SYSTEM_PROMPT,
        user=_extraction_user_payload(source),
        schema=SourceExtraction,
        temperature=EXTRACT_TEMPERATURE,
    )
    extraction = result.data
    assert isinstance(extraction, SourceExtraction)
    return (
        list(extraction.facts),
        result.total_tokens,
        result.prompt_tokens,
        result.completion_tokens,
    )


def build_ledger(
    provider: ModelProvider,
    *,
    child: Child,
    sources: list[Source],
    model: str,
) -> tuple[Ledger, dict[str, int], int, int, list[str], list[str], GapReport, list[Timeline]]:
    """
    Extract facts from each source independently and assemble a Ledger.

    Injects request-time dob + derived age_years after extraction.
    Timelines are a computed view — not stored on the ledger.
    Returns (ledger, tokens_by_source, prompt_tokens, completion_tokens,
             predicates_for_review, subjects_for_review, gap_report, timelines).
    """

    facts: list[Fact] = []
    tokens_by_source: dict[str, int] = {}
    prompt_tokens = completion_tokens = 0
    review: list[str] = []
    subject_review: list[str] = []
    next_id = 1
    known_source_ids = {s.id for s in sources}

    for source in sources:
        drafts, total, p_tok, c_tok = extract_source_facts(
            provider, child=child, source=source, model=model
        )
        tokens_by_source[source.id] = total
        prompt_tokens += p_tok
        completion_tokens += c_tok

        for draft in drafts:
            fact = draft_to_fact(draft, fact_id=f"f_{next_id:03d}", source=source, child=child)
            next_id += 1
            if needs_predicate_review(fact.predicate) and fact.predicate not in review:
                review.append(fact.predicate)
            if (
                needs_subject_review(fact.subject, known_source_ids=known_source_ids)
                and fact.subject not in subject_review
            ):
                subject_review.append(fact.subject)
            facts.append(fact)

    facts, _next_id = inject_derived_and_request_facts(facts, child, next_id=next_id)

    ledger = Ledger(
        child=child,
        ledger_version=LEDGER_VERSION,
        built_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sources=list(sources),
        facts=facts,
    )
    gap_report = build_gap_report(ledger)
    timelines = compute_timelines(ledger.facts)
    return (
        ledger,
        tokens_by_source,
        prompt_tokens,
        completion_tokens,
        review,
        subject_review,
        gap_report,
        timelines,
    )
