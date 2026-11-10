"""Putting people on projects, how much of their working day each project takes, and for which dates.

Allocation is a percentage of the working day (DAY_HOURS = 8 hours). The limit is checked over time: on any single day, one person's
approved allocations over all approved projects may not pass MAX_TOTAL_PCT (100% = 8 hours). Two projects that do not overlap in time
therefore do not add up.

Dates (optional everywhere, ISO "YYYY-MM-DD"):
    a project has a start and an end date; a person on it follows those dates unless they are given their own dates (which must lie
    inside the project's dates). A missing start or end means "no limit on that side", so a project without dates counts as always running.

Who does what: a project manager (or the admin) asks for an assignment, a change (time or dates), a removal, or new project dates;
each is a proposal that an HR manager (or the admin) approves. Nothing is saved until then. The limit is checked when the request is
made (counting requests still waiting, so two managers cannot both book the last free hours) and again when HR approves (counting only
what is already approved).

A project that is cancelled ends all of its assignments and drops its waiting requests.
"""
import json
from datetime import date

from .. import db

DAY_HOURS = 8
MAX_TOTAL_PCT = 100
ACTIONS = ("assign", "change", "remove")

_ACTIVE = "a.status='active' AND b.status='approved'"
_MIN, _MAX = date.min, date.max


def hours(pct: int | float) -> float:
    return round(pct * DAY_HOURS / 100, 2)


def _clean(s, n=60) -> str:
    return " ".join(str(s or "").split())[:n]


# ----------------------------------------------------------------------------- dates
def parse_date(value, label: str = "date") -> str | None:
    """'' or None -> None; otherwise a valid YYYY-MM-DD string."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(f"The {label} must be a real date written as YYYY-MM-DD") from None


def parse_period(start, end, what: str = "") -> tuple[str | None, str | None]:
    s, e = parse_date(start, f"{what}start date".strip()), parse_date(end, f"{what}end date".strip())
    if s and e and e < s:
        raise ValueError(f"The {what}end date ({e}) is before the start date ({s})")
    return s, e


def _d(v: str | None, default: date) -> date:
    return date.fromisoformat(v) if v else default


def period_text(s: str | None, e: str | None) -> str:
    if s and e:
        return f"{s} to {e}"
    if s:
        return f"from {s}"
    if e:
        return f"until {e}"
    return "no dates set"


# ----------------------------------------------------------------------------- load over time
def _brief_dates(brief_id: int) -> tuple[str | None, str | None]:
    b = db.one("SELECT start_date, end_date FROM project_briefs WHERE id=?", (brief_id,))
    return (b["start_date"], b["end_date"]) if b else (None, None)


def _approved(employee_id: int, exclude_assignment: int | None = None) -> list[dict]:
    return db.rows(
        "SELECT a.id, a.brief_id, b.title, a.position, a.allocation_pct, COALESCE(a.start_date, b.start_date) AS s, COALESCE(a.end_date, b.end_date) AS e, "
        f"a.start_date AS own_start, a.end_date AS own_end FROM project_assignments a JOIN project_briefs b ON b.id=a.brief_id "
        f"WHERE a.employee_id=? AND {_ACTIVE} AND a.id<>? ORDER BY a.id", (employee_id, exclude_assignment or 0))


def _pending_rows() -> list[dict]:
    out = []
    for r in db.rows("SELECT id, requested_by, payload FROM agent_proposals WHERE kind='project_assignment' AND status='pending'"):
        try:
            p = json.loads(r["payload"])
        except ValueError:
            continue
        p["proposal_id"], p["requested_by"] = r["id"], r["requested_by"]
        out.append(p)
    return out


def _pending(employee_id: int) -> list[dict]:
    """What the requests still waiting for HR would add for this person, as (start, end, percent)."""
    out = []
    for p in _pending_rows():
        if p.get("employee_id") != employee_id:
            continue
        pct = int(p.get("allocation_pct") or 0)
        if p.get("action") == "change":
            pct = max(0, pct - int(p.get("allocation_before") or 0))
        elif p.get("action") != "assign":
            continue
        out.append({"s": p.get("period_start"), "e": p.get("period_end"), "allocation_pct": pct})
    return out


def peak(items: list[dict], start: str | None, end: str | None) -> int:
    """The highest total percent on any single day inside [start, end] (None = no limit on that side)."""
    lo, hi = _d(start, _MIN), _d(end, _MAX)
    spans = [(_d(i["s"], _MIN), _d(i["e"], _MAX), i["allocation_pct"]) for i in items]
    spans = [x for x in spans if x[0] <= hi and x[1] >= lo]
    points = {lo} | {x[0] for x in spans if x[0] > lo}
    return max((sum(p for s, e, p in spans if s <= pt <= e) for pt in points), default=0)


def allocated(employee_id: int, exclude_assignment: int | None = None) -> int:
    """The busiest day of approved work over the whole timeline."""
    return peak(_approved(employee_id, exclude_assignment), None, None)


def capacity_for(employee_id: int, start: str | None = None, end: str | None = None, exclude_assignment: int | None = None) -> dict:
    """The person's booking inside a window (no window = the whole timeline). `allocated_pct` is the busiest day of approved work."""
    approved = _approved(employee_id, exclude_assignment)
    used = peak(approved, start, end)
    with_wait = peak(approved + _pending(employee_id), start, end)
    lo, hi = _d(start, _MIN), _d(end, _MAX)
    rows = [r for r in approved if _d(r["s"], _MIN) <= hi and _d(r["e"], _MAX) >= lo]
    return {"allocated_pct": used, "allocated_hours": hours(used), "free_pct": max(0, MAX_TOTAL_PCT - used),
            "free_hours": hours(max(0, MAX_TOTAL_PCT - used)), "pending_pct": with_wait - used,
            "free_after_pending_pct": max(0, MAX_TOTAL_PCT - with_wait), "window": period_text(start, end),
            "projects": [{"assignment_id": r["id"], "brief_id": r["brief_id"], "title": r["title"], "position": r["position"],
                          "allocation_pct": r["allocation_pct"], "hours": hours(r["allocation_pct"]), "start": r["s"], "end": r["e"]} for r in rows]}


def capacity_all(start: str | None = None, end: str | None = None) -> dict[int, dict]:
    ids = {r["employee_id"] for r in db.rows(f"SELECT a.employee_id FROM project_assignments a JOIN project_briefs b ON b.id=a.brief_id WHERE {_ACTIVE}")}
    ids |= {p["employee_id"] for p in _pending_rows() if isinstance(p.get("employee_id"), int)}
    return {i: capacity_for(i, start, end) for i in ids}


def check_capacity(employee_id: int, name: str, new_pct: int, start: str | None, end: str | None,
                   exclude_assignment: int | None = None, count_pending: bool = False) -> None:
    items = _approved(employee_id, exclude_assignment)
    used = peak(items, start, end)
    wait = (peak(items + _pending(employee_id), start, end) - used) if count_pending else 0
    free = MAX_TOTAL_PCT - used - wait
    if new_pct > free:
        free = max(free, 0)
        when = "" if not (start or end) else f" ({period_text(start, end)})"
        why = f"on its busiest day {used}% is already approved" + (f" and {wait}% more is waiting for HR" if wait else "")
        raise ValueError(f"{name} cannot take {new_pct}% ({hours(new_pct)} h/day){when}: {why}, so only {free}% ({hours(free)} h/day) is free "
                         f"of the {DAY_HOURS}-hour day.")


# ----------------------------------------------------------------------------- requests
def _pct(value) -> int:
    try:
        pct = int(value)
    except (TypeError, ValueError):
        raise ValueError("The allocation must be a whole number of percent of the working day") from None
    if not 1 <= pct <= MAX_TOTAL_PCT:
        raise ValueError(f"The allocation must be from 1% to {MAX_TOTAL_PCT}% of the {DAY_HOURS}-hour day")
    return pct


def _inside_project(name: str, own_s, own_e, ps, pe) -> None:
    if own_s and ps and own_s < ps or own_e and pe and own_e > pe or own_s and pe and own_s > pe or own_e and ps and own_e < ps:
        raise ValueError(f"{name}'s dates ({period_text(own_s, own_e)}) must lie inside the project's dates ({period_text(ps, pe)})")


def validate(payload: dict) -> dict:
    """Clean a request. Does not check the load (see check_capacity)."""
    action = payload.get("action")
    if action not in ACTIONS:
        raise ValueError("The action must be assign, change or remove")
    try:
        brief_id, eid = int(payload.get("brief_id")), int(payload.get("employee_id"))
    except (TypeError, ValueError):
        raise ValueError("brief_id and employee_id are required") from None
    brief = db.one("SELECT id, title, status, start_date, end_date FROM project_briefs WHERE id=?", (brief_id,))
    if not brief:
        raise ValueError("That project does not exist")
    if brief["status"] != "approved":
        raise ValueError("That project is no longer approved, so nobody can be assigned to it")
    emp = db.one("SELECT id, name FROM employees WHERE id=?", (eid,))
    if not emp:
        raise ValueError("That employee does not exist")
    out = {"action": action, "brief_id": brief_id, "brief_title": brief["title"], "employee_id": eid, "employee_name": emp["name"],
           "position": _clean(payload.get("position")), "allocation_pct": None, "assignment_id": None, "allocation_before": None,
           "start_date": None, "end_date": None, "period_start": None, "period_end": None}
    current = db.one("SELECT id, allocation_pct, position, start_date, end_date FROM project_assignments WHERE brief_id=? AND employee_id=? AND status='active'", (brief_id, eid))
    if action == "assign":
        if current:
            raise ValueError(f"{emp['name']} is already on this project")
        out["allocation_pct"] = _pct(payload.get("allocation_pct"))
        own_s, own_e = parse_period(payload.get("start_date"), payload.get("end_date"), "person's ")
    else:
        if not current:
            raise ValueError(f"{emp['name']} is not on this project")
        out["assignment_id"], out["allocation_before"] = current["id"], current["allocation_pct"]
        out["position"] = out["position"] or current["position"]
        if action == "change":
            out["allocation_pct"] = _pct(payload.get("allocation_pct"))
            own_s, own_e = (parse_period(payload.get("start_date"), payload.get("end_date"), "person's ")
                            if ("start_date" in payload or "end_date" in payload) else (current["start_date"], current["end_date"]))
            if (out["allocation_pct"], out["position"], own_s, own_e) == (current["allocation_pct"], current["position"], current["start_date"], current["end_date"]):
                raise ValueError("Nothing is different from the current assignment")
        else:
            out["allocation_pct"] = current["allocation_pct"]
            own_s, own_e = current["start_date"], current["end_date"]
    _inside_project(emp["name"], own_s, own_e, brief["start_date"], brief["end_date"])
    out["start_date"], out["end_date"] = own_s, own_e
    out["period_start"], out["period_end"] = own_s or brief["start_date"], own_e or brief["end_date"]
    if out["period_start"] and out["period_end"] and out["period_end"] < out["period_start"]:
        raise ValueError("The end date is before the start date")
    return out


def request_check(payload: dict) -> None:
    """Extra checks only for a NEW request: the load (counting requests already waiting) and no second waiting request for the same person."""
    for p in _pending_rows():
        if p.get("brief_id") == payload["brief_id"] and p.get("employee_id") == payload["employee_id"]:
            raise ValueError(f"A request for {payload['employee_name']} on this project is already waiting for HR")
    if payload["action"] in ("assign", "change"):
        check_capacity(payload["employee_id"], payload["employee_name"], payload["allocation_pct"], payload["period_start"], payload["period_end"],
                       exclude_assignment=payload["assignment_id"], count_pending=True)


def apply(payload: dict, user: dict, requested_by: str = "") -> dict:
    """Called when HR (or the admin) approves. Checks everything again against what is approved right now."""
    p = validate(payload)
    name, when = p["employee_name"], period_text(p["period_start"], p["period_end"])
    if p["action"] == "assign":
        check_capacity(p["employee_id"], name, p["allocation_pct"], p["period_start"], p["period_end"])
        with db.connect() as c:
            aid = c.execute("INSERT INTO project_assignments(brief_id, employee_id, position, allocation_pct, start_date, end_date, requested_by, approved_by) VALUES (?,?,?,?,?,?,?,?)",
                            (p["brief_id"], p["employee_id"], p["position"], p["allocation_pct"], p["start_date"], p["end_date"], requested_by, user["username"])).lastrowid
        msg = f"{name} is now on {p['brief_title']} at {p['allocation_pct']}% ({hours(p['allocation_pct'])} h/day), {when}."
    elif p["action"] == "change":
        check_capacity(p["employee_id"], name, p["allocation_pct"], p["period_start"], p["period_end"], exclude_assignment=p["assignment_id"])
        with db.connect() as c:
            c.execute("UPDATE project_assignments SET allocation_pct=?, position=?, start_date=?, end_date=?, approved_by=? WHERE id=?",
                      (p["allocation_pct"], p["position"], p["start_date"], p["end_date"], user["username"], p["assignment_id"]))
        aid = p["assignment_id"]
        msg = f"{name} on {p['brief_title']}: {p['allocation_before']}% → {p['allocation_pct']}% ({hours(p['allocation_pct'])} h/day), {when}."
    else:
        with db.connect() as c:
            c.execute("UPDATE project_assignments SET status='removed', ended_at=CURRENT_TIMESTAMP, approved_by=? WHERE id=?", (user["username"], p["assignment_id"]))
        aid = p["assignment_id"]
        msg = f"{name} was taken off {p['brief_title']}; {p['allocation_before']}% of their day is free again."
    return {"assignment_id": aid, "message": msg}


# ----------------------------------------------------------------------------- project dates
def validate_dates(payload: dict) -> dict:
    try:
        brief_id = int(payload.get("brief_id"))
    except (TypeError, ValueError):
        raise ValueError("brief_id is required") from None
    brief = db.one("SELECT id, title, status, start_date, end_date FROM project_briefs WHERE id=?", (brief_id,))
    if not brief:
        raise ValueError("That project does not exist")
    if brief["status"] != "approved":
        raise ValueError("That project is no longer approved, so its dates cannot change")
    s, e = parse_period(payload.get("start_date"), payload.get("end_date"), "project ")
    if (s, e) == (brief["start_date"], brief["end_date"]):
        raise ValueError("These are already the project's dates")
    return {"brief_id": brief_id, "brief_title": brief["title"], "start_date": s, "end_date": e,
            "start_before": brief["start_date"], "end_before": brief["end_date"]}


def dates_check(payload: dict) -> None:
    """New dates may not push anyone over the limit or leave a person's own dates outside the project."""
    s, e = payload["start_date"], payload["end_date"]
    for r in db.rows("SELECT a.id, a.employee_id, em.name, a.allocation_pct, a.start_date, a.end_date FROM project_assignments a "
                     "JOIN employees em ON em.id=a.employee_id WHERE a.brief_id=? AND a.status='active'", (payload["brief_id"],)):
        _inside_project(r["name"], r["start_date"], r["end_date"], s, e)
        check_capacity(r["employee_id"], r["name"], r["allocation_pct"], r["start_date"] or s, r["end_date"] or e, exclude_assignment=r["id"])


def request_dates_check(payload: dict) -> None:
    for r in db.rows("SELECT payload FROM agent_proposals WHERE kind='project_dates' AND status='pending'"):
        try:
            if json.loads(r["payload"]).get("brief_id") == payload["brief_id"]:
                raise ValueError("A date change for this project is already waiting for HR")
        except ValueError as ex:
            if "already waiting" in str(ex):
                raise
    dates_check(payload)


def apply_dates(payload: dict, user: dict) -> dict:
    p = validate_dates(payload)
    dates_check(p)
    with db.connect() as c:
        c.execute("UPDATE project_briefs SET start_date=?, end_date=? WHERE id=?", (p["start_date"], p["end_date"], p["brief_id"]))
    return {"message": f"{p['brief_title']} now runs {period_text(p['start_date'], p['end_date'])}."}


def end_for_brief(brief_id: int) -> None:
    """The project was cancelled."""
    with db.connect() as c:
        c.execute("UPDATE project_assignments SET status='ended', ended_at=CURRENT_TIMESTAMP WHERE brief_id=? AND status='active'", (brief_id,))
        c.execute("UPDATE agent_proposals SET status='rejected', decided_by='system', decided_at=CURRENT_TIMESTAMP, note='The project was cancelled' "
                  "WHERE kind IN ('project_assignment','project_dates') AND status='pending' AND json_extract(payload,'$.brief_id')=?", (brief_id,))


def team(brief_id: int) -> dict:
    brief = db.one("SELECT id, title, headcount, roles, status, start_date, end_date FROM project_briefs WHERE id=?", (brief_id,))
    if not brief:
        raise LookupError("Project not found")
    members = []
    for r in db.rows("SELECT a.id, a.employee_id, e.name, a.position, a.allocation_pct, a.start_date, a.end_date, a.created_at, a.approved_by "
                     "FROM project_assignments a JOIN employees e ON e.id=a.employee_id WHERE a.brief_id=? AND a.status='active' ORDER BY a.id", (brief_id,)):
        ps, pe = r["start_date"] or brief["start_date"], r["end_date"] or brief["end_date"]
        others = peak(_approved(r["employee_id"], r["id"]), ps, pe)
        members.append({"assignment_id": r["id"], "employee_id": r["employee_id"], "name": r["name"], "position": r["position"],
                        "allocation_pct": r["allocation_pct"], "hours": hours(r["allocation_pct"]), "other_pct": others,
                        "total_pct": others + r["allocation_pct"], "start_date": r["start_date"], "end_date": r["end_date"],
                        "period_start": ps, "period_end": pe, "since": r["created_at"], "approved_by": r["approved_by"]})
    names = {u["id"]: (u["display_name"] or u["username"]) for u in db.rows("SELECT id, username, display_name FROM users")}
    waiting = [{"proposal_id": p["proposal_id"], "action": p["action"], "employee_id": p["employee_id"], "employee_name": p["employee_name"],
                "position": p.get("position", ""), "allocation_pct": p["allocation_pct"], "hours": hours(p["allocation_pct"] or 0),
                "period_start": p.get("period_start"), "period_end": p.get("period_end"), "requested_by_name": names.get(p["requested_by"])}
               for p in _pending_rows() if p.get("brief_id") == brief_id]
    for r in db.rows("SELECT id, requested_by, payload FROM agent_proposals WHERE kind='project_dates' AND status='pending'"):
        d = json.loads(r["payload"])
        if d.get("brief_id") == brief_id:
            waiting.append({"proposal_id": r["id"], "action": "dates", "employee_id": None, "employee_name": "", "position": "", "allocation_pct": None,
                            "hours": 0, "period_start": d.get("start_date"), "period_end": d.get("end_date"), "requested_by_name": names.get(r["requested_by"])})
    total = sum(m["allocation_pct"] for m in members)
    return {"brief": {"id": brief["id"], "title": brief["title"], "status": brief["status"], "headcount": brief["headcount"],
                      "roles": json.loads(brief["roles"] or "[]"), "start_date": brief["start_date"], "end_date": brief["end_date"]},
            "members": members, "waiting": waiting, "day_hours": DAY_HOURS, "max_pct": MAX_TOTAL_PCT,
            "total_pct": total, "total_hours": hours(total)}
