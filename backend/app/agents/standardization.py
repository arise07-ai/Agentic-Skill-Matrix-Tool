"""Standardization agent: clears the review queue as far as is safe, and proposes the rest.

Deterministic step first (no model): a spelling whose normalisation is now an EXACT match, or has an earlier human decision
(manual override), is applied automatically and logged as 'auto_applied'. Everything else goes to the model, which can only propose.
"""
from .. import db
from ..services import repo, requested, standardization
from . import prompts, proposals, tools
from .runtime import AgentContext, AgentDef, loop

MAX_ITEMS = 30


def _pending() -> list[dict]:
    rows = db.rows("SELECT MIN(raw_skill) AS raw, canonical_skill AS current, method, COUNT(*) AS occurrences FROM employee_skills "
                   "WHERE needs_review=1 GROUP BY lower(raw_skill), canonical_skill ORDER BY occurrences DESC")
    seen = {r["raw"].lower() for r in rows}
    for r in requested.list_requested("pending"):  # skills projects asked for that the taxonomy lacks
        if r["name"].lower() not in seen:
            rows.append({"raw": r["name"], "current": None, "method": "requested by projects", "occurrences": r["times_requested"]})
    return rows


def _auto_apply(ctx: AgentContext, item: dict) -> bool:
    res = standardization.normalize(item["raw"])
    if res["method"] not in ("exact", "manual") or res["confidence"] < 1.0:
        return False
    with db.connect() as c:
        c.execute("UPDATE employee_skills SET canonical_skill=?, category=?, method=?, confidence=1.0, needs_review=0 "
                  "WHERE lower(raw_skill)=lower(?) AND needs_review=1", (res["canonical"], res["category"], res["method"], item["raw"]))
    label = "earlier human decision" if res["method"] == "manual" else "exact match"
    proposals.log_auto_applied(ctx, "skill_mapping", f"{item['raw']} → {res['canonical']}",
                               {"raw_skill": item["raw"], "canonical": res["canonical"], "category": res["category"]}, label)
    try:  # a project-requested name that is now known no longer needs approval
        requested.decide(item["raw"], "approve", res["canonical"], res["category"])
    except LookupError:
        pass
    return True


def run(ctx: AgentContext, inp: dict) -> dict:
    items = _pending()
    ctx.step("info", f"{len(items)} spellings waiting for review")
    auto, rest = [], []
    for it in items:
        (auto if _auto_apply(ctx, it) else rest).append(it)
    if auto:
        repo.reindex_employees()
        ctx.step("info", f"Applied {len(auto)} exact / earlier-decision matches automatically")
    batch = rest[:MAX_ITEMS]
    if not batch:
        return {"summary": f"{len(auto)} applied automatically, nothing left to review.", "auto_applied": len(auto), "proposals": 0}
    lines = "\n".join(f"- \"{b['raw']}\" (used {b['occurrences']}x; app guess: {b['current'] or 'none'}, {b['method']})" for b in batch)
    msg = f"Spellings to resolve ({len(batch)} of {len(rest)} waiting):\n{lines}"
    messages = [{"role": "system", "content": prompts.STANDARDIZATION}, {"role": "user", "content": msg}]
    final = loop(ctx, AGENT, messages)
    return {"summary": final, "auto_applied": len(auto), "considered": len(batch), "left_over": max(len(rest) - len(batch), 0)}


def _tools():
    from . import websearch

    t = [tools.t_search_taxonomy(), tools.t_employee_usage(), tools.t_propose_mapping()]
    if websearch.is_configured():
        t.append(tools.t_web_lookup())
    return t


AGENT = AgentDef("standardization", "Skill standardization", prompts.STANDARDIZATION, _tools, run)
