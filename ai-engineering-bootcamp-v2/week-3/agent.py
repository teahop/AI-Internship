"""Molly case-query agent — dual-answer prototype of POST /query (§6.5).

Patterns copied from adk-multi-agent-systems/demo1_routing.py:
  - Agent + tools list (lines 60–85)
  - Runner + InMemorySessionService + ask() loop (lines 89–97)
  - types.Content user message (line 93)
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from costs import merge_usage, usage_from_metadata
from tools import check_conflicts, draft_section, search_ledger, search_source_text
from tier3 import answer_from_case_files

load_dotenv()

MODEL = "gemini-3.6-flash"
# Soft target for the model; hard safety stop is higher so one last answer turn can finish.
MAX_ITERATIONS = 8
HARD_CAP = 14
APP_NAME = "molly_case_query"

INSTRUCTION = f"""
You are molly_case_query_agent — a lookup assistant for a Licensed Educational
Psychologist reviewing a synthetic evaluation case file.

## Dual-panel contract for YOUR final text (non-negotiable)
Your final answer MUST include BOTH of these panels, always, never merged:

### Tier 1 — From the verified ledger
Facts that passed the reliability layer. Cite fact_id, source_id, as_of_date.
You may briefly summarize within class rules (record vs perspectival).
If empty OR vocabulary_status is unregistered_predicate, say so — that is a finding.

### Tier 2 — Found in the source documents — NOT VERIFIED
Verbatim quotes only from search_source_text. One block per source. Never
compose a sentence that spans two sources. Never present this panel as
verified. Always include: "not checked against the reliability layer".

Every quote must keep its citation line exactly as returned by the tool
(e.g. `doc_26 · 2013-09-10 · 12y 10m ago`). Do not strip dates or age gaps.

## Dating / currency (presentation only — not §9.6 staleness)
- When you introduce who/what a quote supports, state the **newest** supporting
  source.date among the quotes you used.
- If the newest supporting passage is more than ~2 years old (citation says
  `Ny … ago` with N≥2), you MUST NOT present a person, provider, or status as
  current. Say plainly that no later record names one (when that is true) and
  that the only support is the old passage — include the year/date.
- Example shape for an old-only pediatrician hit: "No current pediatrician is
  named. The only physician in the record appears in a 2013 assessment …"
  Surfacing the name alone without the 2013 date is a fail.

(A separate Tier 3 — unstructured model answer over packed case files — is
computed outside your loop for comparison. Do not invent a Tier 3 section.)

## Tools
1. search_ledger(predicate, subject="child") — tier 1. unregistered_predicate =
   vocabulary gap (not silence). Empty facts on a registered predicate = ledger silent.
2. search_source_text(query) — tier 2. Quotes only. Most misuse-prone tool.
3. check_conflicts() — only when comparing multiple ledger values / class matters.
4. draft_section(section) — only if the user asks for drafted prose.

## Speed rules (load-bearing — you were failing these)
- Typical lookup budget: **at most 2** search_ledger calls and **at most 2**
  search_source_text calls, then ANSWER.
- As soon as tier-1 status is known (facts found, OR unregistered_predicate, OR
  registered-but-empty) AND you have either source hits OR one clear empty
  source search → **STOP calling tools. Write both panels immediately.**
- Do NOT keep trying alternate spellings or "Dr." / "Screening" / extra name
  guesses after you already have usable quotes — optional LLM query expansion
  (QUERY_EXPANSION) already bridges clinical synonyms into the retriever.
- Prefer one precise source query (e.g. "pediatrician", "hearing", "DAS-II")
  over many broad ones. Passages are ranked with BM25 — one good query is
  usually enough.
- Stay within {MAX_ITERATIONS} tool calls total. Prefer finishing early.

## Done looks like
- Both panels present (even if one is empty).
- Every Tier 2 quote keeps its date · age-gap citation.
- Old-only provider/person support is disclosed, never stated as current.
- Perspectival predicates shown as comparison, never a single yes/no.
- Record conflicts surfaced, never resolved by you.
- No invented facts.
- If both tiers empty, name WHICH refusal case applies.
""".strip()


root_agent = Agent(
    name="molly_case_query_agent",
    model=MODEL,
    description=(
        "Answers case lookup questions with ledger-backed and source-quote panels; "
        "Tier 3 raw-context answer is attached by the runner for comparison."
    ),
    instruction=INSTRUCTION,
    tools=[search_ledger, search_source_text, check_conflicts, draft_section],
)


def _part_text(part: Any) -> str | None:
    text = getattr(part, "text", None)
    return text if text and str(text).strip() else None


def _trace_event(
    event: Any, tool_rounds: int, usage_acc: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], int, str | None, dict[str, Any] | None]:
    """Map one ADK event to Think / Act / Observe steps; accumulate token usage."""

    steps: list[dict[str, Any]] = []
    final: str | None = None
    author = getattr(event, "author", "agent")
    content = getattr(event, "content", None)

    um = getattr(event, "usage_metadata", None)
    if um is not None:
        usage_acc = merge_usage(usage_acc, usage_from_metadata(um))

    if not content or not getattr(content, "parts", None):
        return steps, tool_rounds, None, usage_acc

    for part in content.parts:
        fc = getattr(part, "function_call", None)
        fr = getattr(part, "function_response", None)
        text = _part_text(part)

        if fc:
            tool_rounds += 1
            args = dict(fc.args) if fc.args else {}
            steps.append(
                {
                    "phase": "Act",
                    "author": author,
                    "tool": fc.name,
                    "args": args,
                    "tool_round": tool_rounds,
                }
            )
            print(f"[Act] {author} → {fc.name}({args})")
        elif fr:
            result = fr.response
            steps.append(
                {
                    "phase": "Observe",
                    "author": author,
                    "tool": fr.name,
                    "result": result,
                    "result_preview": str(result)[:400] if result is not None else "",
                    "tool_round": tool_rounds,
                }
            )
            print(f"[Observe] {fr.name} → {str(result)[:200]}")
        elif text:
            is_final = bool(event.is_final_response())
            steps.append(
                {
                    "phase": "Think",
                    "author": author,
                    "text": text,
                    "is_final": is_final,
                }
            )
            print(f"[Think] {text[:300]}")
            if is_final:
                final = text

    return steps, tool_rounds, final, usage_acc


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        try:
            return result.model_dump()
        except Exception:  # noqa: BLE001
            pass
    return {}


def _synthesize_panels_from_trace(trace: list[dict[str, Any]], *, reason: str) -> str:
    """Build dual panels from tool observations when the model never emitted a final."""

    ledger_bits: list[str] = []
    source_bits: list[str] = []

    for step in trace:
        if step.get("phase") != "Observe":
            continue
        data = _as_dict(step.get("result"))
        tool = step.get("tool")
        if tool == "search_ledger":
            status = data.get("vocabulary_status")
            pred = data.get("predicate")
            facts = data.get("facts") or []
            if status == "unregistered_predicate":
                ledger_bits.append(
                    f"- Predicate `{pred}` is **not in the vocabulary** "
                    f"(vocabulary gap — not ledger silence)."
                )
            elif facts:
                for f in facts:
                    ledger_bits.append(
                        f"- `{f.get('predicate')}` = {f.get('value')!r} "
                        f"(fact_id={f.get('fact_id')}, source_id={f.get('source_id')}, "
                        f"as_of={f.get('as_of_date')})"
                    )
            else:
                ledger_bits.append(
                    f"- Registered predicate `{pred}`: no facts on the ledger."
                )
        elif tool == "search_source_text":
            hits = data.get("hits") or []
            if not hits:
                source_bits.append(
                    f"- No quotes for query {data.get('query')!r}."
                )
                continue
            newest = data.get("newest_supporting_date")
            if data.get("age_disclosure_required") and newest:
                source_bits.append(
                    f"- Newest supporting source.date is {newest} "
                    f"(>2y ago) — do not state provider/person/status as current."
                )
            for hit in hits:
                cite = hit.get("citation") or (
                    f"{hit.get('source_id')} · {hit.get('date')}"
                )
                header = (
                    f"- **{hit.get('label')}** ({cite}) — "
                    "not checked against the reliability layer"
                )
                source_bits.append(header)
                for q in hit.get("quotes") or []:
                    source_bits.append(f"  > {q}")
            for trap in data.get("as_of_traps") or []:
                source_bits.append(f"- _{trap}_")

    # De-dupe while preserving order
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for i in items:
            if i in seen:
                continue
            seen.add(i)
            out.append(i)
        return out

    ledger_bits = _uniq(ledger_bits)
    source_bits = _uniq(source_bits)

    tier1 = (
        "\n".join(ledger_bits)
        if ledger_bits
        else "(No ledger tool results captured.)"
    )
    tier2 = (
        "\n".join(source_bits)
        if source_bits
        else "(No source quotes captured.)"
    )

    return (
        "## Tier 1 — From the verified ledger\n"
        f"{tier1}\n\n"
        "## Tier 2 — Found in the source documents — NOT VERIFIED\n"
        "not checked against the reliability layer\n"
        f"{tier2}\n\n"
        f"_Note: {reason}_"
    )


def _attach_tier3(result: dict[str, Any], question: str) -> dict[str, Any]:
    """Always compute Tier 3 so the UI can compare three answers."""

    print("[Act] tier3 → answer_from_case_files")
    tier3 = answer_from_case_files(question)
    preview = (tier3.get("answer") or tier3.get("detail") or "")[:200]
    print(f"[Observe] tier3 → ok={tier3.get('ok')} {preview}")
    result["tier3"] = tier3
    return result


async def ask(
    message: str,
    *,
    max_iterations: int = MAX_ITERATIONS,
    hard_cap: int = HARD_CAP,
) -> dict[str, Any]:
    """Run one question through the agent.

    Soft budget = max_iterations (instruction + warning). Hard cap stops the
    generator only as a last resort; panels are then synthesized from tool
    observations so Molly still sees both tiers. Tier 3 (raw case-file model
    call) is always attached afterward for comparison.
    """

    if not os.getenv("GOOGLE_API_KEY"):
        return _attach_tier3(
            {
                "ok": False,
                "error": "missing_GOOGLE_API_KEY",
                "final": None,
                "trace": [],
                "capped": False,
                "tool_rounds": 0,
                "question": message,
            },
            message,
        )

    service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=service)
    session = await service.create_session(app_name=APP_NAME, user_id="user1")
    content = types.Content(role="user", parts=[types.Part(text=message)])

    trace: list[dict[str, Any]] = []
    tool_rounds = 0
    final: str | None = None
    soft_warned = False
    hard_capped = False
    agent_usage: dict[str, Any] | None = None

    agen = runner.run_async(
        user_id="user1",
        session_id=session.id,
        new_message=content,
    )
    try:
        async for event in agen:
            steps, tool_rounds, maybe_final, agent_usage = _trace_event(
                event, tool_rounds, agent_usage
            )
            trace.extend(steps)
            if maybe_final:
                final = maybe_final

            if tool_rounds >= max_iterations and not soft_warned:
                soft_warned = True
                print(
                    f"[Think] soft budget {max_iterations} tool calls reached — "
                    "model should answer now; continuing briefly for a final turn"
                )

            # Only hard-stop well past the soft budget so a final answer turn can land.
            if tool_rounds >= hard_cap and not maybe_final:
                hard_capped = True
                print(
                    f"[Think] hard cap {hard_cap} — synthesizing panels from tool results"
                )
                break
    except Exception as exc:  # noqa: BLE001 — surface model/API failures as observations
        detail = str(exc)
        print(f"[Observe] runner_error → {detail[:300]}")
        has_observations = any(
            s.get("phase") == "Observe" and s.get("tool") in {
                "search_ledger",
                "search_source_text",
                "check_conflicts",
            }
            for s in trace
        )
        if has_observations:
            # Gemini 503 / network blip after tools already ran — still show both panels.
            final = _synthesize_panels_from_trace(
                trace,
                reason=(
                    f"Model/API error after tools ran ({type(exc).__name__}): "
                    f"{detail[:200]}. Panels rebuilt from tool observations."
                ),
            )
            print(f"[Think] synthesized final after API error ({len(final)} chars)")
            return _attach_tier3(
                {
                    "ok": True,
                    "error": type(exc).__name__,
                    "detail": detail[:800],
                    "final": final,
                    "trace": trace,
                    "capped": hard_capped or soft_warned,
                    "tool_rounds": tool_rounds,
                    "question": message,
                    "synthesized": True,
                    "recovered_from_error": True,
                    "agent_usage": agent_usage,
                    "cost_usd": (agent_usage or {}).get("cost_usd", 0.0),
                },
                message,
            )
        return _attach_tier3(
            {
                "ok": False,
                "error": type(exc).__name__,
                "detail": detail[:800],
                "final": (
                    "## Tier 1 — From the verified ledger\n"
                    "(Agent run failed before tools returned usable results.)\n\n"
                    "## Tier 2 — Found in the source documents — NOT VERIFIED\n"
                    "not checked against the reliability layer\n\n"
                    f"Runner error: {detail[:400]}"
                ),
                "trace": trace,
                "capped": hard_capped or soft_warned,
                "tool_rounds": tool_rounds,
                "question": message,
                "synthesized": False,
                "agent_usage": agent_usage,
                "cost_usd": (agent_usage or {}).get("cost_usd", 0.0),
            },
            message,
        )
    finally:
        # Close cleanly when we broke early; swallow telemetry detach noise.
        aclose = getattr(agen, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except (GeneratorExit, ValueError, RuntimeError) as exc:
                print(f"[Think] runner close note (safe to ignore): {type(exc).__name__}")

    capped = hard_capped or (soft_warned and not final)
    if not final:
        reason = (
            f"Hard-capped at {hard_cap} tool calls; panels rebuilt from observations."
            if hard_capped
            else (
                f"No model final after soft budget ({max_iterations}); "
                "panels rebuilt from observations."
                if soft_warned
                else "No model final; panels rebuilt from observations."
            )
        )
        final = _synthesize_panels_from_trace(trace, reason=reason)
        print(f"[Think] synthesized final ({len(final)} chars)")

    if agent_usage:
        print(
            f"[Think] agent usage cost=${agent_usage.get('cost_usd')} "
            f"tokens={agent_usage.get('total_tokens')}"
        )

    return _attach_tier3(
        {
            "ok": True,
            "final": final,
            "trace": trace,
            "capped": capped,
            "tool_rounds": tool_rounds,
            "question": message,
            "synthesized": not any(
                s.get("phase") == "Think" and s.get("is_final") for s in trace
            ),
            "agent_usage": agent_usage,
            "cost_usd": (agent_usage or {}).get("cost_usd", 0.0),
        },
        message,
    )
