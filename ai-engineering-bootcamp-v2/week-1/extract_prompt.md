# Fact extraction (single source)

Extract **atomic facts only** from the one source document in the user payload.
You do **not** see any other sources or case metadata. Do not invent, harmonize, or reconcile.
Do **not** write prose, narrative, tone guidance, or paste-ready text.

## Output

Return `SourceExtraction` with a `facts` list. Each fact:

| field | rule |
|-------|------|
| `subject` | Canonical entity only: `child` \| `mother` \| `father` \| `school` (see payload `canonical_subjects`). Default **`child`** for claims about the student. **Do not** use a source id. For `defers_to`, subject is ignored — the server stamps this source's `id`. |
| `predicate` | **Must** be a registered name from the preferred-predicate list below, **or** `__unregistered__`. |
| `proposed_predicate` | When `predicate` is `__unregistered__`, give a new `snake_case` name here. Otherwise `null`. |
| `value` | Normalized comparison value (see normalization). **Required non-empty** — never invent a DOB/year/null from silence. |
| `value_text` | Claim in the source's own words (short quote or close paraphrase). |
| `qualifier` | What the predicate is about when it can apply to more than one thing (e.g. substance, domain). `null` when the predicate admits only one subject-matter (`legal_name`, `dob`, `birth_term`, …). |
| `assertion` | `asserted` or `denied` only — see speech acts below. |
| `reporter` | Who the **source text** attributes the claim to, or `null`. Never guess. |
| `life_stage` | `birth` \| `infancy` \| `preschool` \| `school-age` \| `current` |
| `grade` | Grade at the time of the claim **only if the source states it**; else `null`. Never infer grade from age or age from grade. |
| `as_of_date` | `YYYY-MM-DD` — the date the claim is **about**. See temporal anchoring below. Omit or `null` when the claim has no explicit anchor (server defaults to `source.date`). |
| `confidence` | `stated` if asserted outright; `hedged` if qualified (`about`, `around`, `generally`). |

Temporality (`durable` / `as_of`) is **not** an extraction field — the server stamps it from the predicate vocabulary.

## Temporal anchoring — `as_of_date`

`source.date` is when the document was written. `as_of_date` is when the claim was true.
They often match; set `as_of_date` explicitly only when the source names a **clear temporal anchor**.

| Source text | `as_of_date` | Notes |
|-------------|--------------|-------|
| "Per the 2024 IEP, he was in second grade" | `2024-09-01` (or the IEP date if stated) | Document names a dated record |
| "The 2021 SLP evaluation found expressive delay" | `2021-…` | Named evaluation year |
| "Cumulative file dated 2024-09-01 states student is in 2nd grade" | `2024-09-01` | Explicit date on the cited record |
| "He struggled with reading last year" | omit → defaults to `source.date` | Vague relative time is **not** an anchor |
| "He has always been anxious about reading" | omit | No point-in-time anchor |

**Do not infer aggressively.** A sentence that merely sounds historical is not an anchor.
Wrong `as_of_date` is a provenance error; falling back to `source.date` is merely imprecise.

Use ISO `YYYY-MM-DD`. If only a year is named, use `YYYY-01-01` unless a more specific date appears in the text.

## Speech acts — assertion vs silence

| Speech act | Example | Handling |
|------------|---------|----------|
| **Asserted** | "Nurse documents a known peanut allergy" | Emit a fact with `assertion: asserted` |
| **Denied** | "No prior formal special education eligibility documented" | Emit a fact with `assertion: denied` — negative findings are real information |
| **Non-assertion** | "Father did not describe health-plan status" / silence / omission / "see other records" without denying | **Emit no fact** for that topic. There is no `not_stated` value and no `none` invented from silence. |

If the source **defers** detail to other records ("take health background from the school health file and the IEP"), emit one provenance fact:

- `predicate` = `defers_to`
- `subject` = any canonical value (ignored; server stamps this source's `id`)
- `value` = the targets named (normalized short form)
- `assertion` = `asserted`

Do **not** also invent clinical status facts (`allergy_status`, `health_plan_status`, …) from that deferral.

## Qualifier

When a predicate can be about more than one thing, put that thing in `qualifier` and keep `predicate` generic:

- Known peanut allergy → `predicate: allergy_status`, `qualifier: peanuts`, `value: known`
- Undiagnosed dairy sensitivity → `predicate: allergy_status`, `qualifier: dairy`, `value: undiagnosed`

Do **not** mint compound predicates like `peanut_allergy_status`. Predicates that are inherently singular (`legal_name`, `dob`, `birth_term`) leave `qualifier` null.

## Normalization (write `value` this way)

- Ages in months → integer string only: `13 months`, `thirteen months`, `walked at 13 mos` → `13`
- Ages in years → integer string only: `7 years old` → `7`
- Grades → `K` or integer string: `2nd grade`, `grade 2` → `2`
- Names → the name tokens only (e.g. `Justin M.`)
- Status / classification strings stay **distinct** — do not collapse different labels into one value
- Rating scores keep numerator/denominator when present: `6 of 7` → `6/7`
- Qualifiers → short lowercase tokens (`peanuts`, `dairy`)
- **Never** emit `value: null` or an empty value. If the source does not state a DOB, emit **no** `dob` fact.

## Hard rules

1. Extract only what this source states or explicitly denies. Omit gaps; do not fill with typical-development assumptions.
2. One claim per fact. Split compound sentences into separate facts.
3. If age and grade both appear, emit **two** facts (`age_years` and `grade`) — never derive one from the other.
4. `reporter` is null unless the source text itself attributes the claim to someone/something.
5. Prefer registered predicates; use `__unregistered__` + `proposed_predicate` only when none fit.
6. Set `as_of_date` only from explicit anchors in the source text — never from vague relative time.
7. Synthetic data only.

## Preferred predicates

{{PREDICATE_LIST}}
