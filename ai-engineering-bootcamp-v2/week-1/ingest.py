"""Cheap document classification for /ingest — metadata suggestion only."""

from __future__ import annotations

from provider import INGEST_TEMPERATURE, ModelProvider
from schemas import IngestSuggestion

INGEST_SYSTEM_PROMPT = """
You classify one raw educational / clinical document for a psycho-educational case packet.

Return only:
- source_type: one of assessment, school, parent, teacher, observation, prior_eval, other
- source_date: ISO YYYY-MM-DD — the document's own date if stated; if only a month/year,
  use the first of that month; if no date appears, use today's date from the user message
  and keep label honest that the date was not found
- label: short human label (e.g. "School Nurse Health Report", "Parent developmental history")

Never invent clinical content. Classification only. Synthetic data.
""".strip()


def classify_document(
    provider: ModelProvider,
    *,
    content: str,
    model: str,
    today: str,
) -> tuple[IngestSuggestion, int, int, int]:
    user = (
        f"Today's date (fallback only if the document states no date): {today}\n\n"
        f"Document:\n{content}"
    )
    result = provider.complete_structured(
        model=model,
        system=INGEST_SYSTEM_PROMPT,
        user=user,
        schema=IngestSuggestion,
        temperature=INGEST_TEMPERATURE,
    )
    suggestion = result.data
    assert isinstance(suggestion, IngestSuggestion)
    return (
        suggestion,
        result.total_tokens,
        result.prompt_tokens,
        result.completion_tokens,
    )
