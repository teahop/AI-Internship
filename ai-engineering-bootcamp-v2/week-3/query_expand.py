"""LLM query expansion for the shared FTS5 retriever.

Lexical search cannot bridge pediatrician → primary care provider. This module
asks Gemini for alternate clinical phrasing. Expansion is additive and optional:
any failure returns []; disable with QUERY_EXPANSION=0 to A/B the divergence log.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# gemini-3.6-flash — same family as the week-3 agent / Tier 3.
_EXPAND_MODEL = os.getenv("QUERY_EXPANSION_MODEL", "gemini-3.6-flash")
_MAX_TERMS = 8

# Cache: exact question string → list of alternate terms (or [] on prior failure).
_cache: dict[str, list[str]] = {}


def query_expansion_enabled() -> bool:
    """Env switch for divergence A/B. Default on; set QUERY_EXPANSION=0 to disable."""

    raw = (os.getenv("QUERY_EXPANSION") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def clear_expansion_cache() -> None:
    _cache.clear()


def expand_query(question: str) -> list[str]:
    """One gemini-3.6-flash call. Return ≤8 alternate terms a clinical record might use.

    Cache per question string. On any error return [] — expansion is additive,
    never required.
    """

    q = (question or "").strip()
    if not q:
        return []
    if not query_expansion_enabled():
        return []
    if q in _cache:
        return list(_cache[q])

    terms: list[str] = []
    # A couple of retries — empty must not be sticky (API flakes).
    for _ in range(3):
        terms = _call_expand(q)
        if terms:
            break
    if terms:
        _cache[q] = terms
    return list(terms)


def _call_expand(question: str) -> list[str]:
    key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        return []

    prompt = (
        "You help search a clinical / psychoeducational case record "
        "(IEPs, interviews, score reports, medical notes).\n"
        "Given the user's question, list up to 8 alternate words or short phrases "
        "that the SAME fact might appear under in source documents.\n"
        "Prefer phrasing that actually appears in charts: role titles, abbreviations, "
        "form/interview question stems, screening labels, score names "
        '(e.g. "primary care provider", "sleep routine", "hearing screening", "GCA").\n'
        "For screening or test questions, also include pass/fail result phrasing and "
        "common instrument/form names when they help lexical match "
        '(e.g. "pure tone audiometer", "PASSED with both ears") — as search terms only.\n'
        "Avoid pure diagnostic jargon that rarely appears verbatim "
        "(e.g. sleep latency, insomnia taxonomy) unless it is a common chart label.\n"
        "Do NOT answer the question. Do NOT invent patient-specific names or dates.\n"
        "Return ONLY a JSON array of strings.\n\n"
        f"Question: {question}\n"
    )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        # Thinking models can burn hundreds of tokens before the JSON; keep headroom.
        response = client.models.generate_content(
            model=_EXPAND_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1024,
                response_mime_type="application/json",
            ),
        )
        text = (response.text or "").strip()
    except Exception:  # noqa: BLE001 — expansion is optional
        return []

    return _parse_terms(text)[:_MAX_TERMS]


def _parse_terms(text: str) -> list[str]:
    """Parse a JSON array, or fall back to line / comma splitting."""

    text = (text or "").strip()
    if not text:
        return []

    # Strip markdown fences if the model wraps JSON.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    terms: list[str] = []
    try:
        data: Any = json.loads(text)
        if isinstance(data, list):
            terms = [str(x).strip() for x in data if str(x).strip()]
        elif isinstance(data, dict):
            for key in ("terms", "expansions", "alternates", "phrases"):
                if isinstance(data.get(key), list):
                    terms = [str(x).strip() for x in data[key] if str(x).strip()]
                    break
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, list):
                    terms = [str(x).strip() for x in data if str(x).strip()]
            except json.JSONDecodeError:
                terms = []
        if not terms:
            chunks = re.split(r"[\n,;]+", text)
            for c in chunks:
                c = re.sub(r"^[\-\*\d\.\)\s]+", "", c).strip().strip('"')
                if c and not c.lower().startswith("question"):
                    terms.append(c)

    # Dedup case-insensitively; drop empties, fences, and overlong junk.
    reject = {"```", "```json", "[", "]", "{", "}", "json"}
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        t = t.strip().strip("`")
        if not t or len(t) > 80 or t.lower() in reject:
            continue
        if t.startswith("```") or t in {"[", "]"}:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out
