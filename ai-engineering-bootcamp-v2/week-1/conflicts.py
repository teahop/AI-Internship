"""Deterministic disagreement detection — no model call, no domain keywords.

Grouping key: subject + predicate + qualifier.

Temporality-aware comparison:
  durable  — compare all values directly; disagreement is a conflict/variance
  as_of    — assemble a timeline by as_of_date; different values at different
             dates are expected change; same as_of_date with different values
             is a conflict (record) or variance (perspectival)

Never resolve, rank, or pick a winner.
"""

from __future__ import annotations

from collections import defaultdict

from predicates import (
    fact_grouping_key,
    needs_predicate_review,
    needs_subject_review,
    predicate_class_of,
)
from schemas import (
    Disagreement,
    DisagreementVersion,
    Fact,
    Ledger,
    Timeline,
    TimelineEntry,
)


def _version(fact: Fact) -> DisagreementVersion:
    return DisagreementVersion(
        fact_id=fact.id,
        source_id=fact.source_id,
        source_date=fact.source_date,
        as_of_date=fact.as_of_date or fact.source_date,
        reporter=fact.reporter,
        value=fact.value,
        value_text=fact.value_text,
        assertion=fact.assertion,
    )


def _signature(fact: Fact) -> tuple[str, str]:
    """Comparison key within a group — asserted X vs denied X disagree."""

    return (fact.assertion, fact.value)


def _topic(predicate: str, qualifier: str | None) -> str:
    return predicate if qualifier is None else f"{predicate}:{qualifier}"


def _as_of_date(fact: Fact) -> str:
    return fact.as_of_date or fact.source_date


def build_timeline(
    subject: str,
    predicate: str,
    qualifier: str | None,
    facts: list[Fact],
) -> Timeline | None:
    """Ordered as_of timeline for one grouping key. None if fewer than one as_of fact."""

    as_of_facts = [f for f in facts if f.temporality == "as_of"]
    if not as_of_facts:
        return None

    ordered = sorted(as_of_facts, key=lambda f: (_as_of_date(f), f.id))
    latest_date = _as_of_date(ordered[-1])
    pclass = predicate_class_of(predicate)
    bucket_class = pclass if pclass is not None else "record"
    entries = [
        TimelineEntry(
            fact_id=f.id,
            as_of_date=_as_of_date(f),
            source_id=f.source_id,
            source_date=f.source_date,
            value=f.value,
            value_text=f.value_text,
            assertion=f.assertion,
            is_latest=_as_of_date(f) == latest_date,
        )
        for f in ordered
    ]
    return Timeline(
        subject=subject,
        predicate=predicate,
        qualifier=qualifier,
        predicate_class=bucket_class,  # type: ignore[arg-type]
        topic=_topic(predicate, qualifier),
        entries=entries,
    )


def latest_as_of_fact_ids(facts: list[Fact]) -> set[str]:
    """Fact ids that are the latest entry on their as_of timeline."""

    buckets: dict[tuple[str, str, str | None], list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.temporality != "as_of":
            continue
        key = fact_grouping_key(fact.subject, fact.predicate, fact.qualifier)
        buckets[key].append(fact)

    latest: set[str] = set()
    for group in buckets.values():
        ordered = sorted(group, key=lambda f: (_as_of_date(f), f.id))
        latest_date = _as_of_date(ordered[-1])
        for f in ordered:
            if _as_of_date(f) == latest_date:
                latest.add(f.id)
    return latest


def compute_timelines(facts: list[Fact]) -> list[Timeline]:
    """
    Computed as_of view — not stored. Filter to as_of, group by
    subject+predicate+qualifier, sort each by as_of_date.
    """

    buckets: dict[tuple[str, str, str | None], list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.temporality != "as_of":
            continue
        key = fact_grouping_key(fact.subject, fact.predicate, fact.qualifier)
        buckets[key].append(fact)

    timelines: list[Timeline] = []
    for (subject, predicate, qualifier), group in sorted(
        buckets.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")
    ):
        timeline = build_timeline(subject, predicate, qualifier, group)
        if timeline is not None:
            timelines.append(timeline)
    return timelines


def compute_timelines_from_ledger(ledger: Ledger) -> list[Timeline]:
    return compute_timelines(ledger.facts)


def detect_disagreements(
    facts: list[Fact],
    *,
    known_source_ids: set[str] | None = None,
) -> tuple[list[Disagreement], list[Disagreement], list[Timeline], list[str], list[str]]:
    """
    Returns (conflicts, variance, timelines, predicates_for_review, subjects_for_review).
    """

    buckets: dict[tuple[str, str, str | None], list[Fact]] = defaultdict(list)
    review_preds: list[str] = []
    review_subjects: list[str] = []
    source_ids = known_source_ids or set()

    for fact in facts:
        key = fact_grouping_key(fact.subject, fact.predicate, fact.qualifier)
        buckets[key].append(fact)
        if needs_predicate_review(fact.predicate) and fact.predicate not in review_preds:
            review_preds.append(fact.predicate)
        if (
            needs_subject_review(fact.subject, known_source_ids=source_ids)
            and fact.subject not in review_subjects
        ):
            review_subjects.append(fact.subject)

    conflicts: list[Disagreement] = []
    variance: list[Disagreement] = []
    timelines: list[Timeline] = []

    for (subject, predicate, qualifier), group in buckets.items():
        timeline = build_timeline(subject, predicate, qualifier, group)
        if timeline is not None and len(timeline.entries) >= 1:
            timelines.append(timeline)

        pclass = predicate_class_of(predicate)
        bucket_class = pclass if pclass is not None else "record"
        topic = _topic(predicate, qualifier)

        # --- durable: compare across the whole group ---
        durable = [f for f in group if f.temporality == "durable"]
        if len(durable) >= 2 and len({_signature(f) for f in durable}) >= 2:
            disagreement = Disagreement(
                subject=subject,
                predicate=predicate,
                qualifier=qualifier,
                predicate_class=bucket_class,  # type: ignore[arg-type]
                topic=topic,
                versions=[_version(f) for f in durable],
            )
            if pclass == "perspectival":
                variance.append(disagreement)
            else:
                conflicts.append(disagreement)

        # --- as_of: only same as_of_date with distinct values is a disagreement ---
        by_date: dict[str, list[Fact]] = defaultdict(list)
        for fact in group:
            if fact.temporality != "as_of":
                continue
            by_date[_as_of_date(fact)].append(fact)

        for _date, date_group in by_date.items():
            if len(date_group) < 2:
                continue
            if len({_signature(f) for f in date_group}) < 2:
                continue
            disagreement = Disagreement(
                subject=subject,
                predicate=predicate,
                qualifier=qualifier,
                predicate_class=bucket_class,  # type: ignore[arg-type]
                topic=topic,
                versions=[_version(f) for f in date_group],
            )
            if pclass == "perspectival":
                variance.append(disagreement)
            else:
                conflicts.append(disagreement)

    return conflicts, variance, timelines, review_preds, review_subjects


def detect_disagreements_from_ledger(
    ledger: Ledger,
) -> tuple[list[Disagreement], list[Disagreement], list[Timeline], list[str], list[str]]:
    known = {s.id for s in ledger.sources}
    return detect_disagreements(ledger.facts, known_source_ids=known)


def record_value_conflicts(facts: list[Fact]) -> list[dict]:
    """Backward-compatible dict view of the conflicts bucket (tests / Stage 2.5)."""

    conflicts, _, _, _, _ = detect_disagreements(facts)
    return [
        {
            "topic": c.topic,
            "subject": c.subject,
            "predicate": c.predicate,
            "qualifier": c.qualifier,
            "source_ids": sorted({v.source_id for v in c.versions}),
            "values": sorted({v.value for v in c.versions}),
            "versions": c.versions,
        }
        for c in conflicts
    ]
