"""Molly history draft API — typed sources in, attributed ReportSection out, age/provenance guardrails, cost."""

from __future__ import annotations

import json
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import ValidationError

from schemas import AskRequest, AskResponse, ReportSection, SourcedFact
from validators import (
    CONFLICT_RETRY_INSTRUCTION,
    REPORTER_RETRY_INSTRUCTION,
    compute_age_years,
    needs_conflict_retry,
    validate_age_consistency,
    validate_provenance,
    validate_reporter_fidelity,
)

_DIR = Path(__file__).resolve().parent
_FIXTURES = _DIR / "fixtures"
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


def _user_payload(body: AskRequest, extra_instruction: str | None = None) -> str:
    """Serialize the typed case packet for the model (no real PHI in this build)."""

    expected_age = compute_age_years(body.child.dob, body.child.evaluation_date)
    instruction = (
        f"State age only as {expected_age} years if you mention age. "
        "Ignore any other ages appearing inside sources."
    )
    if extra_instruction:
        instruction = f"{instruction}\n\n{extra_instruction}"
    packet = {
        "section": body.section,
        "child": body.child.model_dump(),
        "expected_age_years": expected_age,
        "instruction": instruction,
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
                reporter=None,
            )
        ],
        conflicts=[],
        coverage=["current"],
    )


def call_model_structured(
    body: AskRequest,
    model: str,
    *,
    extra_instruction: str | None = None,
) -> tuple[ReportSection, int, int, int]:
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_payload(body, extra_instruction)},
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


@app.get("/")
def home() -> FileResponse:
    """Multi-source demo UI (static file — avoids blank pages from f-string HTML)."""
    return FileResponse(_DIR / "static" / "index.html", media_type="text/html; charset=utf-8")


@app.get("/favicon.png", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_DIR / "favicon.png", media_type="image/png")


@app.get("/fixtures/{name}")
def get_fixture(name: str) -> FileResponse:
    """Serve synthetic fixtures for the multi-source home UI."""

    safe = Path(name).name
    if not safe.endswith(".json"):
        raise HTTPException(status_code=404, detail="Fixture not found")
    path = _FIXTURES / safe
    if not path.is_file() or not path.resolve().is_relative_to(_FIXTURES.resolve()):
        raise HTTPException(status_code=404, detail="Fixture not found")
    return FileResponse(path, media_type="application/json")


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
    Validators: age, provenance (source_id/date), and one conflict re-check on multi-source empties.
    """

    model = body.model or DEFAULT_MODEL
    last_error: str | None = None
    tokens_used = prompt_tokens = completion_tokens = 0
    extra_instruction: str | None = None
    conflict_retried = False

    # force_bad_age burns attempt 0; conflict re-check may burn another; keep headroom.
    max_attempts = 4 if body.force_bad_age else 3

    for attempt in range(max_attempts):
        try:
            start = time.perf_counter()

            if body.force_bad_age and attempt == 0:
                section = _plant_bad_age_section(body)
                # No OpenAI spend on the planted failure — still exercise the guardrail path.
                tokens_used = prompt_tokens = completion_tokens = 0
            else:
                section, tokens_used, prompt_tokens, completion_tokens = call_model_structured(
                    body, model, extra_instruction=extra_instruction
                )

            expected_age = validate_age_consistency(
                section,
                dob=body.child.dob,
                evaluation_date=body.child.evaluation_date,
            )
            validate_provenance(section, body.sources)
            validate_reporter_fidelity(section, body.sources)

            if needs_conflict_retry(section, body.sources) and not conflict_retried:
                conflict_retried = True
                extra_instruction = CONFLICT_RETRY_INSTRUCTION
                last_error = "multi-source draft returned no conflicts; retrying with conflict check"
                continue

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
            if "Reporter fidelity failed" in last_error:
                extra_instruction = REPORTER_RETRY_INSTRUCTION
            continue

    raise HTTPException(
        status_code=502,
        detail=f"Draft failed validation after retry: {last_error}",
    )
