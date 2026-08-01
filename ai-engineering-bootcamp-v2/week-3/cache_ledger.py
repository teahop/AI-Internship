"""One-time: cache a fixture_001 Ledger under week-3/ (caller holds it; §6.2).

Modes:
  python cache_ledger.py --sources-only
      Zero model cost. Full Source.content on the ledger, facts=[]. Enough for
      the three Maven demos (all are vocabulary / score gaps → tier 1 blank).
  python cache_ledger.py
      Incremental POST /extract against week-1 (narrative sources cost tokens).

Usage:
  cd ../week-1 && uvicorn main:app --host 127.0.0.1 --port 8001   # for full extract
  cd ../week-3 && python cache_ledger.py [--sources-only]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

_DIR = Path(__file__).resolve().parent
load_dotenv(_DIR / ".env")

WEEK1 = Path(__file__).resolve().parents[1] / "week-1"
FIXTURE_DIR = WEEK1 / "fixtures" / "fixture_001"
OUT = _DIR / "cache" / "fixture_001_ledger.json"
BASE = os.getenv("WEEK1_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
TIMEOUT = float(os.getenv("WEEK1_HTTP_TIMEOUT_S", "300"))


def _assemble_ask_payload() -> dict:
    man = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    sources = []
    for f in man["files"]:
        fx = json.loads((FIXTURE_DIR / f["fixture"]).read_text(encoding="utf-8"))
        sources.append(fx["sources"][0])
    return {
        "confirm_synthetic": True,
        "child": man["child"],
        "sources": sources,
        "model": "gpt-4o-mini",
    }


def _sources_only_ledger(payload: dict) -> dict:
    return {
        "child": payload["child"],
        "ledger_version": "week3-sources-only-0",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sources": payload["sources"],
        "facts": [],
        "_week3_note": (
            "Built with --sources-only (no /extract). facts=[] on purpose. "
            "Enough for vocabulary-gap demos; run without the flag for a real extract."
        ),
    }


def _incremental_extract(client: httpx.Client, payload: dict) -> dict:
    """Extract one source at a time with prior_ledger — safer for large packets."""

    child = payload["child"]
    model = payload.get("model") or "gpt-4o-mini"
    prior = None
    last_ledger = None
    total_cost = 0.0
    total_tokens = 0

    for i, source in enumerate(payload["sources"], start=1):
        body: dict = {
            "confirm_synthetic": True,
            "child": child,
            "sources": [source],
            "model": model,
        }
        if prior is not None:
            body["prior_ledger"] = prior
        print(
            f"[{i}/{len(payload['sources'])}] extract {source['id']} "
            f"({source.get('doc_class')}, {len(source.get('content') or '')} chars)…"
        )
        t0 = time.perf_counter()
        resp = client.post(f"{BASE}/extract", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"/extract failed for {source['id']}: {resp.status_code} {resp.text[:500]}"
            )
        data = resp.json()
        prior = data["ledger"]
        last_ledger = prior
        total_cost += float(data.get("cost_usd") or 0)
        total_tokens += int(data.get("tokens_used") or 0)
        print(
            f"    ok in {time.perf_counter() - t0:.1f}s — "
            f"facts={len(prior.get('facts', []))} "
            f"tokens={data.get('tokens_used')} cost=${data.get('cost_usd')}"
        )

    assert last_ledger is not None
    print(f"Done. total_tokens={total_tokens} total_cost=${total_cost:.4f}")
    return last_ledger


def main() -> int:
    parser = argparse.ArgumentParser(description="Cache fixture_001 ledger for week-3")
    parser.add_argument(
        "--sources-only",
        action="store_true",
        help="Skip /extract; write ledger with full sources and empty facts",
    )
    args = parser.parse_args()

    if not FIXTURE_DIR.is_dir():
        print(f"Missing fixture dir: {FIXTURE_DIR}", file=sys.stderr)
        return 1

    payload = _assemble_ask_payload()
    print(f"sources: {len(payload['sources'])}")

    if args.sources_only:
        ledger = _sources_only_ledger(payload)
        print("mode: sources-only (no week-1 call)")
    else:
        print(f"week-1 base: {BASE}")
        try:
            health = httpx.get(f"{BASE}/health", timeout=5.0)
            health.raise_for_status()
            print(f"health: {health.json()}")
        except Exception as exc:  # noqa: BLE001
            print(
                f"Cannot reach week-1 at {BASE}: {exc}\n"
                "Start it with:\n"
                "  cd ../week-1 && source .venv/bin/activate && "
                "uvicorn main:app --host 127.0.0.1 --port 8001\n"
                "Or run: python cache_ledger.py --sources-only",
                file=sys.stderr,
            )
            return 1
        with httpx.Client(timeout=TIMEOUT) as client:
            ledger = _incremental_extract(client, payload)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(
        f"facts={len(ledger.get('facts', []))} "
        f"sources={len(ledger.get('sources', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
