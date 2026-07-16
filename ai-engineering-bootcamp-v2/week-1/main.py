"""Molly history draft API — typed sources in, attributed ReportSection out, age guardrail, cost."""

from __future__ import annotations

import json
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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
