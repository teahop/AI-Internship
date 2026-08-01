"""Registered Background & History predicates (mirrored from week-1/predicates.py).

Kept as a frozen set in week-3 so this package never imports week-1 code or
shares its OpenAI environment. Refresh when the vocabulary changes.
"""

from __future__ import annotations

REGISTERED_PREDICATES: frozenset[str] = frozenset(
    {
        "defers_to",
        "legal_name",
        "dob",
        "age_years",
        "grade",
        "retention_year",
        "pregnancy_course",
        "birth_term",
        "birth_delivery",
        "nicu",
        "walked_age_months",
        "first_words_age_months",
        "two_word_phrases_age_months",
        "developmental_history",
        "allergy_status",
        "allergy_substance",
        "health_plan_status",
        "medications",
        "hospitalizations",
        "sleep",
        "attendance",
        "iep_status",
        "plan_504_status",
        "intervention_tier",
        "private_tutoring",
        "behavioral_referral",
        "referral_reason",
        "basic_reading",
        "reading_fluency",
        "reading_comprehension",
        "spelling",
        "written_expression",
        "writing_fluency",
        "math_computation",
        "math_fluency",
        "math_reasoning",
        "inattention_rating",
        "hyperactivity_rating",
        "behavioral_concern",
        "anxiety_impression",
        "homework_completion_impression",
        "classroom_engagement_impression",
        "testing_impression",
        "developmental_concern_onset",
        "trauma_history",
        "preschool_experience_impression",
        "interview_impression",
    }
)
