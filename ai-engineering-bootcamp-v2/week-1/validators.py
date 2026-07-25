"""Deterministic validators — age/DOB and provenance spine only.

Conflict detection is /conflicts (conflicts.py). No clinical topic keywords here.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from derived import AGE_DERIVATION, validate_derived_facts
from schemas import Fact, Ledger, ReportSection, Source


# Current-age claims only — historical "at age 3" / "records said she was 7"
# must not fail the draft. Backstop for uncited ages; primary check is
# derived-fact recomputation.
_CURRENT_AGE_PATTERNS = [
    # "is an 8-year-old", "is a 8 year old"
    re.compile(
        r"\bis\s+an?\s+(?P<age>\d{1,2})\s*-?\s*years?\s*old\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bis\s+an?\s+(?P<age>\d{1,2})\s*-?\s*year-?old\b",
        re.IGNORECASE,
    ),
    # "is 8 years old"
    re.compile(
        r"\bis\s+(?P<age>\d{1,2})\s+years?\s+old\b",
        re.IGNORECASE,
    ),
    # Present-framed only: "currently age 8", "is age 8", "present age 8".
    # Bare "age 8" / "at age 3" is handled via historical-prefix exclusion below.
    re.compile(
        r"\b(?:currently|present(?:ly)?|is)\s+(?:age[d]?)\s+(?P<age>\d{1,2})\b",
        re.IGNORECASE,
    ),
    # "age 8" / "aged 8" only when not a historical "at/by/around age N".
    re.compile(
        r"\b(?:age[d]?)\s+(?P<age>\d{1,2})\b",
        re.IGNORECASE,
    ),
]

# Immediate left context that marks an age mention as historical, not current.
_HISTORICAL_AGE_PREFIX = re.compile(
    r"(?:"
    r"at|by|around|about|from|until|since|before|after|"
    r"when\s+(?:she|he|they|the\s+(?:child|student|patient))\s+was"
    r")\s+$",
    re.IGNORECASE,
)

_HISTORICAL_HINT = re.compile(
    r"\b("
    r"was|were|stated|indicated|listed|recorded|noted|reported\s+as|"
    r"at\s+age|by\s+age|around\s+age|when\s+\w+\s+was|"
    r"at\s+the\s+time|in\s+\d{4}|cumulative|old\s+record|stale"
    r")\b",
    re.IGNORECASE,
)


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def compute_age_years(dob: str, evaluation_date: str) -> int:
    """Whole years at evaluation_date — the only current age Molly's drafts may assert."""

    born = parse_iso_date(dob)
    as_of = parse_iso_date(evaluation_date)
    if as_of < born:
        raise ValueError("evaluation_date is before dob")
    years = as_of.year - born.year
    if (as_of.month, as_of.day) < (born.month, born.day):
        years -= 1
    return years


def _sentence_window(text: str, start: int, end: int) -> str:
    left = text.rfind(".", 0, start)
    right = text.find(".", end)
    a = 0 if left < 0 else left + 1
    b = len(text) if right < 0 else right
    return text[a:b]


def _is_historical_age_mention(text: str, match: re.Match[str]) -> bool:
    """True when the match is a past-age reference, not a current-age claim."""

    prefix = text[max(0, match.start() - 48) : match.start()]
    if _HISTORICAL_AGE_PREFIX.search(prefix):
        return True
    window = _sentence_window(text, match.start(), match.end())
    return bool(_HISTORICAL_HINT.search(window))


def _extract_wrong_current_ages(text: str, expected: int) -> list[tuple[int, str]]:
    wrong: list[tuple[int, str]] = []
    seen_spans: set[tuple[int, int]] = set()
    for pattern in _CURRENT_AGE_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start("age"), match.end("age"))
            if span in seen_spans:
                continue
            seen_spans.add(span)
            asserted = int(match.group("age"))
            if asserted == expected:
                continue
            # Allow historical ages ("at age 3", "when she was 7", old records).
            if _is_historical_age_mention(text, match):
                continue
            window = _sentence_window(text, match.start(), match.end())
            wrong.append((asserted, window.strip()[:120]))
    return wrong


def _has_current_age_mention(text: str, expected: int) -> bool:
    seen_spans: set[tuple[int, int]] = set()
    for pattern in _CURRENT_AGE_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start("age"), match.end("age"))
            if span in seen_spans:
                continue
            seen_spans.add(span)
            asserted = int(match.group("age"))
            if asserted != expected:
                continue
            if _is_historical_age_mention(text, match):
                continue
            return True
    return False


def derived_age_facts(facts: list[Fact]) -> list[Fact]:
    return [
        f
        for f in facts
        if f.derivation == AGE_DERIVATION and f.predicate == "age_years"
    ]


def validate_age_consistency(
    section: ReportSection,
    *,
    dob: str,
    evaluation_date: str,
    ledger: Ledger | None = None,
) -> int:
    """
    Primary: recompute derived age_years fact(s) and require prose that states
    current age to cite that fact. Regex remains a backstop for wrong/uncited ages.

    Returns the expected age in whole years when valid.
    """

    expected = compute_age_years(dob, evaluation_date)

    if ledger is not None:
        validate_derived_facts(ledger.facts, ledger.child)
        ages = derived_age_facts(ledger.facts)
        if len(ages) != 1:
            raise ValueError(
                f"Expected exactly one derived age_years fact, found {len(ages)}"
            )
        age_fact = ages[0]
        if age_fact.value != str(expected):
            raise ValueError(
                f"Derived age fact {age_fact.id} has value {age_fact.value!r}, "
                f"expected {expected!r}"
            )

        citing = [
            f
            for f in section.facts
            if f.fact_id == age_fact.id
        ]
        prose_mentions_age = _has_current_age_mention(section.prose, expected) or any(
            _has_current_age_mention(f.statement, expected) for f in section.facts
        )
        if prose_mentions_age and not citing:
            raise ValueError(
                f"Prose asserts current age {expected} but does not cite "
                f"derived fact {age_fact.id}"
            )

    texts = [section.prose, *(fact.statement for fact in section.facts)]
    wrong: list[tuple[int, str]] = []
    for text in texts:
        wrong.extend(_extract_wrong_current_ages(text, expected))

    if wrong:
        examples = "; ".join(f"asserted {age} in {snippet!r}" for age, snippet in wrong[:3])
        raise ValueError(
            f"Age mismatch: expected {expected} years from DOB {dob} "
            f"as of {evaluation_date}, but draft asserted otherwise ({examples})"
        )
    return expected


def _all_sourced_facts(section: ReportSection):
    yield from section.facts
    for conflict in section.conflicts:
        yield from conflict.versions


def validate_provenance(section: ReportSection, sources: list[Source]) -> None:
    """
    Enforce the provenance spine: every fact/conflict version cites a real input
    source_id and that source's exact date as source_date.

    Synthetic source_ids (computed / request) are allowed for derived/request facts.
    """

    by_id = {s.id: s for s in sources}
    synthetic = {"computed", "request"}
    errors: list[str] = []

    for fact in _all_sourced_facts(section):
        if fact.source_id in synthetic:
            continue
        source = by_id.get(fact.source_id)
        if source is None:
            errors.append(f"unknown source_id={fact.source_id!r}")
            continue
        if fact.source_date != source.date:
            errors.append(
                f"source_date mismatch for {fact.source_id!r}: "
                f"got {fact.source_date!r}, expected {source.date!r}"
            )

    if not section.facts:
        errors.append("facts list is empty")

    if errors:
        raise ValueError("Provenance validation failed: " + "; ".join(errors[:5]))
