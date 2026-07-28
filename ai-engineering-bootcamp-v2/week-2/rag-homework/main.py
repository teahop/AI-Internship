"""Week 1 v2 demo API: one compact `/ask` endpoint for the intro class.

Run:
  uvicorn main:app --host 127.0.0.1 --port 8000 --reload
"""

import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from vector_store import (
    EMBEDDING_MODEL,
    check_pinecone,
    ingest_document,
    missing_env_vars,
    retrieve_chunks,
)

THIS_DIR = Path(__file__).resolve().parent
load_dotenv(THIS_DIR / ".env")
load_dotenv(THIS_DIR.parent / ".env")

app = FastAPI(
    title="Week 1 v2 /ask Demo (+ Pinecone prep)",
    description=(
        "Session 1 /ask demo extended for Session 2. "
        "POST /ingest chunks+embeds into Pinecone; "
        "GET /debug/pinecone checks reachability."
    ),
)
_client: OpenAI | None = None

ModelName = Literal["gpt-4o-mini", "gpt-4o", "o3-mini"]
DEFAULT_MODEL: ModelName = "gpt-4o-mini"
MODEL_PRICES_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "o3-mini": (0.0011, 0.0044),
}


class Answer(BaseModel):
    """The model output shape we want every caller to receive."""

    answer: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    sources_needed: bool


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    model: ModelName | None = None
    force_bad: bool = False
    top_k: int = Field(default=5, ge=1, le=20, description="How many chunks to retrieve for RAG")


class AttemptResult(BaseModel):
    attempt: int
    step: str
    ok: bool
    message: str
    raw_output: str | None = None
    validation_error: str | None = None


class AskResponse(BaseModel):
    answer: Answer
    tokens_used: int
    model: str
    latency_ms: int
    cost_usd: float
    attempts: list[AttemptResult]
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    refused: bool = False


@app.get("/health")
def health() -> dict[str, object]:
    """Liveness + env presence (does not call Pinecone or OpenAI)."""
    missing = missing_env_vars()
    return {
        "status": "ok" if not missing else "misconfigured",
        "missing_env": missing,
        "embedding_model": EMBEDDING_MODEL,
    }


@app.get("/debug/pinecone")
def debug_pinecone() -> dict[str, object]:
    """Confirm Pinecone is reachable with the configured API key + index name."""
    result = check_pinecone()
    if not result.get("ok"):
        # Still return the body so students can read missing_env / error;
        # use 503 when the dependency is down or misconfigured.
        raise HTTPException(status_code=503, detail=result)
    return result


class RetrieveHit(BaseModel):
    id: str
    score: float
    document_id: str
    chunk_index: int | None = None
    source: str = ""
    text: str = ""


class RetrieveResponse(BaseModel):
    question: str
    top_k: int
    embedding_model: str
    hits: list[RetrieveHit]


@app.get("/debug/retrieve", response_model=RetrieveResponse)
def debug_retrieve(q: str, top_k: int = 5) -> RetrieveResponse:
    """
    Retrieval-only debug: embed q, return top chunks + scores. No LLM.

    curl example:
      curl -s 'http://127.0.0.1:8000/debug/retrieve?q=shipping%20cut-off&top_k=5' \\
        | python3 -m json.tool
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must be non-empty")
    if top_k < 1 or top_k > 20:
        raise HTTPException(status_code=400, detail="top_k must be between 1 and 20")

    missing = missing_env_vars()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing env vars for retrieve: {', '.join(missing)}",
        )

    try:
        hits = retrieve_chunks(question=q, top_k=top_k)[0]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Retrieve failed: {exc}") from exc

    return RetrieveResponse(
        question=q,
        top_k=top_k,
        embedding_model=EMBEDDING_MODEL,
        hits=[RetrieveHit(**h) for h in hits],
    )


class IngestRequest(BaseModel):
    """Plain text + document_id for vector indexing (Session 2 Path A)."""

    text: str = Field(description="Document text to chunk and embed")
    document_id: str = Field(description="Stable id used later in citations")
    source: str | None = Field(
        default=None,
        description="Optional source filename or label stored on each chunk",
    )


class IngestResponse(BaseModel):
    document_id: str
    chunks_indexed: int
    status: str


@app.post("/ingest", response_model=IngestResponse)
def ingest(body: IngestRequest) -> IngestResponse:
    """
    Chunk → embed (text-embedding-3-small) → upsert into Pinecone.

    curl example:
      curl -s -X POST http://127.0.0.1:8000/ingest \\
        -H 'content-type: application/json' \\
        -d '{
          "document_id": "northwind-overview",
          "source": "northwind_overview.txt",
          "text": "Northwind Traders is a fictional specialty food company..."
        }'
    """
    if not body.document_id.strip():
        raise HTTPException(status_code=400, detail="document_id must be non-empty")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text must be non-empty")

    missing = missing_env_vars()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing env vars for ingest: {', '.join(missing)}",
        )

    try:
        result = ingest_document(
            text=body.text,
            document_id=body.document_id.strip(),
            source=(body.source.strip() if body.source else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface provider/index errors
        raise HTTPException(status_code=502, detail=f"Ingest failed: {exc}") from exc

    return IngestResponse(
        document_id=result["document_id"],
        chunks_indexed=result["chunks_indexed"],
        status=result["status"],
    )


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_per_1k, output_per_1k = MODEL_PRICES_PER_1K.get(
        model, MODEL_PRICES_PER_1K[DEFAULT_MODEL]
    )
    return (prompt_tokens / 1000 * input_per_1k) + (
        completion_tokens / 1000 * output_per_1k
    )


# text-embedding-3-small list price (approx) — used only for cost rollup on retrieve.
EMBEDDING_PRICE_PER_1K = 0.00002

GROUNDING_SYSTEM_PROMPT = """You are a careful assistant for a document Q&A API.

Answer ONLY using the CONTEXT passages below.
- If the context is insufficient to answer, refuse clearly. Set sources_needed=true and confidence near 0.
- When you can answer, use only facts supported by the context. Set sources_needed=false.
- In the answer text, cite each supporting document with its document_id in brackets, e.g. [northwind-shipping].
- Do not invent document_ids. Do not use outside knowledge.
"""


def build_grounded_user_prompt(question: str, hits: list[dict]) -> str:
    """Fill the grounding template with retrieved chunks + the user question."""
    if not hits:
        context = "(No passages were retrieved.)"
    else:
        blocks = []
        for i, hit in enumerate(hits, start=1):
            blocks.append(
                f"[{i}] document_id={hit.get('document_id', '')} "
                f"chunk_id={hit.get('id', '')} "
                f"score={float(hit.get('score') or 0):.4f}\n"
                f"{hit.get('text', '')}"
            )
        context = "\n\n".join(blocks)

    return (
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION:\n{question.strip()}\n\n"
        "Respond with the structured Answer schema."
    )


def usage_counts(completion) -> tuple[int, int, int]:
    usage = completion.usage
    if usage is None:
        return 0, 0, 0
    return usage.total_tokens, usage.prompt_tokens, usage.completion_tokens


def call_structured_model(
    *,
    system: str,
    user: str,
    model: ModelName,
) -> tuple[Answer, int, int, int]:
    """Session 1 structured-output generation path (parse → Answer schema)."""
    completion = get_client().chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=Answer,
    )

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("Model returned no parseable structured output")

    total_tokens, prompt_tokens, completion_tokens = usage_counts(completion)
    return parsed, total_tokens, prompt_tokens, completion_tokens


def call_malformed_json_once(
    *,
    system: str,
    user: str,
    model: ModelName,
) -> tuple[str, int, int, int]:
    """Demo-only path: force one malformed response so students can see retry."""

    completion = get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"{user}\n\n"
                    "Reply with ONLY JSON using keys answer, confidence, sources_needed. "
                    "Set confidence to the string 'very high' instead of a number."
                ),
            },
        ],
    )

    raw = completion.choices[0].message.content or ""
    total_tokens, prompt_tokens, completion_tokens = usage_counts(completion)
    return raw, total_tokens, prompt_tokens, completion_tokens


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    """
    RAG /ask: retrieve top-k chunks, then Session 1 structured generation.

    curl example:
      curl -s -X POST http://127.0.0.1:8000/ask \\
        -H 'content-type: application/json' \\
        -d '{"question":"What product categories does Northwind sell?","top_k":5}'
    """
    model = body.model or DEFAULT_MODEL
    last_error: str | None = None
    attempts: list[AttemptResult] = []
    total_tokens_used = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    embedding_tokens = 0
    start = time.perf_counter()

    missing = missing_env_vars()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing env vars for RAG /ask: {', '.join(missing)}",
        )

    # 1–2. Embed question + retrieve (no generation yet)
    try:
        hits, embedding_tokens = retrieve_chunks(question=body.question, top_k=body.top_k)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Retrieve failed: {exc}") from exc

    retrieved_chunk_ids = [str(h.get("id", "")) for h in hits if h.get("id")]
    grounded_user = build_grounded_user_prompt(body.question, hits)

    # No context → refuse without calling the LLM (clearer + cheaper)
    if not hits:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return AskResponse(
            answer=Answer(
                answer=(
                    "I cannot answer from the ingested documents — "
                    "no relevant passages were retrieved."
                ),
                confidence=0.0,
                sources_needed=True,
            ),
            tokens_used=embedding_tokens,
            model=model,
            latency_ms=latency_ms,
            cost_usd=round(embedding_tokens / 1000 * EMBEDDING_PRICE_PER_1K, 6),
            attempts=[
                AttemptResult(
                    attempt=1,
                    step="retrieve",
                    ok=False,
                    message="No chunks retrieved; refused without generation.",
                )
            ],
            retrieved_chunk_ids=[],
            refused=True,
        )

    for attempt in range(2):
        try:
            if body.force_bad and attempt == 0:
                raw, tokens_used, prompt_tokens, completion_tokens = call_malformed_json_once(
                    system=GROUNDING_SYSTEM_PROMPT,
                    user=grounded_user,
                    model=model,
                )
                total_tokens_used += tokens_used
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens

                try:
                    answer = Answer.model_validate_json(raw)
                except ValidationError as exc:
                    last_error = str(exc)
                    attempts.append(
                        AttemptResult(
                            attempt=attempt + 1,
                            step="forced_bad_json",
                            ok=False,
                            message="Validation failed, so the endpoint retries with structured output.",
                            raw_output=raw,
                            validation_error=str(exc),
                        )
                    )
                    continue

                attempts.append(
                    AttemptResult(
                        attempt=attempt + 1,
                        step="forced_bad_json",
                        ok=True,
                        message="Unexpectedly passed validation.",
                        raw_output=raw,
                    )
                )
            else:
                answer, tokens_used, prompt_tokens, completion_tokens = call_structured_model(
                    system=GROUNDING_SYSTEM_PROMPT,
                    user=grounded_user,
                    model=model,
                )
                total_tokens_used += tokens_used
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                attempts.append(
                    AttemptResult(
                        attempt=attempt + 1,
                        step="structured_output",
                        ok=True,
                        message="Structured output matched the Answer schema.",
                    )
                )

            refused = bool(answer.sources_needed) and (
                answer.confidence <= 0.25
                or "does not include" in answer.answer.lower()
                or "not in the" in answer.answer.lower()
                or "cannot answer" in answer.answer.lower()
                or "insufficient" in answer.answer.lower()
                or "no information" in answer.answer.lower()
                or "not mentioned" in answer.answer.lower()
            )

            latency_ms = int((time.perf_counter() - start) * 1000)
            chat_cost = compute_cost_usd(
                model, total_prompt_tokens, total_completion_tokens
            )
            embed_cost = embedding_tokens / 1000 * EMBEDDING_PRICE_PER_1K
            return AskResponse(
                answer=answer,
                tokens_used=total_tokens_used + embedding_tokens,
                model=model,
                latency_ms=latency_ms,
                cost_usd=round(chat_cost + embed_cost, 6),
                attempts=attempts,
                retrieved_chunk_ids=retrieved_chunk_ids,
                refused=refused,
            )
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            attempts.append(
                AttemptResult(
                    attempt=attempt + 1,
                    step="structured_output",
                    ok=False,
                    message="Structured output failed validation.",
                    validation_error=str(exc),
                )
            )

    raise HTTPException(
        status_code=502,
        detail=f"Model response failed schema validation after retry: {last_error}",
    )
