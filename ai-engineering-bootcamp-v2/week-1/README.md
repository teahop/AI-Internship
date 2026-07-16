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

- Health: http://127.0.0.1:8000/health  
- Interactive docs: http://127.0.0.1:8000/docs  

`GET /` has no route — a bare root URL returns `{"detail":"Not Found"}`.

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

Expect JSON with `answer`, `tokens_used`, `cost_usd`, and `age_years_expected`.

If you have the repo checked out, you can still use the fuller fixture file instead: `-d @fixtures/synthetic_history_case.json`.

## Deploy (Render)

1. Create a **Web Service** from this GitHub repo.
2. Set **Root Directory** to `ai-engineering-bootcamp-v2/week-1` (required — `requirements.txt` and `main.py` are not at the repo root).
3. **Build command:** `pip install -r requirements.txt`
4. **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. In **Environment**, add `OPENAI_API_KEY` from your key provider (same value you put in local `.env`). Do not put secrets in the repo or in this README.
6. Deploy, then check `$SERVICE_URL/health` and the curl examples above with `$SERVICE_URL`.

Default model is `gpt-4o` when the request omits `model`. The sample fixture uses `gpt-4o-mini`.

## What `/ask` does (Molly’s #1 task)

Accepts a typed case packet (`section`, `child`, dated `sources`) and returns a `ReportSection` where:

- **`prose`** — paste-ready Background & History draft  
- **`facts`** — every claim with `source_id` + `source_date` + `life_stage`  
- **`conflicts`** — disagreements surfaced, not silently resolved  
- **`coverage`** — which life stages appear (birth → present)

A deterministic **age/DOB validator** recomputes age from `dob` + `evaluation_date` and rejects drafts that assert a different age (retry; if retries fail, returns a controlled error).

## Smoke tests

```bash
python test_all_stages.py
```

Stages 1–4 remain the original bootcamp demos. Stage 5 / `main` runs the Molly history fixture and asserts the age validator fires on a planted bad age.

## Project layout

```
week-1/
├── main.py                         # Domain /ask (history draft + validators + cost)
├── schemas.py                      # Typed request + ReportSection output
├── validators.py                   # Age/DOB consistency (Phase 2 seed)
├── system_prompt.md                # History drafting conventions
├── fixtures/synthetic_history_case.json
├── serve_stage1.py … serve_stage4.py   # Original week-1 teaching stages
├── serve_stage5.py                 # Alias → main
├── test_all_stages.py
├── requirements.txt
├── .env.example                    # Template only — copy to .env
└── .gitignore                      # Ignores .env
```

## Week-1 concepts → this product

| Concept | Where it lives |
|---------|----------------|
| Typed input | `AskRequest`: `section`, `child`, dated `sources`, `confirm_synthetic` |
| Structured output | `ReportSection` / `SourcedFact` / `Conflict` |
| Guardrail + retry | `validate_age_consistency` (replaces demo `force_bad`) |
| Model / cost | unchanged — `model`, `tokens_used`, `cost_usd` |
