"""
Session 3 — Molly dual-answer case query (Streamlit UI).

Run (week-1 on :8001 recommended for check_conflicts):
    cd ai-engineering-bootcamp-v2/week-3
    source .venv/bin/activate
    streamlit run streamlit_app.py

Patterns adapted from adk-multi-agent-systems/streamlit_app.py
(run_agent_sync / ThreadPoolExecutor, trace rendering) — domain is Molly case Q&A.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))
load_dotenv(_DIR / ".env")

from agent import ask  # noqa: E402
from costs import format_cost_line  # noqa: E402
from ledger_store import load_ledger, reload_ledger  # noqa: E402
from panels import detect_refusal, split_panels  # noqa: E402
from retrieval import reset_index  # noqa: E402
from run_query import DEMO_QUESTIONS, _instrument_sides  # noqa: E402
from tier3 import answer_from_case_files  # noqa: E402

st.set_page_config(
    page_title="LEP Case Query Agent",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 3.5rem !important; padding-bottom: 2rem; }
      h3 { margin-top: 0.25rem !important; }
      .tier-panel {
        border: 1px solid #d0d7de;
        border-radius: 6px;
        padding: 0.9rem 1rem;
        min-height: 12rem;
        background: #fafbfc;
      }
      .tier-panel.unverified {
        border-color: #d4a72c;
        background: #fffbeb;
      }
      .tier-panel.refusal {
        border-color: #cf222e;
        background: #fff5f5;
      }
      .unverified-stamp {
        font-size: 0.85rem;
        font-weight: 600;
        color: #9a6700;
        margin: 0 0 0.75rem 0;
        padding: 0.35rem 0.5rem;
        border: 1px dashed #d4a72c;
        background: #fff8c5;
      }
      .div-flag {
        padding: 0.5rem 0.75rem;
        border-radius: 6px;
        margin: 0.5rem 0 1rem 0;
        font-weight: 600;
      }
      .div-source-only { background: #ddf4ff; border: 1px solid #54aeff; }
      .div-agree { background: #dafbe1; border: 1px solid #4ac26b; }
      .div-ledger-only { background: #fff1e5; border: 1px solid #fb8f44; }
      .div-both-empty { background: #ffebe9; border: 1px solid #ff8182; }
      .div-conflict { background: #fbefff; border: 1px solid #e85aad; }
    </style>
    """,
    unsafe_allow_html=True,
)


def run_ask_sync(question: str, timeout: int = 180) -> dict:
    """Run the async ADK ask() from Streamlit's sync context."""

    def _run() -> dict:
        return asyncio.run(ask(question))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run).result(timeout=timeout)


def render_trace(trace: list[dict]) -> None:
    if not trace:
        st.caption("No steps yet.")
        return
    for i, step in enumerate(trace, start=1):
        phase = step.get("phase", "?")
        if phase == "Act":
            args = step.get("args") or {}
            args_s = ", ".join(f"{k}={v!r}" for k, v in args.items())
            st.warning(f"**{i}. Act** — `{step.get('tool')}`({args_s[:220]})")
        elif phase == "Observe":
            preview = step.get("result_preview") or str(step.get("result") or "")[:400]
            st.success(f"**{i}. Observe** — `{step.get('tool')}`")
            if preview:
                st.code(preview[:500], language="json")
        elif phase == "Think":
            text = step.get("text") or ""
            is_final = bool(step.get("is_final"))
            label = "final" if is_final else "think"
            # Final answer can be long dual-panel markdown — don't clip mid-word
            # in the step log (panels above already show the same content).
            if is_final:
                st.info(f"**{i}. Think ({label})**")
                st.markdown(text)
            else:
                preview = text if len(text) <= 600 else text[:600] + "…"
                st.info(f"**{i}. Think ({label})** — {preview}")


with st.sidebar:
    st.title("LEP Case Query Agent")
    st.caption("Dual-answer prototype of `POST /query` (§6.5)")
    key_ok = bool((os.getenv("GOOGLE_API_KEY") or "").strip())
    st.markdown("### Status")
    if key_ok:
        st.success("GOOGLE_API_KEY set")
    else:
        st.error("GOOGLE_API_KEY missing in week-3/.env")
    st.caption(f"WEEK1_BASE_URL={os.getenv('WEEK1_BASE_URL', 'http://127.0.0.1:8001')}")
    try:
        led = load_ledger()
        st.success(
            f"Ledger loaded — {len(led.get('facts') or [])} facts, "
            f"{len(led.get('sources') or [])} sources"
        )
    except FileNotFoundError as exc:
        st.error(str(exc))

    st.markdown("---")
    st.markdown("### Demo questions")
    st.caption("Loads into the query box — then click Query.")
    for i, q in enumerate(DEMO_QUESTIONS):
        if st.button(q, use_container_width=True, key=f"demo_q_{i}"):
            st.session_state["query_box"] = q
            st.rerun()

st.header("Query")

st.markdown(
    """
A Licensed Educational Psychologist (LEP) builds evaluation reports from a thick
packet—IEPs, score reports, parent forms, medical notes. She still spends time
hunting simple facts: who’s the pediatrician, when was hearing screened, what’s
the DAS-II score.

**Week 1** turned that packet into a structured **fact ledger** (with conflict
checks) and draft prose she reviews. The service doesn’t store cases; the caller
keeps the ledger.

**This homework** prototypes **case Q&A** on top of that ledger. One question →
**three columns** so you can see the tradeoff:

1. **Tier 1 — Verified ledger** — only facts that passed the reliability layer
   (may be blank when the vocabulary has no predicate for the question).
2. **Tier 2 — Source quotes (the production bet)** — verbatim passages from an
   in-process FTS5 search, each tagged with `source.date` and how long ago,
   marked **not verified**.
3. **Tier 3 — Model + retrieved passages** — one Gemini answer over a small set
   of ranked passages (learning / cost comparison), not a whole-packet dump.

Tier 1 goes empty often on real lookups—that’s a vocabulary gap, not “the files
said nothing.” Tier 2 is what we’d ship for lookup. Tier 3 shows why “paste the
packet into the model” is fluent but easy to mis-date (e.g. a 2013 PCP named as
if current).

It’s an **agent** because the next tool call depends on what came back:
vocabulary gap vs silent ledger vs competing quotes vs “only support is thirteen
years old.”

**When Molly types a case question and hits Query, the agent should surface
ledger status and dated source quotes she can trust-or-distrust on sight, using
`search_ledger` and `search_source_text`.**

**Agent in five bullets**

1. **One ADK agent, four tools** — no separate router; the path is whatever
   `search_ledger` / `search_source_text` (and rarely `check_conflicts` /
   `draft_section`) return.
2. **Tier 1** — cached ledger only; `unregistered_predicate` means the schema
   doesn’t cover the ask yet.
3. **Tier 2** — shared FTS5 passage index + optional LLM query expansion
   (`QUERY_EXPANSION`); **quotes only**, one block per source, citation like
   `doc_26 · 2013-09-10 · 12y 10m ago`.
4. **Tier 3** — same retriever, passage-packed context (~few k tokens vs ~20k
   whole-doc), must state newest supporting date and not present old-only
   providers as current.
5. **Bounded tool use** (~8 soft / 14 hard); on model failure after tools, panels
   synthesize from observations. Local ledger/source tools stay $0; Gemini cost
   is shown per column.
"""
)

if "query_box" not in st.session_state:
    st.session_state["query_box"] = DEMO_QUESTIONS[0]

st.markdown("##### Your question")
question = st.text_area(
    "Type any case question, or load a demo from the sidebar.",
    key="query_box",
    height=100,
    label_visibility="collapsed",
)

col_run, col_clear = st.columns([1, 1])
with col_run:
    run = st.button("Query", type="primary", use_container_width=True)
with col_clear:
    if st.button("Clear result", use_container_width=True):
        st.session_state.pop("last_result", None)
        st.session_state.pop("last_sides", None)

if run:
    if not (os.getenv("GOOGLE_API_KEY") or "").strip():
        st.error("Set GOOGLE_API_KEY in week-3/.env first.")
    elif not (question or "").strip():
        st.warning("Enter a question (or pick a demo) before querying.")
    else:
        with st.spinner("Agent running (Think → Act → Observe)…"):
            try:
                # Pick up a newly written cache/fixture_001_ledger.json without restarting.
                reload_ledger()
                reset_index()
                result = run_ask_sync(question.strip())
                # Guarantee Tier 3 even if a stale agent module omitted it.
                t3 = result.get("tier3")
                if not isinstance(t3, dict) or "ok" not in t3:
                    result["tier3"] = answer_from_case_files(question.strip())
                sides = _instrument_sides(question.strip())
                st.session_state["last_result"] = result
                st.session_state["last_sides"] = sides
            except Exception as exc:  # noqa: BLE001
                st.exception(exc)

result = st.session_state.get("last_result")
sides = st.session_state.get("last_sides")

if result:
    final = result.get("final") or ""
    tier1, tier2 = split_panels(final)
    divergence = (sides or {}).get("divergence", "unknown")
    refusal = detect_refusal(final)

    flag_class = {
        "source-only": "div-source-only",
        "agree": "div-agree",
        "ledger-only": "div-ledger-only",
        "both-empty": "div-both-empty",
        "conflict": "div-conflict",
    }.get(divergence, "div-source-only")

    flag_labels = {
        "source-only": "Divergence: source-only (under-extraction / vocabulary gap)",
        "agree": "Panels agree",
        "ledger-only": "Divergence: ledger-only",
        "both-empty": "Both empty — check refusal case",
        "conflict": "Conflict / disagreement surfaced",
    }

    st.markdown(
        f'<div class="div-flag {flag_class}">{flag_labels.get(divergence, divergence)}</div>',
        unsafe_allow_html=True,
    )

    if result.get("recovered_from_error"):
        st.warning(
            f"Model/API error after tools ran (`{result.get('error')}`). "
            "Panels below were rebuilt from tool observations."
        )
    elif result.get("synthesized"):
        st.info("Final panels were synthesized from tool observations (no model final).")

    if refusal:
        st.error(f"Refusal / incomplete path detected: “{refusal}”")

    agent_usage = result.get("agent_usage")
    tier3 = result.get("tier3") if isinstance(result.get("tier3"), dict) else None
    agent_cost = float((agent_usage or {}).get("cost_usd") or 0.0)
    t3_cost = float((tier3 or {}).get("cost_usd") or 0.0) if tier3 else 0.0

    left, mid, right = st.columns(3)
    with left:
        st.subheader("Tier 1 — Verified ledger")
        st.caption(format_cost_line(agent_usage, label="ADK agent (shared w/ Tier 2)"))
        panel_cls = "tier-panel refusal" if refusal and "empty" in tier1.lower() else "tier-panel"
        st.markdown(f'<div class="{panel_cls}">', unsafe_allow_html=True)
        st.markdown(tier1)
        st.markdown("</div>", unsafe_allow_html=True)

    with mid:
        st.subheader("Tier 2 — Source quotes")
        st.caption(format_cost_line(agent_usage, label="ADK agent (shared w/ Tier 1)"))
        st.markdown('<div class="tier-panel unverified">', unsafe_allow_html=True)
        st.markdown(
            '<p class="unverified-stamp">'
            "NOT CHECKED AGAINST THE RELIABILITY LAYER — "
            "verbatim source text only; not verified."
            "</p>",
            unsafe_allow_html=True,
        )
        st.markdown(tier2)
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.subheader("Tier 3 — Model + case files")
        st.caption(
            format_cost_line(
                (tier3 or {}).get("usage") if isinstance(tier3, dict) else None,
                label="single Gemini call",
            )
        )
        st.markdown('<div class="tier-panel unverified">', unsafe_allow_html=True)
        if not tier3 or "ok" not in tier3:
            st.warning(
                "Tier 3 missing from this result (likely a run from before the "
                "update). Click **Query** again."
            )
        else:
            st.markdown(
                '<p class="unverified-stamp">'
                + (
                    tier3.get("marker")
                    or "Unstructured model answer over raw case files — NOT verified."
                )
                + "</p>",
                unsafe_allow_html=True,
            )
            if tier3.get("ok") is True:
                st.markdown(tier3.get("answer") or "(empty)")
                used = tier3.get("sources_used") or []
                if used:
                    ids = ", ".join(f"{u.get('source_id')}" for u in used[:12])
                    st.caption(
                        f"Context packed from {len(used)} passage(s) "
                        f"({tier3.get('context_chars', '?')} chars): {ids}"
                        + ("…" if len(used) > 12 else "")
                    )
                pack = tier3.get("pack_stats") or {}
                if pack.get("measured_prompt_tokens") or pack.get("legacy_whole_doc_prompt_tokens"):
                    st.caption(
                        f"Tokens before/after: legacy whole-doc ≈"
                        f"{pack.get('legacy_whole_doc_prompt_tokens', 20252)} → "
                        f"measured {pack.get('measured_prompt_tokens', pack.get('est_prompt_tokens', '?'))}"
                        + (
                            f" (saved ~{pack.get('tokens_saved_vs_legacy')})"
                            if pack.get("tokens_saved_vs_legacy") is not None
                            else ""
                        )
                    )
            else:
                err = tier3.get("error") or "unknown_error"
                detail = (tier3.get("detail") or "").strip()
                st.error(
                    f"Tier 3 failed: {err}"
                    + (f" — {detail[:200]}" if detail else "")
                )
        st.markdown("</div>", unsafe_allow_html=True)

    st.caption(
        f"Estimated total this query: "
        f"${agent_cost + t3_cost:.6f} "
        f"(agent ${agent_cost:.6f} + Tier 3 ${t3_cost:.6f}; "
        f"local ledger/source tools are $0)"
    )

    with st.expander("Think → Act → Observe", expanded=True):
        render_trace(result.get("trace") or [])

    with st.expander("Full agent text / meta"):
        st.markdown(final)
        st.json(
            {
                "tool_rounds": result.get("tool_rounds"),
                "capped": result.get("capped"),
                "synthesized": result.get("synthesized"),
                "recovered_from_error": result.get("recovered_from_error"),
                "divergence": divergence,
                "error": result.get("error"),
                "agent_usage": result.get("agent_usage"),
                "cost_usd_agent": result.get("cost_usd"),
                "tier3_ok": (result.get("tier3") or {}).get("ok"),
                "tier3_cost_usd": (result.get("tier3") or {}).get("cost_usd"),
                "tier3_usage": (result.get("tier3") or {}).get("usage"),
                "tier3_pack_stats": (result.get("tier3") or {}).get("pack_stats"),
                "tier3_sources": len((result.get("tier3") or {}).get("sources_used") or []),
            }
        )
else:
    st.caption("Query a question to see three panels and the step log.")
