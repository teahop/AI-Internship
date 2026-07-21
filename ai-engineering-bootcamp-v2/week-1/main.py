"""Molly history API — staged extract → conflicts → draft (+ /ask pipeline, /ingest)."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import ValidationError

from conflicts import detect_disagreements_from_ledger
from draft import draft_section
from extract import build_ledger
from ingest import classify_document
from provider import DEFAULT_MODEL, ModelProvider, compute_cost_usd
from schemas import (
    AskRequest,
    AskResponse,
    ConflictsRequest,
    ConflictsResponse,
    DraftRequest,
    DraftResponse,
    ExtractRequest,
    ExtractResponse,
    IngestRequest,
    IngestResponse,
    ReportSection,
    SourcedFact,
)
from validators import (
    compute_age_years,
    validate_age_consistency,
    validate_provenance,
)

_DIR = Path(__file__).resolve().parent
_FIXTURES = _DIR / "fixtures"
load_dotenv(_DIR / ".env")

app = FastAPI(
    title="Molly History Draft (synthetic OpenAI build)",
    description=(
        "Learning/build runtime on OpenAI — synthetic data only. "
        "Pipeline: /extract → /conflicts → /draft. "
        "/ask runs that pipeline under the course-assignment contract. "
        "/ingest classifies a raw document for user confirmation (never silent). "
        "Production drafting for real cases runs on BastionGPT (BAA), not this repo."
    ),
)
provider = ModelProvider()


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


def _run_pipeline(body: AskRequest, model: str) -> tuple[ReportSection, int, float, int]:
    """
    extract → conflicts → draft.

    Returns (section, tokens_used, cost_usd, age_years_expected).
    Token/cost sum every model call (extraction + draft + entailment).
    """

    (
        ledger,
        tokens_by_source,
        extract_prompt,
        extract_completion,
        _pred_review,
        _subj_review,
        _gap,
        _timelines,
    ) = build_ledger(
        provider,
        child=body.child,
        sources=body.sources,
        model=model,
    )
    extract_tokens = sum(tokens_by_source.values())
    extract_cost = compute_cost_usd(model, extract_prompt, extract_completion)

    conflicts, variance, _tls, _, _ = detect_disagreements_from_ledger(ledger)

    draft_body = DraftRequest(
        confirm_synthetic=True,
        section=body.section,
        ledger=ledger,
        conflicts=conflicts,
        variance=variance,
        model=model,
        entailment_model="gpt-4o-mini",
    )
    draft_resp = draft_section(provider, draft_body)
    if not draft_resp.section_populated or draft_resp.answer is None:
        raise ValueError(draft_resp.empty_reason or "Draft section not populated")

    tokens_used = extract_tokens + draft_resp.tokens_used
    cost_usd = extract_cost + draft_resp.cost_usd

    expected_age = validate_age_consistency(
        draft_resp.answer,
        dob=body.child.dob,
        evaluation_date=body.child.evaluation_date,
        ledger=ledger,
    )
    validate_provenance(draft_resp.answer, body.sources)

    return draft_resp.answer, tokens_used, cost_usd, expected_age


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
        "pipeline": "extract→conflicts→draft",
    }


@app.post("/ingest")
def ingest(body: IngestRequest) -> IngestResponse:
    """
    Classify one raw document → {source_type, source_date, label} for confirmation.

    Never silent: suggestion is returned; caller must confirm before the document
    enters the case packet. A wrong date is a provenance failure.
    """

    model = body.model or "gpt-4o-mini"
    start = time.perf_counter()
    try:
        suggestion, tokens, prompt_tok, completion_tok = classify_document(
            provider,
            content=body.content,
            model=model,
            today=date.today().isoformat(),
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Ingest failed: {exc}") from exc

    return IngestResponse(
        suggestion=suggestion,
        tokens_used=tokens,
        model=model,
        latency_ms=int((time.perf_counter() - start) * 1000),
        cost_usd=round(compute_cost_usd(model, prompt_tok, completion_tok), 6),
    )


@app.post("/extract")
def extract(body: ExtractRequest) -> ExtractResponse:
    """
    Build a case Ledger: one model call per source, atomic facts only.

    Returns ledger + gap_report + timelines (computed view). Nothing persisted.
    """

    model = body.model or DEFAULT_MODEL
    start = time.perf_counter()
    try:
        (
            ledger,
            tokens_by_source,
            prompt_tokens,
            completion_tokens,
            review,
            subject_review,
            gap_report,
            timelines,
        ) = build_ledger(
            provider,
            child=body.child,
            sources=body.sources,
            model=model,
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {exc}") from exc

    latency_ms = int((time.perf_counter() - start) * 1000)
    tokens_used = sum(tokens_by_source.values())
    cost_usd = compute_cost_usd(model, prompt_tokens, completion_tokens)

    return ExtractResponse(
        ledger=ledger,
        gap_report=gap_report,
        timelines=timelines,
        tokens_used=tokens_used,
        model=model,
        latency_ms=latency_ms,
        cost_usd=round(cost_usd, 6),
        tokens_by_source=tokens_by_source,
        predicates_for_review=review,
        subjects_for_review=subject_review,
    )


@app.post("/conflicts")
def conflicts(body: ConflictsRequest) -> ConflictsResponse:
    """
    Ledger in → record conflicts + perspectival variance + timelines out.

    Deterministic: no model call, no domain keywords. Nothing is persisted.
    """

    conflict_list, variance_list, timelines, review, subject_review = (
        detect_disagreements_from_ledger(body.ledger)
    )
    return ConflictsResponse(
        conflicts=conflict_list,
        variance=variance_list,
        timelines=timelines,
        predicates_for_review=review,
        subjects_for_review=subject_review,
    )


@app.post("/draft")
def draft(body: DraftRequest) -> DraftResponse:
    """
    Settled ledger + must-mention conflicts → section prose + review work queue.

    Drafter has no discretion over facts/conflicts. Empty ledger for the section
    returns section_populated=False (not a thin padded draft). Nothing persisted.
    """

    start = time.perf_counter()
    try:
        response = draft_section(provider, body)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Draft failed: {exc}") from exc

    response.latency_ms = int((time.perf_counter() - start) * 1000)
    return response


@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    """
    Course-assignment contract: AskRequest in → answer + tokens_used + cost_usd.

    Internally runs extract → conflicts → draft. Token/cost sum every model call
    in the pipeline (including per-fact entailment). Nothing persisted.
    """

    model = body.model or DEFAULT_MODEL
    last_error: str | None = None

    # force_bad_age burns attempt 0; keep headroom for age/provenance retries.
    max_attempts = 3 if body.force_bad_age else 2

    for attempt in range(max_attempts):
        try:
            start = time.perf_counter()

            if body.force_bad_age and attempt == 0:
                section = _plant_bad_age_section(body)
                tokens_used = 0
                cost_usd = 0.0
                expected_age = validate_age_consistency(
                    section,
                    dob=body.child.dob,
                    evaluation_date=body.child.evaluation_date,
                )
                validate_provenance(section, body.sources)
            else:
                section, tokens_used, cost_usd, expected_age = _run_pipeline(body, model)

            latency_ms = int((time.perf_counter() - start) * 1000)
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
        detail=f"Draft failed validation after retry: {last_error}",
    )
