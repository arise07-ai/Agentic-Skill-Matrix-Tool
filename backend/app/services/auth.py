"""Login, roles and the audit log.

Roles:  admin | project_manager | team_leader | hr_manager | member
* A member is linked (by the admin) to exactly ONE employee record and may only work on that record; every change a member
  makes waits for that member's team leader (users.team_leader_id, set by an admin or HR manager).
* Passwords are stored as scrypt hashes (standard library). Nothing secret is in the code: the signing secret and the first
  admin account come from the environment (AUTH_SECRET, ADMIN_USERNAME, ADMIN_PASSWORD).
* A session is a signed token in an HttpOnly cookie. It stops working when the user is disabled, the password changes,
  or it expires (SESSION_HOURS).
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from .. import db
from ..config import get_settings

log = logging.getLogger(__name__)
ROLES = ("admin", "project_manager", "team_leader", "hr_manager", "member")
ROLE_LABELS = {"admin": "Admin", "project_manager": "Project Manager", "team_leader": "Team Leader", "hr_manager": "HR Manager",
               "member": "Member"}
_UNSET = object()  # "this field was not sent" (None means "clear it")
MIN_PASSWORD = 10
COOKIE = "sm_session"

_fallback_secret = secrets.token_hex(32)  # used only when AUTH_SECRET is not set
_failures: dict[str, list[float]] = {}  # username -> recent failed login times (in memory)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:  # noqa: BLE001
        return False


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")


# ---------------------------------------------------------------- session tokens
def _secret() -> bytes:
    return (get_settings().auth_secret or _fallback_secret).encode()


def make_token(user: dict) -> str:
    exp = int(time.time()) + get_settings().session_hours * 3600
    body = base64.urlsafe_b64encode(json.dumps({"uid": user["id"], "v": user["token_version"], "exp": exp}).encode()).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def user_from_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest(), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    u = db.one("SELECT * FROM users WHERE id=?", (data.get("uid"),))
    if not u or not u["active"] or u["token_version"] != data.get("v"):
        return None
    return public(u)


def public(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"] or u["username"], "role": u["role"],
            "role_label": ROLE_LABELS.get(u["role"], u["role"]), "active": bool(u["active"]), "created_at": u.get("created_at"),
            "employee_id": u.get("employee_id"), "team_leader_id": u.get("team_leader_id")}


# ---------------------------------------------------------------- login
def login(username: str, password: str) -> dict:
    """-> public user dict, or raises PermissionError. Slows down repeated failures for one username."""
    key = username.strip().lower()
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < 300]
    if len(recent) >= 5:
        raise PermissionError("Too many failed attempts. Wait a few minutes and try again.")
    u = db.one("SELECT * FROM users WHERE username=?", (username.strip(),))
    ok = bool(u and u["active"] and verify_password(password, u["password_hash"]))
    if not u:
        verify_password(password, "scrypt$00$00")  # keep the timing similar for unknown names
    if not ok:
        _failures[key] = recent + [now]
        audit(None, "login_failed", {"username": username.strip()[:60]})
        raise PermissionError("Wrong username or password")
    _failures.pop(key, None)
    audit(public(u), "login", {})
    return public(u)


# ---------------------------------------------------------------- users
def _check_links(user_id: int | None, role: str, employee_id, team_leader_id, check_leader: bool = True) -> tuple[int | None, int | None]:
    """Validate the employee link and the team leader of an account. Only members have them; anyone else gets both cleared."""
    if role != "member":
        return None, None
    if employee_id is None:
        raise ValueError("A member account must be linked to one employee")
    emp = db.one("SELECT id, name FROM employees WHERE id=?", (employee_id,))
    if not emp:
        raise ValueError("That employee does not exist")
    taken = db.one("SELECT username FROM users WHERE employee_id=? AND id<>?", (employee_id, user_id or -1))
    if taken:
        raise ValueError(f"{emp['name']} is already linked to the account '{taken['username']}'")
    if team_leader_id is not None and check_leader:  # an unchanged leader who was disabled later must not block other edits
        leader = db.one("SELECT id, role, active FROM users WHERE id=?", (team_leader_id,))
        if not leader or leader["role"] != "team_leader":
            raise ValueError("The team leader must be an account with the Team Leader role")
        if not leader["active"]:
            raise ValueError("That team leader account is disabled")
    return int(employee_id), (int(team_leader_id) if team_leader_id is not None else None)


def create_user(username: str, password: str, role: str, display_name: str = "", actor: dict | None = None,
                employee_id: int | None = None, team_leader_id: int | None = None) -> dict:
    username = username.strip()
    if not username or len(username) > 60:
        raise ValueError("Username is required (max 60 characters)")
    if role not in ROLES:
        raise ValueError(f"Role must be one of: {', '.join(ROLES)}")
    check_password_strength(password)
    if db.one("SELECT 1 AS x FROM users WHERE username=?", (username,)):
        raise ValueError("That username already exists")
    employee_id, team_leader_id = _check_links(None, role, employee_id, team_leader_id)
    with db.connect() as c:
        uid = c.execute("INSERT INTO users(username, display_name, password_hash, role, employee_id, team_leader_id) VALUES (?,?,?,?,?,?)",
                        (username, display_name.strip(), hash_password(password), role, employee_id, team_leader_id)).lastrowid
    audit(actor, "user_created", {"username": username, "role": role, "employee_id": employee_id, "team_leader_id": team_leader_id})
    return public(db.one("SELECT * FROM users WHERE id=?", (uid,)))


def list_users() -> list[dict]:
    names = {e["id"]: e["name"] for e in db.rows("SELECT id, name FROM employees")}
    by_id = {u["id"]: u for u in db.rows("SELECT * FROM users")}
    out = []
    for u in sorted(by_id.values(), key=lambda x: x["username"].lower()):
        p = public(u)
        p["employee_name"] = names.get(u.get("employee_id"))
        leader = by_id.get(u.get("team_leader_id")) if u.get("team_leader_id") else None
        p["team_leader_name"] = (leader["display_name"] or leader["username"]) if leader else None
        out.append(p)
    return out


def update_user(user_id: int, actor: dict, role: str | None = None, active: bool | None = None,
                password: str | None = None, display_name: str | None = None,
                employee_id=_UNSET, team_leader_id=_UNSET) -> dict:
    u = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    if not u:
        raise LookupError("User not found")
    sets, params, changes = [], [], {}
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ROLES)}")
        sets.append("role=?"); params.append(role); changes["role"] = role
    if active is not None:
        sets.append("active=?"); params.append(int(active)); changes["active"] = active
    if display_name is not None:
        sets.append("display_name=?"); params.append(display_name.strip())
    if password:
        check_password_strength(password)
        sets.append("password_hash=?"); params.append(hash_password(password)); changes["password"] = "changed"
    new_role = role if role is not None else u["role"]
    if role is not None or employee_id is not _UNSET or team_leader_id is not _UNSET:
        emp = u["employee_id"] if employee_id is _UNSET else employee_id
        lead = u["team_leader_id"] if team_leader_id is _UNSET else team_leader_id
        emp, lead = _check_links(user_id, new_role, emp, lead, check_leader=team_leader_id is not _UNSET)
        if (emp, lead) != (u["employee_id"], u["team_leader_id"]):
            sets.append("employee_id=?"); params.append(emp)
            sets.append("team_leader_id=?"); params.append(lead)
            changes["employee_id"], changes["team_leader_id"] = emp, lead
    if (role is not None and role != "admin") or active is False:  # never remove the last working admin
        admins = db.one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1 AND id<>?", (user_id,))["n"]
        if u["role"] == "admin" and admins == 0:
            raise ValueError("At least one active admin must remain")
    if role is not None or active is not None or password:
        sets.append("token_version=token_version+1")  # ends this user's current sessions
    if sets:
        with db.connect() as c:
            c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*params, user_id))
    audit(actor, "user_updated", {"user": u["username"], **changes})
    return public(db.one("SELECT * FROM users WHERE id=?", (user_id,)))


def set_team_leader(member_id: int, team_leader_id: int | None, actor: dict) -> dict:
    """Admin or HR manager: choose the one team leader who approves this member's changes (None = nobody, the admin decides)."""
    u = db.one("SELECT * FROM users WHERE id=?", (member_id,))
    if not u or u["role"] != "member":
        raise LookupError("Member account not found")
    _, lead = _check_links(member_id, "member", u["employee_id"], team_leader_id)
    with db.connect() as c:
        c.execute("UPDATE users SET team_leader_id=? WHERE id=?", (lead, member_id))
    audit(actor, "member_team_leader_set", {"member": u["username"], "team_leader_id": lead})
    return next(x for x in list_users() if x["id"] == member_id)


def leader_of_employee(employee_id: int | None) -> dict | None:
    """The active team leader of the member account linked to this employee (None: no account, or no active leader)."""
    if not employee_id:
        return None
    u = db.one("SELECT id FROM users WHERE employee_id=? AND role='member' AND active=1", (employee_id,))
    return team_leader_of(u["id"]) if u else None


def team_leader_of(member_user_id: int | None) -> dict | None:
    """The ACTIVE team leader account of a member, or None (not set, disabled, or no longer a team leader: the admin decides)."""
    if member_user_id is None:
        return None
    m = db.one("SELECT team_leader_id FROM users WHERE id=?", (member_user_id,))
    if not m or not m["team_leader_id"]:
        return None
    lead = db.one("SELECT * FROM users WHERE id=?", (m["team_leader_id"],))
    return public(lead) if lead and lead["active"] and lead["role"] == "team_leader" else None


def bootstrap_admin() -> str:
    """Create the first admin from ADMIN_USERNAME / ADMIN_PASSWORD when there are no users. Returns a status message."""
    s = get_settings()
    if db.one("SELECT 1 AS x FROM users LIMIT 1"):
        return "users exist"
    if not (s.admin_username and s.admin_password):
        return "no users and ADMIN_USERNAME / ADMIN_PASSWORD are not set: nobody can log in until you set them"
    create_user(s.admin_username, s.admin_password, "admin", "Administrator")
    return f"admin account '{s.admin_username}' created"


# ---------------------------------------------------------------- audit
def audit(user: dict | None, action: str, detail: dict | None = None) -> None:
    """Who did what. Never raises."""
    try:
        with db.connect() as c:
            c.execute("INSERT INTO audit_log(user_id, username, role, action, detail) VALUES (?,?,?,?,?)",
                      (user["id"] if user else None, user["username"] if user else "system", user["role"] if user else "",
                       action, json.dumps(detail or {}, ensure_ascii=False, default=str)[:4000]))
    except Exception:  # noqa: BLE001
        log.exception("Could not write the audit log")


def recent_audit(limit: int = 200) -> list[dict]:
    rows = db.rows("SELECT id, ts, username, role, action, detail FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"])
        except ValueError:
            pass
    return rows
