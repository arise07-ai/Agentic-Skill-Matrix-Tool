"""SENIORITY: Junior, Mid or Senior, decided by CODE from an employee's skill levels (0-4). No AI decides the label.

Levels: 0 None, 1 Learned, 2 Applied, 3 Experienced, 4 Mastered.

Rule (tune the constants below; nothing else changes):
  - Only skills rated 1-4 count. A 0 means "no skill" and an unrated skill has no level, so neither counts.
  - Fewer than MIN_RATED rated skills            -> "Not assessed" (too little evidence to judge).
  - average >= SENIOR_AVG and at least SENIOR_MIN_EXPERIENCED skills at 3 or 4  -> Senior
  - average <  JUNIOR_AVG                         -> Junior
  - everything else                               -> Mid

The result is computed on read from the stored ratings, so it is never out of date and needs no database column.
"""
from statistics import mean

LEVEL_NAMES = {0: "None", 1: "Learned", 2: "Applied", 3: "Experienced", 4: "Mastered"}
MAX_LEVEL = 4

MIN_RATED = 3
SENIOR_AVG = 3.0
SENIOR_MIN_EXPERIENCED = 3
JUNIOR_AVG = 1.75

NOT_ASSESSED = "Not assessed"


def assess(ratings: list) -> dict:
    """ratings: the skill levels of ONE employee (None and 0 are ignored). Returns
    {"level": "Junior" | "Mid" | "Senior" | "Not assessed", "rated_skills", "average", "experienced_plus", "mastered", "reason"}."""
    rated = [int(r) for r in ratings if r is not None and 1 <= int(r) <= MAX_LEVEL]
    n = len(rated)
    experienced = sum(1 for r in rated if r >= 3)
    mastered = sum(1 for r in rated if r == 4)
    avg = round(mean(rated), 2) if rated else None
    base = {"rated_skills": n, "average": avg, "experienced_plus": experienced, "mastered": mastered}
    if n < MIN_RATED:
        return {**base, "level": NOT_ASSESSED,
                "reason": f"Only {n} rated skill{'s' if n != 1 else ''}; at least {MIN_RATED} are needed to judge seniority."}
    detail = f"average level {avg} across {n} rated skills; {experienced} at Experienced or Mastered"
    if avg >= SENIOR_AVG and experienced >= SENIOR_MIN_EXPERIENCED:
        return {**base, "level": "Senior", "reason": f"Senior: {detail} (needs average {SENIOR_AVG}+ and {SENIOR_MIN_EXPERIENCED}+ at Experienced or above)."}
    if avg < JUNIOR_AVG:
        return {**base, "level": "Junior", "reason": f"Junior: {detail} (average is below {JUNIOR_AVG})."}
    return {**base, "level": "Mid", "reason": f"Mid: {detail}."}


def assess_record(record: dict) -> dict:
    """The same for an uploaded record: {"skills": [{"raw_skill", "rating"}]}."""
    return assess([s.get("rating") for s in record.get("skills", [])])


def label(ratings: list) -> str:
    return assess(ratings)["level"]


def explain(assessment: dict, skills: list[dict]) -> tuple[str, str]:
    """One sentence that explains the label -> (text, "ai" | "rules"). The model only words facts that code computed; a sentence that
    names another label or a number/skill that is not in the facts is dropped and the code-built reason is used instead."""
    import re

    from . import narrative

    fallback = assessment["reason"]
    if assessment["level"] == NOT_ASSESSED:
        return fallback, "rules"
    top = sorted((s for s in skills if s.get("rating")), key=lambda s: -s["rating"])[:5]
    facts = {"label": assessment["level"], "rated_skills": assessment["rated_skills"], "average_level": assessment["average"],
             "skills_at_experienced_or_mastered": assessment["experienced_plus"], "skills_mastered": assessment["mastered"],
             "top_skills": [{"skill": s["raw_skill"], "level": s["rating"], "level_name": LEVEL_NAMES[s["rating"]]} for s in top],
             "rule": {"senior": f"average {SENIOR_AVG}+ and {SENIOR_MIN_EXPERIENCED}+ skills at Experienced or above",
                      "junior": f"average below {JUNIOR_AVG}", "mid": "everything in between"}}
    try:
        from ..ai import tasks

        text = tasks.explain_seniority(facts)
    except Exception:  # noqa: BLE001  no model, or it failed: the code-built reason is always there
        return fallback, "rules"
    text, why = narrative.verify(text, facts, all_names=[], all_skills=[s["raw_skill"] for s in skills],
                                 allowed_names=set(), allowed_skills={s["raw_skill"] for s in top}, limit=300)
    if text and {w.lower() for w in re.findall(r"\b(junior|mid|senior)\b", text, re.I)} - {assessment["level"].lower()}:
        text = None
    return (text, "ai") if text else (fallback, "rules")
