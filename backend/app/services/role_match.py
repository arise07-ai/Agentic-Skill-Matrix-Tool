"""Role matching job: code finds spelling variants, the AI compares roles with skills, code verifies, a person approves.

For each employee one `employee_roles` proposal is created when something survives verification. Nothing is changed here.
A job runs in a background thread (a long list of employees must not hold a web request); its state is kept in memory, like the agent runs.
"""
import logging
import threading
import uuid
from types import SimpleNamespace

from .. import db
from ..agents import proposals
from ..ai import tasks
from ..ai.llm import AINotConfigured
from . import repo, roles

log = logging.getLogger(__name__)
BATCH = 6
MAX_EMPLOYEES = 300
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _pending_roles_for(emp_id: int) -> bool:
    return bool(db.one("SELECT 1 AS x FROM agent_proposals WHERE kind='employee_roles' AND status='pending' AND subject_employee_id=?", (emp_id,)))


def _ai_input(emp: dict) -> dict:
    top = sorted((s for s in emp["skills"] if (s["rating"] or 0) >= 1), key=lambda s: -(s["rating"] or 0))[:25]
    return {"id": emp["id"], "roles": emp["roles"], "skills": [{"skill": s["canonical_skill"], "level": int(s["rating"])} for s in top]}


def run(employee_ids: list[int] | None, job: dict) -> None:
    ctx = SimpleNamespace(run_id=None, agent="role_match", proposal_ids=[])
    try:
        ids = employee_ids or [r["id"] for r in db.rows("SELECT id FROM employees ORDER BY id")]
        emps = [e for e in (repo.get_employee(i) for i in ids[:MAX_EMPLOYEES]) if e]
        job["total"] = len(emps)
        counts = roles.role_counts()
        canon = [{"role": c["role"], "holders": c["holders"]} for c in counts]
        ai_ok = True
        for start in range(0, len(emps), BATCH):
            batch = emps[start:start + BATCH]
            proposed: dict[int, list[dict]] = {}
            if ai_ok:
                try:
                    proposed = tasks.match_roles(canon, [_ai_input(e) for e in batch if e["roles"] or e["skills"]])
                except AINotConfigured:
                    ai_ok = False
                    job["notes"].append("The AI is not set up, so only spelling variants were checked.")
                except Exception as e:  # noqa: BLE001
                    log.warning("Role matching batch failed: %s", e)
                    job["errors"] += 1
            for e in batch:
                job["done"] += 1
                if _pending_roles_for(e["id"]):
                    job["skipped_pending"] += 1
                    continue
                code_changes = roles.spelling_fixes(e, counts)
                ai_changes, dropped = roles.verify(e, proposed.get(e["id"], []), counts)
                job["dropped"] += len(dropped)
                changes, seen = [], set()
                for ch in code_changes + ai_changes:  # one change per role
                    k = (ch["action"], roles.key(ch["role"]))
                    if k not in seen and not (ch["action"] != "add" and ("rename", roles.key(ch["role"])) in seen):
                        seen.add(k)
                        changes.append(ch)
                if not changes:
                    continue
                try:
                    res = proposals.create(
                        ctx, "employee_roles", f"Roles: {e['name']}",
                        {"employee_id": e["id"], "changes": changes, "roles_before": e["roles"]},
                        evidence=("; ".join(dropped[:3]) if dropped else "Spelling and role-versus-skill check.")[:1500])
                    job["proposals"] += 0 if res.get("duplicate") else 1
                except ValueError as ex:
                    log.warning("Role proposal rejected for employee %s: %s", e["id"], ex)
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        log.exception("Role matching failed")
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"[:300]


def start(employee_ids: list[int] | None) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "total": 0, "done": 0, "proposals": 0, "skipped_pending": 0, "dropped": 0, "errors": 0,
           "notes": [], "error": None}
    with _LOCK:
        if len(_JOBS) > 50:
            _JOBS.pop(next(iter(_JOBS)))
        _JOBS[job_id] = job
    threading.Thread(target=run, args=(employee_ids, job), name=f"role-match-{job_id}", daemon=True).start()
    return job_id


def status(job_id: str) -> dict | None:
    return _JOBS.get(job_id)
