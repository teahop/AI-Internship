"""Agent tools: two local (ledger + source text) and two HTTP (week-1 conflicts/draft).

Tool errors are returned as dict observations — never raised to the Runner.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ledger_store import get_ledger
from dating import (
    annotate_quote,
    as_of_trap_notes,
    citation_line,
    needs_age_disclosure,
    newest_date,
)
from query_expand import expand_query
from retrieval import get_index, search
from vocabulary import REGISTERED_PREDICATES

WEEK1_BASE_URL = os.getenv("WEEK1_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
HTTP_TIMEOUT_S = float(os.getenv("WEEK1_HTTP_TIMEOUT_S", "120"))

_MAX_HITS_PER_SOURCE = 2
_MAX_SOURCES = 8

# Hits that look like they name a clinician/provider (for dating disclosure scope).
_PROVIDER_MARKERS = (
    "primary care",
    "pediatrician",
    "pcp",
    "care provider",
    "care physician",
    "referring physician",
    ", md",
    " md,",
    "m.d.",
)


def _provider_support_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Passages that appear to name a provider — used for newest-date disclosure."""

    out: list[dict[str, Any]] = []
    for h in hits:
        blob = " ".join(str(q) for q in (h.get("quotes") or [])).lower()
        if any(m in blob for m in _PROVIDER_MARKERS):
            out.append(h)
    return out


def _week1_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.ConnectError):
        return {
            "ok": False,
            "error": "connection_refused",
            "detail": f"Cannot reach week-1 at {WEEK1_BASE_URL}. Is uvicorn running?",
        }
    if isinstance(exc, httpx.TimeoutException):
        return {"ok": False, "error": "timeout", "detail": str(exc)}
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "")[:500]
        return {
            "ok": False,
            "error": f"http_{exc.response.status_code}",
            "detail": body,
        }
    return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}


def search_ledger(predicate: str, subject: str = "child") -> dict:
    """Search the cached verified ledger for facts matching predicate + subject.

    Returns fact_id, source_id, value, value_text, assertion, as_of_date,
    source_date, and reporter. Empty result means no matching facts — never a
    fabricated placeholder. Unknown predicates are reported distinctly from
    'no facts found'.
    """

    try:
        ledger = get_ledger()
    except FileNotFoundError as exc:
        return {"ok": False, "error": "ledger_missing", "detail": str(exc)}

    pred = (predicate or "").strip()
    subj = (subject or "child").strip() or "child"

    if pred not in REGISTERED_PREDICATES:
        return {
            "ok": True,
            "vocabulary_status": "unregistered_predicate",
            "predicate": pred,
            "subject": subj,
            "message": (
                f"'{pred}' is not in the Background & History vocabulary. "
                "This is a vocabulary gap, not a silent ledger. "
                "Tier 1 cannot answer; use search_source_text for raw quotes."
            ),
            "facts": [],
            "count": 0,
        }

    facts = [
        {
            "fact_id": f.get("id"),
            "source_id": f.get("source_id"),
            "predicate": f.get("predicate"),
            "subject": f.get("subject"),
            "qualifier": f.get("qualifier"),
            "value": f.get("value"),
            "value_text": f.get("value_text"),
            "assertion": f.get("assertion"),
            "as_of_date": f.get("as_of_date"),
            "source_date": f.get("source_date"),
            "reporter": f.get("reporter"),
            "temporality": f.get("temporality"),
            "confidence": f.get("confidence"),
        }
        for f in ledger.get("facts", [])
        if f.get("predicate") == pred and f.get("subject") == subj
    ]

    return {
        "ok": True,
        "vocabulary_status": "registered",
        "predicate": pred,
        "subject": subj,
        "facts": facts,
        "count": len(facts),
        "message": (
            "No facts on the ledger for this predicate/subject."
            if not facts
            else f"Found {len(facts)} ledger fact(s)."
        ),
    }


def search_source_text(query: str) -> dict:
    """Search raw Source.content for verbatim passages. TIER 2 — NOT VERIFIED.

    HARD RULES (non-negotiable):
    - Return verbatim quotes only. NEVER summarize.
    - NEVER merge claims across sources — one entry (or more) per source,
      each attributed separately.
    - Results have NOT passed the reliability layer. Label them as unverified.

    Ranking: shared FTS5 passage index + optional expand_query (QUERY_EXPANSION).
    """

    q = (query or "").strip()
    if not q:
        return {
            "ok": True,
            "verified": False,
            "marker": "not checked against the reliability layer",
            "query": q,
            "hits": [],
            "count": 0,
            "message": "Empty query.",
        }

    try:
        expansions = expand_query(q)
        passages = search(
            get_index(),
            q,
            expansions=expansions,
            limit=_MAX_SOURCES,
            per_source_limit=_MAX_HITS_PER_SOURCE,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": "ledger_missing", "detail": str(exc)}

    # One hit entry per source; quotes stay verbatim with a citation line prefix.
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for p in passages:
        sid = p["source_id"]
        if sid not in grouped:
            grouped[sid] = {
                "source_id": sid,
                "label": p.get("label"),
                "date": p.get("date"),
                "doc_class": p.get("doc_class"),
                "citation": citation_line(sid, p.get("date")),
                "quotes": [],
                "verified": False,
                "marker": "not checked against the reliability layer",
            }
            order.append(sid)
        grouped[sid]["quotes"].append(
            annotate_quote(
                p["quote"],
                source_id=sid,
                date=p.get("date"),
            )
        )

    hits = [grouped[sid] for sid in order]
    provider_hits = _provider_support_hits(hits)
    dating_hits = provider_hits or hits
    newest = newest_date([h.get("date") for h in dating_hits])
    # Disclose when every provider-support hit is old (no recent naming passage).
    all_old = bool(dating_hits) and all(
        needs_age_disclosure(h.get("date")) for h in dating_hits
    )
    traps = as_of_trap_notes(hits)

    return {
        "ok": True,
        "verified": False,
        "marker": "not checked against the reliability layer",
        "query": q,
        "expansions": expansions,
        "hits": hits,
        "count": len(hits),
        "newest_supporting_date": newest,
        "age_disclosure_required": all_old,
        "as_of_traps": traps,
        "message": (
            "No matching source text."
            if not hits
            else f"Found quotes in {len(hits)} source(s). Quotes only — not verified."
        ),
    }


def check_conflicts() -> dict:
    """POST the cached ledger to week-1 /conflicts (deterministic, no model cost).

    Returns conflicts (record — a source is wrong), variance (perspectival —
    informants legitimately differ), and timelines SEPARATELY. Do not collapse
    variance into a single value.
    """

    try:
        ledger = get_ledger()
    except FileNotFoundError as exc:
        return {"ok": False, "error": "ledger_missing", "detail": str(exc)}

    payload = {"confirm_synthetic": True, "ledger": ledger}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
            resp = client.post(f"{WEEK1_BASE_URL}/conflicts", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 — return as observation
        return _week1_error(exc)

    return {
        "ok": True,
        "conflicts": data.get("conflicts", []),
        "variance": data.get("variance", []),
        "timelines": data.get("timelines", []),
        "predicates_for_review": data.get("predicates_for_review", []),
        "subjects_for_review": data.get("subjects_for_review", []),
        "message": (
            "conflicts = record disagreement (surface, do not resolve). "
            "variance = perspectival disagreement (present as comparison)."
        ),
    }


def draft_section(section: str = "history") -> dict:
    """POST settled ledger + conflicts to week-1 /draft. Use only when facts look settled.

    Calls /conflicts first (deterministic), then /draft. Expensive — model calls
    on week-1. Prefer search tools for lookup questions.
    """

    try:
        ledger = get_ledger()
    except FileNotFoundError as exc:
        return {"ok": False, "error": "ledger_missing", "detail": str(exc)}

    sec = (section or "history").strip() or "history"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
            c_resp = client.post(
                f"{WEEK1_BASE_URL}/conflicts",
                json={"confirm_synthetic": True, "ledger": ledger},
            )
            c_resp.raise_for_status()
            c_data = c_resp.json()

            d_resp = client.post(
                f"{WEEK1_BASE_URL}/draft",
                json={
                    "confirm_synthetic": True,
                    "section": sec,
                    "ledger": ledger,
                    "conflicts": c_data.get("conflicts", []),
                    "variance": c_data.get("variance", []),
                },
            )
            d_resp.raise_for_status()
            d_data = d_resp.json()
    except Exception as exc:  # noqa: BLE001
        return _week1_error(exc)

    answer = d_data.get("answer")
    return {
        "ok": True,
        "section_populated": d_data.get("section_populated"),
        "empty_reason": d_data.get("empty_reason"),
        "prose": (answer or {}).get("prose") if answer else None,
        "review_items": (d_data.get("review") or {}).get("items", []),
        "tokens_used": d_data.get("tokens_used"),
        "cost_usd": d_data.get("cost_usd"),
        "message": "Draft returned. Clinician still reviews and signs.",
    }

