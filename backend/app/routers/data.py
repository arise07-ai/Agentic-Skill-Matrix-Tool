from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..deps import current_user, require_roles
from ..ai.status import component_status
from ..schemas import MappingUpdate, RequestedDecision, TaxonomyItem
from ..services import auth, repo, requested, standardization

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/dashboard")
def dashboard():
    return repo.dashboard()


@router.get("/employees")
def employees(q: str = "", role: str = ""):
    return repo.list_employees(q, role)


@router.get("/employees/{emp_id}")
def employee(emp_id: int):
    e = repo.get_employee(emp_id)
    if not e:
        raise HTTPException(404, "Employee not found")
    return e


@router.delete("/employees/{emp_id}")
def delete_employee(emp_id: int, user: dict = Depends(current_user)):
    auth.audit(user, "employee_deleted", {"employee_id": emp_id})
    repo.delete_employee(emp_id)
    return {"ok": True}


@router.get("/matrix")
def matrix(category: str = "", role: str = ""):
    return repo.matrix(category, role)


@router.get("/roles")
def roles():
    return [r["role"] for r in db.rows("SELECT DISTINCT role FROM employee_roles ORDER BY role")]


# ------------------------------------------------------------------ standardization
@router.get("/skills/mappings")
def mappings(status: str = "needs_review"):
    where = "WHERE needs_review=1" if status == "needs_review" else ""
    return db.rows(
        "SELECT MIN(raw_skill) AS raw_skill, canonical_skill, category, method, ROUND(AVG(confidence),2) AS confidence, "
        f"MAX(needs_review) AS needs_review, COUNT(*) AS occurrences FROM employee_skills {where} "
        "GROUP BY lower(raw_skill), canonical_skill, category, method ORDER BY needs_review DESC, confidence, raw_skill")


@router.patch("/skills/mappings")
def update_mapping(body: MappingUpdate, user: dict = Depends(current_user)):
    auth.audit(user, "skill_mapping_manual", {"raw": body.raw_skill, "canonical": body.canonical})
    changed = standardization.apply_override(body.raw_skill, body.canonical.strip(), body.category)
    repo.reindex_employees()
    return {"updated": changed}


@router.get("/skills/requested")
def requested_skills(status: str = "pending"):
    """Skills that project demands asked for but the taxonomy lacks (status: pending | approved | dismissed | all)."""
    return requested.list_requested(status)


@router.post("/skills/requested")
def decide_requested(body: RequestedDecision, user: dict = Depends(current_user)):
    auth.audit(user, "requested_skill_" + body.action, {"name": body.name, "canonical": body.canonical})
    try:
        out = requested.decide(body.name, body.action, body.canonical, body.category)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.action == "approve":
        repo.reindex_employees()
    return out


@router.get("/taxonomy")
def taxonomy():
    tax, _ = standardization.index()
    return [{"name": n, **m} for n, m in sorted(tax.items(), key=lambda kv: (kv[1]["category"], kv[0]))]


@router.post("/taxonomy")
def add_taxonomy(item: TaxonomyItem):
    import json

    seeded = json.loads(standardization.SEED.read_text(encoding="utf-8")).get(item.name)
    if seeded and item.category != seeded.get("category", "Other"):
        raise HTTPException(
            400,
            f"{item.name} is a bundled taxonomy skill; its category is managed in skill_taxonomy.json "
            f"({seeded.get('category', 'Other')}).",
        )
    with db.connect() as c:
        c.execute("INSERT INTO taxonomy(name, category, aliases) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                  "category=excluded.category, aliases=excluded.aliases",
                  (item.name.strip(), item.category, json.dumps(item.aliases)))
    standardization.invalidate()
    standardization.ensure_taxonomy_indexed(force=True)
    return {"ok": True}


# ------------------------------------------------------------------ admin / ai
@router.get("/ai/status")
def ai_status():
    return component_status()


@router.post("/admin/reindex")
def reindex():
    standardization.ensure_taxonomy_indexed(force=True)
    return {"employees_indexed": repo.reindex_employees()}


@router.delete("/admin/data", dependencies=[Depends(require_roles())])
def reset(user: dict = Depends(current_user)):
    auth.audit(user, "all_data_deleted", {})
    repo.reset_all(keep_taxonomy=True)
    return {"ok": True}
