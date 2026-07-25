# Fixture rules (`POST /ask`)

## Shape

```json
{
  "confirm_synthetic": true,
  "section": "history",
  "child": { "name": "Alex Rivera", "dob": "YYYY-MM-DD", "evaluation_date": "YYYY-MM-DD" },
  "sources": [
    { "id": "…", "type": "…", "date": "YYYY-MM-DD", "label": "…", "content": "…" }
  ]
}
```

- `confirm_synthetic` must be `true` (synthetic / de-identified only).
- `child.name` — synthetic full name (matches content), never a real child.
- `sources[].type` — one of: `assessment`, `school`, `parent`, `teacher`, `observation`, `prior_eval`, `other`.
- `sources[].id` — stable; referenced by facts/conflicts.
- `sources[].date` — that document’s own date (ISO), not the case DOB.

## Content hygiene

1. **`content` looks like a real document** — no `CONFLICT PLANT`, coaching, or builder notes.
2. **Eval expectations** (`expected_conflicts`, `expected_facts`, …) live in sibling fields only — never inside `content`, and never sent to the model.
3. **Do not include the report being drafted** as a source (final / draft psycho-ed reports).
4. **Drop near-duplicate exports** (same instrument, multiple file formats).

## Size (practical)

Extract runs **one model call per source**. A single source that is too large will 429 (TPM) or hit max completion length.

- Prefer short narrative docs for history smoke tests (interview, prior eval, letters).
- Avoid dumping full rating-scale interpretive PDFs unless you need them.
- If `/ask` fails with 429 / `LengthFinishReasonError`, cut or truncate the largest sources first.

## Answer key (sibling fields)

Expectations live as **top-level sibling keys** in the same fixture JSON — next to `confirm_synthetic`, `section`, `child`, `sources`. **Never inside `content`.** `ask_payload()` strips every key in `FIXTURE_META_KEYS` (`test_all_stages.py`) before the request is built, so the answer key never reaches a model prompt. A fixture with no answer key only proves the pipeline *ran* — it measures nothing (spec §10).

Recognized keys:

| Key | Shape | Checked by |
|---|---|---|
| `expected_conflicts` | `[{ "topic", "predicate"?, "qualifier"?, "source_ids": [...] }]` | `conflict_matches`: `source_ids` must be a subset of the detected versions; `predicate` (if given) must match exactly; `qualifier` (if the key is present) is compared normalized; `topic` tokens optional once predicate matches. |
| `expected_facts` | `[{ "statement", "source_id", "life_stage"? }]` | `fact_matches`: same `source_id` and ≥60% of the statement's content tokens present. Fuzzy narrative-presence check. |
| `expected_ledger_facts` | `[{ "source_id", "predicate", "value"?, "assertion"?, "qualifier"? }]` | Structured, ledger-level recall (exact `predicate` / `assertion`). Also read by `measure_stage5*_variance.py`. |
| `forbidden_predicates_by_source` | `{ "source_id": ["predicate", ...] }` | Negative check — those predicates must NOT be extracted from that source (e.g. a source that defers). |
| `expected_gap_life_stages_empty` | `["birth", "infancy", ...]` | Life stages the gap report must show as empty. |
| `expected_as_of_anchor`, `expected_vague_no_anchor`, `expected_grade_timeline` | (as_of-anchor / timeline fixtures) | Specialized `as_of_date` and timeline checks. |

Rules (spec §10, §14.3):

1. **Derive expectations from the sources, not from a finished report.** For a fixture cut from the golden case, the answer key comes from reading the source `content` and applying the vocabulary (record → conflict, perspectival → variance, `as_of` → timeline). The finished golden report stays a held-back acceptance test — never the source of an eval key, or the fixture just re-encodes that one output.
2. **Include negative cases** — packets where sources agree and any returned conflict is a false positive (`expected_conflicts: []` on a genuinely conflict-free packet).
3. **A conflict expectation a domain expert says isn't one is a bad test, not a failing detector** — fix the expectation (see the private-tutoring and allergy corrections, 2026-07-24).
