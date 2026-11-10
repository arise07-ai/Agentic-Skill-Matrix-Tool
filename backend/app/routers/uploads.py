import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import db
from ..config import get_settings
from ..deps import current_user
from types import SimpleNamespace

from ..agents import proposals
from ..ai import AINotConfigured
from ..ai.guardrails import GuardrailViolation
from ..schemas import CommitRequest, CVSubmitRequest, ReparseRequest
from ..services import auth, repo, seniority
from ..services.parsing import cv_parser
from ..services.parsing.excel_parser import parse_excel
from ..services.parsing.pdf_parser import parse_pdf

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
EXCEL = {".xlsx", ".xlsm"}
CV_EXT = {".pdf", ".docx"}


def _parse(up: dict, settings: dict) -> dict:
    path = Path(up["path"])
    if up["kind"] == "excel":
        return parse_excel(path, settings.get("sheet"), settings.get("mapping"), settings.get("layout"))
    return parse_pdf(path, settings.get("mapping"), settings.get("layout"))


def _preview(upload_id: int, up: dict, parsed: dict) -> dict:
    recs = parsed["records"]
    shown = [{**r, "seniority": seniority.assess_record(r)} for r in recs[:200]]  # Junior / Mid / Senior from the levels, by code
    counts: dict[str, int] = {}
    for r in recs:
        level = seniority.assess_record(r)["level"]
        counts[level] = counts.get(level, 0) + 1
    out_of_scale = sum(1 for w in parsed.get("warnings", []) if "0-4 scale" in w)
    return {
        "upload_id": upload_id, "filename": up["filename"], "kind": up["kind"], "source": parsed.get("source", "excel"),
        "sheets": parsed.get("sheets", []), "sheet": parsed.get("sheet"), "columns": parsed.get("columns", []),
        "mapping": parsed.get("mapping", {}), "layout": parsed.get("layout", "wide"),
        "records_count": len(recs), "skills_count": sum(len(r["skills"]) for r in recs),
        "records": shown, "seniority_counts": counts, "out_of_scale_warnings": out_of_scale, "preview_rows": parsed.get("preview_rows", []), "warnings": parsed.get("warnings", [])[:100],
    }


@router.get("")
def list_uploads():
    return db.rows("SELECT id, filename, kind, status, created_at FROM uploads WHERE kind != 'cv' ORDER BY id DESC LIMIT 50")


@router.post("")
async def upload(file: UploadFile = File(...)):
    s = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in EXCEL and ext != ".pdf":
        raise HTTPException(400, "Unsupported file type. Upload .xlsx, .xlsm or .pdf")
    data = await file.read()
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {s.max_upload_mb} MB")
    dest = s.upload_dir / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)
    kind = "pdf" if ext == ".pdf" else "excel"
    with db.connect() as c:
        uid = c.execute("INSERT INTO uploads(filename, kind, path) VALUES (?,?,?)", (file.filename, kind, str(dest))).lastrowid
    up = db.one("SELECT * FROM uploads WHERE id=?", (uid,))
    try:
        parsed = _parse(up, {})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Could not read the file: {e}")
    with db.connect() as c:
        c.execute("UPDATE uploads SET settings_json=? WHERE id=?",
                  (json.dumps({"sheet": parsed.get("sheet"), "mapping": parsed.get("mapping"), "layout": parsed.get("layout")}), uid))
    return _preview(uid, up, parsed)


@router.post("/{upload_id}/reparse")
def reparse(upload_id: int, body: ReparseRequest):
    up = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not up:
        raise HTTPException(404, "Upload not found")
    prev = json.loads(up["settings_json"] or "{}")
    settings = {"sheet": body.sheet or prev.get("sheet"), "mapping": body.mapping or prev.get("mapping"),
                "layout": body.layout or prev.get("layout")}
    if body.sheet and body.sheet != prev.get("sheet"):  # new sheet: re-detect columns and layout
        settings["mapping"], settings["layout"] = body.mapping, body.layout
    parsed = _parse(up, settings)
    with db.connect() as c:
        c.execute("UPDATE uploads SET settings_json=? WHERE id=?",
                  (json.dumps({"sheet": parsed.get("sheet"), "mapping": parsed.get("mapping"), "layout": parsed.get("layout")}), upload_id))
    return _preview(upload_id, up, parsed)


@router.post("/{upload_id}/commit")
def commit(upload_id: int, body: CommitRequest, user: dict = Depends(current_user)):
    up = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not up:
        raise HTTPException(404, "Upload not found")
    parsed = _parse(up, json.loads(up["settings_json"] or "{}"))
    if not parsed["records"]:
        raise HTTPException(422, "No employee records to import. Check the column mapping.")
    result = repo.commit_records(parsed["records"], upload_id, body.mode)
    auth.audit(user, "upload_imported", {"upload_id": upload_id, "mode": body.mode, **{k: result[k] for k in ("created", "updated", "skills_imported")}})
    with db.connect() as c:
        c.execute("UPDATE uploads SET status='imported' WHERE id=?", (upload_id,))
    return result


# ----------------------------------------------------------------------------- one CV (PDF or Word)
def _existing_employee(name: str | None, employee_no: str | None) -> dict | None:
    if employee_no:
        rows = db.rows("SELECT id, name, employee_no FROM employees WHERE employee_no=?", (employee_no,))
    elif name:
        rows = db.rows("SELECT id, name, employee_no FROM employees WHERE lower(name)=lower(?)", (name,))
    else:
        return None
    return rows[0] if len(rows) == 1 else ({"id": None, "name": name, "ambiguous": True} if len(rows) > 1 else None)


@router.post("/cv")
async def upload_cv(file: UploadFile = File(...), user: dict = Depends(current_user)):
    """Read one CV and PROPOSE a record: the model estimates levels (0-4, never above 3), code checks every quote and computes the
    seniority. Nothing is saved to the matrix here."""
    return await cv_preview(file, user)


async def cv_preview(file: UploadFile, user: dict) -> dict:
    """Shared by this page and the member page (routers/member.py)."""
    s = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in CV_EXT:
        raise HTTPException(400, "A CV must be a PDF or a Word .docx file")
    data = await file.read()
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {s.max_upload_mb} MB")
    dest = s.upload_dir / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)
    with db.connect() as c:
        uid = c.execute("INSERT INTO uploads(filename, kind, path, uploaded_by) VALUES (?,?,?,?)",
                         (file.filename, "cv", str(dest), user["id"])).lastrowid
    try:
        cv = cv_parser.read_cv(dest, file.filename or dest.name)
    except cv_parser.CVError as e:
        raise HTTPException(422, str(e))
    except AINotConfigured as e:
        raise HTTPException(503, f"Reading a CV needs the AI model, which is not set up: {e}")
    except GuardrailViolation as e:
        raise HTTPException(400, str(e))
    assessment = seniority.assess([x["rating"] for x in cv["skills"]])
    text, source = seniority.explain(assessment, cv["skills"])
    return {"upload_id": uid, "filename": file.filename, "candidate": cv, "seniority": assessment, "seniority_explanation": text,
            "seniority_explanation_source": source, "existing_employee": _existing_employee(cv["name"], cv["employee_no"]),
            "max_estimated_level": cv_parser.CV_MAX_LEVEL}


def check_cv_skills(up: dict, body_skills) -> tuple[list[dict], int]:
    """Re-check every evidence quote against the stored CV text. -> (skills, how many quotes were found)."""
    try:
        text, _ = cv_parser.extract_text(Path(up["path"]), up["filename"])
    except cv_parser.CVError as e:
        raise HTTPException(422, str(e))
    norm = cv_parser._norm(text)
    skills = []
    for sk in body_skills:
        ok = cv_parser.quote_in_text(sk.evidence, norm)
        skills.append({"raw_skill": sk.raw_skill, "rating": sk.rating or 0, "evidence": sk.evidence if ok else "", "needs_review": not ok})
    return skills, sum(1 for sk in skills if not sk["needs_review"])


@router.post("/cv/{upload_id}/submit")
def submit_cv(upload_id: int, body: CVSubmitRequest, user: dict = Depends(current_user)):
    """Send the reviewed CV record to the approval queue (an HR manager or admin decides). Quotes are re-checked against the stored CV."""
    up = db.one("SELECT * FROM uploads WHERE id=? AND kind='cv'", (upload_id,))
    if not up:
        raise HTTPException(404, "CV upload not found")
    skills, verified = check_cv_skills(up, body.skills)
    assessment = seniority.assess([sk["rating"] for sk in skills])
    payload = {"name": body.name, "employee_no": body.employee_no, "roles": body.roles, "background": body.background,
               "skills": skills, "upload_id": upload_id, "seniority": assessment}
    ctx = SimpleNamespace(run_id=None, agent="cv_upload", proposal_ids=[])
    try:
        res = proposals.create(ctx, "employee_profile", f"CV: {body.name}", payload,
                               evidence=f"Levels estimated by the AI from {up['filename']}, reviewed by {user['username']}. "
                                        f"{verified} of {len(skills)} skills have a quote that was found in the CV. "
                                        f"Seniority by rule: {assessment['level']}.",
                               confidence=verified / len(skills) if skills else 0)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.connect() as c:
        c.execute("UPDATE uploads SET status='submitted' WHERE id=?", (upload_id,))
    auth.audit(user, "cv_submitted", {"upload_id": upload_id, "proposal": res.get("proposal_id"), "name": body.name})
    return res
