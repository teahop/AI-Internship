"""Normalize Fact.value at extraction write time so Stage 3 comparison stays trivial.

Rules of thumb:
- Durations/ages in months or years → integer strings ("13", "9")
- Grades → canonical token ("K", "1", "2", …) — never derived from age
- Status / classification strings stay distinct (e.g. undiagnosed ≠ known)
- Prefer the model's value when already normalized; fall back to value_text
"""

from __future__ import annotations

import re

_WORD_NUMBERS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
    "thirty-six": 36,
}

_ORDINAL_GRADE = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "1st": "1",
    "2nd": "2",
    "3rd": "3",
    "4th": "4",
    "5th": "5",
    "6th": "6",
    "7th": "7",
    "8th": "8",
    "9th": "9",
    "10th": "10",
    "11th": "11",
    "12th": "12",
}

# Predicates whose values are month-ages → integer months.
_MONTH_AGE_PREDICATES = frozenset(
    {
        "walked_age_months",
        "first_words_age_months",
        "two_word_phrases_age_months",
    }
)

# Predicates whose values are whole-year ages → integer years.
_YEAR_AGE_PREDICATES = frozenset({"age_years"})

# Binary / short status predicates — lowercase, compress whitespace, keep meaning.
_STATUS_PREDICATES = frozenset(
    {
        "birth_term",
        "birth_delivery",
        "nicu",
        "allergy_status",
        "health_plan_status",
        "iep_status",
        "plan_504_status",
        "special_education_eligibility",
        "attendance",
        "private_tutoring",
        "epinephrine_available",
        "medications",
        "hospitalizations",
        "behavioral_referral",
        "intervention_tier",
    }
)


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _word_to_int(token: str) -> int | None:
    return _WORD_NUMBERS.get(token.lower().replace(" ", "-"))


def _extract_int(text: str) -> int | None:
    text = text.lower().strip()
    if text.isdigit():
        return int(text)
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    # "thirteen months" / "thirteen"
    for word, num in sorted(_WORD_NUMBERS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", text):
            return num
    return None


def _normalize_month_age(raw: str) -> str:
    n = _extract_int(raw)
    return str(n) if n is not None else _collapse_ws(raw)


def _normalize_year_age(raw: str) -> str:
    n = _extract_int(raw)
    return str(n) if n is not None else _collapse_ws(raw)


def _normalize_grade(raw: str) -> str:
    text = raw.lower().strip()
    if text in {"k", "kg", "kindergarten", "kinder"}:
        return "K"
    for key, val in _ORDINAL_GRADE.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            return val
    m = re.search(r"\b(?:grade\s*)?(\d{1,2})\b", text)
    if m:
        return str(int(m.group(1)))
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text)
    if m:
        return str(int(m.group(1)))
    return _collapse_ws(raw)


def _normalize_name(raw: str) -> str:
    # Keep distinctive tokens; collapse whitespace; title-case lightly.
    text = _collapse_ws(raw)
    # Strip labels like "Student name on header:" if the model leaked them into value.
    text = re.sub(
        r"^(?:student\s+name(?:\s+on\s+header)?|name|iep\s+body\s+student\s+name)\s*:?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return _collapse_ws(text)


def _normalize_status(predicate: str, raw: str) -> str:
    text = _collapse_ws(raw).lower()
    text = text.replace("_", " ")

    if predicate == "birth_term":
        if "full" in text and "term" in text:
            return "full-term"
        if "preterm" in text or "pre-term" in text or "premature" in text:
            return "preterm"
        m = re.search(r"\b(\d{2})\s*-?\s*weeks?\b", text)
        if m:
            return f"{m.group(1)}-weeks"

    if predicate == "nicu":
        if re.search(r"\b(no|none|without|denied)\b", text) and "nicu" in text:
            return "none"
        if "nicu" in text:
            return "yes"
        if text in {"none", "no", "n/a", "na"}:
            return "none"

    if predicate == "allergy_status":
        # Keep known vs undiagnosed distinct — do not collapse.
        if "undiagnosed" in text:
            return "undiagnosed"
        if re.search(r"\bno\s+(known\s+)?allerg", text) or text in {
            "none",
            "no",
            "no known allergies",
            "nkda",
        }:
            return "none"
        if "known" in text or re.search(r"\ballerg", text):
            return "known"
        return text

    if predicate == "health_plan_status":
        if "draft" in text or "emailed" in text or "not yet returned" in text:
            return "draft"
        if "active" in text or "on file" in text or "on-file" in text:
            return "active"
        if text in {"none", "no", "n/a"}:
            return "none"
        return text

    if predicate in {"iep_status", "plan_504_status"}:
        if re.search(r"\b(no|none|not\s+in\s+place|without)\b", text):
            return "none"
        if "active" in text or "in place" in text or "on file" in text:
            return "active"
        return text

    if predicate == "private_tutoring":
        if re.search(r"\b(no|none|not|denie)\b", text):
            return "none"
        if "tutor" in text or text in {"yes", "true"}:
            return "yes"
        return text

    if predicate == "epinephrine_available":
        if re.search(r"\b(no|none)\b", text):
            return "none"
        if "health office" in text or "kept" in text or text in {"yes", "true"}:
            return "yes"
        return text

    if predicate == "special_education_eligibility":
        if re.search(r"\b(no|none|not)\b", text):
            return "none"
        return text

    if predicate == "attendance":
        if "regular" in text:
            return "regular"
        return text

    if predicate == "intervention_tier":
        m = re.search(r"\btier\s*([123])\b", text)
        if m:
            return f"tier-{m.group(1)}"
        return text

    if predicate == "medications":
        if re.search(r"\b(no|none|not)\b", text):
            return "none"
        return text

    if predicate == "hospitalizations":
        if re.search(r"\b(no|none)\b", text):
            return "none"
        return text

    if predicate == "birth_delivery":
        if "vaginal" in text:
            return "vaginal"
        if "cesarean" in text or "c-section" in text or "c section" in text:
            return "cesarean"
        return text

    if predicate == "behavioral_referral":
        if re.search(r"\b(no|none)\b", text):
            return "none"
        return text

    return text


def normalize_qualifier(qualifier: str | None) -> str | None:
    """Normalize qualifier tokens for stable grouping (peanuts, dairy, …)."""

    if qualifier is None:
        return None
    text = _collapse_ws(qualifier).lower()
    if not text:
        return None
    # Light singularization for common allergen plurals.
    if text.endswith("s") and text not in {"asthma", "eczema"}:
        # peanuts → peanut; keep multi-word as-is aside from whitespace.
        parts = text.split()
        if len(parts) == 1 and len(parts[0]) > 3:
            text = parts[0][:-1] if parts[0].endswith("s") else parts[0]
    return text


def normalize_value(predicate: str, value: str, value_text: str = "") -> str:
    """
    Produce the comparison value for a fact.

    Uses `value` first; if empty, falls back to `value_text`.
    """

    raw = (value or "").strip() or (value_text or "").strip()
    if not raw:
        return ""

    if predicate in _MONTH_AGE_PREDICATES:
        return _normalize_month_age(raw)
    if predicate in _YEAR_AGE_PREDICATES:
        return _normalize_year_age(raw)
    if predicate == "grade" or predicate == "retention_year":
        # retention_year may be "2" (grade retained) or a school year — prefer grade token.
        return _normalize_grade(raw)
    if predicate == "legal_name":
        return _normalize_name(raw)
    if predicate == "dob":
        # Prefer ISO YYYY-MM-DD when present.
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", raw)
        if m:
            return m.group(1)
        return _collapse_ws(raw)
    if predicate == "allergy_substance":
        text = _collapse_ws(raw).lower()
        text = re.sub(r"\b(allerg(?:y|ies)|to|known)\b", "", text)
        return _collapse_ws(text)
    if predicate in _STATUS_PREDICATES:
        return _normalize_status(predicate, raw)

    # Rating / free-text: lowercase + collapse; keep numeric scores if present.
    text = _collapse_ws(raw).lower()
    m = re.search(r"\b(\d+)\s*(?:of|/)\s*(\d+)\b", text)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return text
