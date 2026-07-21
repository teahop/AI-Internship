"""Preferred / banned descriptors for psycho-ed drafting (spec §9.7).

Deterministic list check — not a prompt instruction. Extend as Molly provides
more pairs. Matching is case-insensitive on banned phrases in prose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TerminologyRule:
    banned: str
    preferred: str
    notes: str = ""


# Seed list — extend from Molly's preference inventory.
TERMINOLOGY_RULES: tuple[TerminologyRule, ...] = (
    TerminologyRule(
        banned="Extremely Low",
        preferred="Very Low",
        notes="Standard score band label preference",
    ),
    TerminologyRule(
        banned="extremely low",
        preferred="Very Low",
    ),
)


def find_terminology_violations(text: str) -> list[tuple[str, str]]:
    """
    Return list of (banned_phrase, preferred) found in text.

    Case-insensitive search for each banned string.
    """

    hits: list[tuple[str, str]] = []
    lower = text.lower()
    seen: set[str] = set()
    for rule in TERMINOLOGY_RULES:
        key = rule.banned.lower()
        if key in seen:
            continue
        if key in lower:
            seen.add(key)
            hits.append((rule.banned, rule.preferred))
    return hits
