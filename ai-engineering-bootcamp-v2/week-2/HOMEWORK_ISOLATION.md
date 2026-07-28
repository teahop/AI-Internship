# TEMPORARY — Week 2 homework isolation

**Remove this file (and the related Cursor rule / check script) after Maven Session 2 is submitted.**

## Why this exists

Capstone (`week-1/`) and course RAG homework share names like `/ingest` and `/ask` but mean different things. This checklist keeps Path A on the **test build** only.

## Allowed vs forbidden

| Do work here | Do **not** touch for homework |
|---|---|
| `ai-engineering-bootcamp-v2/week-2/rag-homework/` (Session 1 base = upstream `week-1v2`) | `ai-engineering-bootcamp-v2/week-1/` (Molly capstone) |
| `week-2/rag-vector-databases/` (notebook only) | Capstone Render service / `main` deploy root |
| Homework branch: `homework/session-2-rag` | Merging homework into capstone `/ingest` or `/ask` |

**Base code:** official Session 1 homework from
`https://github.com/akshika47/AI-Internship/tree/main/ai-engineering-bootcamp-v2/week-1v2`
(copied into `rag-homework/`). Premature RAG scaffold (if any) sits in `week-2/_premature_rag_scaffold/` — ignore until Path A.

**Pinecone:** use a homework-only index (e.g. `bootcamp-northwind`). Not an ed-code / capstone index.

**Ed-code RAG** (`POST /cite`, spec §9.8) is Phase 3 — learn the pattern here; do not implement it on `week-1` for this assignment.

## Preflight (run every homework session)

From the repo root (`AI-Internship/`):

```bash
./ai-engineering-bootcamp-v2/week-2/check-isolation.sh
```

**Green flags:** status only shows `week-2/...` (plus maybe `.env`), branch is `homework/session-2-rag`.

**Red flags — stop:** any modified source file under `week-1/`, editing classify-`/ingest` or ledger-`/ask`, deploying homework to the Molly Render service.

## What “done” means for Path A (not capstone)

- `POST /ingest` — plain text + `document_id` → chunk/embed/store (Pinecone)
- Retrieval debug before generation (`GET /debug/retrieve`)
- `POST /ask` — answer from docs + cite; refuse when not in docs
- Streamlit UI for ingest + ask
- Public demo URL + screenshot for Maven

## After submit

1. Delete this file, `check-isolation.sh`, and `.cursor/rules/week2-homework-isolation.mdc`.
2. Remove the sticky from `docs/project-management/TODAY.md` / backlog “remove isolation” item.
3. Leave `week-2/rag-homework/` as course archive; keep capstone on `week-1/`.
