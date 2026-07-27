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


@dataclass(frozen=True, slots=True)
class ScoreBand:
    """House score-descriptor band — lookup data for tables and prose (§9)."""

    label: str
    standard_score: str = ""
    t_score: str = ""
    scaled_score: str = ""
    percentile: str = ""
    notes: str = ""


# Ability / processing bands from Molly's returned worksheet §1.
# Part 4 #1: do NOT encode the <70 band yet (Very Low vs Exceptionally Low).
ABILITY_SCORE_BANDS: tuple[ScoreBand, ...] = (
    ScoreBand("Exceptionally High", ">130", ">70", ">16", "99th-100th"),
    ScoreBand("Above Average", "116-130", "61-70", "14-16", "85th-98th"),
    ScoreBand("High Average", "110-115", "57-60", "12-13", "75th-84th"),
    ScoreBand(
        "Average",
        "90-109",
        "43-56",
        "8-11",
        "24th-74th",
        notes=(
            "Classification label for tables and formal summaries. "
            "Narrative may use 'typical for age/grade' as explanation (§2 Q2)."
        ),
    ),
    ScoreBand("Low Average", "85-89", "40-42", "6-7", "16th-23rd"),
    ScoreBand("Below Average", "70-84", "30-39", "4-6", "2nd-15th"),
)


@dataclass(frozen=True, slots=True)
class BehaviorRatingBand:
    adaptive_t_score: str
    adaptive_label: str
    clinical_t_score: str
    clinical_label: str


BEHAVIOR_RATING_BANDS: tuple[BehaviorRatingBand, ...] = (
    BehaviorRatingBand("70+", "Very High", "70+", "Clinically Significant"),
    BehaviorRatingBand("60-69", "High", "60-69", "At-Risk"),
    BehaviorRatingBand("41-59", "Average/Typical", "41-59", "Average/Typical"),
    BehaviorRatingBand("31-40", "At-Risk", "31-40", "Low"),
    BehaviorRatingBand("30 or below", "Clinically Significant", "30 or below", "Very Low"),
)


# Confirmed house rules from Molly's returned worksheet (2026-07-27).
# Part 4 open questions stay FLAG-only or unencoded — do not guess.
TERMINOLOGY_RULES: tuple[TerminologyRule, ...] = (
    # --- Seed (Part 4 #1: leave Exact pair untouched until she rules) ---
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
    # --- 2a. Mechanical closed forms (§7) ---
    TerminologyRule("psycho-educational", "psychoeducational", RuleAction.REPLACE),
    TerminologyRule("re-evaluation", "reevaluation", RuleAction.REPLACE),
    TerminologyRule("sub-test", "subtest", RuleAction.REPLACE),
    TerminologyRule(
        "non-verbal",
        "nonverbal",
        RuleAction.REPLACE,
        notes="Construct closed form; person-language uses non-speaking (FLAG).",
    ),
    # Molly reversed the proposal: prefer multi-step, not multistep (Part 4 #6).
    TerminologyRule(
        "multistep",
        "multi-step",
        RuleAction.REPLACE,
        notes="Molly: 'I like the multi-step.' Confirmation pending Part 4 #6.",
    ),
    # --- 2a. Mechanical hyphenated compound modifiers (§7) ---
    TerminologyRule("social emotional", "social-emotional", RuleAction.REPLACE),
    TerminologyRule("self report", "self-report", RuleAction.REPLACE),
    TerminologyRule("problem solving", "problem-solving", RuleAction.REPLACE),
    TerminologyRule("visual spatial", "visual-spatial", RuleAction.REPLACE),
    TerminologyRule("off task", "off-task", RuleAction.REPLACE),
    TerminologyRule("open ended", "open-ended", RuleAction.REPLACE),
    TerminologyRule("one on one", "one-on-one", RuleAction.REPLACE),
    # --- 2b. Person-first / neutral REPLACE ---
    TerminologyRule(
        "learning-disabled student",
        "student with a Specific Learning Disability",
        RuleAction.REPLACE,
    ),
    TerminologyRule(
        "confined to a wheelchair",
        "uses a wheelchair to navigate the environment",
        RuleAction.REPLACE,
    ),
    TerminologyRule(
        "suffers from",
        "has",
        RuleAction.REPLACE,
        notes="Prefer has / was diagnosed with / experienced as fits the fact.",
    ),
    TerminologyRule(
        "victim of",
        "experienced",
        RuleAction.REPLACE,
        notes="Prefer has / was diagnosed with / experienced as fits the fact.",
    ),
    # Narrative only — statutory "Emotional Disturbance" collision (Part 4 #5).
    TerminologyRule(
        "emotional disturbance",
        "emotional disability",
        RuleAction.REPLACE,
        RuleScope.NARRATIVE,
        notes="Molly: never say emotional disturbance; always emotional disability (ED).",
    ),
    TerminologyRule(
        "emotional disturbance",
        "emotional disability",
        RuleAction.FLAG,
        RuleScope.ELIGIBILITY,
        notes="Part 4 #5: confirm override in statutory/eligibility contexts before REPLACE.",
    ),
    # --- 2b. Context-sensitive person language (FLAG) ---
    TerminologyRule(
        "mentally ill",
        "has a diagnosis of [condition]",
        RuleAction.FLAG,
        notes="Follow attributed preference; preserve student's/parent's own words when useful.",
    ),
    TerminologyRule(
        "nonverbal",
        "non-speaking",
        RuleAction.FLAG,
        notes=(
            "Person who does not use speech → non-speaking. "
            "Constructs (nonverbal reasoning/memory) are protected."
        ),
    ),
    TerminologyRule(
        "autistic student",
        "student with autism or autistic student (family preference)",
        RuleAction.FLAG,
        notes="Ask or follow the student's/family's preference.",
    ),
    TerminologyRule(
        "student with autism",
        "student with autism or autistic student (family preference)",
        RuleAction.FLAG,
        notes="Ask or follow the student's/family's preference.",
    ),
    # --- 2b. Neutral school / prior-eval wording ---
    TerminologyRule(
        "the district failed to assess",
        "the prior evaluation did not include",
        RuleAction.REPLACE,
        notes="Or: the available records did not show…",
    ),
    TerminologyRule(
        "the prior evaluator was wrong",
        "the current findings differ from the prior evaluation because",
        RuleAction.REPLACE,
    ),
    TerminologyRule(
        "ignored",
        "state the observable record without assigning motive",
        RuleAction.FLAG,
        notes="Also dismissed / refused when intent is not established.",
    ),
    TerminologyRule(
        "dismissed",
        "state the observable record without assigning motive",
        RuleAction.FLAG,
        notes="When intent is not established.",
    ),
    TerminologyRule(
        "refused",
        "state the observable record without assigning motive",
        RuleAction.FLAG,
        notes="When intent is not established; preserve attributed quotations.",
    ),
    # --- 2c. Strengths-based review (all FLAG) ---
    TerminologyRule(
        "weakness",
        "area of need, relative difficulty",
        RuleAction.FLAG,
        notes="Part 4 #4: Molly's answer incomplete — FLAG only, do not REPLACE.",
    ),
    TerminologyRule(
        "weaknesses",
        "area of need, relative difficulty",
        RuleAction.FLAG,
        notes="Part 4 #4: Molly's answer incomplete — FLAG only, do not REPLACE.",
    ),
    TerminologyRule(
        "deficit",
        "area of need, challenge area",
        RuleAction.FLAG,
    ),
    TerminologyRule(
        "deficits",
        "area of need, challenge area",
        RuleAction.FLAG,
    ),
    TerminologyRule("bad at", "had difficulty with, needed support with", RuleAction.FLAG),
    TerminologyRule("poor at", "had difficulty with, needed support with", RuleAction.FLAG),
    TerminologyRule(
        "unable to",
        "describe what happened (did not begin, did not respond, …)",
        RuleAction.FLAG,
        notes="Keep unable when evidence establishes inability.",
    ),
    TerminologyRule(
        "unwilling to",
        "describe what happened (did not begin, did not respond, …)",
        RuleAction.FLAG,
        notes="When intent is not established.",
    ),
    TerminologyRule(
        "clinically significant",
        "pair the score label with plain-language observation and implication",
        RuleAction.FLAG,
        notes="Bare score label without plain-language pairing.",
    ),
    TerminologyRule(
        "at-risk",
        "pair the score label with plain-language observation and implication",
        RuleAction.FLAG,
        notes="Bare score label without plain-language pairing.",
    ),
    TerminologyRule(
        "atypical",
        "pair the score label with plain-language observation and implication",
        RuleAction.FLAG,
        notes="Bare score label without plain-language pairing.",
    ),
    # --- 2d. Naming people (stateful — FLAG only) ---
    TerminologyRule(
        "Teacher reported",
        "Name first, then role on first mention; role/name thereafter",
        RuleAction.FLAG,
        notes="Molly: person's name, then role, on first description; then use their name.",
    ),
    # --- 2e. Eligibility / process acronyms — first-use state (FLAG) ---
    TerminologyRule(
        "SLD",
        "Specific Learning Disability (spell out on first use)",
        RuleAction.FLAG,
        RuleScope.ELIGIBILITY,
        notes="Spell out eligibility category on first use; acronym thereafter.",
    ),
    TerminologyRule(
        "OHI",
        "Other Health Impairment (spell out on first use)",
        RuleAction.FLAG,
        RuleScope.ELIGIBILITY,
        notes="Spell out eligibility category on first use; acronym thereafter.",
    ),
    TerminologyRule(
        "SLI",
        "Speech or Language Impairment (spell out on first use)",
        RuleAction.FLAG,
        RuleScope.ELIGIBILITY,
        notes="Spell out eligibility category on first use; acronym thereafter.",
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

# Nouns that take a hyphenated grade-level / age-appropriate modifier (§7).
_MODIFIER_NOUNS = frozenset(
    {
        "standards",
        "expectations",
        "curriculum",
        "skills",
        "texts",
        "work",
        "performance",
        "material",
        "materials",
        "reading",
        "math",
        "writing",
        "instruction",
        "content",
        "tasks",
        "demands",
        "peers",
        "behavior",
        "behaviour",
        "development",
        "functioning",
    }
)

# Prepositions/adverbs after which "grade level" / "age appropriate" stay open (§7).
_OPEN_COMPOUND_PRECURSORS = frozenset(
    {
        "at",
        "to",
        "near",
        "above",
        "below",
        "toward",
        "towards",
        "around",
        "about",
    }
)

_COMPOUND_HYPHEN_RE = re.compile(
    r"\b(?P<head>grade|age)(?P<sep>[ -])(?P<tail>level|appropriate)\b",
    re.IGNORECASE,
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


def _token_before(text: str, index: int) -> str:
    i = index - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    end = i + 1
    while i >= 0 and (text[i].isalnum() or text[i] in "'-"):
        i -= 1
    return text[i + 1 : end].lower()


def _token_after(text: str, index: int) -> str:
    i = index
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    start = i
    while i < n and (text[i].isalnum() or text[i] in "'-"):
        i += 1
    return text[start:i].lower()


def _hyphenation_candidates(
    text: str,
    *,
    quote_spans: list[tuple[int, int]],
    protected_spans: list[tuple[int, int]],
    occupied: list[tuple[int, int]],
) -> list[tuple[int, int, TerminologyRule, bool]]:
    """
    Position-aware grade-level / age-appropriate checks (worksheet §7).

    Hyphen before a noun; open form after a verb/preposition. Clear mismatches
    REPLACE; ambiguous cases FLAG.
    """

    out: list[tuple[int, int, TerminologyRule, bool]] = []
    for match in _COMPOUND_HYPHEN_RE.finditer(text):
        head = match.group("head").lower()
        tail = match.group("tail").lower()
        if head == "grade" and tail != "level":
            continue
        if head == "age" and tail != "appropriate":
            continue
        start, end = match.start(), match.end()
        if _span_overlaps(start, end, protected_spans):
            continue
        if _span_overlaps(start, end, occupied):
            continue
        sep = match.group("sep")
        hyphenated = sep == "-"
        before = _token_before(text, start)
        after = _token_after(text, end)
        preferred_hyphen = f"{head}-{tail}"
        preferred_open = f"{head} {tail}"
        in_quote = _span_overlaps(start, end, quote_spans)

        if after in _MODIFIER_NOUNS and not hyphenated:
            rule = TerminologyRule(
                banned=match.group(0),
                preferred=preferred_hyphen,
                action=RuleAction.REPLACE,
                notes="Compound modifier before a noun takes a hyphen.",
            )
        elif before in _OPEN_COMPOUND_PRECURSORS and hyphenated:
            rule = TerminologyRule(
                banned=match.group(0),
                preferred=preferred_open,
                action=RuleAction.REPLACE,
                notes="After a verb/preposition, leave the phrase open (no hyphen).",
            )
        elif after in _MODIFIER_NOUNS and hyphenated:
            continue  # correct: grade-level standards
        elif before in _OPEN_COMPOUND_PRECURSORS and not hyphenated:
            continue  # correct: at grade level
        else:
            # Ambiguous position — highlight rather than risk a wrong hyphen.
            rule = TerminologyRule(
                banned=match.group(0),
                preferred=preferred_hyphen if not hyphenated else preferred_open,
                action=RuleAction.FLAG,
                notes="Check grade-level/age-appropriate hyphenation against noun vs. adverbial use.",
            )
        occupied.append((start, end))
        out.append((start, end, rule, in_quote))
    return out


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
    protected_list = protected_terms if protected_terms is not None else PROTECTED_TERMS

    quote_spans = _quotation_spans(text)
    protected_spans = _protected_spans(text, protected_list)

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
            if _span_overlaps(start, end, protected_spans):
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

    candidates.extend(
        _hyphenation_candidates(
            text,
            quote_spans=quote_spans,
            protected_spans=protected_spans,
            occupied=occupied,
        )
    )

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
