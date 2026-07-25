"""Molly history API — staged extract → conflicts → draft (+ /ask pipeline, /ingest)."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from conflicts import detect_disagreements_from_ledger
from draft import draft_section
from extract import build_ledger
from ingest import classify_document
from provider import DEFAULT_MODEL, ModelProvider, compute_cost_usd
from retries import VALIDATION_RETRY_ATTEMPTS, run_with_validation_retries
from schemas import (
    AskRequest,
    AskResponse,
    Child,
    ConflictsRequest,
    ConflictsResponse,
    DraftRequest,
    DraftResponse,
    ExtractRequest,
    ExtractResponse,
    IngestRequest,
    IngestResponse,
    ReportSection,
    Source,
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
            f"{body.child.name} is a {wrong_age}-year-old student referred for "
            "evaluation of reading concerns. (PLANTED BAD AGE FOR VALIDATOR DEMO.)"
        ),
        facts=[
            SourcedFact(
                statement=f"{body.child.name} is {wrong_age} years old.",
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


@app.get("/fixtures/{name}", response_model=None)
def get_fixture(name: str) -> FileResponse | JSONResponse:
    """Serve synthetic fixtures for the multi-source home UI."""

    safe = Path(name).name
    # Per-file case: assemble ask-shaped payload from the manifest (do not
    # inline ~900KB of duplicated content into a single static file).
    if safe in {"fixture_001", "fixture_001.json"}:
        return JSONResponse(_assemble_fixture_001_ask())

    if not safe.endswith(".json"):
        raise HTTPException(status_code=404, detail="Fixture not found")
    path = _FIXTURES / safe
    if not path.is_file() or not path.resolve().is_relative_to(_FIXTURES.resolve()):
        raise HTTPException(status_code=404, detail="Fixture not found")
    return FileResponse(path, media_type="application/json")


def _assemble_fixture_001_ask() -> dict:
    """Build an /ask-shaped body from fixtures/fixture_001/manifest.json."""

    man_path = _FIXTURES / "fixture_001" / "manifest.json"
    if not man_path.is_file():
        raise HTTPException(status_code=404, detail="fixture_001 manifest not found")

    man = json.loads(man_path.read_text(encoding="utf-8"))
    child = Child.model_validate(man["child"]).model_dump()
    sources: list[dict] = []
    for f in man["files"]:
        fx_path = man_path.parent / f["fixture"]
        if not fx_path.is_file():
            raise HTTPException(status_code=404, detail=f"Missing {f['fixture']}")
        fx = json.loads(fx_path.read_text(encoding="utf-8"))
        sources.append(Source.model_validate(fx["sources"][0]).model_dump())
    return {
        "confirm_synthetic": True,
        "section": man.get("section") or "history",
        "child": child,
        "sources": sources,
        "model": "gpt-4o-mini",
    }


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
    Build or grow a case Ledger: one model call per new source, atomic facts only.

    Optional prior_ledger → extract new source(s), merge by source_id, recompute
    derived facts. No prior → build from scratch (batch / demo path).
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
            prior_ledger=body.prior_ledger,
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

    Retries on draft-validation failure with the same budget as /ask (default).
    """

    def _attempt(_attempt_i: int) -> DraftResponse:
        start = time.perf_counter()
        response = draft_section(provider, body)
        response.latency_ms = int((time.perf_counter() - start) * 1000)
        return response

    return run_with_validation_retries(
        _attempt,
        max_attempts=VALIDATION_RETRY_ATTEMPTS,
        failure_prefix="Draft failed validation after retry",
    )


@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    """
    Course-assignment contract: AskRequest in → answer + tokens_used + cost_usd.

    Internally runs extract → conflicts → draft. Token/cost sum every model call
    in the pipeline (including per-fact entailment). Nothing persisted.
    """

    model = body.model or DEFAULT_MODEL

    # force_bad_age burns attempt 0; keep headroom for age/provenance retries.
    max_attempts = 3 if body.force_bad_age else VALIDATION_RETRY_ATTEMPTS

    def _attempt(attempt: int) -> AskResponse:
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

    return run_with_validation_retries(
        _attempt,
        max_attempts=max_attempts,
        failure_prefix="Draft failed validation after retry",
    )
