"""What a MEMBER may do, and nothing else: look at and propose changes to their OWN record.

A member is linked by the admin to exactly one employee (users.employee_id). Every endpoint here works on that employee only; the id is never
taken from the request. Nothing a member does changes the matrix directly: each change becomes a proposal that the member's team leader
(or an admin) approves under Approvals.
"""
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import db
from ..agents import proposals
from ..deps import member_user
from ..schemas import CVSkill, LearningPlanCheckRequest, Requirement
from ..services import auth, learning, repo, seniority, standardization
from . import uploads

router = APIRouter(prefix="/api/me", tags=["member"])


class MemberRating(BaseModel):
    skill: str = Field(..., min_length=1, max_length=80)
    rating: int = Field(..., ge=0, le=4)  # 0 None (removes the skill), 1 Learned, 2 Applied, 3 Experienced, 4 Mastered


class MemberChange(BaseModel):
    ratings: list[MemberRating] = Field(default_factory=list, max_length=60)
    roles: list[str] | None = Field(None, max_length=5)
    domain_tags: list[str] | None = Field(None, max_length=10)
    background: str | None = Field(None, max_length=1500)


class MemberCVSubmit(BaseModel):
    roles: list[str] = Field(default_factory=list, max_length=5)
    background: str = Field("", max_length=1500)
    skills: list[CVSkill] = Field(..., min_length=1, max_length=60)


class MemberPlanSubmit(BaseModel):
    markdown: str = Field(..., min_length=1, max_length=20000)
    requirements: list[Requirement] = Field(..., min_length=1, max_length=20)
    weeks: int = Field(4, ge=1, le=52)
    hours_per_week: int = Field(5, ge=1, le=60)
    mode: str = Field("", max_length=20)
    edited: bool = False


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(run_id=None, agent="member", proposal_ids=[])


def _approver(user: dict) -> dict:
    lead = auth.team_leader_of(user["id"])
    return {"team_leader": lead["display_name"] if lead else None,
            "approver_label": lead["display_name"] if lead else "an admin (no active team leader is set for you yet)"}


@router.get("/profile")
def profile(user: dict = Depends(member_user)):
    emp = repo.get_employee(user["employee_id"])
    if not emp:
        raise HTTPException(409, "The employee record linked to your account no longer exists. Ask an admin to link it again.")
    tax, _ = standardization.index()
    plans = db.rows("SELECT id, title, markdown, requirements, weeks, hours_per_week, mode, approved_by, created_at FROM learning_plans "
                    "WHERE employee_id=? ORDER BY id DESC LIMIT 20", (user["employee_id"],))
    return {"user": user, "employee": emp, **_approver(user),
            "skill_options": [{"name": n, "category": m["category"]} for n, m in sorted(tax.items(), key=lambda kv: (kv[1]["category"], kv[0]))],
            "learning_plans": plans}


@router.get("/taxonomy")
def taxonomy(user: dict = Depends(member_user)):
    """The skill list for the target-skill pickers (the staff /api/taxonomy is closed to members)."""
    tax, _ = standardization.index()
    return [{"name": n, **m} for n, m in sorted(tax.items(), key=lambda kv: (kv[1]["category"], kv[0]))]


@router.post("/changes")
def propose_changes(body: MemberChange, user: dict = Depends(member_user)):
    """Ratings and profile (roles, domain tags, background). Only what really differs from the saved record is sent for approval."""
    emp = repo.get_employee(user["employee_id"])
    if not emp:
        raise HTTPException(409, "The employee record linked to your account no longer exists")
    current = {s["canonical_skill"].lower(): s for s in emp["skills"]}
    raw_to_canon = {s["raw_skill"].lower(): s for s in emp["skills"]}
    ratings = []
    for r in body.ratings:
        have = current.get(r.skill.strip().lower()) or raw_to_canon.get(r.skill.strip().lower())
        before = have["rating"] if have else 0
        if (before or 0) == r.rating:
            continue  # unchanged
        ratings.append({"skill": have["canonical_skill"] if have else r.skill.strip(), "rating": r.rating, "before": before or 0})
    payload: dict = {"employee_id": emp["id"], "ratings": ratings}
    if body.roles is not None and sorted(x.strip() for x in body.roles if x.strip()) != sorted(emp["roles"]):
        payload["roles"], payload["roles_before"] = body.roles, emp["roles"]
    if body.domain_tags is not None and sorted(x.strip() for x in body.domain_tags if x.strip()) != sorted(emp["domain_tags"]):
        payload["domain_tags"], payload["domain_tags_before"] = body.domain_tags, emp["domain_tags"]
    if body.background is not None and body.background.strip() != (emp["background"] or "").strip():
        payload["background"], payload["background_before"] = body.background, (emp["background"] or "")[:1500]
    if not (ratings or any(k in payload for k in ("roles", "domain_tags", "background"))):
        raise HTTPException(400, "Nothing changed")
    try:
        res = proposals.create(_ctx(), "member_update", f"Skills and profile: {emp['name']}", payload,
                               evidence=f"Changed by the member {user['display_name']} on their own record.", requested_by=user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    auth.audit(user, "member_change_submitted", {"proposal": res.get("proposal_id"), "ratings": len(ratings)})
    return {**res, **_approver(user)}


@router.post("/cv")
async def upload_cv(file: UploadFile = File(...), user: dict = Depends(member_user)):
    """Read the member's own CV. Same checks as the staff upload (quotes verified, seniority by rule); nothing is saved."""
    out = await uploads.cv_preview(file, user)
    out["existing_employee"] = None  # a member always works on their own record
    return out


@router.post("/cv/{upload_id}/submit")
def submit_cv(upload_id: int, body: MemberCVSubmit, user: dict = Depends(member_user)):
    up = db.one("SELECT * FROM uploads WHERE id=? AND kind='cv' AND uploaded_by=?", (upload_id, user["id"]))
    if not up:
        raise HTTPException(404, "CV upload not found")  # also what a member sees for someone else's upload
    skills, verified = uploads.check_cv_skills(up, body.skills)
    emp = repo.get_employee(user["employee_id"])
    if not emp:
        raise HTTPException(409, "The employee record linked to your account no longer exists")
    assessment = seniority.assess([s["rating"] for s in skills])
    payload = {"employee_id": emp["id"], "roles": body.roles, "background": body.background, "skills": skills, "upload_id": upload_id,
               "seniority": assessment}
    try:
        res = proposals.create(_ctx(), "member_cv", f"CV: {emp['name']}", payload,
                               evidence=f"Levels estimated by the AI from {up['filename']}, reviewed by the member. {verified} of {len(skills)} "
                                        f"skills have a quote that was found in the CV. Seniority by rule: {assessment['level']}.",
                               confidence=verified / len(skills) if skills else 0, requested_by=user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.connect() as c:
        c.execute("UPDATE uploads SET status='submitted' WHERE id=?", (upload_id,))
    auth.audit(user, "member_cv_submitted", {"upload_id": upload_id, "proposal": res.get("proposal_id")})
    return {**res, **_approver(user)}


@router.post("/learning-plan/submit")
def submit_plan(body: MemberPlanSubmit, user: dict = Depends(member_user)):
    """The plan (as generated, or as the member edited it) goes to the team leader. The numbers are re-checked here, not trusted from the page."""
    emp = repo.get_employee(user["employee_id"])
    if not emp:
        raise HTTPException(409, "The employee record linked to your account no longer exists")
    try:
        check = learning.check_edited_plan(LearningPlanCheckRequest(employee_id=emp["id"], requirements=body.requirements, weeks=body.weeks,
                                                                     hours_per_week=body.hours_per_week, plan_markdown=body.markdown))
    except LookupError as e:
        raise HTTPException(404, str(e))
    payload = {"employee_id": emp["id"], "title": f"Learning plan for {emp['name']}", "markdown": body.markdown,
               "requirements": [{"skill": r.skill, "level": r.level} for r in body.requirements], "weeks": body.weeks,
               "hours_per_week": body.hours_per_week, "mode": body.mode, "problems": check["problems"], "edited": body.edited}
    try:
        res = proposals.create(_ctx(), "member_learning_plan", f"Learning plan: {emp['name']}", payload,
                               evidence=("Edited by the member. " if body.edited else "As generated. ")
                                        + ("The numbers match the ratings." if check["ok"] else f"{len(check['problems'])} thing(s) no longer match the ratings."),
                               requested_by=user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    auth.audit(user, "member_plan_submitted", {"proposal": res.get("proposal_id"), "edited": body.edited})
    return {**res, "problems": check["problems"], **_approver(user)}
