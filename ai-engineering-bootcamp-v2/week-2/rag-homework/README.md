# Week 1 v2: Minimal `/ask` Demo

> **Local note (Session 2 prep):** This folder is a copy of
> [`akshika47/AI-Internship` → `ai-engineering-bootcamp-v2/week-1v2`](https://github.com/akshika47/AI-Internship/tree/main/ai-engineering-bootcamp-v2/week-1v2)
> placed under `week-2/rag-homework/` so you can extend Session 1 with RAG
> without touching the Molly capstone in `week-1/`. Your `.env` was kept as-is.

This folder is the simplified class version of the Week 1 AI Engineering bootcamp demo.
Students run one final API and one small Streamlit page. The `stages/` files are optional
teaching references that show how the endpoint grows step by step.

## What Students Will Build

A typed FastAPI endpoint that accepts a question and returns:

- `answer`: a structured answer object
- `tokens_used`: token usage returned by the model provider
- `model`: the model used for the request
- `latency_ms`: how long the request took
- `cost_usd`: an estimated request cost
- `attempts`: validation and retry details for the guardrail demo

The main idea: an LLM call becomes more useful in software when it has a predictable
request shape, a predictable response shape, and observable runtime metadata.

## Quick Start

Run these commands from this `week-1v2` folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
test -f .env || cp .env.example .env
```

Open `.env` and add keys (see `.env.example`):

```bash
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=bootcamp-northwind
```

Create the Pinecone index with **dimension 1536** and **cosine** metric
(`text-embedding-3-small`). Same embedding model is used at ingest and query.

Check Pinecone without running RAG yet:

```bash
curl -s http://127.0.0.1:8000/debug/pinecone | python3 -m json.tool
```

## Terminal 1: Start the API

```bash
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Check that the API is running without spending tokens:

```bash
curl http://127.0.0.1:8000/health
```

You can also open the generated API docs:

```text
http://127.0.0.1:8000/docs
```

## Terminal 2: Start the Demo Page

```bash
source .venv/bin/activate
streamlit run demo_page.py
```

Open:

```text
http://localhost:8501
```

Use the page to ask a question, switch models, inspect the JSON response, and copy the
equivalent `curl` request.

## Try the Guardrail Demo

Turn on **Force a bad first response to demo validation + retry** in the Streamlit page.
The API intentionally asks the model for malformed JSON on the first attempt, validates
that response with Pydantic, records the failure, and retries with structured output.

This is a small classroom-friendly example of a production habit: do not trust free-form
LLM output at the boundary of your application.

## Test With Curl

Normal request:

```bash
curl -s -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Retrieval-Augmented Generation in one sentence?", "model": "gpt-4o-mini"}'
```

Validation and retry demo:

```bash
curl -s -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is a vector database?", "model": "gpt-4o-mini", "force_bad": true}'
```

## Instructor Flow

Use `main.py` and `demo_page.py` for the live student demo. Open the stage files only when
you want to explain how each capability was introduced:

| Stage | File | Teaching point |
|-------|------|----------------|
| 1 | `stages/stage_1_bare_ask.py` | Smallest typed `/ask`: question in, string answer out. |
| 2 | `stages/stage_2_structured_output.py` | Add a Pydantic `Answer` schema and OpenAI structured output. |
| 3 | `stages/stage_3_guardrails_and_observability.py` | Add validation retry, model selection, latency, and cost. |

Run one stage at a time if you want to teach the build-up live:

```bash
uvicorn stages.stage_1_bare_ask:app --host 127.0.0.1 --port 8000 --reload
uvicorn stages.stage_2_structured_output:app --host 127.0.0.1 --port 8000 --reload
uvicorn stages.stage_3_guardrails_and_observability:app --host 127.0.0.1 --port 8000 --reload
```

## Smoke Test

This starts the final API, checks `/health` and `/docs`, and does not call OpenAI:

```bash
source .venv/bin/activate
python smoke_test.py
```

## File Map

```text
week-1v2/
├── README.md
├── main.py                         # Final API used by students
├── demo_page.py                    # Streamlit UI for the final API
├── smoke_test.py                   # No-token API startup check
├── requirements.txt
├── .env.example
├── .gitignore
└── stages/
    ├── stage_1_bare_ask.py
    ├── stage_2_structured_output.py
    └── stage_3_guardrails_and_observability.py
```

## Troubleshooting

- `Cannot reach http://127.0.0.1:8000`: start the API server in another terminal.
- `OPENAI_API_KEY` error: make sure `.env` exists and contains a real key.
- `Address already in use`: another server is already using port `8000`; stop it or use a different port.
- Streamlit opens but requests fail: confirm the sidebar API base URL is `http://127.0.0.1:8000`.

## Golden-set eval (Path A)

Corpus: [Northwind Robotics Employee Handbook](https://tailabs.ai/courses/ai-eng-syllabus/sample_docs/doc1_handbook.txt) (`document_id`: `pol-101-handbook`).  
Fill **retrieval hit / faithfulness / correctness** with `Y` or `N` after running against your live API.  
**Retrieval hit** = a relevant handbook chunk appears in top-5. **Faithfulness** = answer only uses retrieved text. **Correctness** = matches expected (or correctly refuses).

| #   | question                                                                   | expected answer (short)                      | retrieval hit? | faithfulness? | correctness? |
| --- | -------------------------------------------------------------------------- | -------------------------------------------- | -------------- | ------------- | ------------ |
| 1   | What are the standard working hours at Northwind Robotics?                 | 09:00–17:30, Monday–Friday                   | Y              | Y             | Y            |
| 2   | How many days per week may employees work remotely?                        | Up to three days per week                    | Y              | Y             | Y            |
| 3   | What are Slack core hours for remote employees?                            | 10:00–15:00                                  | Y              | Y             | Y            |
| 4   | How much annual leave do employees get?                                    | 28 days plus public holidays                 | Y              | Y             | Y            |
| 5   | Who must approve a fully remote arrangement, and how often is it reviewed? | Director approval; reviewed every six months | Y              | Y             | Y            |
| 6   | What is the company’s 401(k) matching percentage?                          | **Refusal** — not in the handbook            | Y              | Y             | Y            |

### How to run each row
1. `GET /debug/retrieve?q=...&top_k=5` → mark **retrieval hit**
2. `POST /ask` with the same question → mark **faithfulness** and **correctness**
3. For #6, expect `refused: true` (or clear “cannot answer from documents”) and no invented benefits policy

### Pass bar (suggested)
- Rows 1–5: all three columns `Y`
- Row 6: retrieval may be weak/irrelevant; **faithfulness** + **correctness** = `Y` only if the model **refuses** (does not invent a 401k answer)