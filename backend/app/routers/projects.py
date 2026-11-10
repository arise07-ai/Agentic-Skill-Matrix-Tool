from fastapi import APIRouter, Depends, HTTPException

from ..ai import AINotConfigured
from ..ai.guardrails import GuardrailViolation
from ..deps import current_user, ensure_own_employee, staff_user
from ..schemas import LearningCoachRequest, LearningPlanCheckRequest, LearningRequest, ProjectInput, RequirementsRequest
from ..services import learning, projects

router = APIRouter(prefix="/api", tags=["projects"])

# Who may edit a finished learning plan (the admin always may). Add "team_leader" here to let team leaders edit too.
# A member may edit THEIR OWN plan (the server checks the employee id); it only takes effect when their team leader approves it.
PLAN_EDITORS = ("project_manager", "hr_manager")


def plan_editor(user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ("admin", "member", *PLAN_EDITORS):
        raise HTTPException(403, "Your role is not allowed to do this")
    return user


@router.post("/projects/requirements", dependencies=[Depends(staff_user)])
def requirements(body: RequirementsRequest):
    try:
        return projects.extract_requirements(body.prompt)
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))


@router.post("/projects/gaps", dependencies=[Depends(staff_user)])
def gaps(body: ProjectInput):
    if not body.requirements:
        raise HTTPException(400, "Add at least one required skill")
    return projects.gap_report(body)


@router.post("/projects/staffing", dependencies=[Depends(staff_user)])
def staffing(body: ProjectInput):
    if not body.requirements and not body.prompt.strip():
        raise HTTPException(400, "Provide a project description or required skills")
    try:
        return projects.staffing(body)
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))


@router.post("/learning-paths")
def learning_path(body: LearningRequest, user: dict = Depends(current_user)):
    ensure_own_employee(user, body.employee_id)
    try:
        return learning.build(body, require_approval=True)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))


@router.post("/learning-paths/check")
def learning_path_check(body: LearningPlanCheckRequest, user: dict = Depends(plan_editor)):
    """Re-check the numbers of a plan that a project manager, HR manager, admin or (their own plan) a member edited. The edit is never blocked."""
    ensure_own_employee(user, body.employee_id)
    try:
        return learning.check_edited_plan(body)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.post("/learning-paths/coach")
def learning_coach(body: LearningCoachRequest, user: dict = Depends(current_user)):
    ensure_own_employee(user, body.employee_id)
    try:
        return learning.coach(body)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))
    except AINotConfigured as e:
        raise HTTPException(503, f"Learning coach is unavailable: {e}")
