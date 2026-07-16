"""Deterministic validators — Phase 2 reliability seed (age/DOB first)."""

from __future__ import annotations

import re
from datetime import date, datetime

from schemas import ReportSection


# Current-age claims only — historical "records said she was 7" must not fail the draft.
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
    # "currently age 8" / "age 8" when not clearly historical
    re.compile(
        r"\b(?:currently\s+)?(?:age[d]?)\s+(?P<age>\d{1,2})\b",
        re.IGNORECASE,
    ),
]

_HISTORICAL_HINT = re.compile(
    r"\b("
    r"was|were|stated|indicated|listed|recorded|noted|reported\s+as|"
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


def _extract_wrong_current_ages(text: str, expected: int) -> list[tuple[int, str]]:
    wrong: list[tuple[int, str]] = []
    for pattern in _CURRENT_AGE_PATTERNS:
        for match in pattern.finditer(text):
            asserted = int(match.group("age"))
            if asserted == expected:
                continue
            window = _sentence_window(text, match.start(), match.end())
            # Allow attributed historical ages from old records.
            if _HISTORICAL_HINT.search(window):
                continue
            wrong.append((asserted, window.strip()[:120]))
    return wrong


def validate_age_consistency(
    section: ReportSection,
    *,
    dob: str,
    evaluation_date: str,
) -> int:
    """
    Recompute age from dob + evaluation_date.
    Reject drafts that assert a *current* age different from that value.
    Historical mentions (e.g. "2024 file stated student was 7") are allowed.

    Returns the expected age in whole years when valid.
    Raises ValueError when inconsistent (caller retries / fails cleanly).
    """

    expected = compute_age_years(dob, evaluation_date)
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
