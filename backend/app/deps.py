"""Request dependencies: who is logged in, and which roles may call an endpoint."""
from fastapi import Cookie, Depends, HTTPException

from .services import auth


def current_user(sm_session: str | None = Cookie(default=None)) -> dict:
    user = auth.user_from_token(sm_session)
    if not user:
        raise HTTPException(401, "Please log in")
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
