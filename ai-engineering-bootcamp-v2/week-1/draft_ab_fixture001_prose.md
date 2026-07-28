# Draft A/B — fixture_001 Background & History

**Date:** 2026-07-28
**Part 4 verify:** span-level contract (`quote` + `fact_ids`) + labeled-block prompt in A.
**Controls:** same ledger, same model, `DRAFT_TEMPERATURE=1.0`. Only the system prompt
(+ schema description for statements) differs. No scores.
**A diagnostics:** statements=20; mean_fact_ids=1.20; multi_id_entries=4; max_fact_ids=2; inline `f_…` in clean prose=0;
unanchored quotes=2. B inline `f_…`=0.

---

## Variant A — production `draft_prompt.md` (span-level)

*(Coverage required; one-claim-per-sentence composition is not. Ledger ids stay out of prose.)*

**Pregnancy and Delivery:** Emma Rose Callahan was born on March 22, 2010. Her mother, Diane Callahan, reported that Emma was adopted at 19 months. Due to this adoption, information on Emma's birth is limited and includes exposure to trauma and alleged in utero exposure to methamphetamines.

**Developmental History:** According to the October 2024 IEP, Emma met all her developmental milestones on time except for walking at about 19 months and talking at about 2 years. However, the IEP also stated that Emma demonstrates academic weakness in math fluency, numerical operations, and written language. Conversely, her communication development is reported to be average and not a concern at this time. Her mother noted that Emma has significant deficits in executive functioning and adaptive skills as of December 2025.

**Behavioral Concerns:** Emma's behavioral history has included meltdowns occurring 2-5 times a week, characterized by inconsolable screaming and hitting her surroundings. The 2013 early childhood diagnostic assessment described her as having borderline clinical range for emotional reactivity and oppositional defiant problems. As of the latest information, Emma's behavior has been reported to be adaptive by her school on October 2, 2024, and does not impede her learning.

**Trauma History:** Emma has a complex history that includes early trauma, adoption, and multiple placements. According to the October 2024 IEP, she has been diagnosed with Reactive Attachment Disorder (RAD) and ADHD, reflecting her struggles with attention regulation, and emotional responses.

**Educational History:** Emma has had an Individualized Education Program (IEP) since May 29, 2019, with her most recent evaluation dated October 2, 2024. The IEP indicates her placement in general education with accommodations to meet her educational needs. In the past, she has shown difficulties succeeding in traditional school environments without intensive support. 

**Current Health:** At the time of the latest evaluation, Emma has no known allergies according to her IEP from October 2024. However, she has been described as having seasonal allergies in a previous report from May 29, 2019. Emma is currently prescribed veterinary medications and is being fitted for a CPAP machine due to reported sleep difficulties. As of April 2025, her mother described her sleep as poor without medications but fine with them. 

**Student / Caregiver / Teacher Input:** Emma's mother reported that she is aware of her learning challenges, stating, "I am okay with failure. I understand that it’s part of learning and I know I need to try". Teachers have remarked on her ability to cooperate during learning tasks and engage effectively when prompted. Emma's teacher noted that she shows lower energy levels but can engage more fully when encouraged. 

Overall, Emma exhibits a range of strengths and challenges, demonstrating resilience and a positive attitude toward her learning journey.

---

## Variant A — annotated review view

*(fact_ids re-injected at quote spans)*

**Pregnancy and Delivery:** Emma Rose Callahan was born on March 22, 2010. [f_doc_01_001] Her mother, Diane Callahan, reported that Emma was adopted at 19 months. [f_doc_25_016, f_doc_25_002] Due to this adoption, information on Emma's birth is limited and includes exposure to trauma and alleged in utero exposure to methamphetamines.

**Developmental History:** According to the October 2024 IEP, Emma met all her developmental milestones on time except for walking at about 19 months and talking at about 2 years. [f_doc_11_014, f_doc_25_015] However, the IEP also stated that Emma demonstrates academic weakness in math fluency, numerical operations, and written language. [f_doc_11_006] Conversely, her communication development is reported to be average and not a concern at this time. [f_doc_11_011] Her mother noted that Emma has significant deficits in executive functioning and adaptive skills as of December 2025. [f_doc_13_006]

**Behavioral Concerns:** Emma's behavioral history has included meltdowns occurring 2-5 times a week, characterized by inconsolable screaming and hitting her surroundings. [f_doc_26_005] The 2013 early childhood diagnostic assessment described her as having borderline clinical range for emotional reactivity and oppositional defiant problems. [f_doc_26_010] As of the latest information, Emma's behavior has been reported to be adaptive by her school on October 2, 2024, and does not impede her learning. [f_doc_11_010]

**Trauma History:** Emma has a complex history that includes early trauma, adoption, and multiple placements. [f_doc_13_003] According to the October 2024 IEP, she has been diagnosed with Reactive Attachment Disorder (RAD) and ADHD, reflecting her struggles with attention regulation, and emotional responses. [f_doc_11_013]

**Educational History:** Emma has had an Individualized Education Program (IEP) since May 29, 2019, with her most recent evaluation dated October 2, 2024. [f_doc_25_005, f_doc_11_005] The IEP indicates her placement in general education with accommodations to meet her educational needs. [f_doc_11_019] In the past, she has shown difficulties succeeding in traditional school environments without intensive support. [f_doc_13_007] 

**Current Health:** At the time of the latest evaluation, Emma has no known allergies according to her IEP from October 2024. [f_doc_11_012] However, she has been described as having seasonal allergies in a previous report from May 29, 2019. [f_doc_25_012] Emma is currently prescribed veterinary medications and is being fitted for a CPAP machine due to reported sleep difficulties. [f_doc_25_014, f_doc_11_009] As of April 2025, her mother described her sleep as poor without medications but fine with them. 

**Student / Caregiver / Teacher Input:** Emma's mother reported that she is aware of her learning challenges, stating, "I am okay with failure. I understand that it’s part of learning and I know I need to try". Teachers have remarked on her ability to cooperate during learning tasks and engage effectively when prompted. [f_doc_21_007] Emma's teacher noted that she shows lower energy levels but can engage more fully when encouraged. 

Overall, Emma exhibits a range of strengths and challenges, demonstrating resilience and a positive attitude toward her learning journey.

unanchored: Her mother described her sleep as poor without medications but fine with them.
unanchored: Emma's mother reported that she is aware of her learning challenges, stating, "I am okay with failure. I understand that it’s part of learning and I know I need to try."

---

## Variant B — statements/fact_id relaxed (control)

*(Ledger-grounded prose still required; statements optional/coarse; multi-fact sentences encouraged.)*

Emma Rose Callahan was born on March 22, 2010, and has a documented history that includes a series of developmental milestones as well as some challenges. By the time she was 19 months old, Emma was walking, and she began to talk around the age of 2 years. Overall, she met most development milestones on time, although there were some delays in walking and talking. Her communication development was reported to be average, which was not seen as a concern at that time (doc_11, 2024-10-02). 

In terms of her early academic challenges, records indicate that Emma displayed academic weaknesses particularly in math fluency, numerical operations, and written language during her time in the fourth grade (doc_11, 2024-10-02). More recently, however, she has been noted to exhibit significant deficits in executive functioning and adaptive skills as reported in December 2025, raising additional concerns (doc_13, 2025-12-15). While one assessment mentions that she demonstrates attention deficits associated with her diagnosis of ADHD, another teacher's report stated that her behavior is adaptive and does not impede her learning at this point (doc_11, 2024-10-02). 

Additionally, Emma's background is notable for exposure to trauma, including a history of Reactive Attachment Disorder (RAD) and ADHD, which has complicated her developmental trajectory (doc_11, 2024-10-02; doc_13, 2025-12-15). Emma was adopted at the age of 19 months, and this adoption came after a period marked by trauma and disruption due to multiple placements (doc_13, 2025-12-15). Reports from early assessments noted interactions with her siblings potentially trigger emotional dysregulation, resulting in unsafe behaviors (doc_13, 2025-12-15). 

As of now, Emma is 15 years old, assessed on July 11, 2025. Over the years, she has faced various challenges, including difficulties in maintaining focus in educational settings, with her executive functioning noted as a particular area of need (doc_13, 2025-12-15). Throughout her schooling, Emma has been provided with various forms of support, but she has struggled to succeed in traditional educational environments without comprehensive assistance. As such, one of the critical points in her educational journey includes receiving an IEP, which has been reviewed multiple times, with the latest update logged in October 2024, when she was in the ninth grade (doc_11, 2024-10-02). 

Emma's story reflects a complex interplay of strengths and challenges, shaped significantly by her early experiences and ongoing educational needs.

---

# Reading — 2026-07-28 (Part 4 verify)

**Verdict: pass conditions met.** n=1 at temperature 1.0 — no effect-size claim.

| Condition | Result |
|---|---|
| 0 `f_…` in clean A prose | **pass** (0) |
| annotated review still shows ids | **pass** (ids at quote ends) |
| statements populated / coarser | **pass, mild** — 20 entries, mean 1.20 ids, 4 multi-id (prior A: 17 single-fact). Not fewer overall; fusion started. |
| no provenance paragraph | **pass** — labeled life-stage blocks; no "conflicting accounts" / "further complexity" collector |
| must-mention conflict in life-stage block | **pass** — `developmental_history` bag narrated inside **Developmental History** (milestones / academic weakness / communication average / executive deficits), both sides, unresolved |
| most quotes anchor | **pass** — 2 unanchored (tolerable); one looks like curly-quote mismatch on the mother quote, one drops the "As of April 2025," prefix |

**What moved:** clean/annotated split works; bold run-ins organize by theme; ledger ids stay out of Molly's paste string.

**What didn't (and Part 4 said not to over-read):** statement coarseness is mild; Developmental History still chains *However / Conversely* inside the block; A omits current age (`f_computed_age_years`); "veterinary medications" looks like a ledger garble worth spot-checking later. Hypothesis 2 (flattened ledger / connective tissue) untouched — B still narrates by document.

