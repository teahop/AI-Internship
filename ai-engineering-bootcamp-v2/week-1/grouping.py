"""Record/perspectival disagreement grouping — re-exports from conflicts.py."""

from __future__ import annotations

from conflicts import detect_disagreements, detect_disagreements_from_ledger, record_value_conflicts

__all__ = [
    "detect_disagreements",
    "detect_disagreements_from_ledger",
    "record_value_conflicts",
]
