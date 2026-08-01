"""Estimate USD cost for Gemini calls (configurable rates via env).

Defaults are approximate Flash-class list prices — override if your account
differs. Thinking/thought tokens are billed as output.
"""

from __future__ import annotations

import os
from typing import Any


# USD per 1M tokens — override with GEMINI_INPUT_USD_PER_MTOK / GEMINI_OUTPUT_USD_PER_MTOK
# Defaults ≈ Gemini 2.5 Flash paid tier (thinking tokens billed as output).
_DEFAULT_IN = 0.30
_DEFAULT_OUT = 2.50


def _rates() -> tuple[float, float]:
    return (
        float(os.getenv("GEMINI_INPUT_USD_PER_MTOK", str(_DEFAULT_IN))),
        float(os.getenv("GEMINI_OUTPUT_USD_PER_MTOK", str(_DEFAULT_OUT))),
    )


def cost_usd(*, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = _rates()
    return (prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout


def usage_from_metadata(usage: Any) -> dict[str, Any]:
    """Normalize google.genai usage_metadata → token counts + estimated USD."""

    if usage is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

    prompt = int(getattr(usage, "prompt_token_count", None) or 0)
    tool_prompt = int(getattr(usage, "tool_use_prompt_token_count", None) or 0)
    candidates = int(getattr(usage, "candidates_token_count", None) or 0)
    thoughts = int(getattr(usage, "thoughts_token_count", None) or 0)
    cached = int(getattr(usage, "cached_content_token_count", None) or 0)

    prompt_tokens = prompt + tool_prompt
    # Thought tokens are typically billed like output on Gemini.
    completion_tokens = candidates + thoughts
    total = int(getattr(usage, "total_token_count", None) or (prompt_tokens + completion_tokens))

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "thoughts_tokens": thoughts,
        "candidates_tokens": candidates,
        "cached_tokens": cached,
        "total_tokens": total,
        "cost_usd": round(cost_usd(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens), 6),
    }


def merge_usage(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    a = a or {}
    b = b or {}
    prompt = int(a.get("prompt_tokens") or 0) + int(b.get("prompt_tokens") or 0)
    completion = int(a.get("completion_tokens") or 0) + int(b.get("completion_tokens") or 0)
    thoughts = int(a.get("thoughts_tokens") or 0) + int(b.get("thoughts_tokens") or 0)
    candidates = int(a.get("candidates_tokens") or 0) + int(b.get("candidates_tokens") or 0)
    cached = int(a.get("cached_tokens") or 0) + int(b.get("cached_tokens") or 0)
    total = int(a.get("total_tokens") or 0) + int(b.get("total_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "thoughts_tokens": thoughts,
        "candidates_tokens": candidates,
        "cached_tokens": cached,
        "total_tokens": total or (prompt + completion),
        "cost_usd": round(cost_usd(prompt_tokens=prompt, completion_tokens=completion), 6),
    }


def format_cost_line(usage: dict[str, Any] | None, *, label: str) -> str:
    usage = usage or {}
    cost = usage.get("cost_usd")
    if cost is None:
        return f"**Cost:** {label} — n/a"
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    return (
        f"**Cost:** ${float(cost):.6f} · {label} · "
        f"{pt:,} in / {ct:,} out tok"
    )
