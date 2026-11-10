"""Project demand -> requirements, gap prediction, and AI-semantic staffing."""
import logging
import os
import re

import numpy as np

from .. import db
from ..ai import AINotConfigured, eval_log, predict, reranker, tasks, vectorstore
from ..ai.guardrails import check_input
from ..config import get_settings
from ..schemas import LearningRequest, ProjectInput, Requirement
from . import groups, narrative, repo, requested, seniority, standardization

log = logging.getLogger(__name__)

LEVEL_WORDS = {"beginner": 1, "junior": 1, "basic": 1, "learned": 1, "intermediate": 2, "mid": 2, "applied": 2, "proficient": 2,
               "advanced": 3, "senior": 3, "strong": 3, "experienced": 3, "expert": 4, "lead": 4, "mastered": 4}  # levels 1-4


# =========================================================================== requirements
LIST_INTRO = re.compile(r"(?:skills?|experience|expertise|knowledge|proficiency|background)\s+(?:in|with|of|on|using)\s+([^.;\n]+)", re.I)
LIST_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\bor\b|&|\bplus\b)\s*", re.I)
FILLER = re.compile(r"^(?:the|a|an|some|any|good|strong|solid|basic|deep|hands-on|at least|also)\s+", re.I)
REMEDIATION_SCRIPTS = re.compile(r"\bremediation scripts?\b", re.I)
SYSTEM_ALERT_DIAGNOSIS = re.compile(
    r"(?:(?:diagnos\w*|investigat\w*|troubleshoot\w*).{0,40}system alerts?|system alerts?.{0,40}(?:diagnos\w*|investigat\w*|troubleshoot\w*))",
    re.I,
)
LEGACY_BATCH = re.compile(
    r"\b(?:legacy\s+)?batch jobs?.{0,50}(?:failures?|incidents?|errors?|resolution|resolving|troubleshoot\w*)|"
    r"(?:resolution|resolving|troubleshoot\w*).{0,50}\b(?:legacy\s+)?batch jobs?\b",
    re.I,
)
CONTEXT_ONLY_TASKS = re.compile(
    r"\bIT\s+service\s+desk\s+operations?\b|\binfrastructure\s+troubleshooting\b|"
    r"\bautonomous\s+tier[\s-]*1\s+responder\s+systems?\b",
    re.I,
)


def _domain_key(tag: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", tag.casefold()))


def _has_domain_tags(employee: dict, tags: list[str]) -> bool:
    available = {_domain_key(tag) for tag in employee.get("domain_tags", [])}
    return all(_domain_key(tag) in available for tag in tags)


def _clean_domain_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(tag).strip() for tag in value if str(tag).strip()))


def _known_lookup() -> tuple[dict, dict]:
    """(taxonomy, lookup of lowercase term -> canonical). Besides the taxonomy, every skill that an imported employee
    actually has counts as known (COBOL may not be in the starter taxonomy, but people in your data have it)."""
    tax, lookup = standardization.index()
    lookup = dict(lookup)
    for r in db.rows("SELECT DISTINCT canonical_skill AS s FROM employee_skills"):
        if len(r["s"]) >= 3:  # 'Go' / 'R' / 'C' would match ordinary words
            lookup.setdefault(r["s"].lower(), r["s"])
    for g in groups.names():  # broad skills (Cloud, Mobile Development…) are known requirements too
        lookup.setdefault(g.lower(), g)
    for alias, canonical in groups.aliases().items():
        lookup.setdefault(alias, canonical)
    return tax, lookup


def _listed_unknown_skills(prompt: str, covered: set[str]) -> list[str]:
    """Skills the user listed ('skills in Java, Python, COBOL, Claude and OpenAI') that match nothing we know.
    They are kept, flagged as new, instead of being silently dropped: nobody may have them, and that is a real gap."""
    out: list[str] = []
    for m in LIST_INTRO.finditer(prompt):
        for item in LIST_SPLIT.split(m.group(1)):
            item = FILLER.sub("", item.strip(" .:()\"'"))
            words = item.split()
            if not item or len(words) > 4 or len(item) > 40 or len(item) < 2:
                continue
            if item.lower() in covered or item.lower() in {o.lower() for o in out}:
                continue
            out.append(standardization._tidy(item))
    return out


def _heuristic_requirements(prompt: str) -> dict:
    tax, lookup = _known_lookup()
    text = prompt.lower()
    found: list[tuple[int, str, int]] = []  # (pos, canonical, end)
    taken: list[tuple[int, int]] = []
    for term in sorted(lookup, key=len, reverse=True):
        for m in re.finditer(r"(?<![\w+#.])" + re.escape(term) + r"(?![\w+#]|\.\w)", text):
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), lookup[term], m.end()))
    reqs: dict[str, Requirement] = {}
    for pos, canonical, end in sorted(found):
        if canonical in reqs:
            continue
        window = text[end : end + 30]
        level = 2
        m = re.match(r"\s*[\(:\-]?\s*(?:level\s*)?([1-4])\s*(?:/4|\))?", window)
        if m:
            level = int(m.group(1))
        else:
            for word, lv in LEVEL_WORDS.items():
                if re.search(r"\b" + word + r"\b", text[max(0, pos - 20) : end + 20]):
                    level = lv
                    break
        reqs[canonical] = Requirement(skill=canonical, level=level)
    covered = {t for t in lookup if t in text} | {k.lower() for k in reqs}
    for item in _listed_unknown_skills(prompt, covered):
        reqs.setdefault(item, Requirement(skill=item, level=2))
    hc = re.search(r"(\d+)\s*(?:people|persons|engineers|developers|members|staff|resources|fte|analysts|consultants)", text)
    roles = [r["role"] for r in db.rows("SELECT DISTINCT role FROM employee_roles")
             if re.search(r"\b" + re.escape(r["role"].lower()) + r"s?\b", text)]
    return {"requirements": [r.model_dump() for r in reqs.values()],
            "headcount": int(hc.group(1)) if hc else None, "roles": roles}


def _note_unknown(skills: list[str], context: str) -> None:
    """Remember requested skills that neither the taxonomy nor any employee has, for approval on the Standardization page."""
    tax, lookup = _known_lookup()
    known = {k.lower() for k in tax} | {v.lower() for v in lookup.values()} | set(lookup)
    unknown = [x for x in skills if x.strip() and x.strip().lower() not in known]
    if unknown:
        requested.record(unknown, context)


def extract_requirements(prompt: str) -> dict:
    prompt = check_input(prompt)
    tax, _ = standardization.index()
    inferred_legacy_domain = False
    try:
        out = tasks.extract_requirements(prompt, sorted(set(tax) | set(groups.names())))
        mode = "llm"
        merged: dict[str, Requirement] = {}
        for r in out.get("requirements", []):
            raw_skill = str(r.get("skill", "")).strip()
            task_skills = _mapped_task_skills(raw_skill)
            if CONTEXT_ONLY_TASKS.search(raw_skill):
                continue
            if LEGACY_BATCH.search(raw_skill) or _domain_key(raw_skill) == "mainframe batch ops":
                inferred_legacy_domain = True
                continue
            names = task_skills or [raw_skill]
            lvl = int(min(max(round(float(r.get("level", 2))), 1), 4))
            for name in names:
                n = {"canonical": groups.match(name)} if groups.match(name) else standardization.normalize(name)
                if n["canonical"] not in merged:
                    tags = _clean_domain_tags(r.get("domain_tags", []))
                    merged[n["canonical"]] = Requirement(
                        skill=n["canonical"], level=lvl, weight=float(r.get("weight", 1) or 1),
                        min_people=int(r.get("min_people", 1) or 1), domain_tags=tags)
        result = {"requirements": [r.model_dump() for r in merged.values()],
                  "headcount": out.get("headcount"), "roles": out.get("roles", [])}
    except AINotConfigured:
        result, mode = _heuristic_requirements(prompt), "heuristic"
    except Exception as e:  # noqa: BLE001
        log.warning("LLM requirement extraction failed, using heuristic: %s", e)
        result, mode = _heuristic_requirements(prompt), "heuristic"
    _apply_operational_rules(result, prompt, inferred_legacy_domain)
    known = set(tax) | {v for v in _known_lookup()[1].values()} | set(groups.names())
    for r in result["requirements"]:
        r["known"] = r["skill"] in known
    _note_unknown([r["skill"] for r in result["requirements"] if not r["known"]], prompt)
    return {**result, "mode": mode}


def _mapped_task_skills(text: str) -> list[str]:
    if REMEDIATION_SCRIPTS.search(text):
        return ["Python", "Shell Scripting"]
    if SYSTEM_ALERT_DIAGNOSIS.search(text):
        return ["Linux", "Shell Scripting"]
    return []


def _apply_operational_rules(result: dict, prompt: str, inferred_legacy_domain: bool = False) -> None:
    """Replace the confirmed operational task labels with their technical skills and attach domain tags."""
    requirements = result["requirements"]
    has_legacy_domain = inferred_legacy_domain or bool(LEGACY_BATCH.search(prompt))
    filtered = []
    for requirement in requirements:
        skill = str(requirement.get("skill", ""))
        if CONTEXT_ONLY_TASKS.search(skill) or LEGACY_BATCH.search(skill):
            has_legacy_domain |= bool(LEGACY_BATCH.search(skill))
            continue
        if _domain_key(skill) == "mainframe batch ops":
            has_legacy_domain = True
            continue
        filtered.append(requirement)
    requirements[:] = filtered
    for label, skills in (
        (REMEDIATION_SCRIPTS, ("Python", "Shell Scripting")),
        (SYSTEM_ALERT_DIAGNOSIS, ("Linux", "Shell Scripting")),
    ):
        if label.search(prompt):
            for skill in skills:
                if not any(r["skill"].casefold() == skill.casefold() for r in requirements):
                    requirements.append(Requirement(skill=skill).model_dump())

    if has_legacy_domain:
        for requirement in requirements:
            tags = requirement.setdefault("domain_tags", [])
            if "Mainframe/Batch Ops" not in tags:
                tags.append("Mainframe/Batch Ops")


# =========================================================================== helpers
def _rating(emp: dict, skill: str) -> int:
    """Rating for a skill. A broad skill (Cloud, Cybersecurity…) counts the best rating among its tools (services/groups.py)."""
    return _rating_via(emp, skill)[0]


def _rating_via(emp: dict, skill: str) -> tuple[int, str | None]:
    return groups.effective({k: (v["rating"] or 0) for k, v in emp["skills"].items()}, skill)


def _role_key(role: str) -> str:
    key = " ".join(re.findall(r"[a-z0-9]+", role.casefold()))
    key = re.sub(r"\bfullstack\b", "full stack", key)
    return re.sub(r"\s+(?:development|developer|engineering|engineer)$", "", key)


def _role_matches_requirement(roles: list[str], skill: str) -> bool:
    targets = {_role_key(skill)}
    if groups.match(skill) == "Frontend & Backend":
        targets.add("full stack")
    return any(
        target and re.search(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])", _role_key(role))
        for role in roles
        for target in targets
    )


def _skill_vectors(skills: list[str]) -> dict[str, np.ndarray]:
    try:
        raw = vectorstore.get_embeddings("taxonomy", skills)
    except Exception:  # noqa: BLE001
        raw = {}
    return {k: np.array(v) / (np.linalg.norm(v) or 1) for k, v in raw.items()}


def _adjacency(emp: dict, skill: str, vecs: dict) -> tuple[float, str | None]:
    """Closest skill (rated 2+, Applied or better) that the employee already has, by meaning: -> (similarity, that skill)."""
    adj, via = 0.0, None
    explicit = {name.casefold() for name in groups.related_skills_of(skill)}
    for candidate, info in emp["skills"].items():
        rating = info["rating"] or 0
        if candidate.casefold() in explicit and rating >= 2 and rating / 4 > adj:
            adj, via = rating / 4, candidate
    if skill in vecs:
        for sk, info in emp["skills"].items():
            if sk != skill and (info["rating"] or 0) >= 2 and sk in vecs:
                sim = float(vecs[skill] @ vecs[sk])
                if sim > adj:
                    adj, via = sim, sk
    return adj, via


def _upskill_score(have: int, level: int, adj: float, floor: float) -> float:
    """How close someone is to a skill: half their current level, half a related strength (if similar enough)."""
    return 0.5 * (have / level) + 0.5 * (adj if adj >= floor else 0)


LEVEL_NAMES = {0: "None", 1: "Learned", 2: "Applied", 3: "Experienced", 4: "Mastered"}


def _narratives_on() -> bool:
    """The model-written summary and forecast narrative can be switched off with AI_NARRATIVES=false (an environment variable)."""
    return os.environ.get("AI_NARRATIVES", "true").strip().lower() not in ("0", "false", "no", "off")


def _verified_text(writer, facts: dict, emps: list[dict], names: set[str], skills: set[str]) -> dict | None:
    """The model words the facts; narrative.verify decides whether the wording may be shown. -> {"text", "source": "ai"} or None."""
    if not _narratives_on():
        return None
    try:
        raw = writer(facts)
    except AINotConfigured:
        return None
    except Exception as e:  # noqa: BLE001  a missing narrative must never break the computed result
        log.warning("Narrative step failed: %s", e)
        return None
    text, why = narrative.verify(raw, facts, all_names=[e["name"] for e in emps],
                                 all_skills=sorted({sk for e in emps for sk in e["skills"]}), allowed_names=names, allowed_skills=skills)
    if not text and raw:
        log.info("Narrative rejected (%s)", why)
    return {"text": text, "source": "ai"} if text else None


# =========================================================================== gaps
def gap_report(p: ProjectInput) -> dict:
    _note_unknown([r.skill for r in p.requirements], p.prompt)
    emps = repo.all_profiles()
    s = get_settings()
    needed = [r.skill for r in p.requirements]
    all_skills = list({sk for e in emps for sk in e["skills"]} | set(needed))
    vecs = _skill_vectors(all_skills)

    items = []
    for r in p.requirements:
        holders, near, upskill = [], [], []
        for e in emps:
            if not _has_domain_tags(e, r.domain_tags):
                continue
            have = _rating(e, r.skill)
            brief = {"id": e["id"], "name": e["name"], "rating": have}
            if have >= r.level:
                holders.append(brief)
                continue
            if have and have == r.level - 1:
                near.append(brief)
            adj, via = _adjacency(e, r.skill, vecs)
            if have or adj >= s.adjacency_min_similarity:
                score = _upskill_score(have, r.level, adj, s.adjacency_min_similarity)
                upskill.append({**brief, "target": r.level, "adjacent_skill": via if adj >= s.adjacency_min_similarity else None,
                                "adjacency": round(adj, 2), "score": round(score, 2)})
        upskill.sort(key=lambda x: -x["score"])
        upskill = upskill[:5]
        eligible = [e for e in emps if _has_domain_tags(e, r.domain_tags)]
        rated = [e for e in eligible if _rating(e, r.skill)]
        best = max((_rating(e, r.skill) for e in eligible), default=0)
        coverage = min(len(holders) / r.min_people, 1.0)
        status = "covered" if len(holders) >= r.min_people else ("at_risk" if len(holders) + len(near) >= r.min_people else "gap")
        items.append({
            "skill": r.skill, "level": r.level, "min_people": r.min_people, "weight": r.weight,
            "domain_tags": r.domain_tags,
            "holders": holders, "near": near, "upskill": upskill, "best_rating": best,
            "avg_rating": round(sum(_rating(e, r.skill) for e in rated) / len(rated), 2) if rated else None,
            "coverage": round(coverage, 2), "status": status,
            "shortfall_people": max(r.min_people - len(holders), 0),
            "risk": predict.predict_risk(level=r.level, best_rating=best, coverage_ratio=coverage,
                                         near_count=len(near), upskill_count=len(upskill)),
        })
    total_w = sum(i["weight"] for i in items) or 1
    readiness = sum(i["coverage"] * i["weight"] for i in items) / total_w
    items.sort(key=lambda i: -i["risk"])
    gap_facts = {
        "readiness_percent": round(readiness * 100),
        "employees_considered": len(emps),
        "skills": [{"skill": i["skill"], "level_needed": i["level"], "level_name": LEVEL_NAMES.get(i["level"], ""), "people_needed": i["min_people"],
                    "people_meeting_level": len(i["holders"]), "people_one_level_short": len(i["near"]), "best_level_in_company": i["best_rating"],
                    "status": i["status"], "risk_percent": round(i["risk"] * 100),
                    "could_be_upskilled": [{"name": u["name"], "current_level": u["rating"]} for u in i["upskill"][:2]]} for i in items[:6]],
    }
    gap_names = {u["name"] for i in items[:6] for u in i["upskill"][:2]}
    narrative_out = _verified_text(tasks.write_gap_narrative, gap_facts, emps, gap_names, {i["skill"] for i in items[:6]}) if items else None
    return {
        "narrative": narrative_out,
        "items": items,
        "summary": {"readiness": round(readiness, 2), "covered": sum(i["status"] == "covered" for i in items),
                    "at_risk": sum(i["status"] == "at_risk" for i in items), "gap": sum(i["status"] == "gap" for i in items),
                    "employees_considered": len(emps)},
    }


def _employee_relevance(query_text: str, employee_count: int) -> tuple[dict[int, float], bool]:
    """Retrieve employee parents through their entity children, then cross-encoder-rerank likely matches."""
    if employee_count <= 0:
        return {}, False
    parent_hits = vectorstore.query_with_documents(
        "employees", query_text, employee_count, where={"entity_type": "employee_parent"}
    )
    if not parent_hits:
        raise RuntimeError("The employee vector index has no parent entities; re-index employee vectors.")

    scores = {
        int(hit["metadata"]["employee_id"]): max(0.0, min(1.0, hit["similarity"]))
        for hit in parent_hits
        if hit["metadata"].get("employee_id") is not None
    }
    parents = {int(hit["metadata"]["employee_id"]): hit for hit in parent_hits}
    child_count = min(vectorstore.count("employees"), max(100, employee_count * 8))
    child_hits = vectorstore.query_with_documents(
        "employees",
        query_text,
        child_count,
        where={"entity_type": {"$in": [
            "employee_skill", "employee_role", "employee_domain", "employee_background",
        ]}},
    )
    for hit in child_hits:
        employee_id = hit["metadata"].get("employee_id")
        if employee_id is not None:
            eid = int(employee_id)
            similarity = max(0.0, min(1.0, hit["similarity"]))
            scores[eid] = max(scores.get(eid, 0.0), similarity)

    candidates = sorted(scores, key=scores.get, reverse=True)[:100]
    passages = [parents[eid] for eid in candidates if eid in parents]
    try:
        reranked = reranker.rerank(query_text, passages)
    except Exception as e:  # noqa: BLE001  preserve vector ordering if the local model cannot initialize
        log.warning("FlashRank unavailable; retaining vector similarity ordering: %s", e)
        return scores, False
    rerank_scores = {int(hit["metadata"]["employee_id"]): hit["rerank_score"] for hit in reranked}
    for eid, score in rerank_scores.items():
        scores[eid] = (scores[eid] + score) / 2
    return scores, True


# =========================================================================== staffing
def staffing(p: ProjectInput) -> dict:
    _note_unknown([r.skill for r in p.requirements], p.prompt)
    s = get_settings()
    emps = repo.all_profiles()
    if not emps:
        return {"candidates": [], "team": [], "uncovered": [r.skill for r in p.requirements], "semantic_mode": "none"}

    query_text = p.prompt.strip() or "; ".join(f"{r.skill} level {r.level}" for r in p.requirements)
    sim: dict[int, float] = {}
    try:
        sim, reranked = _employee_relevance(query_text, len(emps))
        semantic_mode = "vector_reranked" if reranked else "vector"
    except Exception as e:  # noqa: BLE001
        log.warning("Employee vector retrieval/reranking unavailable, skill-only ranking: %s", e)
        semantic_mode = "unavailable"

    wsum = sum(r.weight for r in p.requirements) or 1
    want_roles = {x.lower() for x in p.roles}
    cands = []
    for e in emps:
        matched, gaps, role_skills = [], [], []
        got = 0.0
        for r in p.requirements:
            have, via = _rating_via(e, r.skill)
            if not _has_domain_tags(e, r.domain_tags):
                have, via = 0, None
            got += r.weight * min(have / r.level, 1.0)
            if have < r.level and _has_domain_tags(e, r.domain_tags) and _role_matches_requirement(e["roles"], r.skill):
                role_skills.append(r.skill)
            (matched if have >= r.level else gaps).append({
                "skill": r.skill, "have": have, "need": r.level,
                **({"domain_tags": r.domain_tags} if r.domain_tags else {}),
                **({"via": via} if via else {}),
            })
        skill_score = got / wsum if p.requirements else 0.0
        role_hit = 1.0 if want_roles & {x.lower() for x in e["roles"]} else 0.0
        sem = sim.get(e["id"], 0.0)
        wk, ws, wr = s.staffing_weight_skill, s.staffing_weight_semantic, s.staffing_weight_role
        if semantic_mode == "unavailable":
            wk, ws = wk + ws, 0.0
        total = (wk * skill_score + ws * sem + wr * role_hit) / (wk + ws + wr)
        parts = [f"Meets {len(matched)}/{len(p.requirements)} required skills"] if p.requirements else []
        via_notes = [f"{m['skill']} through {m['via']} ({m['have']})" for m in matched if m.get("via")]
        if via_notes:
            parts.append("counted via tools: " + ", ".join(via_notes[:3]))
        if role_skills:
            parts.append("role matches " + ", ".join(role_skills))
        if gaps:
            more = len(gaps) - 3  # long lists are cut for readability, and the cut is stated
            parts.append("below target on " + ", ".join(f"{g['skill']} ({g['have']}/{g['need']})" for g in gaps[:3])
                         + (f" and {more} more" if more > 0 else ""))
        if role_hit:
            parts.append("role matches")
        if semantic_mode != "unavailable":
            parts.append(f"profile relevance {sem:.0%}")
        sen = seniority.assess([v["rating"] for v in e["skills"].values()])  # by code, from ALL of the person's ratings
        cands.append({"id": e["id"], "name": e["name"], "employee_no": e["employee_no"], "roles": e["roles"],
                      "seniority": {"level": sen["level"], "reason": sen["reason"]},
                      "score": round(total, 3), "skill_score": round(skill_score, 3), "semantic": round(sem, 3),
                      "matched": matched, "gaps": gaps, "role_skills": role_skills,
                      "role_match": bool(role_hit), "rationale": "; ".join(parts) + "."})
    cands.sort(key=lambda c: -c["score"])
    by_cid = {c["id"]: c for c in cands}
    reqs = p.requirements
    size = p.headcount or min(len(reqs) or 1, 5)

    # ---- team: built skill by skill, heaviest skill first. For each skill the highest-rated people who meet the level are chosen
    # (min_people of them). A role never covers a skill; it is only a tie-break between equal ratings, preferring a role that is not
    # on the team yet. When the team is full, the skill goes to the highest-rated member who already meets it.
    need = {r.skill: r.min_people for r in reqs}
    team: list[int] = []
    adds_by: dict[int, list[str]] = {}
    team_roles: set[str] = set()

    def rating_of(c: dict, skill: str) -> int:
        return next((m["have"] for m in c["matched"] if m["skill"] == skill), 0)

    for r in sorted(reqs, key=lambda r: -r.weight):
        holders = [c for c in cands if rating_of(c, r.skill)]
        holders.sort(key=lambda c: (-rating_of(c, r.skill), len({_role_key(x) for x in c["roles"]} & team_roles), -c["score"]))
        chosen = []
        for c in holders:
            if len(chosen) >= need[r.skill]:
                break
            if c["id"] in team or len(team) < size:  # a new person only while there is room
                chosen.append(c)
        for c in chosen:
            if c["id"] not in team:
                team.append(c["id"])
                team_roles |= {_role_key(x) for x in c["roles"]}
            adds_by.setdefault(c["id"], []).append(r.skill)
    qualified_count = {r.skill: sum(1 for i in team if rating_of(by_cid[i], r.skill)) for r in reqs}
    pool = [c for c in cands if c["id"] not in team]

    extras: list[int] = []
    if not any(qualified_count[r.skill] < r.min_people for r in reqs):
        # Add extra capacity only after the team itself meets the requested skill levels.
        for c in pool:
            if len(team) >= size:
                break
            team.append(c["id"])
            extras.append(c["id"])

    team_details = []
    for cid in team:
        c = by_cid[cid]
        team_details.append({"id": c["id"], "name": c["name"], "employee_no": c["employee_no"], "roles": c["roles"], "score": c["score"],
                             "seniority": c["seniority"],
                             "semantic": c["semantic"], "role": "extra" if cid in extras else "contributor",
                             "meets": [m["skill"] for m in c["matched"]], "adds": adds_by.get(cid, []),
                             "role_matches": [skill for skill in c["role_skills"] if skill in adds_by.get(cid, [])],
                             "below_target": [g["skill"] for g in c["gaps"]], "rationale": c["rationale"]})

    # ---- what is missing: skills nobody (enough people) has, versus skills someone has but the team is too small for
    holders = {r.skill: sum(1 for e in emps if _has_domain_tags(e, r.domain_tags) and _rating(e, r.skill) >= r.level)
               for r in reqs}
    unmet = [r for r in reqs if qualified_count[r.skill] < r.min_people]
    nobody_reqs = [r for r in unmet if holders[r.skill] < r.min_people]
    team_limited = [r.skill for r in unmet if holders[r.skill] >= r.min_people]
    learning = _learning_suggestions(p, emps, by_cid, set(team), [(r, r.min_people - holders[r.skill]) for r in nobody_reqs]) if nobody_reqs else []

    positions = _positions(p, cands, set(team), (wk, ws, wr), emps)

    shown = cands[: p.top_n]
    shown += [by_cid[i] for i in team if by_cid[i] not in shown]  # team members are always listed, even if ranked below top_n
    result = {"candidates": shown, "team": team, "team_details": team_details, "extras": extras, "team_size": size,
              "uncovered": [r.skill for r in unmet], "nobody": [r.skill for r in nobody_reqs], "team_limited": team_limited,
              "learning": learning, "semantic_mode": semantic_mode,
              "positions": positions, "positions_hint": None if positions else "Add preferred roles to see the best candidates for each position.",
              "not_chosen": _not_chosen(cands, set(team), size)}
    team_names = {d["name"] for d in team_details}
    summary_facts = {
        "requirements": [{"skill": r.skill, "level_needed": r.level, "level_name": LEVEL_NAMES.get(r.level, ""), "people_needed": r.min_people} for r in reqs],
        "team": [{"name": d["name"], "role_in_team": d["role"], "covers_required_skills": d["adds"],
                  "ratings": [{"skill": m["skill"], "level": m["have"], "needed": m["need"]} for m in by_cid[d["id"]]["matched"]]} for d in team_details],
        "required_skills_the_team_does_not_cover": result["uncovered"], "skills_nobody_in_company_has_at_the_level": result["nobody"],
        "runner_ups_not_chosen": [{"name": x["name"], "reason": x["reason"]} for x in result["not_chosen"][:2]],
    }
    result["summary"] = _verified_text(tasks.write_staffing_summary, summary_facts, emps,
                                       team_names | {x["name"] for x in result["not_chosen"][:2]}, {r.skill for r in reqs}) if team_details else None
    _log_staffing(p, emps, cands, result)
    return result


POSITION_CANDIDATES = 5  # best candidates shown per position


def _positions(p: ProjectInput, cands: list[dict], team_ids: set[int], weights: tuple[float, float, float], emps: list[dict]) -> list[dict]:
    """One position per preferred role. Each position lists its best candidates (at most POSITION_CANDIDATES), and a person appears under ONE
    position only: pairs are taken best score first, so someone goes where they fit best. The score is the staffing score with the role bonus
    given only for THAT position's role. A candidate who is below a required level is marked `short`, with the gap skills the learning
    path would target. Everything here is computed from the ratings; nothing is estimated by the AI."""
    wk, ws, wr = weights
    roles: list[str] = []
    for r in p.roles:
        if r.strip() and _role_key(r) not in {_role_key(x) for x in roles}:
            roles.append(r.strip())
    if not roles:
        return []
    wanted = {_role_key(k): v for k, v in (p.role_seniority or {}).items()}  # seniority asked for a role (optional)
    pairs = []
    for pi, role in enumerate(roles):
        rk = _role_key(role)
        want = wanted.get(rk)
        for c in cands:
            hit = 1.0 if rk in {_role_key(x) for x in c["roles"]} else 0.0
            if not (c["skill_score"] > 0 or hit):
                continue  # no skill in common with the project and not in the role: not a candidate
            total = (wk * c["skill_score"] + ws * c["semantic"] + wr * hit) / ((wk + ws + wr) or 1)
            # people of the wanted seniority come first, then the rest by score. Only for people who hold the role: a QA Senior is not
            # pulled out of QA just because the Developer position asked for a Senior.
            tier = 1 if want and hit and c["seniority"]["level"] == want else 0
            pairs.append((-tier, -total, c["name"], pi, c, bool(hit), total))
    pairs.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    placed: set[int] = set()
    lists: dict[int, list[dict]] = {i: [] for i in range(len(roles))}
    for _, _, _, pi, c, hit, total in pairs:
        if c["id"] in placed or len(lists[pi]) >= POSITION_CANDIDATES:
            continue
        placed.add(c["id"])
        gaps = [{k: v for k, v in g.items() if k in ("skill", "have", "need", "domain_tags")} for g in c["gaps"]]
        want = wanted.get(_role_key(roles[pi]))
        lists[pi].append({"id": c["id"], "name": c["name"], "employee_no": c["employee_no"], "roles": c["roles"], "score": round(total, 3),
                          "seniority": c["seniority"], "seniority_match": (c["seniority"]["level"] == want) if want else None,
                          "role_match": hit, "in_team": c["id"] in team_ids, "meets": len(c["matched"]), "total": len(c["matched"]) + len(c["gaps"]),
                          "gaps": gaps, "short": bool(gaps),
                          "learning_requirements": [{"skill": g["skill"], "level": g["need"], **({"domain_tags": g["domain_tags"]} if g.get("domain_tags") else {})}
                                                    for g in gaps]})
    out = []
    for pi, role in enumerate(roles):
        holders = sum(1 for e in emps if _role_key(role) in {_role_key(x) for x in e["roles"]})
        cl = lists[pi]
        want = wanted.get(_role_key(role))
        n_match = sum(1 for x in cl if x["seniority_match"])
        notes = []
        if not holders:
            notes.append("Nobody holds this role yet, so candidates are ranked by skills only.")
        if want and n_match == 0:
            notes.append(f"Nobody at {want} level fits this position, so the best fits at other levels are shown.")
        elif want and n_match < len(cl):
            notes.append(f"{n_match} at {want} level; the rest are the next best fits.")
        out.append({"role": role, "holders": holders, "candidates": cl, "ready_now": sum(1 for x in cl if not x["short"]),
                    "wanted_seniority": want, "note": " ".join(notes) or None})
    return out


def _rated(x: dict) -> str:
    return f"{x['skill']} ({'rated ' + str(x['have']) if x['have'] else 'not rated'}, needs {x['need']})"


def _not_chosen(cands: list[dict], team_ids: set[int], size: int, limit: int = 3) -> list[dict]:
    """Why the best of the people left out were not chosen. Only people with something to show (they meet or partly have a required
    skill) are listed, best first, at most `limit`. The reason uses the same ratings as the candidate table."""
    out = []
    for c in cands:  # cands are sorted best first
        if c["id"] in team_ids:
            continue
        if not (c["matched"] or any(g["have"] > 0 for g in c["gaps"])):
            continue
        met = ", ".join(_rated(m) for m in c["matched"])
        short = ", ".join(_rated(g) for g in c["gaps"])
        if c["gaps"]:
            reason = (f"meets {met} but " if met else "") + f"is below target on {short}."
        else:
            reason = f"meets every requirement, but the team size of {size} is already filled."
        out.append({"id": c["id"], "name": c["name"], "employee_no": c["employee_no"], "roles": c["roles"],
                    "meets": [m["skill"] for m in c["matched"]], "below_target": [g["skill"] for g in c["gaps"]], "reason": reason})
        if len(out) == limit:
            break
    return out


def _learning_suggestions(p: ProjectInput, emps: list[dict], by_cid: dict, exclude: set[int],
                          missing: list[tuple[Requirement, int]]) -> list[dict]:
    """For every skill the team cannot cover at the level: the closest person to it, chosen exactly as the Gaps tab ranks upskilling
    candidates (current level and related strengths). A team member who is short on a skill is a valid learner: he stays on the team
    for what he covers and gets a study plan for the rest. Skills given to the same person share one plan."""
    from . import learning  # lazy: learning imports this module too

    s = get_settings()
    floor = s.adjacency_min_similarity
    vecs = _skill_vectors(list({sk for e in emps for sk in e["skills"]} | {r.skill for r, _ in missing}))
    people: dict[int, dict] = {}
    for r, k in missing:
        ranked = []
        for e in emps:
            if not _has_domain_tags(e, r.domain_tags) or _rating(e, r.skill) >= r.level:
                continue
            have = _rating(e, r.skill)
            adj, via = _adjacency(e, r.skill, vecs)
            ranked.append((False, _upskill_score(have, r.level, adj, floor), by_cid[e["id"]]["score"], e, have, adj, via))
        ranked.sort(key=lambda t: (-t[1], -t[2], t[3]["name"]))
        picks = [(t, "closest current level and related skills") for t in ranked if t[1] > 0][:k]
        if len(picks) < k:  # nobody has a starting point for the rest
            picked_ids = {t[3]["id"] for t, _ in picks}
            picks += [(t, "overall staffing score (no one has a starting point)")
                      for t in ranked if t[1] <= 0 and t[3]["id"] not in picked_ids][: k - len(picks)]
        for (role_match, score, _, e, have, adj, via), basis in picks:
            if role_match:
                basis = "matching role; " + basis
            entry = people.setdefault(e["id"], {"employee": {"id": e["id"], "name": e["name"], "employee_no": e["employee_no"], "roles": e["roles"]},
                                                "skills": [], "plan": None, "error": None})
            entry["skills"].append({"skill": r.skill, "target": r.level, "current": have, "score": round(score, 2), "basis": basis,
                                    "domain_tags": r.domain_tags,
                                    "related_skill": via if adj >= floor else None,
                                    "related_rating": _rating(e, via) if via and adj >= floor else None})
    title = learning.short_title(p.prompt)
    out = []
    for pid, entry in people.items():
        try:
            entry["plan"] = learning.build(LearningRequest(
                employee_id=pid, requirements=[Requirement(skill=x["skill"], level=x["target"], domain_tags=x.get("domain_tags", []))
                                               for x in entry["skills"]],
                project=title, weeks=p.learning_weeks, hours_per_week=p.learning_hours_per_week), require_approval=True)
        except Exception as e:  # noqa: BLE001  a failed plan must not hide the suggestion itself
            log.warning("Learning plan for employee %s failed: %s", pid, e)
            entry["error"] = f"The study plan could not be created ({type(e).__name__}); open Learning paths to try again."
        out.append(entry)
    return out


def _best_partial(p: ProjectInput, emps: list[dict]) -> list[tuple[int, float, dict]]:
    """(requirements met, weighted fit, employee), best first, computed from the ratings alone (no similarity)."""
    wsum = sum(r.weight for r in p.requirements) or 1
    rows = []
    for e in emps:
        met = sum(_has_domain_tags(e, r.domain_tags) and _rating(e, r.skill) >= r.level for r in p.requirements)
        fit = sum(r.weight * min((_rating(e, r.skill) if _has_domain_tags(e, r.domain_tags) else 0) / r.level, 1.0)
                  for r in p.requirements) / wsum
        rows.append((met, fit, e))
    return sorted(rows, key=lambda t: (-t[0], -t[1], t[2]["name"]))


def _log_staffing(p: ProjectInput, emps: list[dict], cands: list[dict], result: dict) -> None:
    """Record the answer for RAGAS: question, answer, the evidence behind every claim in it, and a reference derived from
    the ratings. Never raises."""
    try:
        by_id = {e["id"]: e for e in emps}
        reqs = p.requirements
        needs = ", ".join(f"{r.skill} (level {r.level}+)" for r in reqs)
        prompt_text = p.prompt.strip()
        if len(prompt_text) > 200:  # a long project description would dominate the question RAGAS judges the answer against
            from . import learning

            title = learning.short_title(prompt_text, 90)
            if title.endswith("…"):  # cut at a word boundary, not in the middle of a word
                title = title[:-1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
            question = f'Who should staff the project "{title}"?'
        else:
            question = prompt_text or f"Who should staff a project that needs {needs}?"
        if prompt_text and needs:
            question += f" Required skills: {needs}."
        details = result["team_details"]
        by_cid = {c["id"]: c for c in cands}
        n = len(emps)
        nobody = [r.skill for r in reqs if not any(
            _has_domain_tags(e, r.domain_tags) and _rating(e, r.skill) >= r.level for e in emps)]

        # ---- answer: describes every team member, then what the team and the company lack
        # the first sentence answers the question directly: what the project needs, and who is suggested
        if details:
            lines = [f"For a project that needs {needs or 'no specific skills'}, the suggested team of {len(details)} is "
                     f"{', '.join(d['name'] for d in details)}."]
        else:
            lines = [f"For a project that needs {needs or 'no specific skills'}, no team can be suggested because nobody in the company "
                     "has any of the required skills at the requested level."]
        for d in details:
            if d["role"] == "extra":
                adds = "Extra capacity: every required skill is already covered, so this person adds no new required skill."
            else:
                adds = f"Adds {', '.join(d['adds'])} to the team."
            lines.append(f"{d['name']}: {d['rationale']} {adds}")
            candidate = by_cid.get(d["id"])
            if candidate and d["role"] != "extra":
                evidence = ", ".join(
                    f"{m['skill']} rated {m['have']}/4 (needs {m['need']})" for m in candidate["matched"]
                )
                if evidence:
                    lines.append(f"Verified ratings for {d['name']}: {evidence}.")
        for x in result.get("not_chosen", []):
            lines.append(f"Not chosen: {x['name']} {x['reason']}")
        if result["uncovered"]:
            lines.append(f"Not covered by the suggested team: {', '.join(result['uncovered'])}.")
        if result["team_limited"]:
            lines.append(f"Someone in the company has {', '.join(result['team_limited'])}, but the team size is too small to include them.")
        if nobody:
            lines.append(f"No employee in the company reaches the requested level for: {', '.join(nobody)}.")
        for L in result["learning"]:
            sk = "; ".join(f"{x['skill']} (now {x['current']}, target {x['target']})" for x in L["skills"])
            employee_id = L["employee"]["id"]
            in_team = employee_id in result["team"]
            role_match = any(x["basis"].startswith("matching role") for x in L["skills"])
            if in_team:
                reason = "a suggested team member with a matching role" if role_match else "a suggested team member"
            else:
                reason = "a candidate with a matching role" if role_match else "a candidate outside the team"
            lines.append(f"Learning suggestion: {L['employee']['name']}, {reason}, should develop {sk}.")
        answer = " ".join(lines)

        # ---- contexts: the profiles AND the numbers the answer quotes, so every claim can be checked
        contexts = [eval_log.profile_text(by_id[d["id"]]) for d in details if d["id"] in by_id][:10]
        contexts.append("Requirements: " + "; ".join(f"{r.skill} level {r.level}+ (weight {r.weight:g}, at least {r.min_people} person)" for r in reqs))
        cover = []
        for r in reqs:
            eligible = [e for e in emps if _has_domain_tags(e, r.domain_tags)]
            ok = [_rating(e, r.skill) for e in eligible if _rating(e, r.skill) >= r.level]
            best = max((_rating(e, r.skill) for e in eligible), default=0)
            domain = f" with {', '.join(r.domain_tags)}" if r.domain_tags else ""
            cover.append(f"{r.skill}{domain}: {len(ok)} of {n} employees reach level {r.level}+ (best rating in the eligible group: {best or 'none'})")
        broad = [r.skill for r in reqs if groups.is_group(r.skill)]
        if broad:  # explains why someone with AWS counts for Cloud, so the answer's claims can be checked
            contexts.append("Broad skills count the best rating among their tools: " + "; ".join(
                f"{b} = best of {', '.join(groups.tools_of(b)[:12])}" for b in broad) + ".")
        contexts.append(f"Company-wide coverage across {n} employees. " + "; ".join(cover) + ".")
        for L in result["learning"]:  # evidence for each learning suggestion
            e = by_id.get(L["employee"]["id"])
            if e:
                contexts.append(eval_log.profile_text(e))
            team_status = "in the team" if L["employee"]["id"] in result["team"] else "not in the team"
            contexts.append(f"Learning candidate {L['employee']['name']} ({team_status}): " + "; ".join(
                f"{x['skill']}: current rating {x['current'] or 'none'}, target {x['target']}"
                + (f", related strength {x['related_skill']} rated {x['related_rating']}" if x["related_skill"] else "")
                + f", chosen by {x['basis']}" for x in L["skills"]) + ".")
        contexts.append("Computed scores (overall = 0.6 skill fit + 0.3 profile relevance + 0.1 role match): " + "; ".join(
            f"{d['name']}: overall {d['score']:.2f}, profile relevance {d['semantic']:.0%}, "
            f"role match {'yes' if by_cid[d['id']]['role_match'] or by_cid[d['id']]['role_skills'] else 'no'}"
            for d in details) + ".")
        for x in result.get("not_chosen", []):  # evidence for the "Not chosen" sentences: the person's profile and the comparison
            if x["id"] in by_id:
                contexts.append(eval_log.profile_text(by_id[x["id"]]))
            c = by_cid[x["id"]]
            contexts.append(f"Candidate not chosen: {x['name']}. " + "; ".join(
                f"{r['skill']}: rating {r['have'] or 'none'}, needs {r['need']}"
                + (f", required domain tags: {', '.join(r['domain_tags'])}" if r.get("domain_tags") else "")
                for r in c["matched"] + c["gaps"]) + f". Result: {x['reason']}")

        # ---- reference: derived from ratings only (not from the app's ranking). The same facts are also put into the contexts,
        # so the reference never names people or numbers that the retrieved contexts do not contain.
        ranked = _best_partial(p, emps)
        qualified = [e["name"] for m, _, e in ranked if m == len(reqs)] if reqs else []
        key_ctx = ""  # the context that supports the reference: it goes FIRST, because RAGAS context_precision rewards a useful context in front
        if qualified:
            in_team = [d["name"] for d in details if d["name"] in set(qualified)]
            rest = [q for q in qualified if q not in set(in_team)]
            listed = (in_team + rest)[:12]  # suggested team members first, so the reference's names are always in the list
            examples = in_team[:3] or rest[:3]
            reference = (f"{len(qualified)} employee(s) meet every requirement at the requested levels, for example {', '.join(examples)}."
                         + (" The suggested team members are among them." if in_team else ""))
            key_ctx = (f"Employees who meet every requirement ({len(qualified)} in total): " + ", ".join(listed)
                       + (", and others." if len(qualified) > len(listed) else ".")
                       + (f" Suggested team members who meet every requirement: {', '.join(in_team)}." if in_team else ""))
        else:
            seen_profiles, best = set(), []
            for m, _, e in ranked:  # distinct skill profiles only, so five copies of one profile count once
                key = (tuple(sorted((k, v["rating"]) for k, v in e["skills"].items())),
                       tuple(sorted(_domain_key(tag) for tag in e.get("domain_tags", []))))
                if key not in seen_profiles:
                    seen_profiles.add(key)
                    got = [r.skill for r in reqs if _has_domain_tags(e, r.domain_tags) and _rating(e, r.skill) >= r.level]
                    best.append(f"{e['name']} (meets {m} of {len(reqs)}: {', '.join(got) or 'none'})")
                if len(best) == 3:
                    break
            partial = "Best partial fits: " + "; ".join(best) + "."
            team_fit = "; ".join(f"{d['name']} (meets {len(d['meets'])} of {len(reqs)}: {', '.join(d['meets']) or 'none'})" for d in details)
            nobody_txt = f" Nobody in the company reaches the requested level for: {', '.join(nobody)}." if nobody else ""
            # the reference describes the SAME people the answer names, so a different person with an equal fit is not marked wrong
            reference = ("No employee meets every requirement at the requested levels. "
                         + (f"The suggested team: {team_fit}." if team_fit else partial) + nobody_txt)
            key_ctx = ("No employee meets every requirement at the requested levels. "
                       + (f"The suggested team: {team_fit}. " if team_fit else "") + partial)
        contexts.insert(0, key_ctx)
        if result["extras"] or result["uncovered"] or result["learning"]:  # backs the sentences about extras / missing skills / learning
            contexts.append("How the team is chosen: a person is added only for required skills the team still lacks. Nobody is added just "
                            "to fill a seat while a required skill is still missing. When every required skill is covered, the remaining seats "
                            "up to the team size are filled with the best-scoring people and marked extra capacity. For a skill nobody has, "
                            "the best-fit employee outside the team is suggested for a study plan.")
        meta = {"semantic_mode": result["semantic_mode"], "requirements": [r.model_dump() for r in reqs],
                "team": [d["name"] for d in details], "team_size": result["team_size"], "n_employees": n,
                "n_qualified": len(qualified), "uncovered": result["uncovered"], "nobody": nobody,
                "team_details": [{"id": d["id"], "name": d["name"], "role": d["role"], "meets": d["meets"], "adds": d["adds"],
                                  "below_target": d["below_target"]} for d in details],
                "team_limited": result["team_limited"],
                "not_chosen": [{"id": x["id"], "name": x["name"], "meets": x["meets"], "below_target": x["below_target"]} for x in result.get("not_chosen", [])],
                "learning": [{"employee_id": L["employee"]["id"], "name": L["employee"]["name"],
                              "skills": [{"skill": x["skill"], "target": x["target"], "current": x["current"]} for x in L["skills"]]}
                             for L in result["learning"]],
                "data_fingerprint": eval_log.data_fingerprint(), "prompt": prompt_text[:3000]}
        eval_log.log_interaction("staffing", question, answer, contexts, reference, meta)
    except Exception:  # noqa: BLE001
        log.exception("Could not log the staffing answer")