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


# Normative-weakness cutoff. RULED 2026-07-28: a score is a *normative*
# weakness (low relative to same-age peers nationally) at standard score < 85.
# Molly was shown both candidate thresholds from her own explanation — SS <= 79
# / 10th percentile, and "some people use below 85" — and chose 85.
#
# This is the normative test only. A *relative* (personal) weakness is defined
# against the student's own mean and needs a derivation the ledger does not
# currently carry, so the weakness rules below stay FLAG, not REPLACE.
NORMATIVE_WEAKNESS_MAX_SS: int = 85


# Ability / processing bands from Molly's returned worksheet §1.
# Part 3 #1 RULED 2026-07-27 (structure worksheet): the <70 band is
# "Well Below Average". Her answer reversed itself mid-sentence — she wrote
# "Let's go with exceptionally low" then "Let's go with Well Below Average" —
# and Part 3 #2 confirms the same label. Rationale in her words: when every
# score falls in this band, the harshest label repeats page after page and is
# hard for a parent to read. "Very Low" is retired as an ability-band label.
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
    ScoreBand(
        "Well Below Average",
        "<70",
        "<30",
        "1-3",
        "2nd or below",
        notes=(
            "Part 3 #1/#2 (2026-07-27). Replaces the previously recorded "
            "'Very Low' for this band. Chosen for lower perceived negative "
            "connotation when a whole profile sits below 70."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class BehaviorRatingBand:
    adaptive_t_score: str
    adaptive_label: str
    clinical_t_score: str
    clinical_label: str


# Part 3 #3 RULED 2026-07-27: publisher behavior-scale labels are PRESERVED,
# not converted to house labels. Molly: "Use very elevated." So *Very Elevated*,
# *Clinically Elevated*, *Mildly Elevated* etc. stay as the instrument prints
# them. No REPLACE rule is registered for them below — this is deliberate.
#
# NOTE (collision): the clinical T<=30 label here is "Very Low", which is the
# same string just retired as an *ability* band label. These are different
# scales and the behavior label is unchanged. This is why no blanket
# "Very Low" -> "Well Below Average" REPLACE exists; see the FLAG rule below.
BEHAVIOR_RATING_BANDS: tuple[BehaviorRatingBand, ...] = (
    BehaviorRatingBand("70+", "Very High", "70+", "Clinically Significant"),
    BehaviorRatingBand("60-69", "High", "60-69", "At-Risk"),
    BehaviorRatingBand("41-59", "Average/Typical", "41-59", "Average/Typical"),
    BehaviorRatingBand("31-40", "At-Risk", "31-40", "Low"),
    BehaviorRatingBand("30 or below", "Clinically Significant", "30 or below", "Very Low"),
)


# Confirmed house rules from Molly's returned worksheets (2026-07-27).
#
# Structure-worksheet Part 3 closed four of the six open terminology items:
#   #1 lowest ability band  -> "Well Below Average" (supersedes "Very Low")
#   #2 "Well Below Average" -> adopted as the band label itself, not a synonym
#   #3 publisher behavior labels -> PRESERVED ("Use very elevated")
#   #4 "weakness"           -> keep the word, qualify normative vs. relative
#   #5 emotional disturbance-> substitution holds in eligibility too (confirmed)
#   #6 multi-step           -> CLOSED 2026-07-28: hyphenated, confirmed
#
# Follow-up replies 2026-07-28 closed the remainder:
#   - multi-step: "Hyphenated, yes."
#   - normative-weakness threshold: standard score < 85 (she picked 85 over 79).
#   - lowest band / "Very Low" persisting in behavior tables: "That sounds good."
#   - emotional disturbance -> emotional disability: re-confirmed knowingly.
#
# All six terminology items are now closed.
TERMINOLOGY_RULES: tuple[TerminologyRule, ...] = (
    # --- Lowest ability band (Part 3 #1/#2, ruled 2026-07-27) ---
    # Target changed from "Very Low" to "Well Below Average".
    TerminologyRule(
        banned="Extremely Low",
        preferred="Well Below Average",
        action=RuleAction.REPLACE,
        scope=RuleScope.ANY,
        notes="Lowest standard-score band (<70). Ruled 2026-07-27; supersedes 'Very Low'.",
    ),
    TerminologyRule(
        banned="extremely low",
        preferred="Well Below Average",
        action=RuleAction.REPLACE,
        scope=RuleScope.ANY,
    ),
    # "Very Low" is FLAG, not REPLACE, because the same string is the live
    # publisher label for the clinical T<=30 behavior-rating band (preserved
    # per Part 3 #3). Auto-replacing would corrupt behavior-scale reporting.
    # Reviewer decides which scale is in play.
    TerminologyRule(
        banned="Very Low",
        preferred="Well Below Average",
        action=RuleAction.FLAG,
        scope=RuleScope.ANY,
        notes=(
            "Ability/processing scores (<70) now read 'Well Below Average'. "
            "Do NOT change if this is a behavior-rating T-score label — that "
            "band is still 'Very Low'. Same string, two scales."
        ),
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
    # CONFIRMED 2026-07-28 — asked directly, answered "Hyphenated, yes."
    TerminologyRule(
        "multistep",
        "multi-step",
        RuleAction.REPLACE,
        notes="Molly: 'I like the multi-step.' Confirmed hyphenated 2026-07-28.",
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
    # Part 3 #5 CONFIRMED 2026-07-27, re-confirmed 2026-07-28: the substitution
    # holds in eligibility and legal wording too — a deliberate, recorded
    # exception to the otherwise-standing rule that statutory language is
    # reproduced exactly. Molly does not use this term anywhere. That's the rule.
    #
    # Premise correction (on the record): her rationale equated this with
    # "mentally retarded" being "against the law." Rosa's Law (2010) did
    # replace "mental retardation" federally; "emotional disturbance" was
    # not similarly replaced and remains the operative IDEA / CA Ed Code
    # eligibility category. Encoded as house style (Molly is final reviewer),
    # not as a legal requirement. She re-confirmed knowingly on 2026-07-28.
    TerminologyRule(
        "emotional disturbance",
        "emotional disability",
        RuleAction.REPLACE,
        RuleScope.ELIGIBILITY,
        notes=(
            "Molly-confirmed house-style override of the statutory term "
            "(not a legal requirement — 'emotional disturbance' remains the "
            "IDEA / CA Ed Code category). Applies in all scopes."
        ),
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
    # Part 3 #4 ANSWERED 2026-07-27. Molly finished the sentence that had been
    # cut off: she does not want "weakness" removed — she wants it *qualified*
    # as either a normative weakness (low vs. same-age peers nationally) or a
    # relative/personal weakness (low vs. the student's own ability level),
    # with both defined in the About Test Scores section. Still FLAG, not
    # REPLACE: the correct qualifier depends on the score pattern, which this
    # deterministic pass cannot see.
    TerminologyRule(
        "weakness",
        "qualify as a normative weakness or a relative (personal) weakness",
        RuleAction.FLAG,
        notes=(
            "Normative = low vs. same-age peers nationally. Relative = low vs. "
            "the student's own average ability. Molly wants the benchmark named "
            "in plain words ('compared to other children the same age across the "
            "country'). Threshold SETTLED 2026-07-28: standard score < 85 "
            "(NORMATIVE_WEAKNESS_MAX_SS). She was offered 79 vs. 85 and chose 85."
        ),
    ),
    TerminologyRule(
        "weaknesses",
        "qualify as normative weaknesses or relative (personal) weaknesses",
        RuleAction.FLAG,
        notes=(
            "See 'weakness'. Bare plural with no normative/relative qualifier is "
            "the thing Molly flagged. Threshold: SS < 85, ruled 2026-07-28."
        ),
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
    # Technical weakness-type terms (Part 3 #4) — these are the qualifiers
    # Molly wants used, so the strengths-based "weakness" FLAG must not fire
    # on them.
    "Normative Weakness",
    "Normative Weaknesses",
    "Relative Weakness",
    "Relative Weaknesses",
    "Personal Weakness",
    "Personal Weaknesses",
    # Publisher behavior-scale labels preserved per Part 3 #3.
    "Very Elevated",
    "Mildly Elevated",
    "Potentially Clinically Elevated",
    "Clinically Elevated",
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
