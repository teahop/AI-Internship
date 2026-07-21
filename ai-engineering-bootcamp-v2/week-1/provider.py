"""Thin model provider adapter — the only module that talks to an LLM client.

Interface: give me this schema back. Production may swap OpenAI for BastionGPT
without rewriting callers; BastionGPT may lack structured `response_format`,
in which case JSON-mode + parse/repair stays inside this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "gpt-4o"

# Sampling: extraction / ingest / entailment want correct answers (temp 0).
# Drafting stays at the prior API default — clinician-valued prose quality.
EXTRACT_TEMPERATURE = 0.0
INGEST_TEMPERATURE = 0.0
ENTAILMENT_TEMPERATURE = 0.0
DRAFT_TEMPERATURE = 1.0  # Named/configurable; A/B later — do not silently drop to 0.

MODEL_PRICES_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "o3-mini": (0.0011, 0.0044),
}


@dataclass(frozen=True, slots=True)
class StructuredResult:
    data: BaseModel
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int


def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = MODEL_PRICES_PER_1K.get(model, MODEL_PRICES_PER_1K[DEFAULT_MODEL])
    input_per_1k, output_per_1k = prices
    return (prompt_tokens / 1000 * input_per_1k) + (completion_tokens / 1000 * output_per_1k)


class ModelProvider:
    """Structured-output adapter. Callers never import OpenAI directly."""

    def __init__(self, client: OpenAI | None = None) -> None:
        self._client = client or OpenAI()

    def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = None,
    ) -> StructuredResult:
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": schema,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        completion = self._client.chat.completions.parse(**kwargs)
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"Model returned no parseable {schema.__name__}")

        usage = completion.usage
        return StructuredResult(
            data=parsed,
            total_tokens=usage.total_tokens if usage else 0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
