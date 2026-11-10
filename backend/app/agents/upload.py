"""Upload check agent: an extra step before importing. Reads the parsed file, looks for mistakes, tests alternative
settings with dry runs, and proposes a fix (HR approves). It never imports and never edits the file."""
import json
from collections import Counter

from .. import db
from ..services import repo
from . import prompts, proposals, tools
from .runtime import AgentContext, AgentDef, AgentError, Tool, loop


def _parse(upload_id: int, override: dict | None = None) -> tuple[dict, dict]:
    from pathlib import Path

    from ..services.parsing.excel_parser import parse_excel
    from ..services.parsing.pdf_parser import parse_pdf

    up = db.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not up:
        raise AgentError("Upload not found")
    st = json.loads(up["settings_json"] or "{}")
    if override:
        st = {**st, **{k: v for k, v in override.items() if v}}
        if override.get("sheet") and override["sheet"] != json.loads(up["settings_json"] or "{}").get("sheet"):
            st["mapping"], st["layout"] = override.get("mapping"), override.get("layout")
    path = Path(up["path"])
    parsed = parse_excel(path, st.get("sheet"), st.get("mapping"), st.get("layout")) if up["kind"] == "excel" else parse_pdf(path, st.get("mapping"), st.get("layout"))
    return up, parsed


def _summary(parsed: dict) -> dict:
    recs = parsed["records"]
    return {"employees": len(recs), "skills": sum(len(r["skills"]) for r in recs), "sheet": parsed.get("sheet"), "layout": parsed.get("layout"),
            "mapping": parsed.get("mapping"), "columns": parsed.get("columns", [])[:30], "sheets": parsed.get("sheets", []),
            "warnings": parsed.get("warnings", [])[:15], "warnings_total": len(parsed.get("warnings", []))}


def overview(ctx: AgentContext, a: dict) -> dict:
    up, parsed = _parse(ctx.input["upload_id"])
    sample = [{"name": r["name"], "employee_no": r["employee_no"], "roles": r["roles"], "domain_tags": r.get("domain_tags", []), "skills": r["skills"][:6],
               "biography_untrusted_text": r["background"][:200]} for r in parsed["records"][:3]]
    return {"file": up["filename"], "type": up["kind"], "status": up["status"], **_summary(parsed), "sample_records": sample}


def source_stats(ctx: AgentContext, a: dict) -> dict:
    _, parsed = _parse(ctx.input["upload_id"], a.get("settings") if isinstance(a.get("settings"), dict) else None)
    recs = parsed["records"]
    nos = Counter(r["employee_no"] for r in recs if r["employee_no"])
    ratings = Counter(s["rating"] for r in recs for s in r["skills"])
    existing = {r["employee_no"] for r in db.rows("SELECT employee_no FROM employees WHERE employee_no IS NOT NULL")}
    per = [len(r["skills"]) for r in recs]
    return {"employees": len(recs),
            "without_skills": [r["name"] for r in recs if not r["skills"]][:10],
            "without_employee_no": sum(1 for r in recs if not r["employee_no"]),
            "duplicate_employee_numbers": [n for n, c in nos.items() if c > 1][:10],
            "already_in_database_would_update": sum(1 for r in recs if r["employee_no"] in existing),
            "skills_without_rating": ratings.get(None, 0), "rating_distribution": {str(k): v for k, v in sorted(ratings.items(), key=lambda kv: str(kv[0]))},
            "skills_per_employee": {"min": min(per, default=0), "max": max(per, default=0), "avg": round(sum(per) / len(per), 1) if per else 0},
            "employees_without_roles": sum(1 for r in recs if not r["roles"]),
            "employees_without_biography": sum(1 for r in recs if not r["background"])}


def try_parse(ctx: AgentContext, a: dict) -> dict:
    """Dry run with other settings. Nothing is saved."""
    _, parsed = _parse(ctx.input["upload_id"], {"sheet": a.get("sheet") or None, "layout": a.get("layout") or None,
                                                "mapping": a.get("mapping") or None})
    return _summary(parsed)


def _ask(ctx: AgentContext, a: dict) -> dict:
    ctx.state["questions"] = ctx.state.get("questions", 0) + 1
    if ctx.state["questions"] > 2:
        return {"error": "Question limit reached."}
    return {"answer": ctx.ask_user(str(a.get("question", ""))[:500]) or "(no answer)"}


def _propose(ctx: AgentContext, a: dict) -> dict:
    mapping = a.get("mapping") if isinstance(a.get("mapping"), dict) else None
    return proposals.create(ctx, "upload_fix", str(a.get("title") or "Better parsing settings"),
                            {"upload_id": ctx.input["upload_id"], "sheet": a.get("sheet") or None, "layout": a.get("layout") or None, "mapping": mapping},
                            str(a.get("evidence", "")), None)


def _tools() -> list[Tool]:
    o, S = tools._obj, tools.S
    return [
        Tool("upload_overview", "What was read from the file with the current settings, plus 3 sample records.", o({}, []), overview),
        Tool("source_stats", "Quality statistics of the parsed records (missing skills, ratings, duplicates, existing employees).",
             o({"settings": {"type": "object", "properties": {"sheet": S, "layout": S}}}, []), source_stats),
        Tool("try_parse", "Dry run with another sheet/layout ('wide'|'long')/column mapping ({field: column header}). Nothing is saved.",
             o({"sheet": S, "layout": S, "mapping": {"type": "object"}}, []), try_parse),
        Tool("ask_user", "Ask the person one short question and wait for the answer.", o({"question": S}), _ask),
        Tool("propose_upload_fix", "Propose better settings (sheet/layout/mapping) for this file. Call only when try_parse showed an improvement.",
             o({"title": S, "sheet": S, "layout": S, "mapping": {"type": "object"}, "evidence": S}, ["title", "evidence"]), _propose),
    ]


def run(ctx: AgentContext, inp: dict) -> dict:
    try:
        uid = int(inp.get("upload_id"))
    except (TypeError, ValueError):
        raise AgentError("Choose an uploaded file first") from None
    inp["upload_id"] = uid
    _parse(uid)  # fail early with a clear message if the file cannot be read
    messages = [{"role": "system", "content": prompts.UPLOAD}, {"role": "user", "content": f"Check upload {uid}."}]
    return {"summary": loop(ctx, AGENT, messages), "upload_id": uid}


AGENT = AgentDef("upload", "Upload check", prompts.UPLOAD, _tools, run)
