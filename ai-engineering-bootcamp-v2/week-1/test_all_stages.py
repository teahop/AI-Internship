#!/usr/bin/env python3
"""Smoke-test week-1 stages + Molly history fixture (stage 5 / main)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from validators import compute_age_years, validate_age_consistency
from schemas import ReportSection, SourcedFact

WORKDIR = Path(__file__).resolve().parent
QUESTION = "What is Retrieval-Augmented Generation in one sentence?"
FIXTURE_PATH = WORKDIR / "fixtures" / "synthetic_history_case.json"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(module: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(WORKDIR / ".venv/bin/uvicorn"),
            f"{module}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=WORKDIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_up(base: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/docs", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def post(base: str, payload: dict) -> tuple[int, dict]:
    r = httpx.post(f"{base}/ask", json=payload, timeout=180.0)
    try:
        data = r.json()
    except json.JSONDecodeError:
        data = {"_raw": r.text}
    return r.status_code, data


def check(name: str, ok: bool, detail: str) -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {detail}")
    return ok


def test_stage1(base: str) -> bool:
    print("\n=== Stage 1: bare /ask ===")
    status, data = post(base, {"question": QUESTION})
    ok = True
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check(
        "answer type",
        isinstance(data.get("answer"), str),
        f"answer is str: {type(data.get('answer')).__name__}",
    )
    ok &= check(
        "tokens",
        isinstance(data.get("tokens_used"), int) and data["tokens_used"] > 0,
        f"tokens_used={data.get('tokens_used')}",
    )
    ok &= check("no extra", set(data.keys()) == {"answer", "tokens_used"}, f"keys={list(data.keys())}")
    if ok:
        print(f"  answer preview: {data['answer'][:80]}...")
    return ok


def test_stage2(base: str) -> bool:
    print("\n=== Stage 2: structured output ===")
    status, data = post(base, {"question": QUESTION})
    ans = data.get("answer", {})
    ok = True
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check("answer object", isinstance(ans, dict), "answer is object")
    ok &= check(
        "confidence",
        isinstance(ans.get("confidence"), (int, float)),
        f"confidence={ans.get('confidence')}",
    )
    ok &= check(
        "sources_needed",
        isinstance(ans.get("sources_needed"), bool),
        f"sources_needed={ans.get('sources_needed')}",
    )
    ok &= check("tokens", data.get("tokens_used", 0) > 0, f"tokens_used={data.get('tokens_used')}")
    ok &= check("no extra", set(data.keys()) == {"answer", "tokens_used"}, f"keys={list(data.keys())}")
    return ok


def test_stage3(base: str) -> bool:
    print("\n=== Stage 3: guardrail + retry ===")
    status_ok, _ = post(base, {"question": QUESTION})
    status_bad, data_bad = post(base, {"question": QUESTION, "force_bad": True})
    ok = True
    ok &= check("normal", status_ok == 200, f"normal HTTP {status_ok}")
    ok &= check(
        "force_bad recovers",
        status_bad == 200,
        f"force_bad HTTP {status_bad} (retry should recover)",
    )
    ok &= check(
        "structured",
        isinstance(data_bad.get("answer"), dict),
        "force_bad answer is structured object",
    )
    return ok


def test_stage4(base: str) -> bool:
    print("\n=== Stage 4: model + latency ===")
    status, data = post(base, {"question": QUESTION, "model": "gpt-4o-mini"})
    ok = True
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check("model", data.get("model") == "gpt-4o-mini", f"model={data.get('model')}")
    ok &= check(
        "latency",
        isinstance(data.get("latency_ms"), int) and data["latency_ms"] > 0,
        f"latency_ms={data.get('latency_ms')}",
    )
    ok &= check("no cost yet", "cost_usd" not in data, "cost_usd absent (stage 4 only)")
    ok &= check(
        "keys",
        set(data.keys()) == {"answer", "tokens_used", "model", "latency_ms"},
        f"keys={list(data.keys())}",
    )
    return ok


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_age_validator_unit() -> bool:
    """Assert the age validator fires on a planted wrong age (no network)."""

    print("\n=== Validator unit: age/DOB consistency ===")
    fixture = _load_fixture()
    child = fixture["child"]
    expected = compute_age_years(child["dob"], child["evaluation_date"])
    bad = ReportSection(
        section="history",
        prose=f"{child['initials']} is a {expected + 3}-year-old student.",
        facts=[
            SourcedFact(
                statement=f"Student is {expected + 3} years old.",
                source_id="school-cum-2024",
                source_date="2024-09-01",
                life_stage="current",
            )
        ],
        conflicts=[],
        coverage=["current"],
    )
    ok = True
    fired = False
    try:
        validate_age_consistency(
            bad, dob=child["dob"], evaluation_date=child["evaluation_date"]
        )
    except ValueError as exc:
        fired = True
        ok &= check("fires", True, f"validator raised: {exc}")
    if not fired:
        ok &= check("fires", False, "validator did NOT raise on planted bad age")
    return ok


def test_stage5(base: str) -> bool:
    print("\n=== Stage 5 / main: Molly history fixture + age retry ===")
    fixture = _load_fixture()
    expected_age = compute_age_years(fixture["child"]["dob"], fixture["child"]["evaluation_date"])

    # Guard: missing confirm_synthetic must fail before any model spend.
    blocked_status, _ = post(
        base,
        {
            "section": "history",
            "child": fixture["child"],
            "sources": fixture["sources"][:1],
        },
    )
    ok = True
    ok &= check(
        "synthetic guard",
        blocked_status == 422,
        f"missing confirm_synthetic → HTTP {blocked_status} (expect 422)",
    )

    # Planted bad age on attempt 0 → validator rejects → retry with real model.
    payload = {**fixture, "force_bad_age": True}
    status, data = post(base, payload)
    ans = data.get("answer", {})
    ok &= check("status", status == 200, f"HTTP {status}")
    ok &= check(
        "expected age",
        data.get("age_years_expected") == expected_age,
        f"age_years_expected={data.get('age_years_expected')} (want {expected_age})",
    )
    ok &= check("section", ans.get("section") == "history", f"section={ans.get('section')}")
    ok &= check("prose", isinstance(ans.get("prose"), str) and len(ans.get("prose", "")) > 40, "prose present")
    ok &= check("facts", isinstance(ans.get("facts"), list) and len(ans.get("facts", [])) > 0, f"facts={len(ans.get('facts', []))}")
    ok &= check("conflicts field", isinstance(ans.get("conflicts"), list), "conflicts is list")
    ok &= check("coverage", isinstance(ans.get("coverage"), list) and len(ans.get("coverage", [])) > 0, f"coverage={ans.get('coverage')}")
    ok &= check("cost_usd", isinstance(data.get("cost_usd"), (int, float)), f"cost_usd={data.get('cost_usd')}")
    ok &= check(
        "keys",
        {"answer", "tokens_used", "model", "latency_ms", "cost_usd", "age_years_expected"}
        <= set(data.keys()),
        f"keys={list(data.keys())}",
    )

    # Provenance: every fact points at an input source id.
    source_ids = {s["id"] for s in fixture["sources"]}
    bad_refs = [
        f.get("source_id")
        for f in ans.get("facts", [])
        if f.get("source_id") not in source_ids
    ]
    ok &= check("source_ids", len(bad_refs) == 0, f"unknown source_ids={bad_refs[:3]}")
    return ok


TESTS = [
    ("serve_stage1", test_stage1),
    ("serve_stage2", test_stage2),
    ("serve_stage3", test_stage3),
    ("serve_stage4", test_stage4),
    ("serve_stage5", test_stage5),
]


def main() -> int:
    results: list[tuple[str, bool]] = []
    results.append(("validators.age", test_age_validator_unit()))

    for module, test_fn in TESTS:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        proc = start_server(module, port)
        try:
            if not wait_up(base):
                print(f"\n=== {module}: FAIL — server did not start on {base} ===")
                results.append((module, False))
                continue
            results.append((module, test_fn(base)))
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            time.sleep(0.5)

    print("\n" + "=" * 40)
    print("SUMMARY")
    for module, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {module}")
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
