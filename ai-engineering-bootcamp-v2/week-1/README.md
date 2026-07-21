# Week 1 — Source-attributed Background & History (staged pipeline)

A drafting service for a Licensed Educational Psychologist's evaluation reports.
Typed case documents go in; a validated, fully attributed report section comes out.
She reviews and signs — the tool drafts, never decides.

## Compliance: OpenAI ≠ BastionGPT

| Runtime | What it is | What data it may see |
|---------|------------|----------------------|
| **This repo (OpenAI via `OPENAI_API_KEY`)** | Learning / build sandbox | **Synthetic / de-identified fixtures only** |
| **BastionGPT (BAA)** | Production drafting for real cases | Covered under your BAA — **not this repo** |

Every request must set `"confirm_synthetic": true`. Missing/false → refuse before any model call.
Nothing is persisted — the ledger is returned to the caller, never stored.

## Architecture

```
sources → /extract → LEDGER + gap_report + timelines
              ↓
         /conflicts  (deterministic — no model call)
              ↓
           /draft → ReportSection + review queue
```

Extraction, comparison, and prose are separate stages. One combined model call
produced fluent narrative that silently harmonized contradictory sources.
Separating them removes that pressure.

| Stage | Endpoint | Model? | Output |
|-------|----------|--------|--------|
| Classify | `POST /ingest` | 1 cheap call | `{source_type, source_date, label}` for **user confirmation** (never silent) |
| Extract | `POST /extract` | 1 call / source | `Ledger`, `GapReport`, `timelines` (computed view) |
| Conflicts | `POST /conflicts` | none | record `conflicts`, perspectival `variance`, timelines |
| Draft | `POST /draft` | draft + per-fact entailment | `ReportSection` + review queue |
| Ask | `POST /ask` | full pipeline | Same course contract: `answer`, `tokens_used`, `cost_usd` |

`/ask` keeps its request/response shape for the course assignment, but internally
runs extract → conflicts → draft. **Token and cost sum every model call**, including
per-fact entailment checks.

Timelines are a computed `as_of` view (not stored on the ledger). Durable facts have
no timeline. Gap report freshness: `absent` / `stale` / `current` for as_of predicates
only (durable predicates are excluded).

## Setup

```bash
cd ai-engineering-bootcamp-v2/week-1
cp .env.example .env   # set OPENAI_API_KEY — synthetic use only
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Local run

```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
# alias: uvicorn serve_stage5:app --host 127.0.0.1 --port 8000 --reload
```

| URL | What you get |
|-----|----------------|
| http://127.0.0.1:8000/ | Demo UI — pipeline stages visible before prose |
| http://127.0.0.1:8000/docs | OpenAPI |
| http://127.0.0.1:8000/health | Liveness |

## Eval hygiene (fixtures & tests)

1. **Fixture `content` contains only what a real document would contain** — no
   `CONFLICT PLANT`, builder notes, or coaching labels in source text.
2. **Expectations live in sibling fields** (`expected_conflicts`, `expected_facts`,
   `expected_ledger_facts`, …) that are **never** serialized into model prompts.
   `ask_payload()` / `FIXTURE_META_KEYS` strip them.
3. **Tests assert detected-against-expected** (precision, recall, false positives) —
   not “non-empty” or “looks ok.”

## Guardrails (no clinical topic lists)

| Check | Role |
|-------|------|
| `confirm_synthetic` | Synthetic-only runtime |
| Age / derived `age_years` | Recomputation + cite the derived fact; regex backstop |
| Provenance | Every draft statement traces to a ledger `fact_id` / real `source_id` |
| Entailment | Per-statement cheap model call at `ENTAILMENT_TEMPERATURE=0` |
| Conflicts | Group by subject+predicate+qualifier; temporality from vocabulary (`as_of` timelines) |
| Extraction | `EXTRACT_TEMPERATURE=0`; predicate enum + `__unregistered__` escape; no case DOB in payload |
| Draft | `DRAFT_TEMPERATURE=1.0` (named/configurable — not dropped to 0 in Stage 5.2) |
| `force_bad_age` | Planted failure path still exercises the age guard |

Canonical subjects (`child`, `mother`, `father`, `school`, plus source ids for
provenance) — never display names as keys. Names are values of `legal_name`.

## Curl examples

```bash
# Course contract — internally runs the full pipeline
curl -s -X POST "${SERVICE_URL:-http://127.0.0.1:8000}/ask" \
  -H "Content-Type: application/json" \
  -d @fixtures/synthetic_history_case.json | python3 -m json.tool

# Classify a raw document (confirm before adding as a Source)
curl -s -X POST "${SERVICE_URL:-http://127.0.0.1:8000}/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "confirm_synthetic": true,
    "content": "School Nurse Health Report dated 2024-09-12. Known peanut allergy.",
    "model": "gpt-4o-mini"
  }' | python3 -m json.tool
```

## Development notes

**Extraction payload = vocabulary + one source — never case identity.**
Stage 5.2 removed `child` / DOB from the extract payload to stop confabulated
dates, but that also removed knowledge that a canonical subject `child` existed,
so the model keyed facts on source ids and conflict grouping collapsed. Stage 5.3
separates the two roles: the payload may carry **vocabulary** (`canonical_subjects`,
predicate list in the system prompt) without any **case data** (dob, initials,
evaluation_date). Provenance predicates (`defers_to`) get their subject stamped
server-side as the extracting source id.

## Smoke tests

```bash
python test_all_stages.py
python verify_stage47.py   # timelines + freshness
python verify_stage46b.py  # as_of_date anchoring + tutoring conflict
```

## Project layout

```
week-1/
├── main.py                 # /ingest /extract /conflicts /draft /ask
├── extract.py / conflicts.py / draft.py / ingest.py / coverage.py / derived.py
├── schemas.py / predicates.py / validators.py / draft_validators.py
├── provider.py             # sole OpenAI client import
├── static/index.html       # pipeline-visible demo UI
├── fixtures/               # synthetic cases + sibling expected_* fields
├── extract_prompt.md / draft_prompt.md
├── test_all_stages.py
└── serve_stage1.py … serve_stage5.py
```

## Teaching demos vs product

| Piece | Role |
|-------|------|
| `serve_stage1` … `serve_stage4` | Original bootcamp teaching stages (`question` payload) |
| `main.py` / `serve_stage5.py` | Product — staged history pipeline |
| `static/index.html` | Primary demo UI |
| `demo_page.py` | Streamlit runner for teaching stages |

```bash
streamlit run demo_page.py
```
