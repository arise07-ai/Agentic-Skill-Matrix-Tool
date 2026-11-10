"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import type { AgentKey, AgentRun, AgentStep } from "@/lib/types";
import { Alert, Markdown } from "./ui";
import AgentPortrait from "./AgentPortrait";

/** Shows one agent run live: the steps so far, a question box when the agent asks something, and the result. */
const PORTRAIT_BY_AGENT: Record<AgentKey, "curator" | "intake" | "inspector" | "analyst"> = {
  standardization: "curator",
  intake: "intake",
  upload: "inspector",
  chat: "analyst",
};

const TOOL_LABELS: Record<string, string> = {
  search_taxonomy: "Checking the skill catalog",
  employee_usage: "Checking how skills appear in employee profiles",
  web_lookup: "Researching the skill",
  propose_mapping: "Preparing a mapping for review",
  skill_coverage: "Checking skill coverage",
  find_employees: "Finding matching employees",
  get_employee: "Reviewing an employee profile",
  who_has_skill: "Finding employees with this skill",
  skill_overview: "Reviewing skill coverage",
  project_gaps: "Checking project skill gaps",
  list_roles: "Checking available roles",
  upload_overview: "Reviewing the uploaded file",
  source_stats: "Checking the uploaded data",
  try_parse: "Checking an alternative way to read the file",
  propose_upload_fix: "Preparing an upload improvement for review",
  propose_project_brief: "Preparing a project brief for review",
  ask_user: "Preparing a clarification",
};

function stepText(step: AgentStep): string {
  if (step.kind !== "tool") return step.text;
  const tool = step.text.slice(0, step.text.indexOf("(") < 0 ? undefined : step.text.indexOf("("));
  return TOOL_LABELS[tool] ?? "Reviewing relevant information";
}

export default function RunPanel({ runId, onFinished }: { runId: string; onFinished?: (r: AgentRun) => void }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [sending, setSending] = useState(false);
  const finished = useRef(false);

  useEffect(() => {
    finished.current = false;
    let stop = false;
    async function tick() {
      try {
        const r = await api<AgentRun>(`/api/agents/runs/${runId}`);
        if (stop) return;
        setRun(r);
        if (r.status === "done" || r.status === "error") {
          if (!finished.current) { finished.current = true; onFinished?.(r); }
          return;
        }
      } catch (e) { if (!stop) { setErr((e as Error).message); return; } }
      setTimeout(tick, 1000);
    }
    tick();
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  async function send() {
    setSending(true);
    try { await post(`/api/agents/runs/${runId}/answer`, { answer }); setAnswer(""); }
    catch (e) { setErr((e as Error).message); }
    finally { setSending(false); }
  }

  if (err) return <Alert tone="bad">{err}</Alert>;
  if (!run) return <p className="muted"><span className="spin" />Starting…</p>;
  const working = run.status === "running";
  const text = run.result?.summary ?? run.result?.answer;
  const currentStep = run.steps?.filter((step) => step.kind === "tool").slice(-1)[0];
  return (
    <div className="card">
      <div className="run-status">
        {(working || run.status === "waiting_input" || run.status === "done") && (
          <div className={`run-agent-working ${run.status === "waiting_input" ? "run-agent-waiting" : ""} ${run.status === "done" ? "run-agent-celebrating" : ""}`}>
            <AgentPortrait kind={PORTRAIT_BY_AGENT[run.agent]} />
            <div className="run-agent-working__copy">
              <strong>{working ? "Working on your request" : run.status === "waiting_input" ? "Waiting for your answer" : "Finished successfully"}</strong>
              <span>{working ? (currentStep ? stepText(currentStep) : "Thinking through the next step") : run.status === "waiting_input" ? "The agent is ready when you are" : "Your request is complete"}</span>
            </div>
            {run.status !== "done" && <span className="run-agent-working__dots" aria-hidden="true"><i /><i /><i /></span>}
          </div>
        )}
        {!working && run.status !== "waiting_input" && run.status !== "done" && <strong>Stopped</strong>}
      </div>
      {run.status === "waiting_input" && run.question && (
        <div style={{ margin: "10px 0" }}>
          <Alert tone="info">{run.question}</Alert>
          <div className="row">
            <input value={answer} onChange={(e) => setAnswer(e.target.value)} onKeyDown={(e) => e.key === "Enter" && answer.trim() && send()}
              placeholder="Your answer" style={{ flex: 1, minWidth: 200 }} autoFocus />
            <button className="btn" disabled={sending || !answer.trim()} onClick={send}>Answer</button>
          </div>
        </div>
      )}
      {run.status === "error" && <Alert tone="bad">{run.error}</Alert>}
      {!!run.steps?.length && (
        <ul className="steps">{run.steps.map((s, i) => <li key={i} className={s.kind}>{s.kind === "question" ? "Agent asks: " : s.kind === "answer" ? "You answered: " : ""}{stepText(s)}</li>)}</ul>
      )}
      {run.status === "done" && text && <div style={{ marginTop: 12 }}><Markdown>{text}</Markdown></div>}
      {run.status === "done" && !!run.result?.auto_applied && <p className="muted">{run.result.auto_applied} exact / earlier-decision match(es) were applied automatically (listed under Approvals → Applied automatically).</p>}
      {run.status === "done" && !!run.result?.proposal_ids?.length && (
        <Alert tone="ok">{run.result.proposal_ids.length} proposal(s) waiting for a decision. <Link href="/approvals">Open Approvals →</Link></Alert>
      )}
    </div>
  );
}
