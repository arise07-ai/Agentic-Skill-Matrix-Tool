"""Roles: the list in use, AI role matching (proposals only) and manual role edits.

* Manual edit: HR manager, project manager, team leader and admin edit an employee's roles directly (written to the audit log).
  A member's own edits go through their team leader instead (routers/member.py).
* AI matching: a single employee may be matched by any staff role; matching everyone needs an HR manager or admin.
  The result is one `employee_roles` proposal per employee, decided by an HR manager or that employee's team leader.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import count_ai_request, require_roles, staff_user
from ..services import auth, repo, role_match, roles

router = APIRouter(prefix="/api/roles", tags=["roles"])
ROLE_EDITORS = ("hr_manager", "project_manager", "team_leader")


def can_edit_roles(user: dict, emp_id: int) -> bool:
    """HR manager, project manager and admin: any employee. A team leader: only their own members (the employees whose member
    account lists that team leader)."""
    if user["role"] == "team_leader":
        lead = auth.leader_of_employee(emp_id)
        return bool(lead and lead["id"] == user["id"])
    return user["role"] in ("admin", *ROLE_EDITORS)


class MatchBody(BaseModel):
    employee_ids: list[int] | None = Field(None, max_length=300)  # null = everyone (HR manager / admin only)


class RolesBody(BaseModel):
    roles: list[str] = Field(default_factory=list, max_length=10)


@router.get("/overview")  # NOT "" : GET /api/roles is the plain list of role names in routers/data.py (used by the filters)
def overview(user: dict = Depends(staff_user)):
    return roles.role_counts()


@router.post("/match")
def match(body: MatchBody, user: dict = Depends(staff_user)):
    many = body.employee_ids is None or len(body.employee_ids) > 1
    if many and user["role"] not in ("admin", "hr_manager"):
        raise HTTPException(403, "Only an HR manager or admin can match many employees at once")
    if not many and not can_edit_roles(user, body.employee_ids[0]):
        raise HTTPException(403, "A team leader can only match the roles of their own members")
    count_ai_request(user)
    auth.audit(user, "role_match_started", {"employees": body.employee_ids if body.employee_ids else "all"})
    return {"job_id": role_match.start(body.employee_ids)}


@router.get("/match/{job_id}")
def match_status(job_id: str, user: dict = Depends(staff_user)):
    j = role_match.status(job_id)
    if not j:
        raise HTTPException(404, "Unknown job (the server may have restarted)")
    return j


@router.get("/employee/{emp_id}/access")
def access(emp_id: int, user: dict = Depends(staff_user)):
    return {"can_edit": can_edit_roles(user, emp_id)}


@router.put("/employee/{emp_id}")
def edit_roles(emp_id: int, body: RolesBody, user: dict = Depends(require_roles(*ROLE_EDITORS))):
    if not can_edit_roles(user, emp_id):
        raise HTTPException(403, "A team leader can only edit the roles of their own members")
    clean = [roles.clean(r) for r in body.roles if roles.clean(r)]
    try:
        res = repo.set_roles(emp_id, clean)
    except LookupError as e:
        raise HTTPException(404, str(e))
    auth.audit(user, "employee_roles_edited", {"employee_id": emp_id, **res})
    return res
