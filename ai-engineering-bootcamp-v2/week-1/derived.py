"""Derived facts — general provenance for values computed from other inputs.

Age is the first instance; the same path covers elapsed time, grade-for-age,
index discrepancies, and composite scores later. Extraction never invents these
rows — they are injected at ledger-build time.

Verification:
  extracted facts  → entailment against source text
  derived facts    → re-run the named derivation (no source text to entail)
"""

from __future__ import annotations

from collections.abc import Callable

from normalize import normalize_value
from schemas import Child, Fact

# Synthetic provenance ids — not entries in ledger.sources.
COMPUTED_SOURCE_ID = "computed"
REQUEST_SOURCE_ID = "request"

# Stable fact ids so merges do not renumber request/computed rows.
REQUEST_DOB_FACT_ID = "f_request_dob"
COMPUTED_AGE_FACT_ID = "f_computed_age_years"

# Derivation recipes: name → (recompute value from Child).
DerivationFn = Callable[[Child], str]

DERIVATION_RECIPES: dict[str, DerivationFn] = {}


def _register(name: str, fn: DerivationFn) -> None:
    DERIVATION_RECIPES[name] = fn


def _age_from_dob_and_eval(child: Child) -> str:
    from validators import compute_age_years

    return str(compute_age_years(child.dob, child.evaluation_date))


AGE_DERIVATION = "dob + evaluation_date"
_register(AGE_DERIVATION, _age_from_dob_and_eval)


def is_derived_fact(fact: Fact) -> bool:
    return fact.derivation is not None or fact.source_id == COMPUTED_SOURCE_ID


def is_synthetic_source_id(source_id: str) -> bool:
    return source_id in {COMPUTED_SOURCE_ID, REQUEST_SOURCE_ID}


def strip_synthetic_facts(facts: list[Fact]) -> list[Fact]:
    """Drop request/computed rows so they can be re-injected after a merge."""

    return [f for f in facts if not is_synthetic_source_id(f.source_id)]


def recompute_derived_value(derivation: str, child: Child) -> str:
    recipe = DERIVATION_RECIPES.get(derivation)
    if recipe is None:
        raise ValueError(f"Unknown derivation recipe: {derivation!r}")
    return recipe(child)


def validate_derived_facts(facts: list[Fact], child: Child) -> None:
    """
    Recomputation check for every derived fact.

    Stronger than entailment: exact re-run, not a reading of source text.
    """

    errors: list[str] = []
    for fact in facts:
        if not fact.derivation:
            continue
        try:
            expected = recompute_derived_value(fact.derivation, child)
        except ValueError as exc:
            errors.append(f"{fact.id}: {exc}")
            continue
        if fact.value != expected:
            errors.append(
                f"{fact.id} derivation {fact.derivation!r}: "
                f"stored {fact.value!r} != recomputed {expected!r}"
            )
    if errors:
        raise ValueError("Derived-fact recomputation failed: " + "; ".join(errors[:5]))


def request_dob_disputed(facts: list[Fact], child: Child) -> bool:
    """True when any document-stated dob disagrees with child.dob."""

    request_val = normalize_value("dob", child.dob, child.dob)
    for fact in facts:
        if fact.predicate != "dob":
            continue
        if fact.source_id in {REQUEST_SOURCE_ID, COMPUTED_SOURCE_ID}:
            continue
        if fact.value != request_val:
            return True
    return False


def build_request_dob_fact(child: Child, *, fact_id: str) -> Fact:
    """child.dob asserted at request time — ordinary record fact for conflict grouping."""

    value = normalize_value("dob", child.dob, child.dob)
    return Fact(
        id=fact_id,
        subject="child",
        predicate="dob",
        value=value,
        value_text=f"DOB on referral/intake form: {child.dob}",
        qualifier=None,
        assertion="asserted",
        source_id=REQUEST_SOURCE_ID,
        source_date=child.evaluation_date,
        as_of_date=child.evaluation_date,
        reporter=None,
        life_stage="birth",
        grade=None,
        temporality="durable",
        confidence="stated",
        derivation=None,
        inherits_dispute=False,
    )


def build_age_years_fact(
    child: Child,
    *,
    fact_id: str,
    inherits_dispute: bool = False,
) -> Fact:
    """Current age as an ordinary ledger row — citation target for the drafter."""

    value = recompute_derived_value(AGE_DERIVATION, child)
    return Fact(
        id=fact_id,
        subject="child",
        predicate="age_years",
        value=value,
        value_text=f"Age {value} years at evaluation ({AGE_DERIVATION})",
        qualifier=None,
        assertion="asserted",
        source_id=COMPUTED_SOURCE_ID,
        source_date=child.evaluation_date,
        as_of_date=child.evaluation_date,
        reporter=None,
        life_stage="current",
        grade=None,
        temporality="as_of",
        confidence="stated",
        derivation=AGE_DERIVATION,
        inherits_dispute=inherits_dispute,
    )


def inject_derived_and_request_facts(
    facts: list[Fact],
    child: Child,
    *,
    next_id: int = 1,
) -> tuple[list[Fact], int]:
    """
    Append request-time dob + derived age_years to an extracted fact list.

    Strips any prior request/computed rows first so re-injection after a merge
    recomputes against the current evaluation_date rather than carrying stale
    derived values. Fact ids for these rows are stable across merges.

    ``next_id`` is retained for call-site compatibility; synthetic rows use
    fixed ids and do not consume it. Returns (extended facts, next_id unchanged).
    """

    out = strip_synthetic_facts(facts)
    out.append(build_request_dob_fact(child, fact_id=REQUEST_DOB_FACT_ID))

    disputed = request_dob_disputed(out, child)
    out.append(
        build_age_years_fact(
            child,
            fact_id=COMPUTED_AGE_FACT_ID,
            inherits_dispute=disputed,
        )
    )
    return out, next_id
