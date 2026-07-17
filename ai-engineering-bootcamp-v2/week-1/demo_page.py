"""Single test page for week-1 /ask demos.

Stages 1–4: original teaching servers (`question` payload).
Stage 5: Molly history product (`main` / `serve_stage5`) — posts a fixture.

Run:
  streamlit run demo_page.py
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import streamlit as st

WORKDIR = Path(__file__).resolve().parent
WORKDIR_CMD = "ai-engineering-bootcamp-v2/week-1"  # path from repo root
HISTORY_FIXTURE = WORKDIR / "fixtures" / "synthetic_history_case.json"
HEALTH_FIXTURE = WORKDIR / "fixtures" / "synthetic_health_conflict_case.json"

STAGES = [
    {
        "num": 1,
        "title": "Bare /ask",
        "serve": "uvicorn serve_stage1:app --port 8000 --reload",
        "look_for": "Plain `answer` string and real `tokens_used`.",
        "mode": "question",
        "dummy_question": "What is Retrieval-Augmented Generation in one sentence?",
        "fields": [],
    },
    {
        "num": 2,
        "title": "Structured output",
        "serve": "uvicorn serve_stage2:app --port 8000 --reload",
        "look_for": "`answer` is an object with `confidence` and `sources_needed`.",
        "mode": "question",
        "dummy_question": "Explain what an embedding is in one sentence.",
        "fields": [],
    },
    {
        "num": 3,
        "title": "Guardrail + retry",
        "serve": "uvicorn serve_stage3:app --port 8000 --reload",
        "look_for": "Normal question works; `force_bad` triggers retry then succeeds.",
        "mode": "question",
        "dummy_question": "What is a vector database?",
        "dummy_force_bad": True,
        "fields": ["force_bad"],
    },
    {
        "num": 4,
        "title": "Model selectable",
        "serve": "uvicorn serve_stage4:app --port 8000 --reload",
        "look_for": "`model` and `latency_ms` in the response; swap models live.",
        "mode": "question",
        "dummy_question": "What is chunking in RAG?",
        "dummy_model": "gpt-4o-mini",
        "fields": ["force_bad", "model"],
    },
    {
        "num": 5,
        "title": "Molly history (main)",
        "serve": "uvicorn main:app --port 8000 --reload",
        "look_for": (
            "ReportSection with attributed `facts`, surfaced `conflicts`, "
            "`cost_usd`, and `age_years_expected`. Prefer the home UI at `/` "
            "for multi-source demos."
        ),
        "mode": "fixture",
        "fields": ["force_bad_age", "model", "fixture"],
        "dummy_model": "gpt-4o-mini",
    },
]


def build_question_payload(
    question: str,
    stage: dict,
    force_bad: bool,
    model: str | None,
) -> dict:
    payload: dict = {"question": question}
    if "force_bad" in stage["fields"]:
        payload["force_bad"] = force_bad
    if "model" in stage["fields"] and model:
        payload["model"] = model
    return payload


def load_fixture_payload(name: str, force_bad_age: bool, model: str | None) -> dict:
    path = HEALTH_FIXTURE if name == "health" else HISTORY_FIXTURE
    payload = json.loads(path.read_text(encoding="utf-8"))
    if force_bad_age:
        payload["force_bad_age"] = True
    if model:
        payload["model"] = model
    return payload


def call_ask(base_url: str, payload: dict) -> tuple[int, dict | str]:
    try:
        response = httpx.post(f"{base_url.rstrip('/')}/ask", json=payload, timeout=180.0)
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
            return response.status_code, response.text
    except httpx.ConnectError:
        return 0, {"error": f"Cannot reach {base_url} — start the stage server first."}
    except httpx.HTTPError as exc:
        return 0, {"error": str(exc)}


def render_curl(base_url: str, payload: dict) -> str:
    body = json.dumps(payload)
    return (
        f'curl -s -X POST {base_url.rstrip("/")}/ask '
        f'-H "Content-Type: application/json" -d \'{body}\''
    )


def render_terminal_block(stage: dict, base_url: str, payload: dict) -> str:
    return f"""cd {WORKDIR_CMD}
source .venv/bin/activate
pip install -r requirements.txt
{stage["serve"]}

# In another terminal — test this stage:
{render_curl(base_url, payload)}"""


st.set_page_config(page_title="Week 1 /ask Demo", layout="wide")
st.title("Week 1 — `/ask` Demo Runner")
st.caption(
    "Stages 1–4 teach the original bootcamp `/ask` shape. "
    "Stage 5 posts a Molly history fixture to `main`. "
    "For the multi-source UI, open http://127.0.0.1:8000/ after starting `main`."
)

base_url = st.sidebar.text_input("API base URL", "http://127.0.0.1:8000")

st.sidebar.markdown("### Run this page")
st.sidebar.code(
    f"cd {WORKDIR_CMD}\nsource .venv/bin/activate\nstreamlit run demo_page.py",
    language="bash",
)

tabs = st.tabs([f"Demo {s['num']}: {s['title']}" for s in STAGES])

for tab, stage in zip(tabs, STAGES):
    with tab:
        st.subheader(f"Demo {stage['num']} — {stage['title']}")
        st.markdown(f"**Look for:** {stage['look_for']}")

        force_bad = stage.get("dummy_force_bad", False)
        force_bad_age = False
        model = stage.get("dummy_model")
        fixture_choice = "history"

        if stage["mode"] == "question":
            default_q = stage["dummy_question"]
            stage_question = st.text_input(
                "Question",
                default_q,
                key=f"q_{stage['num']}",
                placeholder="Type a question to send to /ask…",
            )
            if "force_bad" in stage["fields"]:
                force_bad = st.checkbox(
                    "force_bad (break schema on attempt 1)",
                    value=stage.get("dummy_force_bad", False),
                    key=f"bad_{stage['num']}",
                )
            if "model" in stage["fields"]:
                options = [None, "gpt-4o", "gpt-4o-mini", "o3-mini"]
                default_model = stage.get("dummy_model")
                model = st.selectbox(
                    "model",
                    options,
                    index=options.index(default_model) if default_model in options else 0,
                    format_func=lambda m: m or "gpt-4o (default)",
                    key=f"model_{stage['num']}",
                )
            payload = build_question_payload(stage_question, stage, force_bad, model)
        else:
            fixture_choice = st.selectbox(
                "Fixture",
                ["history", "health"],
                format_func=lambda n: (
                    "synthetic_history_case.json"
                    if n == "history"
                    else "synthetic_health_conflict_case.json"
                ),
                key=f"fixture_{stage['num']}",
            )
            if "force_bad_age" in stage["fields"]:
                force_bad_age = st.checkbox(
                    "force_bad_age (plant wrong age on attempt 0)",
                    value=False,
                    key=f"bad_age_{stage['num']}",
                )
            if "model" in stage["fields"]:
                options = [None, "gpt-4o", "gpt-4o-mini", "o3-mini"]
                default_model = stage.get("dummy_model")
                model = st.selectbox(
                    "model",
                    options,
                    index=options.index(default_model) if default_model in options else 0,
                    format_func=lambda m: m or "gpt-4o (default)",
                    key=f"model_{stage['num']}",
                )
            payload = load_fixture_payload(fixture_choice, force_bad_age, model)
            st.markdown(
                f"**Sources in fixture:** {len(payload.get('sources', []))} "
                f"(open `/` for the multi-source GUI)."
            )

        st.markdown("**Copy & run (terminal 1 — server, terminal 2 — curl):**")
        st.code(render_terminal_block(stage, base_url, payload), language="bash")

        if st.button("Run test", key=f"run_{stage['num']}", type="primary"):
            with st.spinner("Calling /ask..."):
                status, data = call_ask(base_url, payload)
            if status:
                st.markdown(f"**HTTP {status}**")
            st.json(data)

st.sidebar.divider()
st.sidebar.markdown(
    "**Product UI:** `uvicorn main:app` → http://127.0.0.1:8000/\n\n"
    "**Docs:** `week-1/README.md`"
)
