from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..agents import proposals, runtime, websearch
from ..ai import AINotConfigured
from ..deps import current_user, require_roles, staff_user

router = APIRouter(prefix="/api/agents", tags=["agents"])


class RunBody(BaseModel):
    agent: str
    text: str = ""  # intake: the project description
    upload_id: int | None = None  # upload check
    messages: list[dict] = Field(default_factory=list)  # chat: the conversation so far (kept by the page, not the server)


class AnswerBody(BaseModel):
    answer: str


class DecisionBody(BaseModel):
    action: str  # approve | reject
    note: str = ""
    edits: dict | None = None


class CancelBriefBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@router.get("/status")
def status(user: dict = Depends(current_user)):
    ok, why = runtime.agents_available()
    return {"available": ok, "message": why, "web_lookup": websearch.is_configured(),
            "agents": [{"key": k, "title": a.title} for k, a in runtime.registry().items()],
            "pending": proposals.pending_counts(user)}


@router.post("/runs")
def start(body: RunBody, user: dict = Depends(staff_user)):
    inp = {"text": body.text, "upload_id": body.upload_id, "messages": body.messages[-20:]}
    try:
        return {"run_id": runtime.start_run(body.agent, user, inp)}
    except AINotConfigured as e:
        raise HTTPException(503, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except runtime.AgentError as e:
        raise HTTPException(429, str(e))


@router.get("/runs")
def runs(user: dict = Depends(staff_user)):
    return runtime.list_runs(user)


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: dict = Depends(staff_user)):
    try:
        return runtime.get_run(run_id, user)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.post("/runs/{run_id}/answer")
def answer(run_id: str, body: AnswerBody, user: dict = Depends(staff_user)):
    try:
        runtime.answer_run(run_id, user, body.answer)
        return {"ok": True}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except runtime.AgentError as e:
        raise HTTPException(409, str(e))


@router.get("/proposals")
def list_proposals(status: str = "pending", kind: str | None = None, user: dict = Depends(current_user)):
    return proposals.list_proposals(user, status, kind)


@router.post("/proposals/{proposal_id}/decision")
def decide(proposal_id: int, body: DecisionBody, user: dict = Depends(current_user)):
    try:
        return proposals.decide(proposal_id, user, body.action, body.note, body.edits)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/briefs")
def briefs(user: dict = Depends(staff_user)):
    return proposals.list_briefs()


@router.get("/approval-counts")
def approval_counts(user: dict = Depends(staff_user)):
    return proposals.approval_counts()


@router.post("/briefs/{brief_id}/cancel")
def cancel_brief(
    brief_id: int,
    body: CancelBriefBody,
    user: dict = Depends(require_roles("hr_manager")),
):
    try:
        return proposals.cancel_brief(brief_id, user, body.reason)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
