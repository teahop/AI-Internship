"""Source-date presentation helpers for Tier 2 / Tier 3 (Part 5).

Shows source.date and the gap to today. Does not decide staleness validity
(§9.6) — disclosure only when support is older than DISCLOSURE_YEARS.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# Disclosure trigger only (not a validity rule). Molly still judges §9.6.
DISCLOSURE_YEARS = 2


def parse_source_date(raw: str | None) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def age_gap_label(raw: str | None, *, today: date | None = None) -> str:
    """Human gap, e.g. '12y 10m ago'. Unknown dates → 'date unknown'."""

    today = today or date.today()
    d = parse_source_date(raw)
    if d is None:
        return "date unknown"
    if d > today:
        return "dated in the future"

    months = (today.year - d.year) * 12 + (today.month - d.month)
    if today.day < d.day:
        months -= 1
    if months < 0:
        months = 0
    years, rem = divmod(months, 12)
    if years <= 0 and rem <= 0:
        return "today"
    if years <= 0:
        return f"{rem}m ago"
    if rem <= 0:
        return f"{years}y ago"
    return f"{years}y {rem}m ago"


def citation_line(
    source_id: str | None,
    raw_date: str | None,
    *,
    today: date | None = None,
) -> str:
    """doc_26 · 2013-09-10 · 12y 10m ago"""

    sid = source_id or "unknown_source"
    d = (raw_date or "").strip() or "undated"
    gap = age_gap_label(raw_date, today=today)
    return f"{sid} · {d} · {gap}"


def years_ago(raw: str | None, *, today: date | None = None) -> float | None:
    today = today or date.today()
    d = parse_source_date(raw)
    if d is None:
        return None
    return (today - d).days / 365.25


def needs_age_disclosure(raw: str | None, *, today: date | None = None) -> bool:
    """True when the only/newest support is older than DISCLOSURE_YEARS."""

    y = years_ago(raw, today=today)
    return y is not None and y >= DISCLOSURE_YEARS


def annotate_quote(
    quote: str,
    *,
    source_id: str | None,
    date: str | None,
    today: date | None = None,
) -> str:
    """Prefix a verbatim passage with its citation line (does not alter the quote body)."""

    cite = citation_line(source_id, date, today=today)
    body = (quote or "").strip()
    return f"{cite}\n{body}" if body else cite


def newest_date(dates: list[str | None]) -> str | None:
    parsed = [(parse_source_date(d), d) for d in dates if d]
    parsed = [(p, raw) for p, raw in parsed if p is not None]
    if not parsed:
        return None
    parsed.sort(key=lambda x: x[0], reverse=True)
    return str(parsed[0][1]).strip()[:10]


def as_of_trap_notes(hits: list[dict[str, Any]]) -> list[str]:
    """Flag known source.date vs in-text date mismatches (doc_11 copies 2019 into 2024).

    Detection only — as_of anchoring is week-1 work; do not rewrite dates here.
    """

    notes: list[str] = []
    for h in hits:
        sid = str(h.get("source_id") or "")
        src_date = str(h.get("date") or "")
        quotes = h.get("quotes") or []
        if isinstance(quotes, str):
            quotes = [quotes]
        blob = "\n".join(str(q) for q in quotes)
        if sid == "doc_11" and src_date.startswith("2024"):
            # Copied Fairhaven / 2018–2019 health block signals.
            if re.search(r"7/2018|2019|guanfacine|Singular|melatonin|den\s*st", blob, re.I):
                notes.append(
                    "as_of_trap: doc_11 source.date=2024-10-02 but quote content "
                    "looks like copied 2018–2019 health text (week-1 as_of anchoring; "
                    "not fixed here)."
                )
        # Generic: source year is recent but quote mentions a year ≥3 earlier
        src_y = parse_source_date(src_date)
        if src_y and quotes:
            for m in re.finditer(r"\b(20\d{2})\b", blob):
                y = int(m.group(1))
                if src_y.year - y >= 3:
                    notes.append(
                        f"as_of_trap: {sid} source.date={src_date} but quote mentions {y} "
                        f"(possible copied-forward block; week-1 as_of anchoring)."
                    )
                    break
    # Dedup
    seen: set[str] = set()
    out: list[str] = []
    for n in notes:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out
