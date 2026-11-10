from fastapi import APIRouter, Depends, HTTPException

from .. import db

from ..ai import AINotConfigured
from ..ai.guardrails import GuardrailViolation
from ..deps import count_ai_request, current_user, ensure_own_employee, staff_user
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


def _employee_exists(employee_id: int) -> None:
    """A quick check before an AI request is counted: an unknown employee is refused for free."""
    if not db.one("SELECT 1 AS x FROM employees WHERE id=?", (employee_id,)):
        raise HTTPException(404, "Employee not found")


@router.post("/projects/requirements")
def requirements(body: RequirementsRequest, user: dict = Depends(staff_user)):
    count_ai_request(user)
    try:
        return projects.extract_requirements(body.prompt)
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))


@router.post("/projects/gaps")
def gaps(body: ProjectInput, user: dict = Depends(staff_user)):
    if not body.requirements:
        raise HTTPException(400, "Add at least one required skill")
    count_ai_request(user)
    return projects.gap_report(body)


@router.post("/projects/staffing")
def staffing(body: ProjectInput, user: dict = Depends(staff_user)):
    if not body.requirements and not body.prompt.strip():
        raise HTTPException(400, "Provide a project description or required skills")
    count_ai_request(user)
    try:
        return projects.staffing(body)
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))


@router.post("/learning-paths")
def learning_path(body: LearningRequest, user: dict = Depends(current_user)):
    ensure_own_employee(user, body.employee_id)
    _employee_exists(body.employee_id)
    count_ai_request(user)
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
    _employee_exists(body.employee_id)
    if body.messages[-1].role != "user":
        raise HTTPException(400, "Send a learner message before asking the coach to respond.")
    count_ai_request(user)
    try:
        return learning.coach(body)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))
    except AINotConfigured as e:
        raise HTTPException(503, f"Learning coach is unavailable: {e}")
