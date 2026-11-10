from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import get_settings
from ..deps import current_user, require_roles
from ..services import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


class NewUser(BaseModel):
    username: str
    password: str
    role: str
    display_name: str = ""
    employee_id: int | None = None  # members only: the one employee record this account may work on
    team_leader_id: int | None = None  # members only: who approves their changes


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None
    display_name: str | None = None
    employee_id: int | None = None  # send null to clear; leave out to keep
    team_leader_id: int | None = None


class TeamLeaderBody(BaseModel):
    team_leader_id: int | None = None  # null = nobody; the admin then decides this member's changes


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    """Signs in and ends the session this user had anywhere else (one open session per user)."""
    try:
        user = auth.login(body.username, body.password)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    s = get_settings()
    token = auth.start_session(user["id"], request.headers.get("user-agent", ""))
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=s.auth_cookie_secure, max_age=s.session_hours * 3600, path="/")
    return user


@router.post("/logout")
def logout(response: Response, user: dict = Depends(current_user)):
    """Ends the session on the server too, so the cookie cannot be used again. A user has one session, so this signs them out everywhere."""
    auth.end_session(user["session_id"], "logout")
    auth.audit(user, "logout", {})
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    """The user and their current session (device label and times, UTC). The session id itself is not sent to the page."""
    sid = user.pop("session_id")
    return {**user, "session": auth.session_info(sid)}


@router.get("/sessions")
def sessions(user: dict = Depends(require_roles())):
    """Admin: every open session (one per user at most)."""
    return auth.open_sessions()


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, user: dict = Depends(require_roles())):
    """Admin: sign one user out. Their next request goes to the login page with a message."""
    target = auth.end_session(session_id, "ended_by_admin")
    if not target:
        raise HTTPException(404, "That session has already ended")
    auth.audit(user, "session_ended_by_admin", {"user": target["username"]})
    return {"ok": True}


@router.get("/users")
def users(user: dict = Depends(require_roles())):
    return auth.list_users()


@router.post("/users")
def create_user(body: NewUser, user: dict = Depends(require_roles())):
    try:
        return auth.create_user(body.username, body.password, body.role, body.display_name, actor=user,
                                employee_id=body.employee_id, team_leader_id=body.team_leader_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, user: dict = Depends(require_roles())):
    try:
        links = {k: getattr(body, k) for k in ("employee_id", "team_leader_id") if k in body.model_fields_set}
        return auth.update_user(user_id, user, body.role, body.active, body.password, body.display_name, **links)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/members")
def members(user: dict = Depends(require_roles("hr_manager"))):
    """Member accounts and the team leaders who can approve for them. Admin and HR manager."""
    everyone = auth.list_users()
    return {"members": [u for u in everyone if u["role"] == "member"],
            "team_leaders": [{"id": u["id"], "name": u["display_name"], "username": u["username"]}
                             for u in everyone if u["role"] == "team_leader" and u["active"]]}


@router.patch("/members/{user_id}/team-leader")
def set_team_leader(user_id: int, body: TeamLeaderBody, user: dict = Depends(require_roles("hr_manager"))):
    try:
        return auth.set_team_leader(user_id, body.team_leader_id, user)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/audit")
def audit(limit: int = 200, user: dict = Depends(require_roles())):
    return auth.recent_audit(limit)
