"""Shared validation-retry budget for model-call endpoints.

/ask already retried on ValidationError/ValueError; /draft reuses the same helper
so a transient draft-validation miss (e.g. missing f_computed_age_years cite)
gets another model call before 502.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from pydantic import ValidationError

T = TypeVar("T")

# Default budget matching /ask when force_bad_age is off (attempt 0 + one retry).
VALIDATION_RETRY_ATTEMPTS = 2


def run_with_validation_retries(
    operation: Callable[[int], T],
    *,
    max_attempts: int = VALIDATION_RETRY_ATTEMPTS,
    failure_prefix: str = "Draft failed validation after retry",
) -> T:
    """
    Run ``operation(attempt)`` until it succeeds or attempts are exhausted.

    Retries only on ValidationError / ValueError (draft age/provenance/trace
    failures). Other exceptions propagate immediately.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_error: str | None = None
    for attempt in range(max_attempts):
        try:
            return operation(attempt)
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            continue

    raise HTTPException(
        status_code=502,
        detail=f"{failure_prefix}: {last_error}",
    )
