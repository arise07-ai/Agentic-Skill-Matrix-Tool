"""Ask-the-matrix chat agent: read-only questions about employees and skills. The page keeps the conversation while it is
open and sends it with each question. The server keeps the audit trail (who asked what, which employee ids came back) and, unless
EVAL_LOG_ENABLED=false, appends each question, answer and the data the tools returned to the evaluation log (ai/eval_log.py)."""
import json

from ..ai.guardrails import GuardrailViolation, ToxicInputViolation, check_input, passes_local_checks
from ..ai.conversation import cached_conversational_reply, normalize_messages
from . import prompts, tools
from .runtime import AgentContext, AgentDef, AgentError, loop


def _tools():
    return [tools.t_find_employees(), tools.t_get_employee(), tools.t_who_has_skill(), tools.t_who_has_all_skills(), tools.t_skill_overview(),
            tools.t_related_skills(), tools.t_project_gaps(), tools.t_list_roles(), tools.t_search_taxonomy()]


def drop_refused_turns(history: list[dict]) -> list[dict]:
    """Earlier questions that fail the fast local checks were refused when they were asked: leave them out, together with the
    reply that followed them, so they never reach the model or the evaluation log. The page also stops sending refused turns;
    this is the server's own check. The newest message is not touched (it gets the full check_input)."""
    kept, skip_reply = [], False
    for m in history[:-1]:
        if m["role"] == "user":
            skip_reply = not passes_local_checks(m["content"])
            if not skip_reply:
                kept.append(m)
        elif not skip_reply:
            kept.append(m)
    return [*kept, history[-1]]


def run(ctx: AgentContext, inp: dict) -> dict:
    history = normalize_messages(inp.get("messages") or [])
    if not history or history[-1]["role"] != "user":
        raise AgentError("Ask a question first")
    history = drop_refused_turns(history)
    try:  # the new question gets the input guardrails (injection, cursing/toxic language); earlier turns were checked when asked
        check_input(history[-1]["content"])
    except ToxicInputViolation as e:  # answered politely, but marked so the page leaves this turn out of the history it sends
        return {"answer": str(e), "employee_ids": [], "refused": True}
    except GuardrailViolation as e:
        raise AgentError(str(e)) from e
    cached_reply = cached_conversational_reply(history[-1]["content"], "chat")
    if cached_reply:
        return {"answer": cached_reply, "employee_ids": []}
    inp["messages"] = history
    messages = [{"role": "system", "content": prompts.CHAT}, *history]
    answer = loop(ctx, AGENT, messages)
    _log_for_evaluation(ctx, inp, history[-1]["content"], answer, history[:-1])  # the question is the latest request; earlier turns are context
    return {"answer": answer, "employee_ids": sorted(ctx.returned_ids)[:100]}


def _log_for_evaluation(ctx: AgentContext, inp: dict, question: str, answer: str, earlier: list[dict] | None = None) -> None:
    """Record the question, the answer and the data the tools returned (the evidence) for RAGAS and the rule-based checks.
    Questions from the fixed test set (ai/chat_eval.py) carry an expected result and a reference; real questions from people
    have no reference until you write one in the dataset file. Never raises."""
    try:
        from ..ai import eval_log

        ev = inp.get("_eval") or {}
        log = ctx.state.get("tool_log", [])
        contexts = [f"Tool {t['tool']}({json.dumps(t['args'], ensure_ascii=False)}) returned: {t['result']}" for t in log]
        if earlier:  # only to understand a follow-up such as "yes". Not evidence: the rule checks read the "Tool ..." passages only
            turns = " ".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {str(m['content'])[:500]}" for m in earlier[-6:])
            contexts.append("Earlier conversation, used only to understand the latest request (not evidence): " + turns)
        meta = {"kind": "chat", "source": "fixed" if ev else "logged", "tool_calls": [{"tool": t["tool"], "args": t["args"]} for t in log],
                "employee_ids": sorted(ctx.returned_ids)[:100], "data_fingerprint": eval_log.data_fingerprint(),
                "case": ev.get("id"), "expect": ev.get("expect")}
        eval_log.log_interaction("chat", question, answer, contexts, ev.get("reference", ""), meta)
    except Exception:  # noqa: BLE001
        pass


AGENT = AgentDef("chat", "Ask the matrix", prompts.CHAT, _tools, run)