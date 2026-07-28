# Maven Session 2 — Path A submission pack

**Repo / branch:** https://github.com/teahop/AI-Internship/tree/homework/session-2-rag  
**Service code:** `ai-engineering-bootcamp-v2/week-2/rag-homework/`  
**Not the Molly capstone** (`week-1/`). Separate homework deploy only.

## 1. Live URL

```text
LIVE_URL=https://session-2-rag-homework.onrender.com
```

Dashboard: https://dashboard.render.com/web/srv-d9khshu1egvs73fksdl0  
Branch: `homework/session-2-rag` (homework only — not Molly/`week-1`).

**Before curls work:** in Render → Environment, set `OPENAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` (same as local `.env`), then wait for deploy to go live.

Health check:

```bash
curl -s "https://session-2-rag-homework.onrender.com/health" | python3 -m json.tool
```

## 2. Ingest curl (handbook)

```bash
LIVE_URL=https://session-2-rag-homework.onrender.com

curl -s -X POST "$LIVE_URL/ingest" \
  -H 'content-type: application/json' \
  -d "$(python3 - <<'PY'
import json, urllib.request
url = "https://tailabs.ai/courses/ai-eng-syllabus/sample_docs/doc1_handbook.txt"
text = urllib.request.urlopen(url).read().decode()
print(json.dumps({
    "document_id": "pol-101-handbook",
    "source": "doc1_handbook.txt",
    "text": text,
}))
PY
)" | python3 -m json.tool
```

## 3. Ask curls

### Doc-grounded answer (with cited chunk IDs)

```bash
curl -s -X POST "$LIVE_URL/ask" \
  -H 'content-type: application/json' \
  -d '{"question":"What are the standard working hours at Northwind Robotics?","top_k":5}' \
  | python3 -m json.tool
```

**Local example response (replace with live output when available):**

```json
{
  "answer": {
    "answer": "The standard working hours at Northwind Robotics are 09:00 to 17:30, Monday to Friday.",
    "confidence": 1.0,
    "sources_needed": false
  },
  "retrieved_chunk_ids": [
    "pol-101-handbook::chunk-0",
    "northwind-overview::chunk-1",
    "northwind-overview::chunk-0",
    "handbook::chunk-0",
    "northwind-shipping::chunk-0"
  ],
  "refused": false,
  "tokens_used": 874,
  "cost_usd": 0.000145
}
```

Primary supporting chunk: `pol-101-handbook::chunk-0`.

### Refusal (not in docs)

```bash
curl -s -X POST "$LIVE_URL/ask" \
  -H 'content-type: application/json' \
  -d '{"question":"What is the company 401(k) matching percentage?","top_k":5}' \
  | python3 -m json.tool
```

**Local example response:**

```json
{
  "answer": {
    "answer": "The context provided does not include any information about the company's 401(k) matching percentage.",
    "confidence": 0.0,
    "sources_needed": true
  },
  "retrieved_chunk_ids": [
    "handbook::chunk-0",
    "pol-101-handbook::chunk-0",
    "northwind-overview::chunk-0",
    "northwind-shipping::chunk-0",
    "northwind-shipping::chunk-1"
  ],
  "refused": true,
  "tokens_used": 819,
  "cost_usd": 0.000135
}
```

## 4. Streamlit screenshot

1. API live (local or Render).
2. Local UI:

```bash
cd ai-engineering-bootcamp-v2/week-2/rag-homework
source .venv/bin/activate
streamlit run demo_page.py
```

3. Sidebar **API base URL** = your `LIVE_URL` (or `http://127.0.0.1:8000` for local).
4. Ask the working-hours question; screenshot the page showing the answer JSON (include `retrieved_chunk_ids`).
5. Optionally second screenshot for the 401(k) refusal.

## 5. Deploy checklist (Render — homework only)

- New Web Service (do **not** reuse Molly/`week-1` service)
- Repo: `teahop/AI-Internship`
- Branch: `homework/session-2-rag`
- Root Directory: `ai-engineering-bootcamp-v2/week-2/rag-homework`  
  (or build/start with `cd` into that folder)
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Env: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`  
  (same names as local `.env`; never commit `.env`)

After first deploy, run the **ingest curl** once against `LIVE_URL` so Pinecone is populated for that environment (index is shared if you reuse the same Pinecone index).
