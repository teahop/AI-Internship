"""Per-source ledger extraction — one model call per source, no cross-document view."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from coverage import build_gap_report
from conflicts import compute_timelines
from derived import (
    inject_derived_and_request_facts,
    is_synthetic_source_id,
    strip_synthetic_facts,
)
from normalize import clip_value_text, normalize_qualifier, normalize_value
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

# Soft cap on source content per extraction call. Oversized narrative docs are
# split, extracted per chunk, then de-duplicated (Stage 6.3).
EXTRACT_CHUNK_CHAR_LIMIT = 12_000


def fact_id_for_source(source_id: str, index: int) -> str:
    """
    Namespace fact ids by source so merges cannot collide.

    Unrelated merges leave other sources' ids untouched; re-ingest of one source
    replaces only that source's namespace.
    """

    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", source_id).strip("_") or "source"
    return f"f_{safe}_{index:03d}"


def split_source_content(
    content: str,
    *,
    limit: int = EXTRACT_CHUNK_CHAR_LIMIT,
) -> list[str]:
    """
    Split oversized narrative content into chunks under ``limit`` characters.

    Prefers paragraph boundaries, then whitespace; hard-splits only as a last resort.
    """

    text = content or ""
    if len(text) <= limit:
        return [text] if text else [""]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""

    def _flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    def _append_piece(piece: str) -> None:
        nonlocal current
        piece = piece.strip()
        if not piece:
            return
        if len(piece) > limit:
            _flush()
            # Hard-split a single oversized paragraph.
            for start in range(0, len(piece), limit):
                chunks.append(piece[start : start + limit])
            return
        candidate = f"{current}\n\n{piece}".strip() if current else piece
        if len(candidate) <= limit:
            current = candidate
            return
        _flush()
        current = piece

    for para in paragraphs:
        if len(para) <= limit:
            _append_piece(para)
            continue
        # Oversized paragraph: split on whitespace runs first.
        parts = re.split(r"(\s+)", para)
        buf = ""
        for part in parts:
            if len(buf) + len(part) <= limit:
                buf += part
            else:
                if buf.strip():
                    _append_piece(buf)
                buf = part
        if buf.strip():
            _append_piece(buf)

    _flush()
    return chunks or [text[:limit]]


def fact_dedupe_key(fact: Fact) -> tuple[str, str, str | None, str, str]:
    """Same subject+predicate+qualifier+value+source_id → one fact after chunk merge."""

    return (fact.subject, fact.predicate, fact.qualifier, fact.value, fact.source_id)


def dedupe_facts(facts: list[Fact]) -> list[Fact]:
    """Keep first occurrence of each dedupe key; preserve order."""

    seen: set[tuple[str, str, str | None, str, str]] = set()
    out: list[Fact] = []
    for fact in facts:
        key = fact_dedupe_key(fact)
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


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

    Vocabulary (canonical subject names) is not case data — no dob, name,
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


# Status predicates: explicit "none / not in place" is a denial, not an asserted status.
_STATUS_DENIAL_PREDICATES = frozenset({"iep_status", "plan_504_status"})


def _finalize_assertion(
    draft: ExtractedFactDraft,
    *,
    predicate: str,
    value: str,
) -> FactAssertion:
    assertion: FactAssertion = (
        draft.assertion if draft.assertion in ("asserted", "denied") else "asserted"
    )
    # Lock speech-act convention: normalized none on plan-status preds → denied.
    if (
        predicate in _STATUS_DENIAL_PREDICATES
        and value.strip().lower() == "none"
        and assertion == "asserted"
    ):
        return "denied"
    return assertion


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


def _draft_is_skippable(draft: ExtractedFactDraft, source: Source | None = None) -> bool:
    """True when a draft has no usable value — skip, do not abort the source."""

    raw = (draft.value or "").strip()
    if not raw or raw.lower() in {"null", "none-stated", "n/a", "undefined"}:
        return True
    predicate = _resolve_predicate_name(draft)
    if predicate == "dob" and source is not None and _is_garbage_dob(draft, source):
        return True
    if predicate == "age_years" and source is not None and _is_spurious_age_years(draft, source):
        return True
    if predicate == "grade" and source is not None and _is_spurious_grade(draft, source):
        return True
    if predicate == "iep_status" and source is not None and _is_spurious_iep_status(draft, source):
        return True
    if predicate == "attendance" and _is_spurious_attendance(draft):
        return True
    return False


_DOB_PLACEHOLDER_RE = re.compile(r"^[\s_\.\-/xX]+$")
_DOB_ANCHOR_RE = re.compile(
    r"\b(dob|d\.o\.b\.?|date of birth|born|birth\s*date|birthday|birthdate)\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Explicit child-age statements only (not PE "grades 5, 7 & 9", not milestones).
_AGE_YEARS_MONTHS_RE = re.compile(
    r"\b(?:age\s*:?\s*)?(\d{1,2})\s*years?(?:\s*\(\s*s\s*\))?\s+(\d{1,2})\s*months?\b",
    re.IGNORECASE,
)
_AGE_YEARS_OLD_RE = re.compile(
    r"\b(?:age\s*:?\s*)?(\d{1,2})\s*-?\s*years?\s*old\b|"
    r"\b(?:age\s*:?\s*)?(\d{1,2})\s*-?\s*year-?old\b|"
    r"\bAge\s*:\s*(\d{1,2})\b|"
    r"\b(?:age\s*:?\s*)(\d{1,2})\s*:\s*\d{1,2}\b|"
    r"\b(\d{1,2})\s*y\s+\d{1,2}\s*m\b",
    re.IGNORECASE,
)


def _explicit_age_years_in_text(text: str) -> set[int]:
    """Whole-year ages stated as the child's age — floored years+months, never rounded."""

    ages: set[int] = set()
    for match in _AGE_YEARS_MONTHS_RE.finditer(text or ""):
        ages.add(int(match.group(1)))
    for match in _AGE_YEARS_OLD_RE.finditer(text or ""):
        for group in match.groups():
            if group is not None:
                ages.add(int(group))
    return ages


def _is_spurious_age_years(draft: ExtractedFactDraft, source: Source) -> bool:
    """
    Drop age_years minted by rounding years+months or borrowing a nearby number.

    doc_25 states \"Age: 8 year(s) 10 months\" only — a second value \"9\" is not a
    child-age claim (rounding or PE \"grades 5, 7 & 9\" leakage). When value_text
    carries the years+months phrase, keep the draft and let normalize floor it.
    """

    value = (draft.value or "").strip()
    value_text = (draft.value_text or "").strip()
    effective = normalize_value("age_years", value, value_text)
    try:
        asserted = int(re.search(r"\d{1,2}", effective).group(0))  # type: ignore[union-attr]
    except (AttributeError, ValueError, TypeError):
        return False

    local = _explicit_age_years_in_text(value_text)
    if local:
        return asserted not in local

    allowed = _explicit_age_years_in_text(source.content or "")
    if not allowed:
        # No explicit age statement in the source → do not invent one.
        return True
    return asserted not in allowed


_CURRENT_GRADE_HEADER_RE = re.compile(
    r"\bGrade\s*:\s*0*(\d{1,2})\b|"
    r"\bGrade\s*:\s*(K|KG|Kindergarten)\b|"
    r"\bcurrent(?:ly)?\s+(?:in\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s*grade\b",
    re.IGNORECASE,
)
_FUTURE_GRADE_CONTEXT_RE = re.compile(
    r"\b("
    r"\d{1,2}(?:st|nd|rd|th)?\s*grade\s+semester|"
    r"courses?\s+for\s+the\s+remainder|"
    r"graduation|credit[- ]?plan|course\s+(?:of\s+study|sequence|plan)|"
    r"\d+\s*credits?\s+of|"
    r"remainder\s+of\s+high\s+school"
    r")\b",
    re.IGNORECASE,
)


def _current_grades_in_text(text: str) -> set[str]:
    grades: set[str] = set()
    for match in _CURRENT_GRADE_HEADER_RE.finditer(text or ""):
        for group in match.groups():
            if group is None:
                continue
            token = group.strip()
            if token.lower() in {"k", "kg", "kindergarten"}:
                grades.add("K")
            else:
                grades.add(str(int(token)))
    return grades


def _is_spurious_grade(draft: ExtractedFactDraft, source: Source) -> bool:
    """
    Drop future-track / graduation-plan grades that are not current placement.

    doc_11 lists \"10th Grade Semester 1\" in a course plan while current Grade is 9.
    When the source declares ``Grade: N``, only that placement is allowed.
    """

    value = normalize_value("grade", draft.value or "", draft.value_text or "")
    value_text = (draft.value_text or "").strip()
    blob = f"{value_text} {draft.value or ''}"
    content = source.content or ""

    current = _current_grades_in_text(content)
    if current:
        # Explicit Grade: header(s) are authoritative for this source.
        if value not in current:
            return True
        return False

    future_local = bool(_FUTURE_GRADE_CONTEXT_RE.search(blob))
    if future_local and not _CURRENT_GRADE_HEADER_RE.search(blob):
        return True
    return False


_IEP_INCIDENTAL_RE = re.compile(
    r"school[- ]based\s+eligibility|"
    r"only\s+indicate\s+school",
    re.IGNORECASE,
)
_IEP_TEMPLATE_DENIAL_RE = re.compile(
    r"not\s+eligible\s+for\s+special\s+education|"
    r"i\s+understand\s+that\s+my\s+child\s+is\s+not\s+eligible",
    re.IGNORECASE,
)
_IEP_REAL_STATUS_RE = re.compile(
    r"\b("
    r"meets?\s+eligibility|eligible\s+under|eligibility\s+criteria|"
    r"IEP\s+is\s+in\s+place|no\s+(?:prior\s+)?IEP\s+(?:documented|in\s+place)|"
    r"offer\s+of\s+FAPE|specialized\s+academic\s+instruction|"
    r"initial\s+IEP|IEP\s+team\s+(?:determined|concluded|recommended|discussed)|"
    r"primary\s*:\s*\w+"
    r")\b",
    re.IGNORECASE,
)
_IEP_PRIMARY_FILLED_RE = re.compile(
    r"Primary\s*:\s*(?!None\b)([A-Za-z][\w\s/()-]{1,60})",
    re.IGNORECASE,
)
_IEP_NARRATIVE_DENIAL_RE = re.compile(
    r"\b("
    r"no\s+(?:prior\s+)?IEP\s+(?:documented|in\s+place|on\s+file)|"
    r"team\s+(?:found|determined|concluded).{0,60}not\s+eligible|"
    r"not\s+in\s+place"
    r")\b",
    re.IGNORECASE,
)
_IEP_SIGNATURE_CHECKBOX_RE = re.compile(
    r"i\s+understand\s+that\s+my\s+child\s+is\s+(?:not\s+eligible|no\s+longer\s+eligible)",
    re.IGNORECASE,
)


def _is_spurious_iep_status(draft: ExtractedFactDraft, source: Source) -> bool:
    """
    Drop template-checkbox denials and incidental IEP cross-references.

    doc_11 \"Not Eligible\" beside a filled Primary disability is a blank option.
    Rebuttal-style letters may mention school-based eligibility while arguing ASD —
    that is not an IEP status determination.
    """

    value_text = (draft.value_text or "").strip()
    value = (draft.value or "").strip()
    blob = f"{value_text} {value}"
    assertion = draft.assertion if draft.assertion in ("asserted", "denied") else "asserted"
    content = source.content or ""

    # Incidental mention without a real status determination in the claim.
    if _IEP_INCIDENTAL_RE.search(blob) and not _IEP_REAL_STATUS_RE.search(blob):
        return True
    if not _IEP_REAL_STATUS_RE.search(content) and not re.search(
        r"\bIEP\b|\beligib", content, re.IGNORECASE
    ):
        return True
    # Soft incidental: source only mentions eligibility in passing (rebuttal letters).
    # Drop all iep_status from such sources — do not trust invented determination wording.
    if _IEP_INCIDENTAL_RE.search(content) and not re.search(
        r"\b(meets?\s+eligibility|offer\s+of\s+FAPE|primary\s*:\s*\w+|"
        r"IEP\s+is\s+in\s+place|specialized\s+academic\s+instruction)\b",
        content,
        re.IGNORECASE,
    ):
        return True

    # Unfilled template checkbox denial next to an affirmative eligibility.
    normalized = normalize_value("iep_status", value, value_text)
    is_denial = assertion == "denied" or normalized == "none"
    if is_denial and _IEP_SIGNATURE_CHECKBOX_RE.search(blob):
        return True
    has_affirmative_eligibility = bool(
        _IEP_PRIMARY_FILLED_RE.search(content)
        and re.search(
            r"Offer of FAPE|Specialized Academic Instruction|meets\s+eligibility|"
            r"IEP\s+team\s+consented|eligible\s+under",
            content,
            re.IGNORECASE,
        )
    )
    if is_denial and has_affirmative_eligibility:
        # Active IEP packet: denials are form options, not findings.
        return True
    if is_denial and _IEP_PRIMARY_FILLED_RE.search(content) and _IEP_TEMPLATE_DENIAL_RE.search(
        content
    ):
        # Keep only an explicit narrative denial — not the blank form option.
        if not _IEP_NARRATIVE_DENIAL_RE.search(value_text):
            return True
    if is_denial and _IEP_TEMPLATE_DENIAL_RE.search(blob):
        if re.search(
            r"Primary\s*:\s*\w+|meets\s+eligibility|Offer of FAPE|"
            r"Specialized Academic Instruction",
            content,
            re.IGNORECASE,
        ):
            if not _IEP_NARRATIVE_DENIAL_RE.search(value_text):
                return True
    return False


_ATTENDANCE_BOILERPLATE_RE = re.compile(
    r"vocational\s+skills\s+include|"
    r"skills\s+include\s*:?\s*[^.]*attendance|"
    r"attendance\s*/\s*punctuality|"
    r"where\s+student\s+is\s+in\s+attendance|"
    r"school\s+of\s+attendance\s*:",
    re.IGNORECASE,
)
_ATTENDANCE_REAL_RE = re.compile(
    r"\b("
    r"attendance\s+(?:seems|is|was|has\s+been|appears)|"
    r"(?:good|poor|irregular|inconsistent|excellent)\s+attendance|"
    r"absences?|truant|attendance\s+record|misses?\s+(?:school|class)"
    r")\b",
    re.IGNORECASE,
)


def _is_spurious_attendance(draft: ExtractedFactDraft) -> bool:
    """Drop vocational-skill list / calendar boilerplate posing as attendance status."""

    value_text = (draft.value_text or "").strip()
    blob = f"{value_text} {draft.value or ''}"
    if _ATTENDANCE_BOILERPLATE_RE.search(blob) and not _ATTENDANCE_REAL_RE.search(blob):
        return True
    # Invented "regular" from non-attendance uses of the word.
    value = normalize_value("attendance", draft.value or "", value_text)
    if value == "regular" and not re.search(
        r"attendance.{0,40}regular|regular.{0,40}attendance", blob, re.IGNORECASE
    ):
        if not _ATTENDANCE_REAL_RE.search(blob):
            return True
    return False


def _is_garbage_dob(draft: ExtractedFactDraft, source: Source) -> bool:
    """
    Drop DOB drafts minted from blank template fields or bare document dates.

    Placeholders like ``__________`` and a source's own contract/IEP date without
    a DOB/born anchor are not birth dates (fixture_001 doc_22 finding).
    """

    value = (draft.value or "").strip()
    value_text = (draft.value_text or "").strip()
    blob = f"{value} {value_text}"

    if not value or _DOB_PLACEHOLDER_RE.match(value) or "___" in value or "___" in value_text:
        return True
    if re.search(r"_{3,}|X{3,}|\.{3,}", blob):
        return True

    has_anchor = bool(_DOB_ANCHOR_RE.search(blob))
    # Bare ISO equal to the document date with no DOB language → document date, not DOB.
    if _ISO_DATE_RE.match(value) and value == source.date and not has_anchor:
        return True
    # "Date of birth:" with empty / placeholder fill and no real date tokens.
    if re.search(r"date of birth\s*:?\s*$", value_text, re.IGNORECASE) and not re.search(
        r"\d{4}|\d{1,2}[/-]\d{1,2}", value
    ):
        return True
    if not has_anchor and not re.search(r"\d", value):
        return True
    return False


def draft_to_fact(
    draft: ExtractedFactDraft,
    *,
    fact_id: str,
    source: Source,
    child: Child,
) -> Fact:
    del child  # Subject no longer needs child.name for canonicalization.
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
        value_text=clip_value_text(draft.value_text or value),
        qualifier=qualifier,
        assertion=_finalize_assertion(draft, predicate=predicate, value=value),
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


def extract_source_to_facts(
    provider: ModelProvider,
    *,
    child: Child,
    source: Source,
    model: str,
    chunk_limit: int = EXTRACT_CHUNK_CHAR_LIMIT,
) -> tuple[list[Fact], int, int, int]:
    """
    Extract one source to finalized Fact rows.

    Oversized narrative content is chunked; per-chunk drafts are finalized,
    de-duplicated (subject+predicate+qualifier+value+source_id), and renumbered.
    """

    chunks = split_source_content(source.content, limit=chunk_limit)
    drafts: list[ExtractedFactDraft] = []
    total_tokens = prompt_tokens = completion_tokens = 0

    for chunk_text in chunks:
        chunk_source = source if len(chunks) == 1 else source.model_copy(update={"content": chunk_text})
        chunk_drafts, total, p_tok, c_tok = extract_source_facts(
            provider, child=child, source=chunk_source, model=model
        )
        drafts.extend(chunk_drafts)
        total_tokens += total
        prompt_tokens += p_tok
        completion_tokens += c_tok

    facts: list[Fact] = []
    for draft in drafts:
        if _draft_is_skippable(draft, source):
            continue
        try:
            facts.append(
                draft_to_fact(
                    draft,
                    fact_id=fact_id_for_source(source.id, len(facts) + 1),
                    source=source,
                    child=child,
                )
            )
        except ValueError:
            # One bad draft must not drop the rest of the source.
            continue
    facts = dedupe_facts(facts)
    facts = [
        f.model_copy(update={"id": fact_id_for_source(source.id, i)})
        for i, f in enumerate(facts, start=1)
    ]
    return facts, total_tokens, prompt_tokens, completion_tokens


def merge_ledger_with_extracted(
    *,
    child: Child,
    prior: Ledger | None,
    new_sources: list[Source],
    new_facts_by_source: dict[str, list[Fact]],
) -> Ledger:
    """
    Merge newly extracted facts into a prior ledger (or assemble from scratch).

    Merge is keyed on source_id: re-submitting a source replaces that source's
    prior facts and source row; other sources are untouched. Derived / request
    rows are stripped and recomputed against child.evaluation_date.
    """

    replace_ids = {s.id for s in new_sources}
    prior_sources = list(prior.sources) if prior else []
    prior_facts = strip_synthetic_facts(list(prior.facts)) if prior else []

    kept_sources = [s for s in prior_sources if s.id not in replace_ids]
    sources = kept_sources + list(new_sources)

    kept_facts = [
        f
        for f in prior_facts
        if f.source_id not in replace_ids and not is_synthetic_source_id(f.source_id)
    ]
    used_ids = {f.id for f in kept_facts}

    merged_new: list[Fact] = []
    for source in new_sources:
        for fact in new_facts_by_source.get(source.id, []):
            if fact.id in used_ids:
                raise ValueError(f"Fact id collision on merge: {fact.id!r}")
            if fact.source_id != source.id:
                raise ValueError(
                    f"Fact {fact.id!r} source_id={fact.source_id!r} "
                    f"does not match source {source.id!r}"
                )
            used_ids.add(fact.id)
            merged_new.append(fact)

    facts, _ = inject_derived_and_request_facts(kept_facts + merged_new, child, next_id=1)
    return Ledger(
        child=child,
        ledger_version=LEDGER_VERSION,
        built_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sources=sources,
        facts=facts,
    )


def build_ledger(
    provider: ModelProvider,
    *,
    child: Child,
    sources: list[Source],
    model: str,
    prior_ledger: Ledger | None = None,
) -> tuple[Ledger, dict[str, int], int, int, list[str], list[str], GapReport, list[Timeline]]:
    """
    Extract facts from each source independently and assemble / merge a Ledger.

    When ``prior_ledger`` is set, new sources are extracted and merged into it
    (replace by source_id). When omitted, builds from scratch (batch / demo path).

    Injects request-time dob + derived age_years after extraction / merge.
    Timelines are a computed view — not stored on the ledger.
    Returns (ledger, tokens_by_source, prompt_tokens, completion_tokens,
             predicates_for_review, subjects_for_review, gap_report, timelines).
    """

    facts_by_source: dict[str, list[Fact]] = {}
    tokens_by_source: dict[str, int] = {}
    prompt_tokens = completion_tokens = 0
    review: list[str] = []
    subject_review: list[str] = []

    known_source_ids = {s.id for s in sources}
    if prior_ledger is not None:
        known_source_ids |= {s.id for s in prior_ledger.sources}

    for source in sources:
        # Score reports are deferred to Assessment Results (§6.4 / Phase 3).
        # Record the source for coverage; produce no narrative history facts.
        if source.doc_class == "score_report":
            tokens_by_source[source.id] = 0
            facts_by_source[source.id] = []
            continue

        source_facts, total, p_tok, c_tok = extract_source_to_facts(
            provider, child=child, source=source, model=model
        )
        tokens_by_source[source.id] = total
        prompt_tokens += p_tok
        completion_tokens += c_tok

        for fact in source_facts:
            if needs_predicate_review(fact.predicate) and fact.predicate not in review:
                review.append(fact.predicate)
            if (
                needs_subject_review(fact.subject, known_source_ids=known_source_ids)
                and fact.subject not in subject_review
            ):
                subject_review.append(fact.subject)
        facts_by_source[source.id] = source_facts

    ledger = merge_ledger_with_extracted(
        child=child,
        prior=prior_ledger,
        new_sources=sources,
        new_facts_by_source=facts_by_source,
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
