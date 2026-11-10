"""LLM TASKS: parse and validate model output for each AI task. Prompts live in prompts.py.

Each function raises `AINotConfigured` when no LLM is set up; the caller then uses its heuristic fallback.

  extract_profiles_from_text   PDF text without tables  -> employee records
  match_skills                 LLM judge for skill standardization
  extract_requirements         project description      -> required skills
  extract_cv                   one CV's text            -> proposed employee record (levels are estimates, quotes are checked by code)
  match_roles                  roles + skills of a few employees -> proposed role changes (verified by services/roles.py)
  explain_seniority            seniority facts          -> one sentence (the label itself is decided by code)
  write_staffing_summary       team facts               -> short summary (verified by services/narrative.py)
  write_gap_narrative          gap facts                -> short forecast narrative (verified by services/narrative.py)

CUSTOMIZE: swap `llm.complete_json` for structured output / tool calling, add few-shot examples, retrieval, etc.
"""
import json
import logging
from functools import lru_cache

from ..config import get_settings
from . import jev, llm, prompts
from .guardrails import check_input
from .tracing import traced

log = logging.getLogger(__name__)

# Reply budgets (tokens). The default in llm.py (500) is enough for a one-line answer, but these tasks return JSON lists: a reply cut
# in the middle is invalid JSON, and the caller then silently falls back to the heuristic.
PROFILES_MAX_TOKENS = 4000      # up to several employees with every skill and rating
REQUIREMENTS_MAX_TOKENS = 1500  # up to about 15 requirements with weight, min_people and domain tags
MATCH_MAX_TOKENS = 200          # one canonical skill name and a confidence
CV_MAX_TOKENS = 3000            # up to 30 skills, each with a level and a quoted sentence
ROLE_MATCH_MAX_TOKENS = 1800    # up to 6 employees with up to 3 changes each
SENTENCE_MAX_TOKENS = 150       # one sentence
NARRATIVE_MAX_TOKENS = 350      # a paragraph of at most about 90 words


def _chunks(text: str, size: int) -> list[str]:
    out, cur, n = [], [], 0
    for line in text.splitlines():
        if n + len(line) > size and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def _rating(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0 <= f <= 4 else None  # levels are 0-4; anything else is dropped (the caller sees an unrated skill)


@traced("task.extract_profiles_from_text")
def extract_profiles_from_text(text: str) -> list[dict]:
    """-> [{employee_no, name, roles: [str], background, skills: [{skill, rating}]}]"""
    if not llm.is_configured():
        raise llm.AINotConfigured("OPENAI_API_KEY is not set")
    out: list[dict] = []
    for chunk in _chunks(text, 3500):
        safe = check_input(chunk, toxicity_check=False)  # uploaded file text is not rejected over one word
        data = llm.complete_json(
            prompts.EXTRACT_EMPLOYEES_SYSTEM, f"<data>\n{safe}\n</data>", namespace="extract_profiles",
            max_tokens=PROFILES_MAX_TOKENS,
        )
        for e in data.get("employees", []):
            if not str(e.get("name", "")).strip() and not e.get("employee_no"):
                continue
            roles = e.get("roles") or []
            out.append(
                {
                    "employee_no": str(e["employee_no"]).strip() if e.get("employee_no") else None,
                    "name": str(e.get("name", "")).strip(),
                    "roles": [roles] if isinstance(roles, str) else [str(r) for r in roles],
                    "background": str(e.get("background") or ""),
                    "domain_tags": ([str(tag).strip() for tag in e.get("domain_tags", []) if str(tag).strip()]
                                    if isinstance(e.get("domain_tags"), list) else []),
                    "skills": [
                        {"skill": str(s.get("skill", "")).strip(), "rating": _rating(s.get("rating"))}
                        for s in e.get("skills", [])
                        if isinstance(s, dict) and str(s.get("skill", "")).strip()
                    ],
                }
            )
    return out


NONE_OF_THESE = "NONE_OF_THESE"


@lru_cache(maxsize=4096)
def _jev_pick(raw: str, candidates: tuple[str, ...]) -> tuple[str | None, float]:
    """Jev picks one of `candidates` (or none of them). The key is read from TYPESAFE_API_KEY by the SDK, never from code."""
    choice, confidence = jev.decide(
        state={"skill_spelling": raw, "candidate_skills": list(candidates)},
        question="match",
        instructions=("Does the spelling name, abbreviate, or describe work done with exactly one candidate skill? If yes, answer that "
                      "candidate. Different skills are NOT the same even if related: Java is not JavaScript, React is not React "
                      f"Native. Answer {NONE_OF_THESE} if no single candidate fits."),
        options=(*candidates, NONE_OF_THESE),
    )
    if choice == NONE_OF_THESE:
        return None, 0.0
    return choice, confidence


@traced("task.match_skills")
def match_skills(raw: str, candidates: list[str]) -> dict:
    """Pick the canonical skill that `raw` is a synonym of. -> {"canonical": str | None, "confidence": float, "method": "jev" | "llm"}
    With STANDARDIZE_MATCHER=jev and TYPESAFE_API_KEY set, Jev decides; if Jev fails the configured LLM is used instead."""
    s = get_settings()
    if s.standardize_matcher.lower() == "jev":
        if not s.typesafe_api_key:
            log.warning("STANDARDIZE_MATCHER=jev but TYPESAFE_API_KEY is not set; using the LLM.")
        else:
            try:
                canonical, conf = _jev_pick(raw, tuple(candidates))
                return {"canonical": canonical, "confidence": conf, "method": "jev"}
            except Exception as e:  # noqa: BLE001  a Jev outage must not stop standardization
                log.warning("Jev skill matching failed, using the LLM: %s", type(e).__name__)
    if not llm.is_configured():
        raise llm.AINotConfigured("OPENAI_API_KEY is not set")
    data = llm.complete_json(
        prompts.standardize_system(),
        prompts.STANDARDIZE_USER.format(raw=raw, candidates=candidates),
        namespace="match_skills",
        max_tokens=MATCH_MAX_TOKENS,
    )
    canonical = data.get("canonical")
    if canonical not in candidates:  # never accept a name the model invented
        return {"canonical": None, "confidence": 0.0, "method": "llm"}
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.8))))
    except (TypeError, ValueError):
        conf = 0.8
    return {"canonical": canonical, "confidence": conf, "method": "llm"}


@traced("task.extract_requirements")
def extract_requirements(prompt: str, taxonomy_names: list[str]) -> dict:
    """-> {"requirements": [{skill, level, weight, min_people, domain_tags}], "headcount": int | None, "roles": [str]}"""
    if not llm.is_configured():
        raise llm.AINotConfigured("OPENAI_API_KEY is not set")
    safe = check_input(prompt)
    data = llm.complete_json(
        prompts.requirements_system(),
        f"Taxonomy: {', '.join(taxonomy_names[:300])}\n\n<data>\n{safe}\n</data>",
        namespace="requirements",
        max_tokens=REQUIREMENTS_MAX_TOKENS,
    )
    hc = data.get("headcount")
    return {
        "requirements": [r for r in data.get("requirements", []) if isinstance(r, dict) and r.get("skill")],
        "headcount": int(hc) if isinstance(hc, (int, float)) and hc >= 1 else None,
        "roles": [str(r) for r in data.get("roles", [])],
    }


@traced("task.extract_cv")
def extract_cv(text: str) -> dict:
    """-> {"name": str|None, "employee_no": str|None, "roles": [str], "background": str, "skills": [{"skill", "level", "evidence"}]}.
    The levels are the model's ESTIMATES: services/parsing/cv_parser.py verifies each quote against the CV and caps the levels."""
    if not llm.is_configured():
        raise llm.AINotConfigured("OPENAI_API_KEY is not set")
    safe = "\n".join(check_input(chunk, toxicity_check=False) for chunk in _chunks(text, 3500))  # CV text is data: injection is refused, one word is not
    data = llm.complete_json(prompts.CV_EXTRACT_SYSTEM, f"<data>\n{safe}\n</data>", namespace="extract_cv", max_tokens=CV_MAX_TOKENS)
    roles = data.get("roles") or []
    skills = []
    for s in data.get("skills") or []:
        if not isinstance(s, dict) or not str(s.get("skill", "")).strip():
            continue
        try:
            level = int(float(s.get("level")))
        except (TypeError, ValueError):
            level = 1
        skills.append({"skill": str(s["skill"]).strip()[:80], "level": level, "evidence": str(s.get("evidence") or "").strip()[:400]})
    return {
        "name": str(data["name"]).strip()[:120] if data.get("name") else None,
        "employee_no": str(data["employee_no"]).strip()[:40] if data.get("employee_no") else None,
        "roles": [str(r).strip()[:80] for r in ([roles] if isinstance(roles, str) else roles) if str(r).strip()][:3],
        "background": str(data.get("background") or "")[:1500],
        "skills": skills[:30],
    }


def _text_from(system: str, facts: dict, key: str, namespace: str, max_tokens: int) -> str | None:
    """One short model-written text from verified facts. None when no model is set up or the reply is unusable; the caller verifies it."""
    if not llm.is_configured():
        return None
    data = llm.complete_json(system, json.dumps(facts, ensure_ascii=False), namespace=namespace, max_tokens=max_tokens)
    value = data.get(key) if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


@traced("task.match_roles")
def match_roles(canonical_roles: list[dict], employees: list[dict]) -> dict[int, list[dict]]:
    """canonical_roles: [{role, holders}]; employees: [{id, roles, skills: [{skill, level}]}] -> {employee_id: [raw change dicts]}.
    The changes are PROPOSALS: services/roles.verify keeps only the ones the data supports."""
    if not llm.is_configured():
        raise llm.AINotConfigured("OPENAI_API_KEY is not set")
    facts = {"canonical_roles": canonical_roles[:60], "employees": employees}
    data = llm.complete_json(prompts.ROLE_MATCH_SYSTEM, f"<data>\n{json.dumps(facts, ensure_ascii=False)}\n</data>",
                             namespace="match_roles", max_tokens=ROLE_MATCH_MAX_TOKENS)
    ids = {e["id"] for e in employees}
    out: dict[int, list[dict]] = {}
    for r in (data.get("results") if isinstance(data, dict) else None) or []:
        try:
            eid = int(r.get("employee_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if eid in ids and isinstance(r.get("changes"), list):
            out[eid] = [c for c in r["changes"] if isinstance(c, dict)][:5]
    return out


@traced("task.explain_seniority")
def explain_seniority(facts: dict) -> str | None:
    return _text_from(prompts.SENIORITY_EXPLAIN_SYSTEM, facts, "explanation", "seniority_explain", SENTENCE_MAX_TOKENS)


@traced("task.write_staffing_summary")
def write_staffing_summary(facts: dict) -> str | None:
    return _text_from(prompts.STAFFING_SUMMARY_SYSTEM, facts, "summary", "staffing_summary", NARRATIVE_MAX_TOKENS)


@traced("task.write_gap_narrative")
def write_gap_narrative(facts: dict) -> str | None:
    return _text_from(prompts.GAP_NARRATIVE_SYSTEM, facts, "narrative", "gap_narrative", NARRATIVE_MAX_TOKENS)
