"""Request dependencies: who is logged in, which roles may call an endpoint, and the per-user limit on AI requests."""
import threading
import time
from collections import deque

from fastapi import Cookie, Depends, HTTPException

from .config import get_settings
from .services import auth

# Recent AI requests per user id (kept in memory: the backend runs as one instance; a restart starts the count again).
_ai_requests: dict[int, deque] = {}
_ai_lock = threading.Lock()


def current_user(sm_session: str | None = Cookie(default=None)) -> dict:
    """The logged-in user (with "session_id"). A 401 says why the session stopped, so the login page can tell the person."""
    user, reason = auth.session_from_token(sm_session)
    if not user:
        raise HTTPException(401, auth.SIGNED_OUT.get(reason, "Please log in"))
    return user


def require_roles(*roles: str):
    """Allow the listed roles (and always the admin). With no roles listed, only the admin passes."""

    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin" and user["role"] not in roles:
            raise HTTPException(403, "Your role is not allowed to do this")
        return user

    return dep


def staff_user(user: dict = Depends(current_user)) -> dict:
    """Everything except a member. Members have their own endpoints under /api/me and may not call these."""
    if user["role"] == "member":
        raise HTTPException(403, "Members can only work on their own profile")
    return user


def member_user(user: dict = Depends(current_user)) -> dict:
    """A member account that the admin has linked to an employee. The linked employee id is user['employee_id']."""
    if user["role"] != "member":
        raise HTTPException(403, "This is for member accounts")
    if not user.get("employee_id"):
        raise HTTPException(409, "Your account is not linked to an employee yet. Ask an admin to link it.")
    return user


def ensure_own_employee(user: dict, employee_id: int) -> None:
    """A member may only name their own employee record. Anyone else is unrestricted here (their roles are checked elsewhere)."""
    if user["role"] == "member" and (not user.get("employee_id") or int(employee_id) != int(user["employee_id"])):
        raise HTTPException(403, "You can only work on your own record")


def count_ai_request(user: dict) -> None:
    """Count one AI request for this user, or refuse it (429) when they already made AI_MAX_REQUESTS_PER_HOUR in the last hour.
    The count is shared by every endpoint that can call the AI (staffing, gaps, requirements, learning paths, the Learning Coach,
    PDF and CV reading, imports, role matching). A route calls this AFTER its own quick checks, so a request refused for invalid
    data, a missing field, a record that does not exist or a wrong file does not use up the allowance. The admin is not limited.
    Agent runs have their own limit (AGENT_MAX_RUNS_PER_HOUR)."""
    limit = get_settings().ai_max_requests_per_hour
    if user["role"] == "admin" or limit <= 0:
        return
    now = time.time()
    with _ai_lock:
        recent = _ai_requests.setdefault(user["id"], deque())
        while recent and now - recent[0] >= 3600:
            recent.popleft()
        if len(recent) >= limit:
            minutes = int((3600 - (now - recent[0])) // 60) + 1
            raise HTTPException(429, f"You have reached the limit of {limit} AI requests per hour. Try again in {minutes} minute"
                                     f"{'s' if minutes != 1 else ''}.")
        recent.append(now)
