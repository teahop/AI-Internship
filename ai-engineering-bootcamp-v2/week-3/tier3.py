"""Tier 3 — one model call with retrieved case-file passages as context.

No ledger, no quote-only constraint: the model may summarize. Clearly marked
as unverified. Context is packed from the shared FTS5 passage retriever (same
index as Tier 2) so divergence logs measure architecture, not tokenizer drift.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from costs import usage_from_metadata
from dating import (
    as_of_trap_notes,
    citation_line,
    needs_age_disclosure,
    newest_date,
)
from query_expand import expand_query
from retrieval import get_index, search

load_dotenv()

MODEL = os.getenv("TIER3_MODEL", "gemini-3.6-flash")
# Passage budgets replace the old char caps (MAX_CONTEXT_CHARS / MAX_PER_SOURCE_CHARS).
MAX_PASSAGES = int(os.getenv("TIER3_MAX_PASSAGES", "12"))
MAX_PER_SOURCE = int(os.getenv("TIER3_MAX_PER_SOURCE", "3"))

# Measured on fixture_001 with the old whole-document TF packer (~120k char budget).
_LEGACY_WHOLE_DOC_PROMPT_TOKENS = 20_252

_MARKER = (
    "Unstructured model answer over raw case files — "
    "NOT checked against the reliability layer. May summarize, omit, or flatten."
)


def _est_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for pre-call logging."""

    return max(0, (len(text) + 3) // 4)


def _build_context(question: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Pack top-ranked passages, grouped under source headers for provenance."""

    expansions = expand_query(question)
    passages = search(
        get_index(),
        question,
        expansions=expansions,
        limit=MAX_PASSAGES,
        per_source_limit=MAX_PER_SOURCE,
    )

    # Preserve BM25 order while grouping consecutive quotes under one header.
    by_source: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for p in passages:
        sid = str(p.get("source_id") or "")
        if sid not in by_source:
            by_source[sid] = []
            order.append(sid)
        by_source[sid].append(p)

    parts: list[str] = []
    used: list[dict[str, Any]] = []
    for sid in order:
        group = by_source[sid]
        head = group[0]
        cite = citation_line(sid, head.get("date"))
        header = (
            f"--- SOURCE {cite} | {head.get('label')} | "
            f"{head.get('doc_class')} ---\n"
        )
        bodies: list[str] = []
        for p in group:
            body = (p.get("quote") or "").strip()
            if not body:
                continue
            # Per-passage citation so age gap survives even inside a multi-quote block.
            bodies.append(f"[{citation_line(sid, p.get('date'))}]\n{body}")
        if not bodies:
            continue
        block = header + "\n\n".join(bodies) + "\n\n"
        parts.append(block)
        for p in group:
            used.append(
                {
                    "source_id": p.get("source_id"),
                    "label": p.get("label"),
                    "date": p.get("date"),
                    "citation": citation_line(p.get("source_id"), p.get("date")),
                    "offset": p.get("offset"),
                    "score": p.get("score"),
                    "chars_included": len((p.get("quote") or "")),
                }
            )

    context = "".join(parts)
    trap_hits = [
        {
            "source_id": p.get("source_id"),
            "date": p.get("date"),
            "quotes": [p.get("quote") or ""],
        }
        for p in passages
    ]
    traps = as_of_trap_notes(trap_hits)

    provider_markers = (
        "primary care",
        "pediatrician",
        "pcp",
        "care provider",
        "care physician",
        ", md",
        " md",
    )
    provider_passages = [
        p
        for p in passages
        if any(m in (p.get("quote") or "").lower() for m in provider_markers)
    ]
    dating_passages = provider_passages or passages
    newest = newest_date([p.get("date") for p in dating_passages])
    all_old = bool(dating_passages) and all(
        needs_age_disclosure(p.get("date")) for p in dating_passages
    )

    stats = {
        "expansions": expansions,
        "passage_count": len(used),
        "source_count": len(order),
        "context_chars": len(context),
        "est_prompt_tokens": _est_tokens(context),
        "legacy_whole_doc_prompt_tokens": _LEGACY_WHOLE_DOC_PROMPT_TOKENS,
        "newest_supporting_date": newest,
        "age_disclosure_required": all_old,
        "as_of_traps": traps,
    }
    print(
        f"[Tier3] context before/after tokens: "
        f"legacy_whole_doc≈{_LEGACY_WHOLE_DOC_PROMPT_TOKENS} → "
        f"passage_pack≈{stats['est_prompt_tokens']} "
        f"({stats['passage_count']} passages / {stats['source_count']} sources, "
        f"{stats['context_chars']} chars)"
    )
    if traps:
        for t in traps:
            print(f"[Tier3] {t}")
    return context, used, stats


def answer_from_case_files(question: str) -> dict[str, Any]:
    """Tier 3 tool/API: Gemini answers from retrieved passages only.

    Always returns a marker that this path skipped the reliability layer.
    """

    q = (question or "").strip()
    if not q:
        return {
            "ok": False,
            "error": "empty_question",
            "marker": _MARKER,
            "answer": "",
            "sources_used": [],
        }

    try:
        context, used, pack_stats = _build_context(q)
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": "ledger_missing",
            "detail": str(exc),
            "marker": _MARKER,
            "answer": "",
            "sources_used": [],
        }

    if not context.strip():
        return {
            "ok": True,
            "marker": _MARKER,
            "answer": "No case file text was available to answer from.",
            "sources_used": [],
            "model": MODEL,
            "pack_stats": pack_stats,
        }

    key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        return {
            "ok": False,
            "error": "missing_GOOGLE_API_KEY",
            "marker": _MARKER,
            "answer": "",
            "sources_used": used,
            "pack_stats": pack_stats,
        }

    prompt = (
        "You answer questions about a synthetic educational-evaluation case.\n"
        "Use ONLY the documents below. If the documents do not support an answer, "
        "say so. Do not invent names, dates, scores, or diagnoses.\n"
        "You may summarize across documents when needed (unlike a quote-only tool).\n\n"
        "DATING RULES (required):\n"
        "- Every claim about a person, provider, or status must carry the source.date "
        "of the passage(s) that support it (shown on each passage as "
        "`id · YYYY-MM-DD · Ny Nm ago`).\n"
        "- State the date of the **newest** supporting passage for your answer.\n"
        "- If that newest support is more than ~2 years old, do NOT present it as "
        "current. Say plainly that no later record names one (when true) and that "
        "the only support is the old passage.\n"
        "- Example for an old-only pediatrician: \"No current pediatrician is named. "
        "The only physician in the record appears in a 2013 assessment (age 3): …\"\n"
        "- Naming a 2013 physician as the child's pediatrician with no date is a fail.\n\n"
        f"QUESTION:\n{q}\n\n"
        f"DOCUMENTS:\n{context}\n"
    )
    est_full = _est_tokens(prompt)
    print(
        f"[Tier3] full prompt est≈{est_full} tokens "
        f"(legacy whole-doc path ≈{_LEGACY_WHOLE_DOC_PROMPT_TOKENS})"
    )

    try:
        from google import genai

        client = genai.Client(api_key=key)
        response = client.models.generate_content(model=MODEL, contents=prompt)
        text = (response.text or "").strip()
        usage = usage_from_metadata(getattr(response, "usage_metadata", None))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
            "marker": _MARKER,
            "answer": "",
            "sources_used": used,
            "model": MODEL,
            "usage": None,
            "cost_usd": None,
            "pack_stats": pack_stats,
        }

    measured = int(usage.get("prompt_tokens") or 0)
    print(
        f"[Tier3] measured prompt_tokens={measured} "
        f"(before/after vs legacy {_LEGACY_WHOLE_DOC_PROMPT_TOKENS} → {measured})"
    )
    pack_stats = {
        **pack_stats,
        "measured_prompt_tokens": measured,
        "tokens_saved_vs_legacy": max(0, _LEGACY_WHOLE_DOC_PROMPT_TOKENS - measured),
    }

    return {
        "ok": True,
        "marker": _MARKER,
        "answer": text or "(empty model response)",
        "sources_used": used,
        "model": MODEL,
        "context_chars": len(context),
        "usage": usage,
        "cost_usd": usage.get("cost_usd"),
        "pack_stats": pack_stats,
    }
