"""Molly history draft API — typed sources in, attributed ReportSection out, age guardrail, cost."""

from __future__ import annotations

import json
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from openai import OpenAI
from pydantic import ValidationError

from schemas import AskRequest, AskResponse, ReportSection, SourcedFact
from validators import compute_age_years, validate_age_consistency

_DIR = Path(__file__).resolve().parent
load_dotenv(_DIR / ".env")

SYSTEM_PROMPT = (_DIR / "system_prompt.md").read_text(encoding="utf-8")

app = FastAPI(
    title="Molly History Draft (synthetic OpenAI build)",
    description=(
        "Learning/build runtime on OpenAI — synthetic data only. "
        "Production drafting for real cases runs on BastionGPT (BAA), not this repo."
    ),
)
client = OpenAI()

DEFAULT_MODEL = "gpt-4o"

MODEL_PRICES_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "o3-mini": (0.0011, 0.0044),
}


def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = MODEL_PRICES_PER_1K.get(model, MODEL_PRICES_PER_1K[DEFAULT_MODEL])
    input_per_1k, output_per_1k = prices
    return (prompt_tokens / 1000 * input_per_1k) + (completion_tokens / 1000 * output_per_1k)


def _user_payload(body: AskRequest) -> str:
    """Serialize the typed case packet for the model (no real PHI in this build)."""

    expected_age = compute_age_years(body.child.dob, body.child.evaluation_date)
    packet = {
        "section": body.section,
        "child": body.child.model_dump(),
        "expected_age_years": expected_age,
        "instruction": (
            f"State age only as {expected_age} years if you mention age. "
            "Ignore any other ages appearing inside sources."
        ),
        "sources": [s.model_dump() for s in body.sources],
    }
    return json.dumps(packet, indent=2)


def _plant_bad_age_section(body: AskRequest) -> ReportSection:
    """Deterministic bad draft so tests can prove the age validator fires."""

    wrong_age = compute_age_years(body.child.dob, body.child.evaluation_date) + 2
    source = body.sources[0]
    return ReportSection(
        section="history",
        prose=(
            f"{body.child.initials} is a {wrong_age}-year-old student referred for "
            "evaluation of reading concerns. (PLANTED BAD AGE FOR VALIDATOR DEMO.)"
        ),
        facts=[
            SourcedFact(
                statement=f"{body.child.initials} is {wrong_age} years old.",
                source_id=source.id,
                source_date=source.date,
                life_stage="current",
            )
        ],
        conflicts=[],
        coverage=["current"],
    )


def call_model_structured(body: AskRequest, model: str) -> tuple[ReportSection, int, int, int]:
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_payload(body)},
        ],
        response_format=ReportSection,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("Model returned no parseable ReportSection")

    usage = completion.usage
    total = usage.total_tokens if usage else 0
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    return parsed, total, prompt_tokens, completion_tokens


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    sample_ask = (
        "Please draft a Background & History section for this synthetic student.\n\n"
        "Parent notes: Pregnancy uncomplicated. Full-term vaginal birth, no NICU. "
        "Walked at 13 months. Concerns began in kindergarten with letter-sound learning.\n\n"
        "Teacher notes (grade 4): Reading fluency below peers; spelling weak; "
        "anxious when asked to read aloud.\n\n"
        "Mention age only if it matches DOB + evaluation date. Call out any conflicts."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TJ's test service for MH</title>
  <link rel="icon" type="image/png" href="/favicon.png" />
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 44rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #1a1a1a; }}
    h1 {{ font-size: 1.25rem; font-weight: 600; }}
    h2 {{ font-size: 1.05rem; font-weight: 600; margin-top: 2rem; }}
    .ascii {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.85rem;
      line-height: 1.15;
      white-space: pre;
      color: #2a6f6f;
      margin: 1.25rem 0;
      overflow-x: auto;
    }}
    ul {{ padding-left: 1.2rem; }}
    code {{ background: #f3f3f3; padding: 0.1em 0.35em; border-radius: 3px; }}
    .try {{
      margin-top: 1.5rem;
      padding: 1rem 1.1rem;
      border: 1px solid #d8e3e3;
      border-radius: 8px;
      background: #f7fbfb;
    }}
    .try p.hint {{ color: #444; font-size: 0.95rem; margin-top: 0; }}
    label {{ display: block; font-weight: 600; margin: 0.75rem 0 0.35rem; }}
    .fields {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
      gap: 0.75rem;
      margin-top: 0.5rem;
    }}
    .fields label {{ margin-top: 0; }}
    input[type="text"], input[type="date"], textarea {{
      width: 100%;
      box-sizing: border-box;
      font: inherit;
      padding: 0.55rem 0.65rem;
      border: 1px solid #b8c9c9;
      border-radius: 6px;
    }}
    textarea {{
      min-height: 12rem;
      line-height: 1.45;
      resize: vertical;
    }}
    .row {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-top: 0.75rem; }}
    .row label.chk {{ font-weight: 500; display: inline-flex; gap: 0.4rem; align-items: center; margin: 0; }}
    button {{
      background: #2a6f6f;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 0.55rem 1rem;
      font-size: 0.95rem;
      cursor: pointer;
    }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    #status {{ font-size: 0.9rem; color: #555; }}
    #status.error {{ color: #a33; }}
    .answer {{
      margin-top: 0.75rem;
      padding: 0.9rem 1rem;
      background: #fff;
      border: 1px solid #d8e3e3;
      border-radius: 6px;
      white-space: pre-wrap;
      min-height: 3rem;
    }}
    .meta {{ font-size: 0.9rem; color: #555; margin-top: 0.5rem; }}
    details {{ margin-top: 0.75rem; }}
    details summary {{ cursor: pointer; color: #2a6f6f; font-weight: 500; }}
    pre.out {{
      margin-top: 0.5rem;
      padding: 0.75rem;
      background: #111;
      color: #e8f0e8;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 0.75rem;
      line-height: 1.35;
      white-space: pre-wrap;
      word-break: break-word;
    }}
  </style>
</head>
<body>
  <pre class="ascii" aria-hidden="true">
      .--.
     /  o o\\     ~ scribble scribble ~
    |   &gt;  |        (synthetic only)
     \\  --/
    .-`++++`-.
   /  NOTES  \\
   | ~~~~    |
   | ~~~~ .. |
   '---------'
      |  |
     ======
  </pre>
  <h1>Welcome to TJ's test service for MH.</h1>
  <p>Here are the things you can look at and try:</p>
  <ul>
    <li><a href="/health">/health</a> — quick “is the service up?” check</li>
    <li><a href="/docs">/docs</a> — interactive API docs (try <code>POST /ask</code> here)</li>
    <li><a href="/redoc">/redoc</a> — alternate readable API docs</li>
    <li><code>POST /ask</code> — draft a synthetic Background &amp; History (form below, <a href="/docs">/docs</a>, or curl)</li>
  </ul>

  <section class="try" aria-labelledby="try-title">
    <h2 id="try-title">Ask in plain English</h2>
    <p class="hint">
      Type what you want drafted, like you would tell a colleague.
      We’ll package it into the <code>/ask</code> request for you.
      Synthetic / fake data only — no real client records.
    </p>

    <label for="ask">Your ask</label>
    <textarea id="ask" placeholder="e.g. Draft a Background &amp; History from these notes…">{sample_ask}</textarea>

    <div class="fields">
      <div>
        <label for="initials">Initials</label>
        <input id="initials" type="text" value="A.R." autocomplete="off" />
      </div>
      <div>
        <label for="dob">Date of birth</label>
        <input id="dob" type="date" value="2017-03-15" />
      </div>
      <div>
        <label for="evalDate">Evaluation date</label>
        <input id="evalDate" type="date" value="2026-07-16" />
      </div>
    </div>

    <div class="row">
      <label class="chk"><input type="checkbox" id="forceBad" /> Demo age guardrail (force_bad_age)</label>
      <button type="button" id="runBtn">Run draft</button>
      <span id="status"></span>
    </div>

    <label for="answer">Draft</label>
    <div class="answer" id="answer">(draft will show up here)</div>
    <p class="meta" id="meta"></p>
    <details>
      <summary>Show full JSON response</summary>
      <pre class="out" id="result"></pre>
    </details>
  </section>

  <p>Synthetic / de-identified data only. Do not paste real client records.</p>

  <script>
    const askEl = document.getElementById("ask");
    const initialsEl = document.getElementById("initials");
    const dobEl = document.getElementById("dob");
    const evalEl = document.getElementById("evalDate");
    const forceBadEl = document.getElementById("forceBad");
    const runBtn = document.getElementById("runBtn");
    const statusEl = document.getElementById("status");
    const answerEl = document.getElementById("answer");
    const metaEl = document.getElementById("meta");
    const resultEl = document.getElementById("result");

    function buildBody() {{
      const ask = askEl.value.trim();
      if (!ask) throw new Error("Write your ask in the text box first.");
      const initials = (initialsEl.value || "A.R.").trim();
      const dob = dobEl.value;
      const evaluation_date = evalEl.value;
      if (!dob || !evaluation_date) throw new Error("Pick a DOB and evaluation date.");

      const body = {{
        confirm_synthetic: true,
        section: "history",
        child: {{ initials, dob, evaluation_date }},
        sources: [{{
          id: "user-ask",
          type: "other",
          date: evaluation_date,
          label: "User ask (plain English)",
          content: ask,
        }}],
        model: "gpt-4o-mini",
      }};
      if (forceBadEl.checked) body.force_bad_age = true;
      return body;
    }}

    runBtn.addEventListener("click", async () => {{
      statusEl.className = "";
      statusEl.textContent = "Thinking… (can take ~10–30s)";
      answerEl.textContent = "";
      metaEl.textContent = "";
      resultEl.textContent = "";
      runBtn.disabled = true;

      let body;
      try {{
        body = buildBody();
      }} catch (err) {{
        statusEl.className = "error";
        statusEl.textContent = String(err.message || err);
        runBtn.disabled = false;
        return;
      }}

      try {{
        const res = await fetch("/ask", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(body),
        }});
        const text = await res.text();
        let data = null;
        try {{ data = JSON.parse(text); }} catch (_) {{}}
        resultEl.textContent = data ? JSON.stringify(data, null, 2) : text;

        if (res.ok && data && data.answer) {{
          answerEl.textContent = data.answer.prose || JSON.stringify(data.answer, null, 2);
          metaEl.textContent =
            "Model: " + (data.model || "?") +
            " · Tokens: " + (data.tokens_used ?? "?") +
            " · Cost: $" + (data.cost_usd ?? "?") +
            " · Expected age: " + (data.age_years_expected ?? "?");
          statusEl.textContent = "Done · HTTP " + res.status;
        }} else {{
          statusEl.className = "error";
          statusEl.textContent = "Error · HTTP " + res.status;
          answerEl.textContent = data && data.detail
            ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail, null, 2))
            : text;
        }}
      }} catch (err) {{
        statusEl.className = "error";
        statusEl.textContent = "Request failed";
        answerEl.textContent = String(err);
      }} finally {{
        runBtn.disabled = false;
      }}
    }});
  </script>
</body>
</html>
"""


@app.get("/favicon.png", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_DIR / "favicon.png", media_type="image/png")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "runtime": "openai-synthetic-only",
        "production": "bastiongpt-baa-not-this-repo",
    }


@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    """
    Draft Background & History with source-attributed facts.

    Guard: confirm_synthetic must be true (enforced by schema Literal[True]).
    Validator: age must match dob + evaluation_date; one retry on failure.
    """

    model = body.model or DEFAULT_MODEL
    last_error: str | None = None
    tokens_used = prompt_tokens = completion_tokens = 0

    # Allow a second model retry after the planted failure (age echoes from stale
    # sources are occasional); three attempts keeps the demo reliable.
    max_attempts = 3 if body.force_bad_age else 2

    for attempt in range(max_attempts):
        try:
            start = time.perf_counter()

            if body.force_bad_age and attempt == 0:
                section = _plant_bad_age_section(body)
                # No OpenAI spend on the planted failure — still exercise the guardrail path.
                tokens_used = prompt_tokens = completion_tokens = 0
            else:
                section, tokens_used, prompt_tokens, completion_tokens = call_model_structured(
                    body, model
                )

            expected_age = validate_age_consistency(
                section,
                dob=body.child.dob,
                evaluation_date=body.child.evaluation_date,
            )

            latency_ms = int((time.perf_counter() - start) * 1000)
            cost_usd = compute_cost_usd(model, prompt_tokens, completion_tokens)

            return AskResponse(
                answer=section,
                tokens_used=tokens_used,
                model=model,
                latency_ms=latency_ms,
                cost_usd=round(cost_usd, 6),
                age_years_expected=expected_age,
            )
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            continue

    raise HTTPException(
        status_code=502,
        detail=f"Draft failed age/schema validation after retry: {last_error}",
    )
