"""Skills that project demands asked for but the taxonomy does not have yet.

They are recorded when a requirement is extracted or analysed, listed on the Standardization page ("Requested by
projects"), and become part of the taxonomy only when a person approves them. Nothing is added automatically.
"""
import logging

from .. import db
from . import standardization

log = logging.getLogger(__name__)


def record(names: list[str], context: str = "") -> None:
    """Remember unknown skills (never raises: this is bookkeeping and must not break a project analysis)."""
    try:
        ctx = " ".join((context or "").split())[:300]
        with db.connect() as c:
            for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
                key = name.lower()
                row = c.execute("SELECT status FROM requested_skills WHERE key=?", (key,)).fetchone()
                if row is None:
                    c.execute("INSERT INTO requested_skills(key, name, last_context) VALUES (?,?,?)", (key, name, ctx))
                elif row["status"] == "pending":
                    c.execute("UPDATE requested_skills SET times_requested=times_requested+1, last_context=?, "
                              "last_seen=CURRENT_TIMESTAMP WHERE key=?", (ctx or "", key))
    except Exception:  # noqa: BLE001
        log.exception("Could not record requested skills")


def list_requested(status: str = "pending") -> list[dict]:
    where, params = ("", ()) if status == "all" else ("WHERE r.status=?", (status,))
    return db.rows(
        "SELECT r.name, r.status, r.times_requested, r.last_context, r.last_seen, "
        "(SELECT COUNT(DISTINCT employee_id) FROM employee_skills e WHERE lower(e.canonical_skill)=r.key) AS holders "
        f"FROM requested_skills r {where} ORDER BY r.status='pending' DESC, r.times_requested DESC, r.last_seen DESC",
        params)


def decide(name: str, action: str, canonical: str | None = None, category: str | None = None) -> dict:
    key = name.strip().lower()
    if not db.one("SELECT 1 AS x FROM requested_skills WHERE key=?", (key,)):
        raise LookupError(f"'{name}' is not in the requested list")
    updated = 0
    if action == "approve":
        target = (canonical or name).strip()
        # Same mechanism as the Standardization page: adds the skill to the taxonomy (new) or maps the spelling to an
        # existing one, learns it for future uploads, and updates employees who already list it.
        updated = standardization.apply_override(name.strip(), target, category)
        new_status = "approved"
    elif action == "dismiss":
        new_status = "dismissed"
    else:
        new_status = "pending"
    with db.connect() as c:
        c.execute("UPDATE requested_skills SET status=? WHERE key=?", (new_status, key))
    return {"status": new_status, "employees_updated": updated}
