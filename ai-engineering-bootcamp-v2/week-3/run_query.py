"""CLI: ask the dual-answer agent one question; log Think/Act/Observe + jsonl.

Usage:
  python run_query.py "Who is her pediatrician?"
  python run_query.py --demo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_DIR = Path(__file__).resolve().parent
load_dotenv(_DIR / ".env")

from agent import ask  # noqa: E402
from ledger_store import load_ledger  # noqa: E402
from tools import search_ledger, search_source_text  # noqa: E402
from vocabulary import REGISTERED_PREDICATES  # noqa: E402

DEMO_QUESTIONS = [
    "Who is her pediatrician?",
    "When was her hearing tested?",
    "What's her DAS-II score?",
]

LOG_DIR = _DIR / "logs"


def _guess_predicate(question: str) -> str | None:
    """Lightweight hint for divergence logging — not used by the agent."""

    q = question.lower()
    aliases = {
        "pediatrician": None,  # unregistered
        "hearing": None,
        "das-ii": None,
        "das ii": None,
        "sleep": "sleep",
        "iep": "iep_status",
        "medication": "medications",
        "dob": "dob",
        "date of birth": "dob",
    }
    for needle, pred in aliases.items():
        if needle in q:
            return pred
    return None


def _divergence_flag(ledger_hit: bool, source_hit: bool, conflictish: bool) -> str:
    if conflictish:
        return "conflict"
    if ledger_hit and source_hit:
        return "agree"
    if ledger_hit and not source_hit:
        return "ledger-only"
    if source_hit and not ledger_hit:
        return "source-only"
    return "both-empty"


def _instrument_sides(question: str) -> dict:
    """Deterministic side measurements for the jsonl deliverable (under-extraction log)."""

    pred = _guess_predicate(question)
    ledger_side: dict
    if pred is None and any(
        k in question.lower() for k in ("pediatrician", "hearing", "das", "therapist", "provider")
    ):
        # Known vocabulary gaps for demo questions
        fake_pred = (
            "provider_name"
            if "pediatrician" in question.lower() or "therapist" in question.lower()
            else "hearing_screening"
            if "hearing" in question.lower()
            else "score_fact"
        )
        ledger_side = {
            "vocabulary_status": "unregistered_predicate",
            "predicate_tried": fake_pred,
            "facts": [],
            "count": 0,
        }
        ledger_hit = False
    elif pred and pred in REGISTERED_PREDICATES:
        ledger_side = search_ledger(pred)
        ledger_hit = bool(ledger_side.get("count"))
    elif pred is None:
        # Try a few tokens as predicate names
        ledger_side = {"vocabulary_status": "unknown", "facts": [], "count": 0}
        ledger_hit = False
    else:
        ledger_side = search_ledger(pred)
        ledger_hit = bool(ledger_side.get("count"))

    # Source search: use distinctive keywords from the question
    q = question.lower()
    if "pediatrician" in q:
        src_q = "pediatrician"
    elif "hearing" in q:
        src_q = "hearing"
    elif "das" in q:
        src_q = "DAS-II"
    else:
        tokens = [t for t in re.findall(r"[A-Za-z0-9-]+", question) if len(t) > 3]
        src_q = " ".join(tokens[:3]) or question

    source_side = search_source_text(src_q)
    source_hit = bool(source_side.get("count"))

    flag = _divergence_flag(ledger_hit, source_hit, conflictish=False)
    return {
        "ledger_answer": ledger_side,
        "source_answer": {
            "verified": False,
            "marker": "not checked against the reliability layer",
            "hits": source_side.get("hits", []),
            "count": source_side.get("count", 0),
            "query": src_q,
            "expansions": source_side.get("expansions"),
            "newest_supporting_date": source_side.get("newest_supporting_date"),
            "age_disclosure_required": source_side.get("age_disclosure_required"),
            "as_of_traps": source_side.get("as_of_traps") or [],
        },
        "divergence": flag,
        "as_of_traps": source_side.get("as_of_traps") or [],
    }


def _write_jsonl(record: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"queries_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _compact_trace(trace: list | None) -> list:
    """Drop bulky tool payloads from the jsonl log; keep preview + args."""

    out = []
    for step in trace or []:
        item = {k: v for k, v in step.items() if k != "result"}
        out.append(item)
    return out


async def _run_one(question: str) -> int:
    print(f"\n=== Question ===\n{question}\n")
    try:
        load_ledger()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    sides = _instrument_sides(question)
    result = await ask(question)

    t3 = result.get("tier3") or {}
    pack = t3.get("pack_stats") or {}
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "agent_final": result.get("final"),
        "tool_rounds": result.get("tool_rounds"),
        "capped": result.get("capped"),
        "synthesized": result.get("synthesized"),
        "recovered_from_error": result.get("recovered_from_error"),
        "error": result.get("error"),
        "tier3": result.get("tier3"),
        "tier3_token_before_after": {
            "legacy_whole_doc_prompt_tokens": pack.get(
                "legacy_whole_doc_prompt_tokens"
            ),
            "est_prompt_tokens": pack.get("est_prompt_tokens"),
            "measured_prompt_tokens": pack.get("measured_prompt_tokens"),
            "tokens_saved_vs_legacy": pack.get("tokens_saved_vs_legacy"),
        },
        "trace": _compact_trace(result.get("trace")),
        "ledger_answer": sides["ledger_answer"],
        "source_answer": sides["source_answer"],
        "divergence": sides["divergence"],
        "as_of_traps": list(
            dict.fromkeys(
                (sides.get("as_of_traps") or [])
                + (pack.get("as_of_traps") or [])
            )
        ),
    }
    path = _write_jsonl(record)
    print(f"\n=== Divergence === {sides['divergence']}")
    if record["as_of_traps"]:
        print("=== as_of traps (source.date may lie) ===")
        for note in record["as_of_traps"]:
            print(f"- {note}")
    if result.get("recovered_from_error"):
        print(f"(recovered panels after API error: {result.get('error')})")
    elif result.get("synthesized"):
        print("(final panels synthesized from tool observations)")
    agent_u = result.get("agent_usage") or {}
    print(
        f"Cost: agent=${agent_u.get('cost_usd', 0):.6f} "
        f"({agent_u.get('prompt_tokens', 0)} in / {agent_u.get('completion_tokens', 0)} out) · "
        f"tier3=${(t3.get('cost_usd') or 0):.6f} "
        f"· total≈${(float(agent_u.get('cost_usd') or 0) + float(t3.get('cost_usd') or 0)):.6f}"
    )
    print(
        f"Tier 3 tokens before/after: "
        f"legacy≈{pack.get('legacy_whole_doc_prompt_tokens')} → "
        f"measured={pack.get('measured_prompt_tokens')} "
        f"(est pack≈{pack.get('est_prompt_tokens')})"
    )
    print(
        f"Tier 3: ok={t3.get('ok')} sources_used={len(t3.get('sources_used') or [])} "
        f"chars={t3.get('context_chars')} "
        f"newest={pack.get('newest_supporting_date')} "
        f"age_disclosure={pack.get('age_disclosure_required')}"
    )
    print(f"Logged → {path}")
    print("\n=== Final (Tiers 1–2) ===")
    print(result.get("final"))
    print("\n=== Tier 3 (model + case files) ===")
    print(t3.get("answer") or t3.get("detail") or t3)
    if not result.get("ok"):
        print(f"\n(agent error: {result.get('error')} — {result.get('detail', '')[:200]})")
        return 1
    return 0


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Molly dual-answer case query agent")
    parser.add_argument("question", nargs="?", help="Case question")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the three Maven demo questions",
    )
    args = parser.parse_args(argv)

    if args.demo:
        code = 0
        for q in DEMO_QUESTIONS:
            code = (await _run_one(q)) or code
        return code
    if not args.question:
        parser.print_help()
        return 2
    return await _run_one(args.question)


def main() -> None:
    raise SystemExit(asyncio.run(_main_async(sys.argv[1:])))


if __name__ == "__main__":
    main()
