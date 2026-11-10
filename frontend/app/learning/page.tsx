"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { post, useApi, useProject } from "@/lib/api";
import { useUser } from "@/components/AppFrame";
import type { EmployeeRow, LearningCoachMessage, LearningResult, Requirement } from "@/lib/types";
import { Alert, Loading, Markdown, Pill } from "@/components/ui";
import AgentPortrait from "@/components/AgentPortrait";
import LearningPlanApproval from "@/components/LearningPlanApproval";
import RequirementsEditor from "@/components/RequirementsEditor";

// Who may edit a finished plan. The server enforces the same list (PLAN_EDITORS in routers/projects.py); this only hides the button.
const PLAN_EDITORS = ["admin", "project_manager", "hr_manager"];

function LearningInner() {
  const params = useSearchParams();
  const { user } = useUser();
  const isMember = user?.role === "member";  // a member works on their own record only (the server checks too)
  const { data: employees } = useApi<EmployeeRow[]>(isMember ? null : "/api/employees");
  const [project, , ready] = useProject();
  const [employeeId, setEmployeeId] = useState<string>(isMember ? String(user?.employee_id ?? "") : params.get("employee") ?? "");
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [weeks, setWeeks] = useState(6);
  const [hours, setHours] = useState(5);
  const [result, setResult] = useState<LearningResult | null>(null);
  const [coachContext, setCoachContext] = useState<{
    employeeId: number; requirements: Requirement[]; weeks: number; hoursPerWeek: number;
  } | null>(null);
  const [approvalDecision, setApprovalDecision] = useState<"approved" | "declined" | null>(null);
  const [coachMessages, setCoachMessages] = useState<LearningCoachMessage[]>([]);
  const [coachOpen, setCoachOpen] = useState(false);
  const [coachInput, setCoachInput] = useState("");
  const [coachBusy, setCoachBusy] = useState(false);
  const [coachError, setCoachError] = useState<string | null>(null);
  const [coachWarnings, setCoachWarnings] = useState<string[]>([]);
  const coachMessagesRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editedMarkdown, setEditedMarkdown] = useState<string | null>(null);
  const [editedBy, setEditedBy] = useState("");
  const [editProblems, setEditProblems] = useState<string[]>([]);
  const [editBusy, setEditBusy] = useState(false);
  const [submitBusy, setSubmitBusy] = useState(false);
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);
  const appliedRequirementsParam = useRef<string | null | undefined>(undefined);
  const requirementsParam = params.get("requirements");

  useEffect(() => {
    const messages = coachMessagesRef.current;
    if (coachOpen && messages) {
      messages.scrollTo({ top: messages.scrollHeight, behavior: "smooth" });
    }
  }, [coachMessages, coachBusy, coachOpen, coachError, coachWarnings]);

  useEffect(() => { const e = params.get("employee"); if (e && !isMember) setEmployeeId(e); }, [params, isMember]);
  useEffect(() => {
    if (!ready || appliedRequirementsParam.current === requirementsParam) return;
    appliedRequirementsParam.current = requirementsParam;
    if (!requirementsParam) {
      setRequirements(project.requirements);
      return;
    }
    try {
      const parsed: unknown = JSON.parse(requirementsParam);
      if (!Array.isArray(parsed) || parsed.some((item) =>
        !item || typeof item.skill !== "string" || !item.skill.trim()
        || !Number.isInteger(item.level) || item.level < 1 || item.level > 4
      )) {
        throw new Error("Invalid project skill gaps");
      }
      setRequirements(parsed.map((item) => ({
        skill: item.skill.trim(), level: item.level, weight: 1, min_people: 1,
        domain_tags: Array.isArray(item.domain_tags) ? item.domain_tags.filter((tag: unknown): tag is string => typeof tag === "string") : [],
      })));
    } catch {
      setError("The project skill gaps could not be loaded. Select the skills again before generating a plan.");
      setRequirements([]);
    }
  }, [ready, requirementsParam, project.requirements]);

  async function generate() {
    setBusy(true); setError(null); setResult(null); setApprovalDecision(null); setSubmitMsg(null);
    setEditing(false); setEditedMarkdown(null); setEditProblems([]);
    setCoachContext(null);
    setCoachMessages([]); setCoachOpen(false); setCoachError(null); setCoachWarnings([]);
    try {
      const request = {
        employee_id: Number(employeeId),
        requirements: requirements.filter((r) => r.skill.trim()).map((r) => ({ ...r })),
        weeks,
        hours_per_week: hours,
        project: project.prompt,
      };
      setCoachContext({
        employeeId: request.employee_id,
        requirements: request.requirements,
        weeks: request.weeks,
        hoursPerWeek: request.hours_per_week,
      });
      setResult(await post<LearningResult>("/api/learning-paths", {
        ...request,
      }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function sendCoachMessage(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = coachInput.trim();
    if (!result || !coachContext || !content || coachBusy) return;
    const nextMessages: LearningCoachMessage[] = [...coachMessages, { role: "user", content }];
    setCoachMessages(nextMessages);
    setCoachInput("");
    setCoachBusy(true); setCoachError(null); setCoachWarnings([]);
    try {
      const response = await post<{ answer: string; warnings: string[] }>("/api/learning-paths/coach", {
        employee_id: coachContext.employeeId,
        requirements: coachContext.requirements,
        weeks: coachContext.weeks,
        hours_per_week: coachContext.hoursPerWeek,
        plan_markdown: displayedMarkdown,
        messages: nextMessages.slice(-12),
      });
      setCoachMessages([...nextMessages, { role: "assistant", content: response.answer }]);
      setCoachWarnings(response.warnings);
    } catch (e) {
      setCoachError((e as Error).message);
    } finally {
      setCoachBusy(false);
    }
  }

  async function saveEdit() {
    if (!coachContext || !user) return;
    setEditBusy(true); setError(null);
    try {
      const check = await post<{ ok: boolean; problems: string[] }>("/api/learning-paths/check", {
        employee_id: coachContext.employeeId,
        requirements: coachContext.requirements,
        weeks: coachContext.weeks,
        hours_per_week: coachContext.hoursPerWeek,
        plan_markdown: draft,
      });
      setEditProblems(check.problems);
      setEditedMarkdown(draft); setEditedBy(user.display_name); setEditing(false); setSubmitMsg(null);
    } catch (e) { setError((e as Error).message); } finally { setEditBusy(false); }
  }

  const canEdit = Boolean(user && (PLAN_EDITORS.includes(user.role) || isMember));
  const name = employees?.find((e) => String(e.id) === employeeId)?.name ?? (isMember ? user?.display_name ?? "employee" : "employee");
  const awaitingApproval = Boolean(result?.approval_required && approvalDecision === null);
  const generatedMarkdown = result
    ? approvalDecision === "declined" ? result.template_markdown ?? result.markdown : result.markdown
    : "";
  const displayedMarkdown = editedMarkdown ?? generatedMarkdown;
  const displayedMode = approvalDecision === "declined" ? "template" : result?.mode;
  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([displayedMarkdown], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url; a.download = `learning-path-${name.toLowerCase().replace(/\s+/g, "-")}.md`; a.click();
    URL.revokeObjectURL(url);
  };
  async function submitForApproval() {
    if (!coachContext || !result) return;
    setSubmitBusy(true); setError(null); setSubmitMsg(null);
    try {
      const r = await post<{ proposal_id: number; approver_label: string; duplicate?: boolean }>("/api/me/learning-plan/submit", {
        markdown: displayedMarkdown, requirements: coachContext.requirements.map((q) => ({ ...q })), weeks: coachContext.weeks,
        hours_per_week: coachContext.hoursPerWeek, mode: displayedMode ?? "", edited: editedMarkdown !== null,
      });
      setSubmitMsg(r.duplicate ? "This plan is already waiting for approval."
        : `Sent to ${r.approver_label} for approval (#${r.proposal_id}). Follow it under My submissions.`);
    } catch (e) { setError((e as Error).message); } finally { setSubmitBusy(false); }
  }
  const copy = async () => { try { await navigator.clipboard.writeText(displayedMarkdown); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {} };

  if (!ready) return <Loading />;
  const ok = employeeId && requirements.some((r) => r.skill.trim());
  return (
    <>
      <h1>Learning paths</h1>
      <p className="sub">Turns an employee’s skill gaps into a Markdown study plan using courses and videos from your catalog, Microsoft Learn and YouTube. {isMember
        ? <>Pick the skills you want to reach. You can edit the plan, then send it to your team leader for approval.</>
        : <>Requirements are shared with the <Link href="/projects">Project planner</Link>.</>}</p>
      {error && <Alert tone="bad">{error}</Alert>}
      <div className="card">
        <div className="row" style={{ marginBottom: 14 }}>
          <div><label className="f">Employee</label>
            {isMember
              ? <select value={employeeId} disabled style={{ minWidth: 240 }}><option value={employeeId}>{user?.display_name} (you)</option></select>
              : <select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)} style={{ minWidth: 240 }}>
                  <option value="">Select…</option>{employees?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>}</div>
          <div><label className="f">Weeks</label><input type="number" min={1} max={52} style={{ width: 80 }} value={weeks} onChange={(e) => setWeeks(Math.max(1, Number(e.target.value) || 1))} /></div>
          <div><label className="f">Hours / week</label><input type="number" min={1} max={40} style={{ width: 80 }} value={hours} onChange={(e) => setHours(Math.max(1, Number(e.target.value) || 1))} /></div>
        </div>
        <h3>Target skills</h3>
        <RequirementsEditor value={requirements} onChange={setRequirements} taxonomyPath={isMember ? "/api/me/taxonomy" : "/api/taxonomy"} />
        <div style={{ marginTop: 14 }}><button className="btn" disabled={!ok || busy} onClick={generate}>{busy ? "Generating…" : "Generate study plan"}</button></div>
      </div>

      {result && (
        <>
          {result.warnings.filter((warning) => !warning.includes("Jev excluded ")).map((w, i) => <Alert key={i} tone="warn">{w}</Alert>)}
          {displayedMode === "llm" && (result as { review_notes?: string[] }).review_notes?.length ? (
            <Alert tone="info"><strong>AI reviewer changed the draft:</strong> {(result as { review_notes?: string[] }).review_notes!.join(" ")}</Alert>
          ) : null}
          {awaitingApproval ? (
            <LearningPlanApproval
              result={result}
              onApprove={() => setApprovalDecision("approved")}
              onDecline={() => setApprovalDecision("declined")}
            />
          ) : (
            <div className="card">
              <div className="row" style={{ marginBottom: 8 }}>
                <Pill tone={displayedMode === "llm" ? "ok" : "info"}>{displayedMode === "llm" ? "AI-planned · code-verified" : displayedMode === "template" ? "rule-based plan" : "no gaps"}</Pill>
                {editedMarkdown !== null && <Pill tone="warn">edited by {editedBy}</Pill>}
                <span className="spacer" />
                {canEdit && !editing && displayedMode !== "none" && (
                  <button className="btn ghost sm" onClick={() => { setDraft(displayedMarkdown); setEditing(true); }}>Edit plan</button>
                )}
                {editedMarkdown !== null && !editing && (
                  <button className="btn ghost sm" onClick={() => { setEditedMarkdown(null); setEditProblems([]); }}>Discard edits</button>
                )}
                {isMember && !editing && displayedMode !== "none" && (
                  <button className="btn sm" disabled={submitBusy || !!submitMsg} onClick={submitForApproval}>{submitBusy ? "Sending…" : "Send for approval"}</button>
                )}
                <button className="btn ghost sm" onClick={copy}>{copied ? "Copied" : "Copy Markdown"}</button>
                <button className="btn ghost sm" onClick={download}>Download .md</button>
              </div>
              {submitMsg && <Alert tone="ok">{submitMsg}</Alert>}
              {editing ? (
                <>
                  <p className="muted" style={{ fontSize: 12.5 }}>
                    Edit the Markdown. When you save, the app re-checks the budget, the skill-gap table, the schedule and the hours breakdown against the
                    ratings and tells you what no longer matches. Keep the lines of the Hours Breakdown in their format: the Learning Coach reads them.
                  </p>
                  <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={26} maxLength={20000}
                    style={{ width: "100%", fontFamily: "monospace", fontSize: 13 }} aria-label="Learning plan Markdown" />
                  <div className="row" style={{ marginTop: 10 }}>
                    <button className="btn" disabled={editBusy || !draft.trim()} onClick={saveEdit}>{editBusy ? "Checking…" : "Save edits"}</button>
                    <button className="btn ghost" disabled={editBusy} onClick={() => setEditing(false)}>Cancel</button>
                  </div>
                </>
              ) : (
                <>
                  {editedMarkdown !== null && editProblems.map((problem, i) => <Alert key={i} tone="warn">{problem}</Alert>)}
                  <Markdown>{displayedMarkdown}</Markdown>
                </>
              )}
            </div>
          )}
        </>
      )}
      {result && !awaitingApproval && coachContext && (
        <div className="learning-coach-widget">
          {coachOpen && (
            <section id="learning-coach-panel" className="learning-coach-window" aria-labelledby="learning-coach-title">
              <header className="learning-coach__header">
                <AgentPortrait kind="professor" compact />
                <div className="learning-coach__identity">
                  <h2 id="learning-coach-title">The Professor</h2>
                  <p>Learning Coach · Ask about your plan and progress</p>
                </div>
                <button className="learning-coach__close" type="button" onClick={() => setCoachOpen(false)} aria-label="Close Learning Coach">×</button>
              </header>
              <div className="learning-coach__messages" ref={coachMessagesRef} aria-live="polite">
                {(result.coach_notices ?? []).map((notice, index) => (
                  <div className="learning-coach__message learning-coach__message--assistant" key={`notice-${index}`}>
                    <strong>Professor</strong>
                    <Markdown>{notice}</Markdown>
                  </div>
                ))}
                {coachMessages.map((message, index) => (
                  <div className={`learning-coach__message learning-coach__message--${message.role}`} key={index}>
                    <strong>{message.role === "user" ? "You" : "Professor"}</strong>
                    {message.role === "assistant" ? <Markdown>{message.content}</Markdown> : <p>{message.content}</p>}
                  </div>
                ))}
                {coachBusy && <p className="muted" role="status">I’m putting together a response…</p>}
              </div>
              {coachWarnings.map((warning, index) => <Alert key={index} tone="warn">{warning}</Alert>)}
              {coachError && <Alert tone="bad">{coachError}</Alert>}
              <form className="learning-coach__form" onSubmit={sendCoachMessage}>
                <textarea
                  id="learning-coach-input"
                  aria-label="Your question for the Learning Coach"
                  value={coachInput}
                  maxLength={2000}
                  rows={2}
                  placeholder="Ask about your learning plan…"
                  onChange={(event) => setCoachInput(event.target.value)}
                />
                <button className="btn" type="submit" disabled={!coachInput.trim() || coachBusy}>
                  {coachBusy ? "Thinking…" : "Send"}
                </button>
              </form>
            </section>
          )}
          <button
            className={`learning-coach-launcher ${coachOpen ? "is-open" : ""}`}
            type="button"
            onClick={() => setCoachOpen((open) => !open)}
            aria-expanded={coachOpen}
            aria-controls="learning-coach-panel"
          >
            <AgentPortrait kind="professor" compact />
            <span>{coachOpen ? "Close coach" : "Ask The Professor"}</span>
          </button>
        </div>
      )}
    </>
  );
}

export default function LearningPage() {
  return <Suspense fallback={<Loading />}><LearningInner /></Suspense>;
}
