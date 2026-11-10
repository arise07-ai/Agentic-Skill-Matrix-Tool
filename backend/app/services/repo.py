"""Employee persistence + read models (matrix, dashboard) + vector indexing of profiles."""
import logging
import hashlib

from .. import db
from ..ai import vectorstore
from . import roles as roles_svc, seniority, standardization

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- reads
def list_employees(q: str = "", role: str = "") -> list[dict]:
    emps = db.rows(
        "SELECT e.id, e.employee_no, e.name, COUNT(s.id) AS skill_count, ROUND(AVG(s.rating),2) AS avg_rating "
        "FROM employees e LEFT JOIN employee_skills s ON s.employee_id=e.id GROUP BY e.id ORDER BY e.name"
    )
    roles: dict[int, list[str]] = {}
    for r in db.rows("SELECT employee_id, role FROM employee_roles ORDER BY role"):
        roles.setdefault(r["employee_id"], []).append(r["role"])
    levels: dict[int, list] = {}
    for r in db.rows("SELECT employee_id, rating FROM employee_skills"):
        levels.setdefault(r["employee_id"], []).append(r["rating"])
    out = []
    for e in emps:
        e["roles"] = roles.get(e["id"], [])
        e["seniority"] = seniority.assess(levels.get(e["id"], []))  # Junior / Mid / Senior, computed from the levels on every read
        if q and q.lower() not in f"{e['name']} {e['employee_no'] or ''} {' '.join(e['roles'])}".lower():
            continue
        if role and role.lower() not in [x.lower() for x in e["roles"]]:
            continue
        out.append(e)
    return out


def get_employee(emp_id: int) -> dict | None:
    e = db.one("SELECT * FROM employees WHERE id=?", (emp_id,))
    if not e:
        return None
    e["roles"] = [r["role"] for r in db.rows("SELECT role FROM employee_roles WHERE employee_id=? ORDER BY role", (emp_id,))]
    e["domain_tags"] = [r["tag"] for r in db.rows(
        "SELECT tag FROM employee_domain_tags WHERE employee_id=? ORDER BY tag", (emp_id,))]
    e["skills"] = db.rows(
        "SELECT raw_skill, canonical_skill, category, rating, method, confidence, needs_review "
        "FROM employee_skills WHERE employee_id=? ORDER BY category, canonical_skill", (emp_id,))
    e["seniority"] = seniority.assess([s["rating"] for s in e["skills"]])
    return e


def all_profiles() -> list[dict]:
    """Every employee with roles and skills (used by gap / staffing computations)."""
    emps = db.rows("SELECT id, employee_no, name, background FROM employees ORDER BY name")
    by_id = {e["id"]: {**e, "roles": [], "domain_tags": [], "skills": {}} for e in emps}
    for r in db.rows("SELECT employee_id, role FROM employee_roles"):
        by_id[r["employee_id"]]["roles"].append(r["role"])
    for r in db.rows("SELECT employee_id, tag FROM employee_domain_tags"):
        by_id[r["employee_id"]]["domain_tags"].append(r["tag"])
    for s in db.rows("SELECT employee_id, canonical_skill, rating, category FROM employee_skills"):
        by_id[s["employee_id"]]["skills"][s["canonical_skill"]] = {"rating": s["rating"], "category": s["category"]}
    return list(by_id.values())


def matrix(category: str = "", role: str = "") -> dict:
    emps = [e for e in list_employees(role=role)]
    ids = {e["id"] for e in emps}
    skills_rows = db.rows(
        "SELECT canonical_skill AS name, category, COUNT(*) AS holders FROM employee_skills "
        "GROUP BY canonical_skill, category ORDER BY category, holders DESC, name")
    if category:
        skills_rows = [s for s in skills_rows if s["category"] == category]
    cells: dict[str, dict[str, int | None]] = {}
    for r in db.rows("SELECT employee_id, canonical_skill, rating FROM employee_skills"):
        if r["employee_id"] in ids:
            cells.setdefault(str(r["employee_id"]), {})[r["canonical_skill"]] = r["rating"]
    return {"skills": skills_rows, "employees": emps, "cells": cells}


def dashboard() -> dict:
    n_emp = db.one("SELECT COUNT(*) AS n FROM employees")["n"]
    n_skill = db.one("SELECT COUNT(DISTINCT canonical_skill) AS n FROM employee_skills")["n"]
    review = db.one("SELECT COUNT(*) AS n FROM employee_skills WHERE needs_review=1")["n"]
    unrated = db.one("SELECT COUNT(*) AS n FROM employee_skills WHERE rating IS NULL")["n"]
    avg = db.one("SELECT ROUND(AVG(rating),2) AS a FROM employee_skills")["a"]
    top = db.rows("SELECT canonical_skill AS skill, category, COUNT(*) AS holders, ROUND(AVG(rating),2) AS avg_rating "
                  "FROM employee_skills GROUP BY canonical_skill, category ORDER BY holders DESC LIMIT 10")
    thin = db.rows("SELECT canonical_skill AS skill, category, COUNT(*) AS holders FROM employee_skills "
                   "WHERE rating>=3 GROUP BY canonical_skill, category HAVING holders=1 ORDER BY canonical_skill LIMIT 10")
    cats = db.rows("SELECT category, COUNT(DISTINCT canonical_skill) AS skills, ROUND(AVG(rating),2) AS avg_rating "
                   "FROM employee_skills GROUP BY category ORDER BY skills DESC")
    dist = {r["rating"]: r["n"] for r in db.rows("SELECT rating, COUNT(*) AS n FROM employee_skills WHERE rating IS NOT NULL GROUP BY rating")}
    return {"employees": n_emp, "skills": n_skill, "needs_review": review, "unrated": unrated, "avg_rating": avg,
            "top_skills": top, "single_point_of_failure": thin, "categories": cats,
            "rating_distribution": [{"rating": i, "count": dist.get(i, 0)} for i in range(1, 5)]}


# --------------------------------------------------------------------------- writes
def commit_records(records: list[dict], upload_id: int | None, mode: str = "merge") -> dict:
    if mode == "replace":
        reset_all(keep_taxonomy=True)
    created = updated = n_skills = 0
    touched: list[int] = []
    # a skill rated 0 means "no skill": it is not stored (a missing skill and a 0 are the same thing in the matrix)
    skipped_none = sum(1 for rec in records for sk in rec["skills"] if sk["rating"] == 0)
    records = [{**rec, "skills": [sk for sk in rec["skills"] if sk["rating"] != 0]} for rec in records]
    memo = standardization.normalize_many([sk["raw_skill"] for rec in records for sk in rec["skills"]])
    for rec in records:
        # normalise + merge duplicate canonicals (keep the highest rating)
        merged: dict[str, dict] = {}
        for sk in rec["skills"]:
            raw = sk["raw_skill"]
            n = memo[raw]
            cur = merged.get(n["canonical"])
            if cur is None:
                merged[n["canonical"]] = {**n, "raw_skill": raw, "rating": sk["rating"]}
            else:
                ratings = [x for x in (cur["rating"], sk["rating"]) if x is not None]
                cur["rating"] = max(ratings) if ratings else None

        with db.connect() as c:
            if rec.get("employee_no"):
                ex = c.execute("SELECT id FROM employees WHERE employee_no=?", (rec["employee_no"],)).fetchone()
            else:
                ex = c.execute("SELECT id FROM employees WHERE employee_no IS NULL AND lower(name)=lower(?)", (rec["name"],)).fetchone()
            if ex:
                emp_id = ex["id"]
                c.execute("UPDATE employees SET name=?, background=?, source_upload_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                          (rec["name"], rec.get("background", ""), upload_id, emp_id))
                c.execute("DELETE FROM employee_roles WHERE employee_id=?", (emp_id,))
                c.execute("DELETE FROM employee_skills WHERE employee_id=?", (emp_id,))
                if rec.get("domain_tags_provided") or rec.get("domain_tags"):
                    c.execute("DELETE FROM employee_domain_tags WHERE employee_id=?", (emp_id,))
                updated += 1
            else:
                emp_id = c.execute("INSERT INTO employees(employee_no, name, background, source_upload_id) VALUES (?,?,?,?)",
                                   (rec.get("employee_no"), rec["name"], rec.get("background", ""), upload_id)).lastrowid
                created += 1
            for role in rec.get("roles", []):
                c.execute("INSERT OR IGNORE INTO employee_roles(employee_id, role) VALUES (?,?)", (emp_id, role))
            for tag in dict.fromkeys(str(tag).strip() for tag in rec.get("domain_tags", []) if str(tag).strip()):
                c.execute("INSERT OR IGNORE INTO employee_domain_tags(employee_id, tag) VALUES (?,?)", (emp_id, tag))
            for canonical, m in merged.items():
                c.execute("INSERT INTO employee_skills(employee_id, raw_skill, canonical_skill, category, rating, method, confidence, needs_review) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (emp_id, m["raw_skill"], canonical, m["category"], m["rating"], m["method"], m["confidence"], int(m["needs_review"])))
                n_skills += 1
        touched.append(emp_id)

    index_error = None
    try:
        reindex_employees(touched)
    except Exception as e:  # noqa: BLE001
        index_error = str(e)
        log.warning("Employee indexing failed: %s", e)
    review = db.one("SELECT COUNT(*) AS n FROM employee_skills WHERE needs_review=1")["n"]
    counts: dict[str, int] = {}
    for rec in records:
        label = seniority.assess([sk["rating"] for sk in rec["skills"]])["level"]
        counts[label] = counts.get(label, 0) + 1
    return {"created": created, "updated": updated, "skills_imported": n_skills, "needs_review": review,
            "index_error": index_error, "skipped_level_0": skipped_none, "seniority_counts": counts}


def apply_cv_profile(rec: dict, upload_id: int | None = None) -> dict:
    """Add ONE person from an approved CV.
    A NEW person is created exactly like an upload creates one.
    An EXISTING person (same employee number, or the one person with that name) is only ADDED to: skills already in the matrix keep their
    rating, new skills are added, new roles are added, the biography is kept unless it is empty. A CV never lowers or removes anything."""
    name = (rec.get("name") or "").strip()
    if not name:
        raise ValueError("The CV record needs a name")
    skills = [{"raw_skill": s["raw_skill"], "rating": s["rating"]} for s in rec.get("skills", []) if s.get("rating")]
    if rec.get("employee_no"):
        found = db.rows("SELECT id FROM employees WHERE employee_no=?", (rec["employee_no"],))
    else:
        found = db.rows("SELECT id FROM employees WHERE lower(name)=lower(?)", (name,))
    if len(found) > 1:
        raise ValueError(f"Several employees are called {name}; add an employee number to the record")
    if not found:
        res = commit_records([{**rec, "name": name, "skills": skills}], upload_id, "merge")
        row = (db.one("SELECT id FROM employees WHERE employee_no=?", (rec["employee_no"],)) if rec.get("employee_no")
               else db.one("SELECT id FROM employees WHERE lower(name)=lower(?) ORDER BY id DESC", (name,)))
        return {"created": True, "employee_id": row["id"] if row else None, "skills_added": res["skills_imported"], "skills_kept": 0}
    return apply_cv_to_employee(found[0]["id"], {**rec, "skills": skills})


def apply_cv_to_employee(emp_id: int, rec: dict) -> dict:
    """Add a CV's skills and roles to ONE existing employee (found by id, never by name). Nothing is lowered or removed."""
    skills = [{"raw_skill": s["raw_skill"], "rating": s["rating"]} for s in rec.get("skills", []) if s.get("rating")]
    if not db.one("SELECT 1 AS x FROM employees WHERE id=?", (emp_id,)):
        raise ValueError("The employee no longer exists")
    have = {r["canonical_skill"] for r in db.rows("SELECT canonical_skill FROM employee_skills WHERE employee_id=?", (emp_id,))}
    memo = standardization.normalize_many([s["raw_skill"] for s in skills])
    counts = roles_svc.role_counts()
    cv_roles = roles_svc.canonical_list(rec.get("roles", []), counts)
    added = kept = 0
    seen: set[str] = set()
    with db.connect() as c:
        for s in sorted(skills, key=lambda x: -x["rating"]):
            n = memo[s["raw_skill"]]
            if n["canonical"] in have or n["canonical"] in seen:
                kept += 1
                continue
            seen.add(n["canonical"])
            c.execute("INSERT INTO employee_skills(employee_id, raw_skill, canonical_skill, category, rating, method, confidence, needs_review) "
                      "VALUES (?,?,?,?,?,?,?,?)", (emp_id, s["raw_skill"], n["canonical"], n["category"], s["rating"], n["method"],
                                                   n["confidence"], int(n["needs_review"])))
            added += 1
        have_roles = {roles_svc.key(r["role"]) for r in c.execute("SELECT role FROM employee_roles WHERE employee_id=?", (emp_id,))}
        for role in cv_roles:
            if roles_svc.key(role) not in have_roles:
                c.execute("INSERT OR IGNORE INTO employee_roles(employee_id, role) VALUES (?,?)", (emp_id, role))
        if rec.get("background"):
            c.execute("UPDATE employees SET background=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND COALESCE(background,'')=''",
                      (rec["background"], emp_id))
    try:
        reindex_employees([emp_id])
    except Exception as e:  # noqa: BLE001
        log.warning("Employee indexing failed: %s", e)
    return {"created": False, "employee_id": emp_id, "skills_added": added, "skills_kept": kept}


def apply_member_update(emp_id: int, ratings: list[dict], roles: list[str] | None = None, background: str | None = None,
                        domain_tags: list[str] | None = None) -> dict:
    """Apply an APPROVED change that a member made to their own record.
    ratings: [{skill, rating 0-4}]. A level of 1-4 sets that skill (added when new); 0 removes it (0 means "no skill", it is never stored).
    roles / domain_tags / background: replaced when given, untouched when None. Name and employee number are never changed here."""
    if not db.one("SELECT 1 AS x FROM employees WHERE id=?", (emp_id,)):
        raise ValueError("The employee no longer exists")
    memo = standardization.normalize_many([r["skill"] for r in ratings]) if ratings else {}
    if roles is not None:
        counts = roles_svc.role_counts()
        roles = roles_svc.canonical_list(roles, counts)
    changed = removed = added = 0
    with db.connect() as c:
        for r in ratings:
            n = memo[r["skill"]]
            canon = n["canonical"]
            row = c.execute("SELECT id, rating FROM employee_skills WHERE employee_id=? AND canonical_skill=?", (emp_id, canon)).fetchone()
            if r["rating"] == 0:
                if row:
                    c.execute("DELETE FROM employee_skills WHERE employee_id=? AND canonical_skill=?", (emp_id, canon))
                    removed += 1
            elif row:
                if row["rating"] != r["rating"]:
                    c.execute("UPDATE employee_skills SET rating=? WHERE id=?", (r["rating"], row["id"]))
                    changed += 1
            else:
                c.execute("INSERT INTO employee_skills(employee_id, raw_skill, canonical_skill, category, rating, method, confidence, needs_review) "
                          "VALUES (?,?,?,?,?,?,?,?)", (emp_id, r["skill"], canon, n["category"], r["rating"], n["method"], n["confidence"],
                                                       int(n["needs_review"])))
                added += 1
        if roles is not None:
            c.execute("DELETE FROM employee_roles WHERE employee_id=?", (emp_id,))
            for role in roles:
                c.execute("INSERT OR IGNORE INTO employee_roles(employee_id, role) VALUES (?,?)", (emp_id, role))
        if domain_tags is not None:
            c.execute("DELETE FROM employee_domain_tags WHERE employee_id=?", (emp_id,))
            for tag in domain_tags:
                c.execute("INSERT OR IGNORE INTO employee_domain_tags(employee_id, tag) VALUES (?,?)", (emp_id, tag))
        if background is not None:
            c.execute("UPDATE employees SET background=? WHERE id=?", (background, emp_id))
        c.execute("UPDATE employees SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (emp_id,))
    try:
        reindex_employees([emp_id])
    except Exception as e:  # noqa: BLE001
        log.warning("Employee indexing failed: %s", e)
    return {"employee_id": emp_id, "ratings_changed": changed, "skills_added": added, "skills_removed": removed}


def set_roles(emp_id: int, new_roles: list[str]) -> dict:
    """Replace one employee's roles (one spelling per role). -> {before, after}."""
    if not db.one("SELECT 1 AS x FROM employees WHERE id=?", (emp_id,)):
        raise LookupError("Employee not found")
    counts = roles_svc.role_counts()
    after = roles_svc.canonical_list(new_roles, counts)
    before = [r["role"] for r in db.rows("SELECT role FROM employee_roles WHERE employee_id=? ORDER BY role", (emp_id,))]
    with db.connect() as c:
        c.execute("DELETE FROM employee_roles WHERE employee_id=?", (emp_id,))
        for role in after:
            c.execute("INSERT OR IGNORE INTO employee_roles(employee_id, role) VALUES (?,?)", (emp_id, role))
        c.execute("UPDATE employees SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (emp_id,))
    try:
        reindex_employees([emp_id])
    except Exception as e:  # noqa: BLE001
        log.warning("Employee indexing failed: %s", e)
    return {"before": before, "after": sorted(after)}


def apply_role_changes(emp_id: int, changes: list[dict]) -> dict:
    """Apply APPROVED role changes (rename / add / remove) to one employee. A change that no longer fits (the role is gone) is skipped."""
    emp = db.rows("SELECT role FROM employee_roles WHERE employee_id=?", (emp_id,))
    current = [r["role"] for r in emp]
    return set_roles(emp_id, roles_svc.apply_to_list(current, changes))


def employee_document(emp: dict) -> str:
    skills = ", ".join(f"{s['canonical_skill']} {s['rating']}/4" if s["rating"] else s["canonical_skill"] for s in emp["skills"])
    tags = ", ".join(emp.get("domain_tags", [])) or "n/a"
    return f"{emp['name']}. Roles: {', '.join(emp['roles']) or 'n/a'}. Skills: {skills or 'n/a'}. Domain tags: {tags}. Background: {emp.get('background') or 'n/a'}"


def _employee_entities(emp: dict) -> tuple[list[str], list[str], list[dict]]:
    employee_id = int(emp["id"])
    parent_id = f"employee:{employee_id}:parent"
    ids = [parent_id]
    docs = [employee_document(emp)]
    metas = [{"entity_type": "employee_parent", "employee_id": employee_id, "name": emp["name"]}]

    def add(entity_type: str, identity: str, document: str, **fields) -> None:
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        ids.append(f"employee:{employee_id}:{entity_type}:{suffix}")
        docs.append(document)
        metas.append({
            "entity_type": entity_type,
            "employee_id": employee_id,
            "parent_id": parent_id,
            **{key: value for key, value in fields.items() if value is not None},
        })

    roles = emp.get("roles") or []
    tags = emp.get("domain_tags") or []
    for skill in emp.get("skills") or []:
        canonical = skill["canonical_skill"]
        rating = skill.get("rating")
        category = skill.get("category") or "Other"
        role_variants = roles or [None]
        tag_variants = tags or [None]
        for role in role_variants:
            for tag in tag_variants:
                details = [f"Skill: {canonical}"]
                if rating is not None:
                    details.append(f"Rating: {rating}/4")
                if role:
                    details.append(f"Role: {role}")
                if tag:
                    details.append(f"Domain: {tag}")
                add(
                    "employee_skill",
                    f"skill:{canonical}:{rating}:{role}:{tag}",
                    f"{emp['name']}. " + ". ".join(details) + ".",
                    canonical_skill=canonical,
                    rating=rating,
                    role=role,
                    domain_tag=tag,
                    category=category,
                )
    for role in roles:
        add("employee_role", f"role:{role}", f"{emp['name']}. Role: {role}.", role=role)
    for tag in tags:
        add("employee_domain", f"domain:{tag}", f"{emp['name']}. Domain expertise: {tag}.", domain_tag=tag)
    if emp.get("background"):
        add("employee_background", "background", f"{emp['name']}. Background: {emp['background']}")
    return ids, docs, metas


def reindex_employees(ids: list[int] | None = None) -> int:
    if ids is None:
        employee_ids = [e["id"] for e in db.rows("SELECT id FROM employees")]
        entity_ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        for employee_id in employee_ids:
            emp = get_employee(employee_id)
            if emp:
                emp_ids, emp_docs, emp_metas = _employee_entities(emp)
                entity_ids.extend(emp_ids)
                docs.extend(emp_docs)
                metas.extend(emp_metas)
        vectorstore.replace_where("employees", None, entity_ids, docs, metas)
        return len(employee_ids)

    indexed = 0
    for i in ids:
        emp = get_employee(i)
        if not emp:
            vectorstore.delete_where("employees", {"employee_id": int(i)})
            continue
        entity_ids, docs, metas = _employee_entities(emp)
        vectorstore.replace_where(
            "employees", {"employee_id": int(i)}, entity_ids, docs, metas
        )
        indexed += 1
    return indexed


def delete_employee(emp_id: int) -> None:
    with db.connect() as c:
        c.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    try:
        vectorstore.delete_where("employees", {"employee_id": int(emp_id)})
    except Exception as e:  # noqa: BLE001
        log.warning("vector delete failed: %s", e)


def reset_all(keep_taxonomy: bool = True) -> None:
    with db.connect() as c:
        c.execute("DELETE FROM employees")
        if not keep_taxonomy:
            c.execute("DELETE FROM mapping_overrides")
    try:
        vectorstore.reset("employees")
    except Exception as e:  # noqa: BLE001
        log.warning("vector reset failed: %s", e)
