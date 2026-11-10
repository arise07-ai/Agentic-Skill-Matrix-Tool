"""Project assignments. Closed to members (main.py). A project manager (or the admin) asks; an HR manager (or the admin) approves
through the normal approvals queue (POST /api/agents/proposals/{id}/decision)."""
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..agents import proposals
from ..deps import current_user, require_roles
from ..services import assignments, auth

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


class RequestBody(BaseModel):
    action: str = Field("assign", pattern="^(assign|change|remove)$")
    employee_id: int
    position: str = Field("", max_length=60)
    allocation_pct: int | None = Field(None, ge=1, le=100)
    start_date: str | None = Field(None, max_length=10)  # this person's own dates (blank = follow the project)
    end_date: str | None = Field(None, max_length=10)
    note: str = Field("", max_length=500)


class DatesBody(BaseModel):
    start_date: str | None = Field(None, max_length=10)
    end_date: str | None = Field(None, max_length=10)
    note: str = Field("", max_length=500)


@router.get("/capacity")
def capacity(brief_id: int | None = None, user: dict = Depends(current_user)):
    """Allocation of everyone who has any (people missing from the list have nothing allocated). With brief_id, only that project's
    dates are looked at (so a person busy elsewhere only until June is free for a project that starts in July)."""
    start, end = assignments._brief_dates(brief_id) if brief_id else (None, None)
    return {"day_hours": assignments.DAY_HOURS, "max_pct": assignments.MAX_TOTAL_PCT, "window": assignments.period_text(start, end),
            "employees": {str(k): v for k, v in assignments.capacity_all(start, end).items()}}


@router.get("/project/{brief_id}")
def project_team(brief_id: int, user: dict = Depends(current_user)):
    try:
        return assignments.team(brief_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.post("/project/{brief_id}/requests")
def make_request(brief_id: int, body: RequestBody, user: dict = Depends(require_roles("project_manager"))):
    try:
        sent = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}  # a date that was not sent keeps its current value
        payload = assignments.validate({**sent, "brief_id": brief_id})
        assignments.request_check(payload)
        verb = {"assign": "Assign", "change": "Change allocation of", "remove": "Remove"}[payload["action"]]
        title = f"{verb} {payload['employee_name']} — {payload['brief_title']}"
        ctx = SimpleNamespace(run_id=None, agent="project_manager", proposal_ids=[])
        res = proposals.create(ctx, "project_assignment", title, payload, evidence=body.note, requested_by=user["id"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    auth.audit(user, "assignment_requested", {"brief_id": brief_id, "employee_id": body.employee_id, "action": body.action,
                                              "allocation_pct": payload["allocation_pct"]})
    return {**res, "message": "Sent to HR for approval. Nothing changes on the project until HR approves."}


@router.post("/project/{brief_id}/dates")
def request_dates(brief_id: int, body: DatesBody, user: dict = Depends(require_roles("project_manager"))):
    try:
        payload = assignments.validate_dates({"brief_id": brief_id, "start_date": body.start_date, "end_date": body.end_date})
        assignments.request_dates_check(payload)
        title = f"Dates for {payload['brief_title']}: {assignments.period_text(payload['start_date'], payload['end_date'])}"
        ctx = SimpleNamespace(run_id=None, agent="project_manager", proposal_ids=[])
        res = proposals.create(ctx, "project_dates", title, payload, evidence=body.note, requested_by=user["id"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    auth.audit(user, "project_dates_requested", {"brief_id": brief_id, "start_date": payload["start_date"], "end_date": payload["end_date"]})
    return {**res, "message": "Sent to HR for approval. The project's dates stay as they are until HR approves."}
