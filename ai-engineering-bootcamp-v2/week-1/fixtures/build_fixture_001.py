#!/usr/bin/env python3
"""
Regenerate the fixture_001 per-file case from the de-identified raw corpus.

Source of truth for *content*: ``fixture_001_ask.json`` (25 sources, case E.C.).
This script re-shapes that corpus into the incremental model:

  fixtures/fixture_001/<doc_id>.json   one self-contained /ask-shaped fixture per source
  fixtures/fixture_001/manifest.json   case manifest — arrival order, coverage gaps,
                                        candidates, hazards, and case-level expectations

Answer keys are authored here (record -> conflict, perspectival -> variance,
as_of -> timeline; §10 / §14.3) from the source *content* + predicates.py — never
from any finished report. Score-report sources carry an empty narrative key
(Stage 6.2 triage). Decisions confirmed with TJ 2026-07-25 are inlined below.

Run:  python fixtures/build_fixture_001.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "fixture_001_ask.json"
OUT = HERE / "fixture_001"

# --- Classification (Stage 6.2 triage) ------------------------------------
# score_report -> skipped for Background & History; empty narrative key.
SCORE_REPORTS = {
    "doc_02", "doc_03", "doc_04", "doc_05", "doc_06", "doc_07", "doc_08",
    "doc_09", "doc_10", "doc_12", "doc_17", "doc_20", "doc_23", "doc_24", "doc_28",
}
# Near-duplicate RIAS-2 exports of doc_17's session — dropped (FIXTURE_RULES §4,
# confirmed w/ TJ). doc_17 (canonical PDF) stays so coverage knows RIAS-2 exists.
# doc_27 (MERIDIAN review letter) was included erroneously and removed from the
# ask corpus; listed here so dropped_sources stays an audit trail.
DROP = {"doc_18", "doc_19", "doc_27"}

# De-id smudge: synthetic-name replacement left a trailing "Jasmine" on some IEP
# legal-name headers (doc_11, doc_25). Scrub before writing so content matches
# the body name and does not invent a false legal_name conflict (TJ 2026-07-25).
_JASMINE_FULL = "Emma Rose Callahan Jasmine"
_JASMINE_TRAIL = re.compile(r"(?<=Emma Rose Callahan)\s+Jasmine\b")


def sanitize_content(text: str) -> str:
    """Remove known de-identification artifacts from source content."""

    text = text.replace(_JASMINE_FULL, "Emma Rose Callahan")
    text = _JASMINE_TRAIL.sub("", text)
    return text


# --- Per-file answer keys (narrative docs only) ---------------------------
# Conservative: only clearly-stated facts. Thin candidates are EXCLUDED and
# listed in the manifest instead (confirmed w/ TJ). Identity facts come from
# narrative sources because score-report headers are skipped (Stage 7 §7.2).
KEYS: dict[str, dict] = {
    # --- administrative narrative: identity only, near-empty ---
    "doc_01": {  # assessment invoice
        "expected_ledger_facts": [
            {"source_id": "doc_01", "predicate": "legal_name", "value": "Emma Rose Callahan"},
            {"source_id": "doc_01", "predicate": "dob", "value": "2010-03-22"},
        ],
        "expected_conflicts": [],
    },
    "doc_14": {  # permission to exchange information
        "expected_ledger_facts": [
            {"source_id": "doc_14", "predicate": "legal_name", "value": "Emma Rose Callahan"},
            {"source_id": "doc_14", "predicate": "dob", "value": "2010-03-22"},
        ],
        "expected_conflicts": [],
    },
    "doc_22": {  # agreement for professional services (DOB left blank in this doc)
        "expected_ledger_facts": [
            {"source_id": "doc_22", "predicate": "legal_name", "value": "Emma Rose Callahan"},
        ],
        "expected_conflicts": [],
    },
    # --- student interview (observation) ---
    "doc_21": {
        "expected_ledger_facts": [
            {"source_id": "doc_21", "predicate": "legal_name", "value": "Emma Rose Callahan"},
            {"source_id": "doc_21", "predicate": "dob", "value": "2010-03-22"},
            {"source_id": "doc_21", "predicate": "sleep"},               # perspectival, reporter=student
            {"source_id": "doc_21", "predicate": "testing_impression"},  # perspectival, examiner in-session
        ],
        "expected_facts": [
            {"source_id": "doc_21",
             "statement": "Without medication sleep is really hard; with medication it is fine, and she always wakes up feeling tired.",
             "life_stage": "current"},
            {"source_id": "doc_21",
             "statement": "Cooperative and persistent but works slowly and carefully, with a flat affect until comfortable.",
             "life_stage": "current"},
        ],
        "expected_conflicts": [],
    },
    # --- WRAP services advocacy letter (LEP) ---
    "doc_13": {
        "expected_ledger_facts": [
            {"source_id": "doc_13", "predicate": "legal_name", "value": "Emma Rose Callahan"},
            {"source_id": "doc_13", "predicate": "dob", "value": "2010-03-22"},
            {"source_id": "doc_13", "predicate": "trauma_history"},  # "complex history including early trauma"
        ],
        "expected_facts": [
            {"source_id": "doc_13",
             "statement": "She has a complex history including early trauma, adoption, and multiple placements.",
             "life_stage": "birth"},
        ],
        "expected_conflicts": [],
    },
    # --- 2013 prior early-childhood diagnostic eval (age 3) ---
    "doc_26": {
        "expected_ledger_facts": [
            {"source_id": "doc_26", "predicate": "legal_name", "value": "Emma Rose Callahan"},
            {"source_id": "doc_26", "predicate": "dob", "value": "2010-03-22"},
            {"source_id": "doc_26", "predicate": "age_years", "value": "3"},
            {"source_id": "doc_26", "predicate": "walked_age_months", "value": "19"},
            {"source_id": "doc_26", "predicate": "allergy_status", "value": "none"},   # "no ... allergies" (as_of 2013)
            {"source_id": "doc_26", "predicate": "medications", "value": "none"},       # "No medications" (as_of 2013)
            {"source_id": "doc_26", "predicate": "hospitalizations", "value": "none"},  # "No Hospitalizations; surgeries"
            {"source_id": "doc_26", "predicate": "iep_status", "assertion": "denied", "value": "none"},  # "not eligible for special ed" (2013)
            {"source_id": "doc_26", "predicate": "trauma_history"},        # "History of trauma: Yes of neglect"
            {"source_id": "doc_26", "predicate": "sleep"},                 # perspectival, parent, 2013
            {"source_id": "doc_26", "predicate": "behavioral_concern"},    # meltdowns 2-5x/wk, aggression
        ],
        "expected_facts": [
            {"source_id": "doc_26", "statement": "She was walking at 19 months of age.", "life_stage": "infancy"},
            {"source_id": "doc_26", "statement": "History of trauma: neglect reported prior to adoption.", "life_stage": "birth"},
            {"source_id": "doc_26", "statement": "No hospitalizations, surgeries, medications, or allergies.", "life_stage": "preschool"},
        ],
        "expected_conflicts": [],
    },
    # --- 2019 initial IEP (4th grade) ---
    "doc_25": {
        "expected_ledger_facts": [
            {"source_id": "doc_25", "predicate": "legal_name", "value": "Emma Rose Callahan"},  # body name; "Jasmine" header artifact excluded (TJ)
            {"source_id": "doc_25", "predicate": "dob", "value": "2010-03-22"},
            {"source_id": "doc_25", "predicate": "age_years", "value": "8"},
            {"source_id": "doc_25", "predicate": "grade", "value": "4"},
            {"source_id": "doc_25", "predicate": "iep_status", "assertion": "asserted"},  # IEP in place; SLD primary / OHI secondary
            {"source_id": "doc_25", "predicate": "walked_age_months", "value": "19"},     # corroborates doc_26 (agreement)
            {"source_id": "doc_25", "predicate": "medications"},          # guanfacine, Singulair, melatonin (as_of 2019)
            {"source_id": "doc_25", "predicate": "allergy_status"},       # "seasonal allergies" (as_of 2019)
            {"source_id": "doc_25", "predicate": "sleep"},                # sleep problems / CPAP / sleep study
        ],
        "expected_facts": [
            {"source_id": "doc_25",
             "statement": "She met milestones on time except walking at about 19 months and talking at about 2 years.",
             "life_stage": "infancy"},
            {"source_id": "doc_25",
             "statement": "She takes guanfacine, Singulair, and melatonin at night.",
             "life_stage": "current"},
        ],
        "expected_conflicts": [],
    },
    # --- 2024 current IEP (9th grade) ---
    "doc_11": {
        "expected_ledger_facts": [
            {"source_id": "doc_11", "predicate": "legal_name", "value": "Emma Rose Callahan"},
            {"source_id": "doc_11", "predicate": "dob", "value": "2010-03-22"},
            {"source_id": "doc_11", "predicate": "age_years", "value": "14"},
            {"source_id": "doc_11", "predicate": "grade", "value": "9"},
            {"source_id": "doc_11", "predicate": "iep_status", "assertion": "asserted"},  # SLD, in place
            {"source_id": "doc_11", "predicate": "medications"},          # Geodon, Trileptal, Vyvanse (current 2024)
            {"source_id": "doc_11", "predicate": "allergy_status", "value": "none"},  # "no known allergies" (current 2024)
            {"source_id": "doc_11", "predicate": "attendance"},           # absences impacting grade (as_of 2024)
        ],
        "expected_facts": [
            {"source_id": "doc_11",
             "statement": "She has RAD and ADHD and takes Geodon, Trileptal, and Vyvanse.",
             "life_stage": "current"},
            {"source_id": "doc_11",
             "statement": "She has no known allergies.",
             "life_stage": "current"},
        ],
        "expected_conflicts": [],
    },
}


def main() -> None:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    child = raw["child"]
    by_id = {s["id"]: s for s in raw["sources"]}

    kept = [s["id"] for s in raw["sources"] if s["id"] not in DROP]

    OUT.mkdir(exist_ok=True)
    files_meta = []
    for sid in kept:
        src = by_id[sid]
        doc_class = "score_report" if sid in SCORE_REPORTS else "narrative"
        source = {
            "id": src["id"],
            "type": src["type"],
            "date": src["date"],
            "label": src["label"],
            "content": sanitize_content(src["content"]),
            "doc_class": doc_class,
        }
        fixture = {
            "confirm_synthetic": True,
            "section": "history",
            "child": child,
            "sources": [source],
        }
        key = KEYS.get(sid)
        if doc_class == "score_report":
            # Stage 6.2: recorded for coverage, no narrative history facts.
            fixture["expected_ledger_facts"] = []
            fixture["expected_facts"] = []
            fixture["expected_conflicts"] = []
        elif key:
            fixture.update(key)
        else:
            # Narrative doc with no authored key -> honest empty (should not happen).
            fixture["expected_ledger_facts"] = []
            fixture["expected_conflicts"] = []

        (OUT / f"{sid}.json").write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        files_meta.append(
            {
                "id": sid,
                "fixture": f"{sid}.json",
                "type": src["type"],
                "date": src["date"],
                "doc_class": doc_class,
                "label": src["label"],
            }
        )

    manifest = {
        "case_id": "fixture_001",
        "confirm_synthetic": True,
        "section": "history",
        "child": child,
        "_comment": (
            "Per-file case for the incremental /extract. Each file is extracted once "
            "and merged into an accumulating ledger (Stage 6). Content source of truth: "
            "fixture_001_ask.json. Keys derived from source content + predicates.py, never "
            "from a finished report (§10/§14.3). Arrival order = packet collection order (doc_NN)."
        ),
        # Arrival order: the order the packet was assembled (doc_NN ascending).
        "arrival_order": kept,
        "files": files_meta,
        # --- Case-level expectation: precision / negative case (the headline finding) ---
        # Every apparent cross-document disagreement resolves to a timeline (as_of),
        # variance (perspectival), a diagnosis with no predicate (coverage gap), or a
        # durable-predicate artifact. No clean record conflict spans these sources.
        "expected_conflicts": [],
        # --- Timelines (as_of, different dates) — NOT conflicts ---
        "expected_timelines": [
            {"predicate": "grade", "sequence": "4 (2019) -> 9 (2024/2025-04) -> 10 (2025-06)"},
            {"predicate": "age_years", "sequence": "3 (2013) -> 8 (2019) -> 14 (2024/2025)"},
            {"predicate": "iep_status", "sequence": "none/ineligible (2013) -> in place SLD+OHI (2019) -> in place SLD (2024)"},
            {"predicate": "medications", "sequence": "none (2013) -> guanfacine/Singulair/melatonin (2019) -> Geodon/Trileptal/Vyvanse (2024)"},
            {"predicate": "allergy_status", "sequence": "none (2013) -> seasonal (2019) -> no known (2024)"},
        ],
        # --- Variance (perspectival, legitimately divergent) — NOT conflicts ---
        "expected_variance": [
            {"predicate": "sleep", "note": "student self-report (2025, poor w/o meds) vs parent report (2013 sleeps well; 2019 sleep problems/CPAP)"},
        ],
        # --- Agreements (durable, corroborated across sources) ---
        "expected_agreements": [
            {"predicate": "walked_age_months", "value": "19", "sources": ["doc_26", "doc_25", "doc_11"]},
            {"predicate": "dob", "value": "2010-03-22", "note": "consistent across every source"},
        ],
        # --- Coverage gaps: stated but no predicate exists — do NOT invent one ---
        "coverage_gaps": [
            {"item": "Diagnosis: Adjustment Disorder w/ Depressed Mood (309.0)", "source_id": "doc_26"},
            {"item": "Diagnoses: ASD L2, ADHD-PI (severe), PTSD, Bipolar, SLD (written expression, math)", "source_id": "doc_13"},
            {"item": "Diagnoses: RAD and ADHD", "source_id": "doc_11"},
            {"item": "Diagnosis: ADHD (AD/HD)", "source_id": "doc_25"},
            {"item": "Adoption / foster placement / multiple placements (no predicate)", "source_id": "doc_26"},
        ],
        # --- Candidate facts: derivable but thin/hedged — EXCLUDED from keys (TJ) ---
        "candidate_facts": [
            {"source_id": "doc_21", "candidate": "tonsillectomy -> hospitalizations", "reason": "mentioned in passing; also durable-collision hazard"},
            {"source_id": "doc_21", "candidate": "unnamed 'meds' -> medications", "reason": "no named medication"},
            {"source_id": "doc_25", "candidate": "appendectomy 4/1/19 -> hospitalizations", "reason": "explicit but durable-collision hazard (see known_hazards)"},
            {"source_id": "doc_25", "candidate": "talking ~2 yrs -> first_words_age_months=24", "reason": "'talking' != first words; 'about'"},
            {"source_id": "doc_26", "candidate": "suspected prenatal meth exposure -> pregnancy_course", "reason": "hedged ('alleged'/'suspected')"},
            {"source_id": "doc_11", "candidate": "reading at 7th-grade level -> reading_* ", "reason": "general reading level; vocabulary splits basic/fluency/comprehension"},
            {"source_id": "doc_11", "candidate": "falls asleep in class -> sleep / classroom_engagement_impression", "reason": "predicate ambiguous"},
        ],
        # --- Known hazards a live extraction run may expose (report, do not hide) ---
        "known_hazards": [
            {"hazard": "hospitalizations is durable but the case has acquired events over time: "
                       "none (2013) / appendectomy (2019) / tonsillectomy (2025). If all are extracted, "
                       "the detector groups them as a false record conflict. Treated as a timeline here; "
                       "events kept out of keys as candidates. Real vocabulary limitation (TJ)."},
            {"hazard": "doc_11 copies a stale 2019 health block forward verbatim (seasonal allergies, "
                       "appendectomy, guanfacine) alongside the current 2024 statement (no known allergies, "
                       "Geodon/Trileptal/Vyvanse). A naive extractor may emit both and create a same-source, "
                       "same-date allergy_status/medications collision. Key asserts the CURRENT 2024 values only."},
            {"hazard": "Age quirk: reports state 14 (2024/2025) but DOB 2010-03-22 computes 15. "
                       "This is an age-validator recomputation finding, not a cross-source conflict."},
            {"hazard": "IEP headers formerly read 'Emma Rose Callahan Jasmine' (de-id smudge). "
                       "Scrubbed to 'Emma Rose Callahan' in build_fixture_001.sanitize_content so "
                       "content matches body legal_name; no header-vs-body name conflict expected."},
        ],
        "dropped_sources": [
            {"id": "doc_18", "reason": "near-duplicate RIAS-2 export of doc_17 (FIXTURE_RULES §4)"},
            {"id": "doc_19", "reason": "near-duplicate RIAS-2 export of doc_17 (FIXTURE_RULES §4)"},
            {
                "id": "doc_27",
                "reason": "MERIDIAN review letter included erroneously; removed from case + ask corpus",
            },
        ],
        "counts": {
            "sources_total": len(kept),
            "narrative": sum(1 for f in files_meta if f["doc_class"] == "narrative"),
            "score_report": sum(1 for f in files_meta if f["doc_class"] == "score_report"),
            "dropped": len(DROP),
        },
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {len(kept)} per-file fixtures + manifest.json to {OUT}")
    print(f"  narrative={manifest['counts']['narrative']} "
          f"score_report={manifest['counts']['score_report']} dropped={manifest['counts']['dropped']}")


if __name__ == "__main__":
    main()
