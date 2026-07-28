"""Minimal Streamlit UI for Session 2 Path A — calls the live FastAPI RAG API.

Does NOT reimplement RAG. Only POST /ingest and POST /ask.

Run:
  cd ai-engineering-bootcamp-v2/week-2/rag-homework
  source .venv/bin/activate
  streamlit run demo_page.py
"""

from __future__ import annotations

import json
import os

import httpx
import streamlit as st

DEFAULT_API = os.getenv(
    "RAG_API_URL",
    "https://session-2-rag-homework.onrender.com",
)


def call_post(url: str, payload: dict) -> tuple[int, dict | str]:
    try:
        response = httpx.post(url, json=payload, timeout=120.0)
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
            return response.status_code, response.text
    except httpx.ConnectError:
        return 0, {"error": f"Cannot reach {url}. Is the API URL correct / awake?"}
    except httpx.HTTPError as exc:
        return 0, {"error": str(exc)}


def show_ask_result(data: dict | str) -> None:
    if not isinstance(data, dict):
        st.code(str(data))
        return

    if "detail" in data and "answer" not in data:
        st.error(data["detail"] if isinstance(data["detail"], str) else json.dumps(data["detail"], indent=2))
        return

    answer = data.get("answer") or {}
    refused = bool(data.get("refused"))
    text = answer.get("answer", "")

    if refused:
        st.warning("**Refusal** — answer not grounded in ingested docs")
    else:
        st.success("**Answer from retrieved docs**")

    st.write(text)

    chunk_ids = data.get("retrieved_chunk_ids") or []
    st.markdown("**Cited / retrieved chunk IDs**")
    if chunk_ids:
        for cid in chunk_ids:
            st.code(cid, language=None)
    else:
        st.caption("(none)")

    with st.expander("Full JSON response"):
        st.json(data)


st.set_page_config(page_title="Session 2 RAG — ingest + ask", layout="centered")
st.title("Session 2 RAG — ingest + ask")
st.caption("UI only. FastAPI on Render is the source of truth.")

base_url = st.sidebar.text_input(
    "API base URL",
    value=DEFAULT_API,
    help="Override with env RAG_API_URL, or paste local http://127.0.0.1:8000",
).rstrip("/")

if st.sidebar.button("Check /health"):
    try:
        r = httpx.get(f"{base_url}/health", timeout=30.0)
        st.sidebar.write(r.status_code)
        st.sidebar.json(r.json())
    except httpx.HTTPError as exc:
        st.sidebar.error(str(exc))

tab_ingest, tab_ask = st.tabs(["Ingest", "Ask"])

with tab_ingest:
    st.subheader("POST /ingest")
    document_id = st.text_input("document_id", value="pol-101-handbook")
    source = st.text_input("source (optional filename/label)", value="doc1_handbook.txt")
    text = st.text_area("Document text", height=240, placeholder="Paste handbook or other plain text…")

    if st.button("Ingest document", type="primary"):
        if not document_id.strip() or not text.strip():
            st.error("document_id and text are required")
        else:
            payload = {
                "document_id": document_id.strip(),
                "text": text,
            }
            if source.strip():
                payload["source"] = source.strip()
            with st.spinner("Calling /ingest…"):
                status, data = call_post(f"{base_url}/ingest", payload)
            st.caption(f"HTTP {status}")
            if status == 200 and isinstance(data, dict):
                st.success(
                    f"Indexed `{data.get('document_id')}` — "
                    f"{data.get('chunks_indexed')} chunk(s) — status={data.get('status')}"
                )
            st.json(data)

with tab_ask:
    st.subheader("POST /ask")
    question = st.text_input(
        "Question",
        value="What are the standard working hours at Northwind Robotics?",
    )
    top_k = st.slider("top_k", min_value=1, max_value=10, value=5)

    col_a, col_b = st.columns(2)
    with col_a:
        ask_clicked = st.button("Ask", type="primary")
    with col_b:
        refuse_demo = st.button("Try refusal demo (401k)")

    if refuse_demo:
        question = "What is the company 401(k) matching percentage?"
        ask_clicked = True

    if ask_clicked:
        if not question.strip():
            st.error("question is required")
        else:
            payload = {"question": question.strip(), "top_k": top_k}
            with st.spinner("Calling /ask (retrieve + generate)…"):
                status, data = call_post(f"{base_url}/ask", payload)
            st.caption(f"HTTP {status}")
            if status == 200:
                show_ask_result(data)
            else:
                st.error("Request failed")
                st.json(data if isinstance(data, (dict, list)) else {"body": data})
