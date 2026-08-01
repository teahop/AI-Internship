"""Split dual-answer markdown and detect refusal phrasing (shared by CLI/UI)."""

from __future__ import annotations

import re

REFUSAL_MARKERS = (
    "nothing in this case file speaks to that",
    "silence here isn't evidence",
    "that's your call",
    "stopped at iteration cap",
    "agent run failed before tools",
)


def split_panels(final: str | None) -> tuple[str, str]:
    """Split model/synthesized markdown into tier-1 and tier-2 bodies."""

    text = (final or "").strip()
    if not text:
        return ("(empty)", "(empty)")

    parts = re.split(
        r"(?i)#{2,3}\s*Tier\s*2\s*[—–-].*?(?:NOT VERIFIED|not verified).*?\n",
        text,
        maxsplit=1,
    )
    if len(parts) == 2:
        tier1 = re.sub(
            r"(?i)^#{2,3}\s*Tier\s*1\s*[—–-].*?\n",
            "",
            parts[0],
            count=1,
        ).strip()
        tier2 = parts[1].strip()
        return (tier1 or "(empty)", tier2 or "(empty)")

    m = re.search(r"(?i)\n\s*tier\s*2\b", text)
    if m:
        return (text[: m.start()].strip(), text[m.start() :].strip())
    return (text, "(Tier 2 not separately labeled — see full answer below.)")


def detect_refusal(final: str | None) -> str | None:
    low = (final or "").lower()
    for marker in REFUSAL_MARKERS:
        if marker in low:
            return marker
    return None
