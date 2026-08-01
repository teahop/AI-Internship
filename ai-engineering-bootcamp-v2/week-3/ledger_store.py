"""Load and hold the cached case Ledger (caller-owned; week-1 stores nothing)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent

_ledger: dict[str, Any] | None = None
_ledger_path: Path | None = None
_ledger_mtime: float | None = None


def ledger_path() -> Path:
    raw = os.getenv("LEDGER_PATH", "cache/fixture_001_ledger.json")
    path = Path(raw)
    if not path.is_absolute():
        path = _DIR / path
    return path


def reload_ledger(path: Path | None = None) -> dict[str, Any]:
    """Force re-read from disk (e.g. after cache_ledger.py finishes)."""

    global _ledger, _ledger_path, _ledger_mtime
    _ledger = None
    _ledger_path = None
    _ledger_mtime = None
    return load_ledger(path)


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    """Load ledger.json into memory. Reloads automatically if the file changed on disk."""

    global _ledger, _ledger_path, _ledger_mtime
    target = path or ledger_path()
    if not target.is_file():
        raise FileNotFoundError(
            f"Cached ledger not found at {target}. "
            "Run: python cache_ledger.py  (week-1 must be up; see README)."
        )

    mtime = target.stat().st_mtime
    if (
        _ledger is not None
        and _ledger_path == target
        and _ledger_mtime is not None
        and _ledger_mtime == mtime
    ):
        return _ledger

    _ledger = json.loads(target.read_text(encoding="utf-8"))
    _ledger_path = target
    _ledger_mtime = mtime
    return _ledger


def get_ledger() -> dict[str, Any]:
    return load_ledger()


def source_index(ledger: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Map source_id → source dict for label/date lookups."""

    led = ledger or get_ledger()
    return {s["id"]: s for s in led.get("sources", [])}
