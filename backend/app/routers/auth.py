from fastapi import APIRouter, Depends, HTTPException, Response
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
def login(body: LoginBody, response: Response):
    try:
        user = auth.login(body.username, body.password)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    s = get_settings()
    response.set_cookie(auth.COOKIE, auth.make_token(db_user(user["id"])), httponly=True, samesite="lax",
                        secure=s.auth_cookie_secure, max_age=s.session_hours * 3600, path="/")
    return user


def db_user(user_id: int) -> dict:
    from .. import db

    return db.one("SELECT * FROM users WHERE id=?", (user_id,))


@router.post("/logout")
def logout(response: Response, user: dict = Depends(current_user)):
    auth.audit(user, "logout", {})
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


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
