"""Preferred / banned descriptors for psycho-ed drafting (spec §9.7).

Deterministic list check — not a prompt instruction. Extend as Molly provides
more pairs. Matching is case-insensitive on banned phrases in prose.

Architecture (Molly worksheet §9): replace only exact confirmed pairs; highlight
context-sensitive items. Direct quotations and official names are global
carve-outs applied as a pre-pass before any rule runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class RuleAction(StrEnum):
    """Two-tier enforcement — Molly: replace confirmed pairs; flag the rest."""

    REPLACE = "replace"  # exact confirmed pair — safe to substitute
    FLAG = "flag"  # context-sensitive — highlight for review, never auto-replace


class RuleScope(StrEnum):
    ANY = "any"
    NARRATIVE = "narrative"  # prose only
    TABLE = "table"  # score tables only
    ELIGIBILITY = "eligibility"  # eligibility / legal wording


@dataclass(frozen=True, slots=True)
class TerminologyRule:
    banned: str
    preferred: str
    action: RuleAction = RuleAction.REPLACE
    scope: RuleScope = RuleScope.ANY
    notes: str = ""


@dataclass(frozen=True, slots=True)
class TerminologyHit:
    banned: str
    preferred: str
    action: RuleAction
    scope: RuleScope
    start: int
    end: int
    notes: str = ""
    in_quotation: bool = False


@dataclass(frozen=True, slots=True)
class TerminologyResult:
    """Scan outcome: rewritten text plus every hit (applied or highlighted)."""

    original: str
    rewritten: str
    hits: tuple[TerminologyHit, ...]

    def __iter__(self):
        """Backward-compatible (banned, preferred) pairs for every hit."""
        for hit in self.hits:
            yield (hit.banned, hit.preferred)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TerminologyResult):
            return (
                self.original == other.original
                and self.rewritten == other.rewritten
                and self.hits == other.hits
            )
        if isinstance(other, (list, tuple)):
            return list(self) == list(other)
        return NotImplemented

    @property
    def replacements(self) -> list[TerminologyHit]:
        return [h for h in self.hits if h.action == RuleAction.REPLACE and not h.in_quotation]

    @property
    def flags(self) -> list[TerminologyHit]:
        return [
            h
            for h in self.hits
            if h.action == RuleAction.FLAG or h.in_quotation
        ]


# Seed list — extend from Molly's preference inventory.
# Part 4 #1: leave Extremely Low → Very Low exactly as-is until she rules
# Very Low vs Exceptionally Low for the <70 band.
TERMINOLOGY_RULES: tuple[TerminologyRule, ...] = (
    TerminologyRule(
        banned="Extremely Low",
        preferred="Very Low",
        action=RuleAction.REPLACE,
        scope=RuleScope.ANY,
        notes="Standard score band label preference",
    ),
    TerminologyRule(
        banned="extremely low",
        preferred="Very Low",
        action=RuleAction.REPLACE,
        scope=RuleScope.ANY,
    ),
)


# Official instrument / scale / construct / diagnosis names — never auto-replaced.
# Seeded from instruments appearing in data/approved-anonymized/example-reports/.
PROTECTED_TERMS: tuple[str, ...] = (
    # Cognitive / achievement batteries
    "Wechsler Intelligence Scale for Children",
    "Wechsler Individual Achievement Test",
    "WISC-V",
    "WISC-IV",
    "WISC-5",
    "WIAT-4",
    "WIAT-III",
    "WIAT-3",
    "Woodcock-Johnson IV Tests of Cognitive Abilities",
    "Woodcock Johnson Tests of Cognitive Ability",
    "Woodcock Johnson Tests of Achievement",
    "Woodcock-Johnson IV",
    "Woodcock Johnson IV",
    "WJ-IV",
    "WJIV-ACA",
    "WJ-ACH",
    "Reynolds Intellectual Ability Scales",
    "DAS-II",
    "DAS-2-NU",
    "DAS-2",
    # Behavior / rating / adaptive
    "Behavior Assessment System for Children",
    "BASC-3",
    "BASC-2",
    "BRIEF-2",
    "Conners 4th Edition",
    "Conners 4",
    "Conners-4",
    "Conners-3",
    "Adaptive Behavior Assessment System",
    "ABAS-3",
    "Vineland-3",
    "Vineland Adaptive",
    # Neuro / language / motor
    "NEPSY-II",
    "NEPSY-2",
    "Delis-Kaplan Executive Function System",
    "Delis Kaplan Executive Function System",
    "Delis Kaplan Executive Functioning System",
    "D-KEFS",
    "DKEFS",
    "CTOPP-2",
    "KTEA-3",
    "TOWL-4",
    "TOWL4",
    "Beery VMI",
    "Beery-VMI",
    # Nonverbal construct names (person-language FLAG must not fire here)
    "nonverbal reasoning",
    "nonverbal memory",
    "nonverbal IQ",
    "nonverbal index",
    "nonverbal ability",
    "nonverbal cognitive",
    "nonverbal fluid",
    # Diagnostic / eligibility labels preserved as official names
    "Autism Spectrum Disorder",
    "Specific Learning Disability",
    "Other Health Impairment",
    "Speech or Language Impairment",
    "Intellectual Disability",
    "Normative Weakness",
)


_QUOTE_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"|'
    r"'(?:[^'\\]|\\.)*'|"
    r"\u201c[^\u201d]*\u201d|"
    r"\u2018[^\u2019]*\u2019"
)


def _span_overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for s, e in spans:
        if start < e and end > s:
            return True
    return False


def _quotation_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _QUOTE_RE.finditer(text)]


def _protected_spans(text: str, terms: tuple[str, ...] = PROTECTED_TERMS) -> list[tuple[int, int]]:
    lower = text.lower()
    spans: list[tuple[int, int]] = []
    for term in terms:
        key = term.lower()
        start = 0
        while True:
            idx = lower.find(key, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(key)))
            start = idx + 1
    return spans


def _rule_applies(rule: TerminologyRule, requested: RuleScope) -> bool:
    if rule.scope == RuleScope.ANY:
        return True
    if requested == RuleScope.ANY:
        # Unknown mixed context: surface context-scoped FLAG rules, but do not
        # auto-apply narrowly scoped REPLACE (avoids statutory mis-edits).
        return rule.action == RuleAction.FLAG
    return rule.scope == requested


def _compile_banned_pattern(banned: str) -> re.Pattern[str]:
    """Case-insensitive match; word boundaries when the phrase has edge word chars."""
    escaped = re.escape(banned)
    prefix = r"\b" if banned[:1].isalnum() else ""
    suffix = r"\b" if banned[-1:].isalnum() else ""
    return re.compile(rf"{prefix}{escaped}{suffix}", re.IGNORECASE)


def _dedupe_key(rule: TerminologyRule) -> str:
    return rule.banned.lower()


def find_terminology_violations(
    text: str,
    *,
    scope: RuleScope = RuleScope.ANY,
    rules: tuple[TerminologyRule, ...] | None = None,
    protected_terms: tuple[str, ...] | None = None,
) -> TerminologyResult:
    """
    Scan text for house-terminology issues.

    Carve-outs (pre-pass, every rule):
      1. Direct quotations — never REPLACE inside quotes; FLAG instead.
      2. Official / protected names — untouched (no hit).

    REPLACE hits outside carve-outs are applied in ``rewritten``.
    FLAG hits (and quote-demoted REPLACE) appear in ``hits`` without substitution.
    """

    active_rules = rules if rules is not None else TERMINOLOGY_RULES
    protected = protected_terms if protected_terms is not None else PROTECTED_TERMS

    quote_spans = _quotation_spans(text)
    protected = _protected_spans(text, protected)

    # Longer banned phrases first so "extremely low" wins over a later short rule.
    ordered = sorted(
        (r for r in active_rules if _rule_applies(r, scope)),
        key=lambda r: (-len(r.banned), r.banned.lower()),
    )

    # Collect candidate matches; skip protected overlaps; one hit per span.
    candidates: list[tuple[int, int, TerminologyRule, bool]] = []
    occupied: list[tuple[int, int]] = []
    seen_keys: set[tuple[int, int, str]] = set()

    for rule in ordered:
        pattern = _compile_banned_pattern(rule.banned)
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if _span_overlaps(start, end, protected):
                continue
            if _span_overlaps(start, end, occupied):
                continue
            key = (start, end, _dedupe_key(rule))
            if key in seen_keys:
                continue
            # Same span already claimed by another rule (e.g. Extremely Low vs extremely low)
            if any(start == s and end == e for s, e, _, _ in candidates):
                continue
            in_quote = _span_overlaps(start, end, quote_spans)
            seen_keys.add(key)
            occupied.append((start, end))
            candidates.append((start, end, rule, in_quote))

    # When scope is ANY and both FLAG and REPLACE hit the same banned key at
    # different spans, leave as-is. Same-span conflicts already collapsed above.
    # Prefer FLAG over REPLACE when two rules share a banned key and both
    # matched overlapping logic under ANY — handled by collecting FLAG-only
    # scoped rules in _rule_applies.

    candidates.sort(key=lambda c: c[0])

    hits: list[TerminologyHit] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, rule, in_quote in candidates:
        effective = RuleAction.FLAG if in_quote else rule.action
        hits.append(
            TerminologyHit(
                banned=text[start:end],
                preferred=rule.preferred,
                action=effective,
                scope=rule.scope,
                start=start,
                end=end,
                notes=rule.notes,
                in_quotation=in_quote,
            )
        )
        if effective == RuleAction.REPLACE and not in_quote:
            pieces.append(text[cursor:start])
            pieces.append(rule.preferred)
            cursor = end
    pieces.append(text[cursor:])
    rewritten = "".join(pieces)

    return TerminologyResult(
        original=text,
        rewritten=rewritten,
        hits=tuple(hits),
    )


def apply_terminology_replacements(
    text: str,
    *,
    scope: RuleScope = RuleScope.ANY,
) -> str:
    """Return text with REPLACE rules applied (quotations / protected names intact)."""

    return find_terminology_violations(text, scope=scope).rewritten
