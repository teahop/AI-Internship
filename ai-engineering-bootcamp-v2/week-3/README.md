# Week 3 — Session 3 ADK case-query agent

Dual-answer client of the week-1 capstone over HTTP. Prototypes `POST /query`
(spec §6.5): every answer shows a **ledger-backed** panel and a **source-backed**
panel side by side. Never edits `week-1/`.

## Setup

```bash
cd ai-engineering-bootcamp-v2/week-3
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # set GOOGLE_API_KEY; WEEK1_BASE_URL if not :8001
```

`GOOGLE_API_KEY` is for Gemini/ADK ([get one here](https://aistudio.google.com/apikey)).
week-1 keeps its own `OPENAI_API_KEY` — separate processes, separate `.env` files.
If the agent exits with `API_KEY_INVALID`, replace the value in `week-3/.env` only.

## Cache the ledger (once)

```bash
# Zero cost — full Source.content, empty facts. Enough for the three Maven demos.
python cache_ledger.py --sources-only

# Or real extract (week-1 must be up; spends OpenAI tokens on narrative sources):
# cd ../week-1 && source .venv/bin/activate
# uvicorn main:app --host 127.0.0.1 --port 8001
# cd ../week-3 && source .venv/bin/activate && python cache_ledger.py
```

Port **8001** is the default so week-2 RAG can keep 8000.

## Run a question

```bash
# Terminal A — week-1 (needed for check_conflicts / draft_section)
cd ../week-1 && source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8001

# Terminal B — agent
cd ../week-3 && source .venv/bin/activate
python run_query.py "Who is her pediatrician?"
python run_query.py --demo
```

Stdout shows Think / Act / Observe. Each question appends a jsonl row under
`logs/` with ledger side, source side, and a divergence flag (`source-only` is
under-extraction evidence).

If Gemini returns 503 (or similar) **after** tools already ran, the agent
**rebuilds both panels from tool observations** instead of showing an empty stub.

## Streamlit UI (Path A)

```bash
# Terminal A — week-1 (optional for pure lookup demos; needed for check_conflicts)
cd ../week-1 && source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8001

# Terminal B — UI
cd ../week-3 && source .venv/bin/activate
streamlit run streamlit_app.py
```

Opens a local browser UI: question box, **three** always-visible panels with
estimated **Gemini cost** under each column header (Tiers 1–2 share the ADK
agent run; Tier 3 is a separate call), a divergence flag, and a Think → Act →
Observe step log. Rates default to Flash-class list prices; override with
`GEMINI_INPUT_USD_PER_MTOK` / `GEMINI_OUTPUT_USD_PER_MTOK`.

### Maven screenshots (two)

1. **Dual panels** — ask *"Who is her pediatrician?"* (or use the sidebar button).
   Capture the page showing:
   - left: vocabulary gap on the ledger
   - right: unverified stamp + Karen Vance / pediatrician quotes
   - blue **source-only** divergence flag
2. **Step log** — same run (or hearing / DAS-II), expander **Think → Act → Observe**
   open, showing at least one `search_ledger` Act/Observe and one
   `search_source_text` Act/Observe.

Crop so **no `.env`, API keys, or sidebar secrets** appear. Prefer the main
panels + step log only.

## Demo questions (fixture_001)

1. Who is her pediatrician?
2. When was her hearing tested?
3. What's her DAS-II score?

All three are **tier 1 blank / tier 2 quotes** (vocabulary or score-fact gaps).

## Agent in five bullets

1. **One agent, four tools** — no router; lookup shape only appears after tools run.
2. **Tier 1** (`search_ledger`) reads cached verified facts; unknown predicates are a vocabulary gap, not silence.
3. **Tier 2** (`search_source_text`) returns **verbatim quotes only**, one block per source, marked unverified. Quotes come from a shared in-process **FTS5 passage index** ([`retrieval.py`](retrieval.py)) — same retriever as Tier 3. Optional **LLM query expansion** ([`query_expand.py`](query_expand.py)) bridges clinical synonyms (`QUERY_EXPANSION=0` to disable for A/B).
4. **`check_conflicts`** POSTs the ledger to week-1 (deterministic): conflicts ≠ variance.
5. **Bounded at 8** tool rounds (hard safety 14); fail closed / synthesize from observations.

Patterns from `adk-multi-agent-systems/demo1_routing.py`: `Agent(tools=[…])`
(L60–85), `Runner` + `InMemorySessionService` + `ask()` (L89–97),
`types.Content` user message (L93). Streamlit runner pattern from
`streamlit_app.py` (`run_agent_sync` / ThreadPoolExecutor ~L143–169).

## Boundary

- `git diff --stat` on `week-1/` must stay empty.
- Synthetic only; week-1 calls use `"confirm_synthetic": true`.
- Never commit `.env` or paste keys into screenshots.
