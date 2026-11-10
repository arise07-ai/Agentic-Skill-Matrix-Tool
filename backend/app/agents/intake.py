"""Project intake agent: free text -> structured project brief (proposal for a PM / team leader to approve)."""
import re

from ..services import requested
from . import prompts, proposals, tools
from .runtime import AgentContext, AgentDef, AgentError, Tool, loop

MAX_QUESTIONS = 3
_COUNT = r"(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)"
_PEOPLE = r"(?:people|persons|engineers?|developers?|analysts?|consultants?|members?|staff|resources?|positions?|roles?)"
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _count(value: str) -> int:
    return int(value) if value.isdigit() else _NUMBER_WORDS[value.lower()]


def _explicit_skill_count(skill: str, text: str) -> int | None:
    name = re.escape(skill.strip())
    patterns = (
        rf"\b({_COUNT})\s+{_PEOPLE}\s+(?:with|for|who\s+(?:know|have)|"
        rf"(?:must|should|need\s+to)\s+be\s+(?:proficient|qualified|skilled)\s+in|skilled\s+in)\s+{name}\b",
        rf"\b({_COUNT})\s+{name}\s+{_PEOPLE}\b",
        rf"\b{name}\s+(?:needs?|requires?|for)\s+({_COUNT})\s+{_PEOPLE}\b",
        rf"\b{name}\s*[:=]\s*({_COUNT})\s+{_PEOPLE}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _count(match.group(1))

    every_skill = re.search(
        rf"\b({_COUNT})\s+{_PEOPLE}\s+(?:for\s+each|per)\s+(?:skill|position|role)\b",
        text,
        re.IGNORECASE,
    )
    if every_skill:
        return _count(every_skill.group(1))
    return None


def _requirement_counts(ctx: AgentContext, requirements: list[dict]) -> list[dict]:
    """Keep per-skill staffing counts only when the person's text explicitly ties a count to that skill."""
    texts = [str(ctx.input.get("text", ""))]
    texts.extend(step["text"] for step in ctx._steps if step.get("kind") == "answer")
    text = "\n".join(texts)
    return [
        {**requirement, "min_people": _explicit_skill_count(str(requirement.get("skill", "")), text) or 1}
        for requirement in requirements
    ]


def _ask(ctx: AgentContext, a: dict) -> dict:
    ctx.state["questions"] = ctx.state.get("questions", 0) + 1
    if ctx.state["questions"] > MAX_QUESTIONS:
        return {"error": "Question limit reached. Continue with what you know and say what you assumed."}
    return {"answer": ctx.ask_user(str(a.get("question", ""))[:500]) or "(no answer)"}


def _propose(ctx: AgentContext, a: dict) -> dict:
    payload = {**a, "requirements": _requirement_counts(ctx, a.get("requirements") or [])}
    out = proposals.create(ctx, "project_brief", str(a.get("title") or "Project"), payload, str(a.get("evidence", "")), None)
    unknown = [r["skill"] for r in payload["requirements"] if tools.skill_coverage(ctx, {"skill": r.get("skill")})["match_method"] == "new"]
    if unknown:  # recorded for HR to approve on the Standardization page; nothing is added to the taxonomy here
        requested.record(unknown, str(a.get("title", "")))
    return out


def _tools() -> list[Tool]:
    req = {"type": "array", "items": tools._obj({"skill": tools.S, "level": tools.I, "weight": {"type": "number"},
                                                  "min_people": tools.I,
                                                  "domain_tags": {"type": "array", "items": tools.S}})}
    return [
        tools.t_skill_coverage(), tools.t_list_roles(),
        Tool("ask_user", "Ask the person one short question and wait for the answer.", tools._obj({"question": tools.S}), _ask),
        Tool("propose_project_brief", "Submit the final structured brief for approval. Call once.",
             tools._obj({"title": tools.S, "prompt": tools.S, "requirements": req, "headcount": tools.I,
                         "roles": {"type": "array", "items": tools.S}, "notes": tools.S, "evidence": tools.S}), _propose),
    ]


def run(ctx: AgentContext, inp: dict) -> dict:
    text = str(inp.get("text", "")).strip()
    if len(text) < 10:
        raise AgentError("Describe the project in a sentence or two first.")
    from ..ai.guardrails import check_input

    text = check_input(text)  # the same guardrail the planner uses
    messages = [{"role": "system", "content": prompts.INTAKE}, {"role": "user", "content": f"Project description:\n{text[:4000]}"}]
    final = loop(ctx, AGENT, messages)
    return {"summary": final}


AGENT = AgentDef("intake", "Project intake", prompts.INTAKE, _tools, run)
