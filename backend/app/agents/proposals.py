"""The approval queue. Agents may only PROPOSE; a person with the right role applies a proposal.

Role rules (admin may decide anything):
    skill_mapping   HR Manager             a spelling means a canonical skill (or a new skill is added to the taxonomy)
    upload_fix      HR Manager             new parsing settings for an uploaded file
    project_brief   Project Manager / Team Leader   a structured project demand to plan staffing from
    project_assignment  HR Manager         put a person on an approved project, change their allocation or dates, or take them off (asked by a project manager)
    project_dates   HR Manager             new start and end dates for an approved project (asked by a project manager)
    employee_profile HR Manager            a person read from a CV (levels are estimates) to add to the matrix
    employee_roles  HR Manager, or the team leader of that employee's member account   AI-matched role changes for one employee
    member_update / member_cv / member_learning_plan
                    the member's own Team Leader (or admin)   a change a MEMBER made to their own record; never decided by the member,
                    never by another team leader. No team leader set, or the leader is disabled: the admin decides.

The only thing applied without a person is an exact match or an earlier human decision (confidence 100%); those are
recorded here with status 'auto_applied' so they stay visible and auditable.
"""
import json
import re

from .. import db
from ..services import assignments, auth, repo, requested, standardization

REQUIRED_ROLES = {
    "skill_mapping": {"hr_manager"},
    "upload_fix": {"hr_manager"},
    "project_brief": {"project_manager", "team_leader"},
    "employee_profile": {"hr_manager"},
    "employee_roles": {"hr_manager"},
}
KINDS = tuple(REQUIRED_ROLES)  # what the agents may propose
MEMBER_KINDS = ("member_update", "member_cv", "member_learning_plan")  # only member requests create these (see routers/member.py)
for _k in MEMBER_KINDS:
    REQUIRED_ROLES[_k] = set()  # decided by the member's team leader, not by a role (see can_decide)
REQUIRED_ROLES["project_assignment"] = {"hr_manager"}  # a project manager asks (routers/assignments.py); HR approves
REQUIRED_ROLES["project_dates"] = {"hr_manager"}  # new start/end dates for an approved project


def _leader_id(requested_by) -> int | None:
    lead = auth.team_leader_of(requested_by)
    return lead["id"] if lead else None


def _subject_leader_id(employee_id) -> int | None:
    """The team leader of the member account that is linked to this employee (None: that employee has no account, or no leader)."""
    if not employee_id:
        return None
    u = db.one("SELECT id FROM users WHERE employee_id=? AND role='member' AND active=1", (employee_id,))
    return _leader_id(u["id"]) if u else None


def can_decide(user: dict, kind: str, requested_by: int | None = None, subject_employee_id: int | None = None) -> bool:
    if user["role"] == "admin":
        return True
    if kind in MEMBER_KINDS:
        return user["role"] == "team_leader" and requested_by is not None and _leader_id(requested_by) == user["id"]
    if kind == "employee_roles":
        return user["role"] == "hr_manager" or (user["role"] == "team_leader" and _subject_leader_id(subject_employee_id) == user["id"])
    return user["role"] in REQUIRED_ROLES.get(kind, set())


def _can(user: dict, r: dict) -> bool:
    return can_decide(user, r["kind"], r.get("requested_by"), r.get("subject_employee_id"))


def _clean(s, n=80) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _validate(kind: str, payload: dict) -> dict:
    if kind == "skill_mapping":
        raw, canon = _clean(payload.get("raw_skill")), _clean(payload.get("canonical"))
        if not raw or not canon:
            raise ValueError("raw_skill and canonical are required")
        return {"raw_skill": raw, "canonical": canon, "category": _clean(payload.get("category"), 40) or None,
                "is_new_skill": bool(payload.get("is_new_skill"))}
    if kind == "upload_fix":
        try:
            uid = int(payload.get("upload_id"))
        except (TypeError, ValueError):
            raise ValueError("upload_id is required") from None
        if not db.one("SELECT 1 AS x FROM uploads WHERE id=?", (uid,)):
            raise ValueError(f"Upload {uid} does not exist")
        out = {"upload_id": uid}
        if payload.get("sheet"):
            out["sheet"] = _clean(payload["sheet"], 80)
        if payload.get("layout") in ("wide", "long"):
            out["layout"] = payload["layout"]
        if isinstance(payload.get("mapping"), dict):
            out["mapping"] = {_clean(k, 40): _clean(v, 120) for k, v in payload["mapping"].items()}
        if len(out) == 1:
            raise ValueError("upload_fix needs a sheet, layout or mapping to change")
        return out
    if kind == "project_brief":
        reqs = []
        for r in payload.get("requirements") or []:
            skill = _clean(r.get("skill"))
            if skill:
                raw_tags = r.get("domain_tags")
                domain_tags = raw_tags if isinstance(raw_tags, list) else []
                reqs.append({"skill": skill, "level": int(min(max(int(r.get("level", 2)), 1), 4)),
                             "weight": float(r.get("weight", 1) or 1), "min_people": max(int(r.get("min_people", 1) or 1), 1),
                             "domain_tags": [_clean(tag, 80) for tag in domain_tags if _clean(tag, 80)][:10]})
        if not reqs:
            raise ValueError("A project brief needs at least one requirement")
        hc = payload.get("headcount")
        start, end = assignments.parse_period(payload.get("start_date"), payload.get("end_date"), "project ")
        return {"title": _clean(payload.get("title"), 120) or "Untitled project", "prompt": _clean(payload.get("prompt"), 2000),
                "start_date": start, "end_date": end,
                "requirements": reqs, "headcount": int(hc) if hc else None,
                "roles": [_clean(x, 60) for x in (payload.get("roles") or [])][:10], "notes": _clean(payload.get("notes"), 1000)}
    if kind == "employee_profile":
        name = _clean(payload.get("name"), 120)
        if not name:
            raise ValueError("A CV record needs a name")
        skills, seen = [], set()
        for s in payload.get("skills") or []:
            raw = _clean(s.get("raw_skill"))
            try:
                level = int(s.get("rating"))
            except (TypeError, ValueError):
                continue
            if raw and 0 <= level <= 4 and raw.lower() not in seen:
                seen.add(raw.lower())
                skills.append({"raw_skill": raw, "rating": level, "evidence": _clean(s.get("evidence"), 400), "needs_review": bool(s.get("needs_review"))})
        if not any(s["rating"] for s in skills):
            raise ValueError("A CV record needs at least one skill with a level from 1 to 4")
        sen = payload.get("seniority")
        return {"name": name, "employee_no": _clean(payload.get("employee_no"), 40) or None,
                "roles": [_clean(x, 80) for x in (payload.get("roles") or []) if _clean(x, 80)][:5],
                "background": _clean(payload.get("background"), 1500), "skills": skills[:60],
                "upload_id": payload.get("upload_id") if isinstance(payload.get("upload_id"), int) else None,
                "seniority": _clean(sen.get("level") if isinstance(sen, dict) else sen, 20)}
    if kind in MEMBER_KINDS:
        return _validate_member(kind, payload)
    if kind == "employee_roles":
        emp = _employee_for(payload)
        changes = []
        for c in payload.get("changes") or []:
            action = c.get("action")
            role, to = _clean(c.get("role"), 60), _clean(c.get("to"), 60)
            if action not in ("rename", "add", "remove") or not role or (action == "rename" and not to):
                raise ValueError("Each role change needs an action (rename, add or remove) and a role")
            changes.append({"action": action, "role": role, "to": to if action == "rename" else "",
                            "reason": _clean(c.get("reason"), 300), "skills": [_clean(x, 80) for x in (c.get("skills") or [])][:8],
                            "source": "code" if c.get("source") == "code" else "ai"})
        if not changes:
            raise ValueError("There is no role change to approve")
        return {"employee_id": emp["id"], "employee_name": emp["name"], "changes": changes[:8],
                "roles_before": [_clean(x, 60) for x in (payload.get("roles_before") or [])][:15]}
    if kind == "project_assignment":
        return assignments.validate(payload)
    if kind == "project_dates":
        return assignments.validate_dates(payload)
    raise ValueError(f"Unknown proposal kind '{kind}'")


def _employee_for(payload: dict) -> dict:
    try:
        eid = int(payload.get("employee_id"))
    except (TypeError, ValueError):
        raise ValueError("employee_id is required") from None
    emp = db.one("SELECT id, name FROM employees WHERE id=?", (eid,))
    if not emp:
        raise ValueError("That employee does not exist")
    return emp


def _validate_member(kind: str, payload: dict) -> dict:
    emp = _employee_for(payload)
    base = {"employee_id": emp["id"], "employee_name": emp["name"]}
    if kind == "member_update":
        ratings, seen = [], set()
        for r in payload.get("ratings") or []:
            skill = _clean(r.get("skill"))
            try:
                level = int(r.get("rating"))
            except (TypeError, ValueError):
                raise ValueError(f"The level for '{skill}' must be a whole number from 0 to 4") from None
            if not skill or skill.lower() in seen:
                continue
            if not 0 <= level <= 4:
                raise ValueError(f"The level for '{skill}' must be from 0 to 4")
            seen.add(skill.lower())
            before = r.get("before")
            ratings.append({"skill": skill, "rating": level, "before": before if isinstance(before, int) and 0 <= before <= 4 else None})
        roles = payload.get("roles")
        tags = payload.get("domain_tags")
        bg = payload.get("background")
        out = {**base, "ratings": ratings[:60],
               "roles": None if roles is None else list(dict.fromkeys(_clean(x, 80) for x in roles if _clean(x, 80)))[:5],
               "domain_tags": None if tags is None else list(dict.fromkeys(_clean(x, 80) for x in tags if _clean(x, 80)))[:10],
               "background": None if bg is None else str(bg).strip()[:1500]}
        for k in ("roles_before", "domain_tags_before", "background_before"):  # shown to the approver, never applied
            if k in payload:
                out[k] = payload[k] if k == "background_before" else [_clean(x, 80) for x in (payload[k] or [])][:10]
        if not (out["ratings"] or out["roles"] is not None or out["domain_tags"] is not None or out["background"] is not None):
            raise ValueError("There is nothing to change")
        return out
    if kind == "member_cv":
        skills, seen = [], set()
        for s in payload.get("skills") or []:
            raw = _clean(s.get("raw_skill"))
            try:
                level = int(s.get("rating"))
            except (TypeError, ValueError):
                continue
            if raw and 0 <= level <= 4 and raw.lower() not in seen:
                seen.add(raw.lower())
                skills.append({"raw_skill": raw, "rating": level, "evidence": _clean(s.get("evidence"), 400), "needs_review": bool(s.get("needs_review"))})
        if not any(s["rating"] for s in skills):
            raise ValueError("A CV needs at least one skill with a level from 1 to 4")
        sen = payload.get("seniority")
        return {**base, "roles": [_clean(x, 80) for x in (payload.get("roles") or []) if _clean(x, 80)][:5],
                "background": _clean(payload.get("background"), 1500), "skills": skills[:60],
                "upload_id": payload.get("upload_id") if isinstance(payload.get("upload_id"), int) else None,
                "seniority": _clean(sen.get("level") if isinstance(sen, dict) else sen, 20)}
    if kind == "member_learning_plan":
        md = str(payload.get("markdown") or "").strip()
        if not md:
            raise ValueError("The learning plan is empty")
        reqs = [{"skill": _clean(r.get("skill")), "level": int(min(max(int(r.get("level", 2)), 1), 4))}
                for r in payload.get("requirements") or [] if _clean(r.get("skill"))][:20]
        return {**base, "title": _clean(payload.get("title"), 120) or f"Learning plan for {emp['name']}", "markdown": md[:20000],
                "requirements": reqs, "weeks": int(payload.get("weeks") or 0) or None, "hours_per_week": int(payload.get("hours_per_week") or 0) or None,
                "mode": _clean(payload.get("mode"), 20), "problems": [_clean(x, 300) for x in (payload.get("problems") or [])][:20],
                "edited": bool(payload.get("edited"))}
    raise ValueError(f"Unknown proposal kind '{kind}'")


def create(ctx, kind: str, title: str, payload: dict, evidence: str = "", confidence: float | None = None,
           requested_by: int | None = None) -> dict:
    """Queue a proposal for a person to decide. A pending proposal with the same content is not duplicated."""
    payload = _validate(kind, payload)
    key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    dup = db.one("SELECT id FROM agent_proposals WHERE kind=? AND status='pending' AND payload=? AND COALESCE(requested_by,0)=COALESCE(?,0)",
                 (kind, key, requested_by))
    if dup:
        return {"proposal_id": dup["id"], "duplicate": True}
    with db.connect() as c:
        pid = c.execute("INSERT INTO agent_proposals(run_id, agent, kind, title, payload, evidence, confidence, requested_by, subject_employee_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (ctx.run_id, ctx.agent, kind, _clean(title, 200), key, _clean(evidence, 1500),
                         None if confidence is None else round(float(confidence), 2), requested_by,
                         payload.get("employee_id") if isinstance(payload.get("employee_id"), int) else None)).lastrowid
    ctx.proposal_ids.append(pid)
    return {"proposal_id": pid, "status": "pending", "needs_role": ["team_leader"] if kind in MEMBER_KINDS else sorted(REQUIRED_ROLES[kind])}


def log_auto_applied(ctx, kind: str, title: str, payload: dict, evidence: str) -> int:
    with db.connect() as c:
        return c.execute("INSERT INTO agent_proposals(run_id, agent, kind, title, payload, evidence, confidence, status, decided_by, "
                         "decided_at) VALUES (?,?,?,?,?,?,1.0,'auto_applied','system: exact match or earlier decision',CURRENT_TIMESTAMP)",
                         (ctx.run_id, ctx.agent, kind, _clean(title, 200), json.dumps(payload, ensure_ascii=False),
                          _clean(evidence, 500))).lastrowid


def _decode(r: dict) -> dict:
    try:
        r["payload"] = json.loads(r["payload"])
    except ValueError:
        pass
    r["can_decide"] = None
    return r


def _visible(user: dict, r: dict) -> bool:
    """Who may SEE a proposal. A member's change is private to the member, their team leader and the admin."""
    if user["role"] == "member":
        return r.get("requested_by") == user["id"]
    if r["kind"] in MEMBER_KINDS:
        return _can(user, r) or r.get("decided_by") == user["username"]
    return True


def list_proposals(user: dict, status: str = "pending", kind: str | None = None, limit: int = 200) -> list[dict]:
    where, params = [], []
    if status != "all":
        where.append("status=?")
        params.append(status)
    if kind:
        where.append("kind=?")
        params.append(kind)
    if user["role"] == "member":
        where.append("requested_by=?")
        params.append(user["id"])
    sql = "SELECT * FROM agent_proposals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    names = {u["id"]: (u["display_name"] or u["username"]) for u in db.rows("SELECT id, username, display_name FROM users")}
    out = []
    for r in db.rows(sql, (*params, limit)):
        if not _visible(user, r):
            continue
        r = _decode(r)
        r["can_decide"] = _can(user, r)
        if r["kind"] in ("project_assignment", "project_dates"):
            r["requested_by_name"] = names.get(r.get("requested_by"))
            if r["kind"] == "project_assignment" and r["status"] == "pending" and isinstance(r["payload"], dict):
                x = r["payload"]  # the person's booking in the request's own period, without this person's current share of THIS project
                r["capacity"] = assignments.capacity_for(x["employee_id"], x.get("period_start"), x.get("period_end"), x.get("assignment_id"))
        if r["kind"] in MEMBER_KINDS:
            lead = auth.team_leader_of(r.get("requested_by"))
            r["requested_by_name"] = names.get(r.get("requested_by"))
            r["approver"] = (lead["display_name"] if lead else "an admin (no active team leader is set)")
        out.append(r)
    return out


def pending_counts(user: dict) -> dict:
    """Pending proposals this user may decide (for the badge in the menu)."""
    rows = [r for r in db.rows("SELECT kind, requested_by, subject_employee_id, decided_by FROM agent_proposals WHERE status='pending'") if _visible(user, r)]
    if user["role"] == "member":
        return {"mine": 0, "all": len(rows)}
    return {"mine": sum(1 for r in rows if _can(user, r)), "all": len(rows)}


def approval_counts() -> dict:
    waiting = db.one("SELECT COUNT(*) AS n FROM agent_proposals WHERE status='pending'")["n"]
    briefs = db.one("SELECT COUNT(*) AS n FROM project_briefs WHERE status='approved'")["n"]
    return {"waiting": waiting, "approved_briefs": briefs}


def decide(proposal_id: int, user: dict, action: str, note: str = "", edits: dict | None = None) -> dict:
    p = db.one("SELECT * FROM agent_proposals WHERE id=?", (proposal_id,))
    if not p:
        raise LookupError("Proposal not found")
    if p["status"] != "pending":
        raise ValueError(f"This proposal was already decided ({p['status']})")
    if not _can(user, p):
        if p["kind"] == "employee_roles":
            raise PermissionError("Only an HR manager, or the team leader of that employee's account, can decide this")
        if p["kind"] in MEMBER_KINDS:
            raise PermissionError("Only the member's own team leader (or an admin) can decide this")
        need = " or ".join(sorted(REQUIRED_ROLES[p["kind"]])).replace("_", " ")
        if p["kind"] in ("project_assignment", "project_dates"):
            raise PermissionError("Only an HR manager (or an admin) can approve this")
        raise PermissionError(f"Only a {need} can decide this kind of proposal")
    payload = json.loads(p["payload"])
    result: dict = {}
    if action == "approve":
        if edits:  # the approver may correct the proposal before applying it
            payload = _validate(p["kind"], {**payload, **edits})
        if p["kind"] == "project_assignment":
            who = db.one("SELECT username FROM users WHERE id=?", (p.get("requested_by"),))
            result = assignments.apply(payload, user, who["username"] if who else "")
        elif p["kind"] == "project_dates":
            result = assignments.apply_dates(payload, user)
        else:
            result = _APPLY[p["kind"]](payload, user)
        status = "approved"
    elif action == "reject":
        status = "rejected"
        if p["kind"] == "skill_mapping":  # a rejected unknown skill is remembered so it is not proposed forever
            try:
                requested.decide(payload["raw_skill"], "dismiss")
            except LookupError:
                pass
    else:
        raise ValueError("action must be 'approve' or 'reject'")
    with db.connect() as c:
        c.execute("UPDATE agent_proposals SET status=?, decided_by=?, decided_at=CURRENT_TIMESTAMP, note=?, payload=? WHERE id=?",
                  (status, user["username"], _clean(note, 500), json.dumps(payload, ensure_ascii=False), proposal_id))
    auth.audit(user, f"proposal_{status}", {"proposal": proposal_id, "kind": p["kind"], "title": p["title"], "agent": p["agent"]})
    return {"status": status, **result}


# ----------------------------------------------------------------------------- applying
def _apply_mapping(payload: dict, user: dict) -> dict:
    changed = standardization.apply_override(payload["raw_skill"], payload["canonical"], payload.get("category"))
    repo.reindex_employees()
    try:  # if a project had asked for this skill, it is no longer pending
        requested.decide(payload["raw_skill"], "approve", payload["canonical"], payload.get("category"))
    except LookupError:
        pass
    return {"employees_updated": changed}


def _apply_upload_fix(payload: dict, user: dict) -> dict:
    up = db.one("SELECT * FROM uploads WHERE id=?", (payload["upload_id"],))
    if not up:
        raise ValueError("The upload no longer exists")
    if up["status"] == "imported":
        raise ValueError("This file was already imported; the settings can no longer change")
    settings = json.loads(up["settings_json"] or "{}")
    if payload.get("sheet"):
        settings["sheet"] = payload["sheet"]
    if payload.get("layout"):
        settings["layout"] = payload["layout"]
    if payload.get("mapping"):
        settings["mapping"] = {**(settings.get("mapping") or {}), **payload["mapping"]}
    with db.connect() as c:
        c.execute("UPDATE uploads SET settings_json=? WHERE id=?", (json.dumps(settings), up["id"]))
    return {"upload_id": up["id"], "message": "Settings saved. Open the file on the Upload page to see the new preview."}


def _apply_brief(payload: dict, user: dict) -> dict:
    requirements = payload["requirements"]
    mappings = standardization.normalize_many([requirement["skill"] for requirement in requirements])
    standardized: dict[str, dict] = {}
    unknown: list[str] = []
    for requirement in requirements:
        raw_skill = requirement["skill"]
        match = mappings[raw_skill]
        canonical = match["canonical"]
        if match["method"] == "new":
            unknown.append(raw_skill)
        current = standardized.get(canonical)
        if current is None:
            standardized[canonical] = {**requirement, "skill": canonical}
        else:
            current["level"] = max(current["level"], requirement["level"])
            current["weight"] = max(current["weight"], requirement["weight"])
            current["min_people"] = max(current["min_people"], requirement["min_people"])
            current["domain_tags"] = sorted(set(current["domain_tags"]) | set(requirement["domain_tags"]))
    if unknown:
        context = f"Approved project: {payload['title']}. {payload['prompt']}"
        requested.record(unknown, context)

    with db.connect() as c:
        bid = c.execute("INSERT INTO project_briefs(title, prompt, requirements, headcount, roles, notes, approved_by, start_date, end_date) VALUES (?,?,?,?,?,?,?,?,?)",
                        (payload["title"], payload["prompt"], json.dumps(list(standardized.values())), payload["headcount"],
                         json.dumps(payload["roles"]), payload["notes"], user["username"], payload.get("start_date"), payload.get("end_date"))).lastrowid
    return {"brief_id": bid}


def _apply_profile(payload: dict, user: dict) -> dict:
    res = repo.apply_cv_profile(payload, payload.get("upload_id"))
    emp = repo.get_employee(res["employee_id"]) if res.get("employee_id") else None
    res["seniority"] = emp["seniority"]["level"] if emp else None  # computed from the levels the matrix now holds
    res["message"] = (f"{'Added' if res['created'] else 'Updated'} {payload['name']}: {res['skills_added']} skills added, "
                      f"{res['skills_kept']} already in the matrix kept. Seniority: {res['seniority']}.")
    return res


def _apply_member_update(payload: dict, user: dict) -> dict:
    res = repo.apply_member_update(payload["employee_id"], [{"skill": r["skill"], "rating": r["rating"]} for r in payload["ratings"]],
                                   payload.get("roles"), payload.get("background"), payload.get("domain_tags"))
    emp = repo.get_employee(payload["employee_id"])
    res["seniority"] = emp["seniority"]["level"] if emp else None
    res["message"] = (f"Applied to {payload['employee_name']}: {res['ratings_changed']} ratings changed, {res['skills_added']} skills added, "
                      f"{res['skills_removed']} removed. Seniority: {res['seniority']}.")
    return res


def _apply_member_cv(payload: dict, user: dict) -> dict:
    res = repo.apply_cv_to_employee(payload["employee_id"], payload)
    emp = repo.get_employee(payload["employee_id"])
    res["seniority"] = emp["seniority"]["level"] if emp else None
    res["message"] = (f"Added to {payload['employee_name']}: {res['skills_added']} skills added, {res['skills_kept']} already in the matrix kept. "
                      f"Seniority: {res['seniority']}.")
    return res


def _apply_member_plan(payload: dict, user: dict) -> dict:
    with db.connect() as c:
        pid = c.execute("INSERT INTO learning_plans(employee_id, title, markdown, requirements, weeks, hours_per_week, mode, approved_by) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (payload["employee_id"], payload["title"], payload["markdown"], json.dumps(payload["requirements"]), payload["weeks"],
                         payload["hours_per_week"], payload["mode"], user["username"])).lastrowid
    return {"learning_plan_id": pid, "message": f"Learning plan saved for {payload['employee_name']}."}


def _apply_roles(payload: dict, user: dict) -> dict:
    res = repo.apply_role_changes(payload["employee_id"], payload["changes"])
    res["message"] = f"Roles of {payload['employee_name']}: {', '.join(res['before']) or 'none'} → {', '.join(res['after']) or 'none'}."
    return res


_APPLY = {"employee_roles": _apply_roles, "skill_mapping": _apply_mapping, "upload_fix": _apply_upload_fix, "project_brief": _apply_brief, "employee_profile": _apply_profile,
          "member_update": _apply_member_update, "member_cv": _apply_member_cv, "member_learning_plan": _apply_member_plan}


def list_briefs() -> list[dict]:
    out = db.rows("SELECT * FROM project_briefs WHERE status='approved' ORDER BY id DESC LIMIT 100")
    for b in out:
        b["requirements"] = json.loads(b["requirements"] or "[]")
        b["roles"] = json.loads(b["roles"] or "[]")
    return out


def cancel_brief(brief_id: int, user: dict, reason: str) -> dict:
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to cancel an approved project")
    if len(reason) > 1000:
        raise ValueError("Cancellation reason must be 1000 characters or fewer")
    brief = db.one("SELECT id, title, status FROM project_briefs WHERE id=?", (brief_id,))
    if not brief:
        raise LookupError("Approved project not found")
    if brief["status"] != "approved":
        raise ValueError("This project is no longer approved")
    with db.connect() as c:
        changed = c.execute(
            "UPDATE project_briefs SET status='cancelled', cancelled_by=?, cancelled_at=CURRENT_TIMESTAMP, "
            "cancellation_reason=? WHERE id=? AND status='approved'",
            (user["username"], reason, brief_id),
        ).rowcount
    if not changed:
        raise ValueError("This project is no longer approved")
    assignments.end_for_brief(brief_id)
    auth.audit(user, "project_brief_cancelled", {"brief_id": brief_id, "title": brief["title"], "reason": reason})
    return {"status": "cancelled", "brief_id": brief_id}
