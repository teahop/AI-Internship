# Week 1 — Psycho-ed History Draft (`/ask`)

Customize the bootcamp `/ask` starter into a **source-attributed Background & History** drafter for a solo Licensed Educational Psychologist (Molly Harrison). The tool drafts; she reviews, edits, and signs.

## Compliance: OpenAI ≠ BastionGPT (read this first)

| Runtime | What it is | What data it may see |
|---------|------------|----------------------|
| **This repo (OpenAI via `OPENAI_API_KEY`)** | Learning / build sandbox | **Synthetic / de-identified fixtures only** |
| **BastionGPT (BAA)** | Production drafting for real cases | Covered under your BAA — **not this repo** |

Same code shape, different runtime. **Do not paste real student/client records into this OpenAI build.** Production drafting happens on BastionGPT. That separation is the compliance story.

Every `/ask` request must set `"confirm_synthetic": true`. If that flag is missing or false, the API refuses before any model call.

## Setup

```bash
cd ai-engineering-bootcamp-v2/week-1

# Secrets live only in a local .env — never commit it.
cp .env.example .env
# Edit .env and set OPENAI_API_KEY (synthetic use only)

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Use `.env.example` as the template. Keep real keys in `.env` only (gitignored).

## Local run

```bash
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Same app via the stage-5 alias:

```bash
uvicorn serve_stage5:app --host 127.0.0.1 --port 8000 --reload
```

| URL | What you get |
|-----|----------------|
| http://127.0.0.1:8000/ | Multi-source demo UI (`static/index.html`) — auto-loads the health-conflict fixture |
| http://127.0.0.1:8000/health | Liveness + runtime reminder |
| http://127.0.0.1:8000/docs | Interactive OpenAPI (`POST /ask`) |
| http://127.0.0.1:8000/fixtures/synthetic_history_case.json | History fixture JSON |
| http://127.0.0.1:8000/fixtures/synthetic_health_conflict_case.json | Health-conflict fixture JSON |

**How to know you’re on the current build:** the home page shows a teal badge **provenance build · multi-source**, the heading **Draft from dated sources**, fixture buttons, and source cards with `id` / `type` / `date` / `label` / `content` (not a single “Your ask” box). After a draft, **Attributed facts** and **Conflicts surfaced** panels appear under the prose.

## Provenance spine (why multi-source matters)

Each input artifact is a dated `Source` (`id`, `type`, `date`, `label`, `content`). Every claim in the draft must cite a real input `source_id` and that source’s exact `source_date`. Prose should cite by **label + date** (e.g. `(School Nurse Health Report, 2024-09-12)`), never invent a tag like `user-ask`.

**Do not** paste several documents into one blob. Keep nurse report, IEP, parent interview, etc. as separate sources so attribution and conflicts can work.

## What `/ask` does (Molly’s #1 task)

Accepts a typed case packet (`section`, `child`, dated `sources`) and returns a `ReportSection` where:

- **`prose`** — paste-ready Background & History draft  
- **`facts`** — every claim with required `source_id` + `source_date` + `life_stage` (+ optional `reporter`)  
- **`conflicts`** — disagreements surfaced, not silently resolved  
- **`coverage`** — which life stages appear (birth → present)

### Guardrails (validate → retry)

| Check | What it enforces |
|-------|------------------|
| `validate_age_consistency` | Current age must match `dob` + `evaluation_date` (`force_bad_age` plants a wrong age on attempt 0) |
| `validate_provenance` | Every fact/conflict version cites a real input `source_id` with matching `source_date` |
| `validate_reporter_fidelity` | Blocks positive allergy (etc.) claims cited to sources that do not positively state them |
| Conflict soft-retry | If ≥2 sources and `conflicts` is empty, one forced re-check with a conflict-focused instruction |

Prompt rules (see `system_prompt.md`) also require: never invent facts, never guess the reporter, and actively scan for name mismatches, status contradictions, classification disagreements, and omission plants (including within a single document).

## Fixtures

| File | Purpose |
|------|---------|
| `fixtures/synthetic_history_case.json` | Multi-source developmental/school packet; plants tutoring conflict + stale age in a school file |
| `fixtures/synthetic_health_conflict_case.json` | Nurse + IEP + father interview; plants Justin/Jason name mismatch, health-plan status conflict, Undiagnosed vs known allergy |

The home UI auto-loads the health-conflict fixture. Use the buttons to switch fixtures or edit sources manually.

## Curl

No local files required — the JSON body is inline. Works against a local server or a deployed host (set `SERVICE_URL` to your service base URL from the host dashboard; do not commit that URL here).

```bash
# Local: SERVICE_URL=http://127.0.0.1:8000
curl -s -X POST "${SERVICE_URL:-http://127.0.0.1:8000}/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "confirm_synthetic": true,
    "section": "history",
    "child": {
      "initials": "A.R.",
      "dob": "2017-03-15",
      "evaluation_date": "2026-07-16"
    },
    "sources": [
      {
        "id": "parent-dev-2026",
        "type": "parent",
        "date": "2026-06-01",
        "label": "Parent developmental history form",
        "content": "Pregnancy uncomplicated. Full-term vaginal birth, no NICU. Walked at 13 months. Concerns began in kindergarten with letter-sound learning."
      },
      {
        "id": "teacher-2026",
        "type": "teacher",
        "date": "2026-06-15",
        "label": "Current classroom teacher questionnaire",
        "content": "Grade 4: reading fluency below peers; spelling weak; anxious when asked to read aloud."
      }
    ],
    "model": "gpt-4o-mini"
  }' | python3 -m json.tool
```

Exercise the age guardrail (plants a bad age on attempt 0, then retries) — same body plus `"force_bad_age": true`:

```bash
curl -s -X POST "${SERVICE_URL:-http://127.0.0.1:8000}/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "confirm_synthetic": true,
    "section": "history",
    "force_bad_age": true,
    "child": {
      "initials": "A.R.",
      "dob": "2017-03-15",
      "evaluation_date": "2026-07-16"
    },
    "sources": [
      {
        "id": "parent-dev-2026",
        "type": "parent",
        "date": "2026-06-01",
        "label": "Parent developmental history form",
        "content": "Full-term birth, no NICU. Concerns began in kindergarten with letter-sound learning."
      }
    ],
    "model": "gpt-4o-mini"
  }' | python3 -m json.tool
```

Expect JSON with `answer` (`prose`, `facts`, `conflicts`, `coverage`), `tokens_used`, `model`, `latency_ms`, `cost_usd`, and `age_years_expected`.

Post a full fixture file instead:

```bash
curl -s -X POST "${SERVICE_URL:-http://127.0.0.1:8000}/ask" \
  -H "Content-Type: application/json" \
  -d @fixtures/synthetic_history_case.json | python3 -m json.tool

curl -s -X POST "${SERVICE_URL:-http://127.0.0.1:8000}/ask" \
  -H "Content-Type: application/json" \
  -d @fixtures/synthetic_health_conflict_case.json | python3 -m json.tool
```

## Deploy (Render)

1. Create a **Web Service** from this GitHub repo (your fork, e.g. `teahop/AI-Internship`).
2. Connect the branch Render should deploy (usually `main` after you merge).
3. Set **Root Directory** to `ai-engineering-bootcamp-v2/week-1` (required — `requirements.txt` and `main.py` are not at the repo root).
4. **Build command:** `pip install -r requirements.txt`
5. **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. In **Environment**, add `OPENAI_API_KEY` from your key provider (same value you put in local `.env`). Do not put secrets in the repo or in this README.
7. Deploy, then check `$SERVICE_URL/` for the **provenance build · multi-source** badge, `$SERVICE_URL/health`, and the curl examples above with `$SERVICE_URL`.

If the service already exists: merge to the deploy branch, then **Manual Deploy → Deploy latest commit** (or wait for auto-deploy).

Default model is `gpt-4o` when the request omits `model`. The sample fixtures use `gpt-4o-mini`.

## Smoke tests

```bash
python test_all_stages.py
```

What that covers:

- Unit: age validator + provenance/reporter fidelity validators  
- Stages 1–4: original bootcamp teaching servers (`serve_stage1` … `serve_stage4`)  
- `main` / stage 5: history fixture (age retry, provenance, ≥1 planted conflict)  
- `main`: health-conflict fixture (≥3 conflicts: name, plan status, allergy class; no father-invented allergy)

## Teaching demos (stages 1–4) vs product (`main`)

| Piece | Role |
|-------|------|
| `serve_stage1.py` … `serve_stage4.py` | Original week-1 teaching stages (`question` payload) |
| `main.py` / `serve_stage5.py` | Molly history product — `AskRequest` with dated `sources` |
| `demo_page.py` | Streamlit runner for stages 1–4; stage 5 tab posts a fixture to `main` |
| `static/index.html` | Primary demo UI for the product |

```bash
streamlit run demo_page.py
```

## Project layout

```
week-1/
├── main.py                              # /ask + validators + cost + static home + /fixtures
├── static/index.html                    # Multi-source home UI (provenance build)
├── schemas.py                           # AskRequest, Source, SourcedFact, Conflict, ReportSection
├── validators.py                        # Age, provenance, reporter fidelity, conflict retry helpers
├── system_prompt.md                     # Cite / never invent reporter / surface conflict classes
├── fixtures/synthetic_history_case.json
├── fixtures/synthetic_health_conflict_case.json
├── serve_stage1.py … serve_stage4.py    # Original week-1 teaching stages
├── serve_stage5.py                      # Alias → main
├── demo_page.py                         # Streamlit stage runner
├── test_all_stages.py
├── favicon.png
├── requirements.txt
├── .env.example                         # Template only — copy to .env
└── .gitignore                           # Ignores .env
```

## Week-1 concepts → this product

| Concept | Where it lives |
|---------|----------------|
| Typed input | `AskRequest`: `section`, `child`, dated `sources`, `confirm_synthetic` |
| Provenance spine | `Source` in → `SourcedFact.source_id` / required `source_date` (+ optional `reporter`) out |
| Structured output | `ReportSection` / `SourcedFact` / `Conflict` |
| Guardrail + retry | `validate_age_consistency`, `validate_provenance`, `validate_reporter_fidelity`, conflict soft-retry |
| Home demo | `static/index.html` + `GET /fixtures/{name}` |
| Model / cost | `model`, `tokens_used`, `latency_ms`, `cost_usd` |
