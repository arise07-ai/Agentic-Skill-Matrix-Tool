"""Agent runtime: a small, bounded tool-calling loop plus run records the UI can poll.

* An agent = a system prompt + a set of tools. The model decides which tool to call next; the loop executes the tool and
  feeds the result back until the model answers or the step budget runs out (AGENT_MAX_STEPS).
* Agents can only READ, or PROPOSE. The only write path is the proposal queue (agents/proposals.py), and a person with
  the right role applies a proposal. The model never gets a tool that changes employees, skills or users directly.
* A run starts when a person clicks a button; it runs on a thread inside the API process (no job queue) and writes its
  steps to the database as it goes, so the page can show them live. `ask_user` pauses the run until the person answers.
* Text that comes from data (biographies, web pages, uploaded files) is untrusted: the prompts tell the model to treat it
  as data, never as instructions, and the tools cannot do anything harmful even if it is ignored.
"""
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .. import db
from ..ai import AINotConfigured, llm
from ..ai.guardrails import GuardrailViolation, check_input, check_output
from ..ai.tracing import traced
from ..config import get_settings
from ..services import auth

log = logging.getLogger(__name__)
MAX_TOOL_RESULT_CHARS = 6000
TOOL_FAILURE_RESULT = {
    "status": "unavailable",
    "data": None,
    "message": (
        "This lookup did not return data. Do not mention tool execution or technical details to the user. "
        "Continue using other verified results; if needed, say the information is unavailable and ask the user to retry."
    ),
}
_events: dict[str, threading.Event] = {}
_answers: dict[str, str] = {}
_lock = threading.Lock()


class AgentError(Exception):
    """A problem to show to the person (not a bug)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[["AgentContext", dict], Any]

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class AgentDef:
    key: str
    title: str
    system: str
    tools: Callable[[], list[Tool]]  # a function so tools that depend on settings (web lookup) are decided per run
    run: Callable[["AgentContext", dict], dict]  # builds the task, calls loop(), returns the result dict


@dataclass
class AgentContext:
    run_id: str
    agent: str
    user: dict
    input: dict
    state: dict = field(default_factory=dict)
    returned_ids: set = field(default_factory=set)
    proposal_ids: list = field(default_factory=list)
    _steps: list = field(default_factory=list)

    def step(self, kind: str, text: str) -> None:
        self._steps.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "text": text[:600]})
        with db.connect() as c:
            c.execute("UPDATE agent_runs SET steps=? WHERE id=?", (json.dumps(self._steps, ensure_ascii=False), self.run_id))

    def ask_user(self, question: str) -> str:
        """Pause this run until the person answers (or the wait times out)."""
        ev = threading.Event()
        with _lock:
            _events[self.run_id] = ev
            _answers.pop(self.run_id, None)
        self.step("question", question)
        with db.connect() as c:
            c.execute("UPDATE agent_runs SET status='waiting_input', question=? WHERE id=?", (question[:1000], self.run_id))
        if not ev.wait(get_settings().agent_ask_timeout_seconds):
            raise AgentError("Nobody answered the agent's question in time, so the run was stopped.")
        with _lock:
            answer = _answers.pop(self.run_id, "")
            _events.pop(self.run_id, None)
        with db.connect() as c:
            c.execute("UPDATE agent_runs SET status='running', question=NULL WHERE id=?", (self.run_id,))
        self.step("answer", answer)
        return answer


# ----------------------------------------------------------------------------- the loop
def mask_secrets(text: str) -> str:
    text = re.sub(r"(Incorrect API key provided: )\S+", r"\1***", text)
    return re.sub(r"(sk|tvly|lsv2)[-_][A-Za-z0-9_\-]{6,}", r"\1-***", text)


def _run_tool(ctx: AgentContext, tools: dict[str, Tool], name: str, args: dict) -> str:
    tool = tools.get(name)
    if not tool:
        log.warning("Agent requested unknown tool %r", name)
        return json.dumps(TOOL_FAILURE_RESULT)
    shown = json.dumps(args, ensure_ascii=False, default=str)
    ctx.step("tool", f"{name}({shown[:300]})")
    try:
        result = tool.fn(ctx, args or {})
    except AgentError:
        raise
    except Exception as e:  # noqa: BLE001  a bad tool call is information for the model, not a crash
        log.exception("Agent tool %s failed", name)
        result = TOOL_FAILURE_RESULT
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '..."(truncated)'
    ctx.state.setdefault("tool_log", []).append({"tool": name, "args": args or {}, "result": text})  # evidence for the evaluation log
    return text


def loop(ctx: AgentContext, agent: AgentDef, messages: list, max_steps: int | None = None) -> str:
    """Run the model/tool loop. Returns the model's final text."""
    s = get_settings()
    tools = {t.name: t for t in agent.tools()}
    model = llm.get_chat_model().bind_tools([t.spec() for t in tools.values()])
    for i in range(max_steps or s.agent_max_steps):
        ai = model.invoke(messages)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            content = ai.content if isinstance(ai.content, str) else str(ai.content)
            content, notes = check_output(content.strip())  # mask cursing / withhold toxic text
            for n in notes:
                ctx.step("info", n)
            return content
        messages.append(ai)
        for call in calls:
            out = _run_tool(ctx, tools, call["name"], call.get("args") or {})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
    ctx.step("info", f"Stopped after {s.agent_max_steps} steps (the step limit).")
    return "(The agent reached its step limit before finishing. Run it again to continue with what is left.)"


# ----------------------------------------------------------------------------- runs
def registry() -> dict[str, AgentDef]:
    from . import chat, intake, standardization, upload

    return {a.key: a for a in (standardization.AGENT, intake.AGENT, upload.AGENT, chat.AGENT)}


def agents_available() -> tuple[bool, str]:
    if not llm.is_configured():
        return False, "Agents need an OpenAI model. Set OPENAI_API_KEY in backend/.env and restart the backend."
    return True, ""


def start_run(agent_key: str, user: dict, input: dict) -> str:
    ok, why = agents_available()
    if not ok:
        raise AINotConfigured(why)
    agent = registry().get(agent_key)
    if not agent:
        raise LookupError(f"Unknown agent '{agent_key}'")
    limit = get_settings().agent_max_runs_per_hour
    recent = db.one("SELECT COUNT(*) AS n FROM agent_runs WHERE user_id=? AND created_at >= datetime('now','-1 hour')", (user["id"],))["n"]
    if recent >= limit:
        raise AgentError(f"Run limit reached ({limit} per hour). Try again later.")
    run_id = uuid.uuid4().hex[:12]
    with db.connect() as c:
        c.execute("INSERT INTO agent_runs(id, agent, user_id, input) VALUES (?,?,?,?)",
                  (run_id, agent_key, user["id"], json.dumps(input, ensure_ascii=False, default=str)[:20000]))
    auth.audit(user, "agent_run_started", {"run": run_id, "agent": agent_key,
                                           "input": {k: (str(v)[:200]) for k, v in input.items() if k != "messages"}})
    threading.Thread(target=_worker, args=(run_id, agent, user, input), name=f"agent-{run_id}", daemon=True).start()
    return run_id


@traced("agent.run")
def _execute(agent: AgentDef, ctx: AgentContext) -> dict:
    return agent.run(ctx, ctx.input)


def _worker(run_id: str, agent: AgentDef, user: dict, input: dict) -> None:
    ctx = AgentContext(run_id=run_id, agent=agent.key, user=user, input=input)
    try:
        result = _execute(agent, ctx)
        result["proposal_ids"] = ctx.proposal_ids
        with db.connect() as c:
            c.execute("UPDATE agent_runs SET status='done', result=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                      (json.dumps(result, ensure_ascii=False, default=str), run_id))
        detail = {"run": run_id, "agent": agent.key, "proposals": ctx.proposal_ids}
        if agent.key == "chat":  # who asked what, and which employees' data was returned (ids only)
            msgs = input.get("messages") or [{}]
            detail.update(question=str(msgs[-1].get("content", ""))[:300], employee_ids=sorted(ctx.returned_ids)[:100])
        auth.audit(user, "agent_run_finished", detail)
    except Exception as e:  # noqa: BLE001
        log.exception("Agent run %s failed", run_id)
        msg = mask_secrets(str(e) if isinstance(e, AgentError) else f"{type(e).__name__}: {e}")
        with db.connect() as c:
            c.execute("UPDATE agent_runs SET status='error', error=?, finished_at=CURRENT_TIMESTAMP WHERE id=?", (msg[:1000], run_id))
        auth.audit(user, "agent_run_failed", {"run": run_id, "agent": agent.key, "error": msg[:200]})


def answer_run(run_id: str, user: dict, text: str) -> None:
    run = db.one("SELECT user_id, status FROM agent_runs WHERE id=?", (run_id,))
    if not run:
        raise LookupError("Run not found")
    if run["user_id"] != user["id"] and user["role"] != "admin":
        raise PermissionError("Only the person who started the run can answer it")
    if run["status"] != "waiting_input":
        raise AgentError("This run is not waiting for an answer")
    with _lock:
        ev = _events.get(run_id)
        if not ev:
            raise AgentError("This run is no longer waiting (it may have been restarted)")
        try:
            _answers[run_id] = check_input(text.strip()[:2000])  # same guardrails as every other free text
        except GuardrailViolation as e:
            raise AgentError(str(e)) from e
        ev.set()


def get_run(run_id: str, user: dict) -> dict:
    r = db.one("SELECT * FROM agent_runs WHERE id=?", (run_id,))
    if not r:
        raise LookupError("Run not found")
    if r["user_id"] != user["id"] and user["role"] != "admin":
        raise PermissionError("Not your run")
    for k in ("steps", "result", "input"):
        try:
            r[k] = json.loads(r[k]) if r[k] else None
        except ValueError:
            pass
    return r


def list_runs(user: dict, limit: int = 20) -> list[dict]:
    where, params = ("", ()) if user["role"] == "admin" else ("WHERE user_id=?", (user["id"],))
    return db.rows(f"SELECT id, agent, status, created_at, finished_at FROM agent_runs {where} ORDER BY created_at DESC LIMIT ?",
                   (*params, limit))


def fail_orphaned_runs() -> int:
    """Runs that were in progress when the API stopped can never finish: mark them so the UI stops waiting."""
    with db.connect() as c:
        return c.execute("UPDATE agent_runs SET status='error', error='Interrupted: the backend was restarted', "
                         "finished_at=CURRENT_TIMESTAMP WHERE status IN ('running','waiting_input')").rowcount
