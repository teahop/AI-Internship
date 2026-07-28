"""Pinecone vector store helpers for Session 2 RAG homework.

Secrets come only from environment variables — never hard-code keys.
Embeddings always use text-embedding-3-small (same model at ingest and query).
"""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI
from pinecone import Pinecone
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Locked for this assignment — switching later means re-indexing.
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

# Chunking defaults — override with CHUNK_SIZE / CHUNK_OVERLAP env vars.
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

_openai: OpenAI | None = None
_pinecone: Pinecone | None = None


def chunk_size() -> int:
    return int(os.getenv("CHUNK_SIZE", str(DEFAULT_CHUNK_SIZE)))


def chunk_overlap() -> int:
    return int(os.getenv("CHUNK_OVERLAP", str(DEFAULT_CHUNK_OVERLAP)))


def required_env_vars() -> tuple[str, ...]:
    return (
        "OPENAI_API_KEY",
        "PINECONE_API_KEY",
        "PINECONE_INDEX_NAME",
    )


def missing_env_vars() -> list[str]:
    return [name for name in required_env_vars() if not os.getenv(name)]


def get_openai() -> OpenAI:
    global _openai
    if _openai is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not set")
        _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai


def get_pinecone() -> Pinecone:
    global _pinecone
    if _pinecone is None:
        if not os.getenv("PINECONE_API_KEY"):
            raise ValueError("PINECONE_API_KEY is not set")
        _pinecone = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    return _pinecone


def get_index_name() -> str:
    name = os.getenv("PINECONE_INDEX_NAME")
    if not name:
        raise ValueError("PINECONE_INDEX_NAME is not set")
    return name


def get_namespace() -> str:
    """Optional Pinecone namespace (empty string = default)."""
    return os.getenv("PINECONE_NAMESPACE", "")


def get_index():
    return get_pinecone().Index(get_index_name())


def embed_texts(texts: list[str]) -> tuple[list[list[float]], int]:
    """Embed with text-embedding-3-small — use for both ingest and query.

    Returns (vectors, total_embedding_tokens).
    """
    if not texts:
        return [], 0
    response = get_openai().embeddings.create(model=EMBEDDING_MODEL, input=texts)
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [item.embedding for item in ordered]
    tokens = response.usage.total_tokens if response.usage else 0
    return vectors, tokens


def embed_query(text: str) -> tuple[list[float], int]:
    vectors, tokens = embed_texts([text])
    return vectors[0], tokens


def retrieve_chunks(*, question: str, top_k: int = 5) -> tuple[list[dict[str, Any]], int]:
    """
    Embed the question and return top-k Pinecone matches.

    No LLM call — for verifying retrieval before wiring /ask generation.
    Returns (hits, embedding_tokens).
    """
    if not question.strip():
        raise ValueError("question must be non-empty")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    qvec, embedding_tokens = embed_query(question.strip())
    index = get_index()
    namespace = get_namespace()
    kwargs: dict[str, Any] = {
        "vector": qvec,
        "top_k": top_k,
        "include_metadata": True,
    }
    if namespace:
        kwargs["namespace"] = namespace

    result = index.query(**kwargs)
    matches = getattr(result, "matches", None)
    if matches is None and isinstance(result, dict):
        matches = result.get("matches") or []

    hits: list[dict[str, Any]] = []
    for match in matches or []:
        if hasattr(match, "to_dict"):
            match = match.to_dict()
        elif not isinstance(match, dict):
            match = {
                "id": getattr(match, "id", ""),
                "score": getattr(match, "score", 0.0),
                "metadata": getattr(match, "metadata", {}) or {},
            }
        meta = match.get("metadata") or {}
        hits.append(
            {
                "id": str(match.get("id", "")),
                "score": float(match.get("score") or 0.0),
                "document_id": str(meta.get("document_id", "")),
                "chunk_index": meta.get("chunk_index"),
                "source": str(meta.get("source", "")),
                "text": str(meta.get("text", "")),
            }
        )
    return hits, embedding_tokens


def chunk_text(text: str) -> list[str]:
    """Split with RecursiveCharacterTextSplitter (size/overlap from env)."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size(),
        chunk_overlap=chunk_overlap(),
    )
    return [c for c in splitter.split_text(text) if c.strip()]


def _safe_id_part(value: str, max_len: int = 64) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned[:max_len] or "doc"


def ingest_document(
    *,
    text: str,
    document_id: str,
    source: str | None = None,
) -> dict[str, Any]:
    """
    Chunk → embed (text-embedding-3-small) → upsert into Pinecone.

    Metadata on each vector: document_id, chunk_index, source, plus text for later RAG.
    """
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("No chunks produced from text after splitting")

    vectors, _embed_tokens = embed_texts(chunks)
    index = get_index()
    namespace = get_namespace()
    source_label = source or document_id
    doc_key = _safe_id_part(document_id)

    records: list[dict[str, Any]] = []
    for i, (chunk, values) in enumerate(zip(chunks, vectors, strict=True)):
        chunk_id = f"{doc_key}::chunk-{i}"
        records.append(
            {
                "id": chunk_id,
                "values": values,
                "metadata": {
                    "document_id": document_id,
                    "chunk_index": i,
                    "source": source_label,
                    # Needed later for retrieval → generation; Pinecone metadata size limit.
                    "text": chunk[:3500],
                },
            }
        )

    batch = 50
    for start in range(0, len(records), batch):
        kwargs: dict[str, Any] = {"vectors": records[start : start + batch]}
        if namespace:
            kwargs["namespace"] = namespace
        index.upsert(**kwargs)

    return {
        "document_id": document_id,
        "chunks_indexed": len(records),
        "status": "ok",
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": chunk_size(),
        "chunk_overlap": chunk_overlap(),
        "index_name": get_index_name(),
    }


def check_pinecone() -> dict[str, Any]:
    """
    Health/debug probe: confirm env is set and Pinecone answers.

    Does not return API keys. Safe to expose on a debug route.
    """
    missing = missing_env_vars()
    if missing:
        return {
            "ok": False,
            "reachable": False,
            "missing_env": missing,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
            "index_name": os.getenv("PINECONE_INDEX_NAME") or None,
            "error": f"Missing env vars: {', '.join(missing)}",
        }

    index_name = get_index_name()
    try:
        pc = get_pinecone()
        # Lightweight control-plane call — proves the API key works.
        names = [idx.name for idx in pc.list_indexes()]
        if index_name not in names:
            return {
                "ok": False,
                "reachable": True,
                "missing_env": [],
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dimensions": EMBEDDING_DIMENSIONS,
                "index_name": index_name,
                "indexes_seen": names,
                "error": (
                    f"Index '{index_name}' not found. "
                    f"Create it with dimension={EMBEDDING_DIMENSIONS}, metric=cosine."
                ),
            }

        stats = get_index().describe_index_stats()
        if hasattr(stats, "to_dict"):
            stats = stats.to_dict()
        elif not isinstance(stats, dict):
            stats = {
                "total_vector_count": getattr(stats, "total_vector_count", None),
                "dimension": getattr(stats, "dimension", None),
            }

        return {
            "ok": True,
            "reachable": True,
            "missing_env": [],
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
            "index_name": index_name,
            "namespace": get_namespace() or "(default)",
            "stats": {
                "total_vector_count": stats.get("total_vector_count"),
                "dimension": stats.get("dimension"),
            },
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — surface connection errors to the caller
        return {
            "ok": False,
            "reachable": False,
            "missing_env": [],
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
            "index_name": index_name,
            "error": str(exc),
        }
