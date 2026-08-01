"""Shared passage retriever (SQLite FTS5) for Tier 2 and Tier 3.

Ranks overlapping passages with BM25 + Porter stemming. No hand-maintained
stoplist, no whole-document frequency scoring. Index stays in-process
(:memory:) so case text never leaves the machine.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any

PASSAGE_CHARS = 900  # holds a full clinical paragraph
PASSAGE_STRIDE = 450  # 50% overlap so a fact on a boundary lands whole in one window

_MARKER = "not checked against the reliability layer"

# Lazy singleton: rebuild when ledger path/mtime changes.
# Streamlit runs ask() in a worker thread then searches again on the main
# thread — check_same_thread=False + a lock keep the shared :memory: index safe.
_index_conn: sqlite3.Connection | None = None
_index_key: tuple[str, float] | None = None
_index_lock = threading.RLock()


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _passages(text: str) -> list[tuple[int, str]]:
    """Split source text into overlapping windows: (char_offset, passage)."""

    text = text or ""
    if not text:
        return []
    if len(text) <= PASSAGE_CHARS:
        return [(0, text)]
    out: list[tuple[int, str]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + PASSAGE_CHARS)
        out.append((start, text[start:end]))
        if end >= n:
            break
        start += PASSAGE_STRIDE
    return out


def _tokens(query: str) -> list[str]:
    """Tokenize a query. No hand stoplist — rare-term selection uses corpus DF."""

    return [t for t in re.split(r"[^\w-]+", (query or "").lower()) if t]


def _dedup_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t.strip())
    return out


def _df(conn: sqlite3.Connection, term: str) -> int:
    """How many passages match a single quoted term (0 if the term is unusable)."""

    safe = term.replace('"', '""').strip()
    if not safe:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM passages WHERE passages MATCH ?",
            (f'"{safe}"',),
        ).fetchone()
        return int(row["n"] if row else 0)
    except sqlite3.OperationalError:
        return 0


def _select_match_terms(
    conn: sqlite3.Connection,
    query_terms: list[str],
    expansions: list[str],
    *,
    max_df_frac: float = 0.04,
) -> list[str]:
    """Keep expansions always; drop query terms that appear in most passages.

    Hand stoplists are deleted on purpose. Ultra-common tokens ("her", "about",
    "who") still wreck an OR MATCH before BM25 can help — corpus DF is the
    automatic filter applied at query construction.
    """

    total = int(conn.execute("SELECT COUNT(*) AS n FROM passages").fetchone()["n"] or 1)
    ceiling = max(3, int(total * max_df_frac))

    kept: list[str] = []
    for t in expansions:
        if t.strip():
            kept.append(t.strip())

    # Prefer substantive tokens (len>=4); allow shorter only if very rare in corpus.
    candidates = [t for t in query_terms if len(t) >= 4 or _df(conn, t) <= max(3, ceiling // 4)]
    if not candidates:
        candidates = list(query_terms)

    rare: list[str] = []
    for t in candidates:
        if _df(conn, t) <= ceiling:
            rare.append(t)

    if rare:
        kept.extend(rare)
    elif candidates:
        scored = sorted(candidates, key=lambda t: (_df(conn, t), -len(t)))
        kept.append(scored[0])

    return _dedup_terms(kept)


def _match_expr(terms: list[str]) -> str | None:
    """FTS5 MATCH: OR over double-quoted terms (phrases stay intact)."""

    parts: list[str] = []
    for t in terms:
        safe = t.replace('"', '""')
        if not safe:
            continue
        parts.append(f'"{safe}"')
    if not parts:
        return None
    return " OR ".join(parts)


def build_index(sources: list[dict]) -> sqlite3.Connection:
    """In-memory FTS5 index over every passage of every source."""

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE VIRTUAL TABLE passages USING fts5(
            body,
            source_id UNINDEXED,
            label UNINDEXED,
            date UNINDEXED,
            doc_class UNINDEXED,
            offset UNINDEXED,
            tokenize='porter unicode61'
        )
        """
    )
    rows: list[tuple[Any, ...]] = []
    for source in sources:
        content = source.get("content") or ""
        sid = source.get("id") or ""
        label = source.get("label") or ""
        date = source.get("date") or ""
        doc_class = source.get("doc_class") or ""
        for offset, passage in _passages(content):
            rows.append((passage, sid, label, date, doc_class, int(offset)))
    conn.executemany(
        """
        INSERT INTO passages(body, source_id, label, date, doc_class, offset)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return conn


def _enrich_expansion_terms(
    conn: sqlite3.Connection, expansions: list[str]
) -> list[str]:
    """Also OR rare unigrams from expansion phrases (audiometer from 'pure tone …')."""

    total = int(conn.execute("SELECT COUNT(*) AS n FROM passages").fetchone()["n"] or 1)
    ceiling = max(3, int(total * 0.04))
    out = list(expansions)
    for phrase in expansions:
        for tok in _tokens(phrase):
            if len(tok) < 6:
                continue
            if _df(conn, tok) <= ceiling:
                out.append(tok)
            # Porter does not equate audiometry ↔ audiometer; chart text uses the latter.
            if tok.startswith("audiometr"):
                out.extend(["audiometer", "audiometry"])
    return _dedup_terms(out)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    expansions: list[str] | None = None,
    limit: int = 8,
    per_source_limit: int = 2,
) -> list[dict[str, Any]]:
    """Rank passages by BM25. Returns verbatim passage text — never summarized."""

    with _index_lock:
        return _search_unlocked(
            conn,
            query,
            expansions=expansions,
            limit=limit,
            per_source_limit=per_source_limit,
        )


def _search_unlocked(
    conn: sqlite3.Connection,
    query: str,
    *,
    expansions: list[str] | None = None,
    limit: int = 8,
    per_source_limit: int = 2,
) -> list[dict[str, Any]]:
    query_terms = _tokens(query)
    expansion_terms = [e.strip() for e in (expansions or []) if e and e.strip()]
    expansion_terms = _enrich_expansion_terms(conn, expansion_terms)
    # Optional lexical family bridges — only when query expansion is enabled so
    # QUERY_EXPANSION=0 remains a pure FTS5 A/B.
    try:
        from query_expand import query_expansion_enabled

        expand_on = query_expansion_enabled()
    except Exception:  # noqa: BLE001
        expand_on = True
    if expand_on:
        if "hearing" in query_terms:
            expansion_terms = _dedup_terms(
                expansion_terms + ["audiometer", "pure tone"]
            )
        if "sleep" in query_terms:
            expansion_terms = _dedup_terms(expansion_terms + ["sleep routine"])
        if "pediatrician" in query_terms or "physician" in query_terms:
            expansion_terms = _dedup_terms(
                expansion_terms
                + ["primary care provider", "primary care physician", "PCP"]
            )

    # Query-only first so expansion jargon cannot bury a direct lexical hit
    # (e.g. sleep-interview stem vs BASC "trouble sleeping" boilerplate).
    primary = _search_match(
        conn,
        _select_match_terms(conn, query_terms, []),
        limit=limit,
        per_source_limit=per_source_limit,
    )
    if not expansion_terms:
        return primary

    expanded = _search_match(
        conn,
        _select_match_terms(conn, query_terms, expansion_terms),
        limit=limit,
        per_source_limit=per_source_limit,
    )
    return _merge_hits(
        primary, expanded, limit=limit, per_source_limit=per_source_limit
    )


def _search_match(
    conn: sqlite3.Connection,
    terms: list[str],
    *,
    limit: int,
    per_source_limit: int,
) -> list[dict[str, Any]]:
    match = _match_expr(terms)
    if not match:
        return []

    fetch_n = max(limit * 6, limit)
    try:
        cur = conn.execute(
            """
            SELECT
                source_id,
                label,
                date,
                doc_class,
                offset,
                body,
                bm25(passages) AS score
            FROM passages
            WHERE passages MATCH ?
            ORDER BY bm25(passages)
            LIMIT ?
            """,
            (match, fetch_n),
        )
    except sqlite3.OperationalError:
        return []

    per_source: dict[str, int] = {}
    hits: list[dict[str, Any]] = []
    for row in cur:
        sid = row["source_id"]
        if per_source.get(sid, 0) >= per_source_limit:
            continue
        per_source[sid] = per_source.get(sid, 0) + 1
        hits.append(
            {
                "source_id": sid,
                "label": row["label"],
                "date": row["date"],
                "doc_class": row["doc_class"],
                "offset": int(row["offset"] or 0),
                "quote": _normalize_ws(row["body"]),
                "score": float(row["score"]),
                "matched_terms": terms,
                "verified": False,
                "marker": _MARKER,
            }
        )
        if len(hits) >= limit:
            break
    return hits


def _merge_hits(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    *,
    limit: int,
    per_source_limit: int = 2,
) -> list[dict[str, Any]]:
    """Rank-merge query-only and expansion hits with a small primary bias.

    Primary bias keeps interview stems near the cutoff; strong expansion scores
    (Ambco / Primary care provider) can still outrank weak primary rows.
    """

    def _key(hit: dict[str, Any]) -> tuple[str, int]:
        return (str(hit.get("source_id")), int(hit.get("offset") or 0))

    best: dict[tuple[str, int], dict[str, Any]] = {}
    for pri, bucket in ((1, primary), (0, secondary)):
        for hit in bucket:
            k = _key(hit)
            row = {
                **hit,
                "_pri": pri,
                "_rank": float(hit.get("score") or 0) - (8.0 * pri),
            }
            prev = best.get(k)
            if prev is None or row["_rank"] < prev["_rank"]:
                best[k] = row

    ordered = sorted(best.values(), key=lambda h: float(h["_rank"]))
    per_source: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for hit in ordered:
        sid = str(hit.get("source_id"))
        if per_source.get(sid, 0) >= per_source_limit:
            continue
        per_source[sid] = per_source.get(sid, 0) + 1
        clean = {k: v for k, v in hit.items() if not k.startswith("_")}
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def get_index() -> sqlite3.Connection:
    """Process-wide index keyed off the cached ledger path + mtime."""

    global _index_conn, _index_key

    from ledger_store import get_ledger, ledger_path

    path = ledger_path()
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    key = (str(path), mtime)

    with _index_lock:
        if _index_conn is not None and _index_key == key:
            return _index_conn

        ledger = get_ledger()
        if _index_conn is not None:
            try:
                _index_conn.close()
            except Exception:  # noqa: BLE001
                pass
        _index_conn = build_index(list(ledger.get("sources") or []))
        _index_key = key
        return _index_conn


def reset_index() -> None:
    """Drop the singleton (e.g. after reload_ledger in tests)."""

    global _index_conn, _index_key
    with _index_lock:
        if _index_conn is not None:
            try:
                _index_conn.close()
            except Exception:  # noqa: BLE001
                pass
        _index_conn = None
        _index_key = None


def search_sources(
    query: str,
    *,
    expansions: list[str] | None = None,
    limit: int = 8,
    per_source_limit: int = 2,
) -> list[dict[str, Any]]:
    """Convenience: ensure index, then search.

    When ``expansions`` is None, call ``expand_query`` if QUERY_EXPANSION is on.
    Pass ``expansions=[]`` to skip expansion for this call.
    """

    if expansions is None:
        from query_expand import expand_query

        expansions = expand_query(query)
    return search(
        get_index(),
        query,
        expansions=expansions,
        limit=limit,
        per_source_limit=per_source_limit,
    )


def passage_count() -> int:
    row = get_index().execute("SELECT COUNT(*) AS n FROM passages").fetchone()
    return int(row["n"] if row else 0)
