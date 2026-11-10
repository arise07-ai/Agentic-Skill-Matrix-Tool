"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { post, useApi } from "@/lib/api";
import type { Brief, Proposal } from "@/lib/types";
import { Alert, Empty, LEVELS, Loading, Markdown, Pill } from "@/components/ui";
import { useUser } from "@/components/AppFrame";
import ProjectTeam from "@/components/ProjectTeam";
import { hoursOf } from "@/components/AssignControl";

const KIND: Record<string, string> = { skill_mapping: "Skill mapping", upload_fix: "Upload settings", project_brief: "Project brief", employee_profile: "CV profile", employee_roles: "Role match", project_assignment: "Project assignment", project_dates: "Project dates",
  member_update: "Member: skills & profile", member_cv: "Member: CV", member_learning_plan: "Member: learning plan" };
const WHO: Record<string, string> = { skill_mapping: "HR manager", upload_fix: "HR manager", project_brief: "Project manager / Team leader", employee_profile: "HR manager", employee_roles: "HR manager or the employee's team leader", project_assignment: "HR manager", project_dates: "HR manager",
  member_update: "team leader of that member", member_cv: "team leader of that member", member_learning_plan: "team leader of that member" };
const STATUS_LABEL: Record<string, string> = { pending: "Waiting", approved: "Approved", rejected: "Rejected", auto_applied: "Applied automatically" };
const TABS = [["pending", "Waiting"], ["approved", "Approved"], ["rejected", "Rejected"], ["auto_applied", "Applied automatically"], ["briefs", "Approved briefs"]] as const;
type ApprovalCounts = { waiting: number; approved_briefs: number };

function Body({ p, edit, setEdit }: { p: Proposal; edit: Record<string, any>; setEdit: (e: Record<string, any>) => void }) {
  const x = p.payload;
  if (p.kind === "skill_mapping") {
    return (
      <div className="row">
        <code>{x.raw_skill}</code> → 
        {p.status === "pending" && p.can_decide
          ? <input value={edit.canonical ?? x.canonical} onChange={(e) => setEdit({ ...edit, canonical: e.target.value })} aria-label="Canonical skill" />
          : <strong>{x.canonical}</strong>}
        {x.category && <Pill>{x.category}</Pill>}{x.is_new_skill && <Pill tone="warn">new skill</Pill>}
      </div>
    );
  }
  if (p.kind === "upload_fix") {
    return <div>Upload #{x.upload_id}: {x.sheet && <Pill>sheet: {x.sheet}</Pill>}{x.layout && <Pill>layout: {x.layout}</Pill>}{x.mapping && Object.entries(x.mapping).map(([k, v]) => <Pill key={k}>{k} = {String(v)}</Pill>)}</div>;
  }
  if (p.kind === "employee_roles") {
    const kept: any[] = edit.changes ?? x.changes;
    const isKept = (c: any) => kept.some((k) => k.action === c.action && k.role === c.role && k.to === c.to);
    const toggle = (c: any) => {
      const next = isKept(c) ? kept.filter((k) => !(k.action === c.action && k.role === c.role && k.to === c.to)) : [...kept, c];
      setEdit({ ...edit, changes: x.changes.filter((a: any) => next.some((k: any) => k.action === a.action && k.role === a.role && k.to === a.to)) });
    };
    const tone = { rename: "info", add: "ok", remove: "bad" } as const;
    return (
      <div>
        <div className="row"><strong>{x.employee_name}</strong><span className="muted">now: {(x.roles_before ?? []).join(", ") || "no role"}</span></div>
        <div className="tablewrap"><table>
          <thead><tr>{p.can_decide && p.status === "pending" && <th>Apply</th>}<th>Change</th><th>Why (numbers are checked by code)</th></tr></thead>
          <tbody>{x.changes.map((c: any, i: number) => (
            <tr key={i}>
              {p.can_decide && p.status === "pending" && <td><input type="checkbox" checked={isKept(c)} aria-label={`Apply change ${i + 1}`} onChange={() => toggle(c)} /></td>}
              <td><Pill tone={tone[c.action as "rename" | "add" | "remove"]}>{c.action}</Pill> {c.action === "rename" ? <>{c.role} → <strong>{c.to}</strong></> : <strong>{c.role}</strong>}
                {c.source === "code" && <span className="muted"> · spelling</span>}</td>
              <td className="muted">{c.reason}</td>
            </tr>
          ))}</tbody>
        </table></div>
        <p className="muted" style={{ margin: "6px 0 0" }}>Untick a change to skip it. Skills and their levels are never changed here.</p>
      </div>
    );
  }
  if (p.kind === "project_assignment") {
    const verb = { assign: "Put on the project", change: "Change time on the project", remove: "Take off the project" }[x.action as "assign" | "change" | "remove"];
    const pct: number = edit.allocation_pct ?? x.allocation_pct;
    const cap = p.capacity;
    const canEdit = p.status === "pending" && p.can_decide && x.action !== "remove";
    const base = 0;  // the server already leaves this person's current share of THIS project out of the numbers
    const after = cap ? cap.allocated_pct + pct : null;
    const when = x.period_start || x.period_end ? `${x.period_start ?? "…"} to ${x.period_end ?? "…"}` : "no dates (always running)";
    return (
      <div>
        <div className="row"><strong>{x.employee_name}</strong><Pill tone="info">{verb}</Pill><span className="muted">{x.brief_title}</span>{x.position && <Pill>{x.position}</Pill>}</div>
        <div className="row" style={{ marginTop: 6 }}>
          <span className="muted">{x.action === "change" ? `Was ${x.allocation_before}% ·` : ""} Share of the 8-hour day</span>
          {canEdit
            ? <input type="number" min={1} max={100} style={{ width: 70 }} value={pct} aria-label="Share of the working day in percent"
                onChange={(e) => setEdit({ ...edit, allocation_pct: Math.min(100, Math.max(1, Number(e.target.value) || 1)) })} />
            : <strong>{x.allocation_pct}%</strong>}
          <span className="muted">= {hoursOf(pct)} h/day</span>
          <span className="muted">· {when}{x.start_date || x.end_date ? " (own dates)" : " (the project's dates)"}</span>
        </div>
        {cap && p.status === "pending" && x.action !== "remove" && (
          <>
            <p className="muted" style={{ margin: "6px 0 0" }}>Already approved on their busiest day in this period (other work): {cap.allocated_pct}% ({hoursOf(cap.allocated_pct)} h/day){cap.projects.length > 0 && <> — {cap.projects.map((q) => `${q.title} ${q.allocation_pct}%`).join(", ")}</>}</p>
            {after != null && (after > 100
              ? <Alert tone="bad">With this request {x.employee_name} would be at {after}%, over the 8-hour day. Lower the percentage or this cannot be approved.</Alert>
              : <p className="muted" style={{ margin: "4px 0 0" }}>After approval: {after}% of the day ({hoursOf(after)} h/day), {100 - after}% free.</p>)}
          </>
        )}
        {x.action === "remove" && <p className="muted" style={{ margin: "6px 0 0" }}>Approving frees {x.allocation_pct}% of {x.employee_name}'s day.</p>}
      </div>
    );
  }
  if (p.kind === "project_dates") {
    const r = (a?: string | null, b?: string | null) => (a || b ? `${a ?? "…"} to ${b ?? "…"}` : "not set");
    return (
      <div>
        <div className="row"><strong>{x.brief_title}</strong><Pill tone="info">New project dates</Pill></div>
        <p style={{ margin: "6px 0 0" }}><span className="muted">Was:</span> {r(x.start_before, x.end_before)} → <strong>{r(x.start_date, x.end_date)}</strong></p>
        <p className="muted" style={{ margin: "4px 0 0" }}>Approving is refused if the new dates would put someone over 8 hours a day or leave a person's own dates outside the project.</p>
      </div>
    );
  }
  if (p.kind === "member_update") {
    const lvl = (n: number | null | undefined) => (n == null ? "—" : `${n} · ${LEVELS[n]}`);
    return (
      <div>
        <div className="row"><strong>{x.employee_name}</strong><span className="muted">asked to change their own record</span></div>
        {!!x.ratings?.length && (
          <div className="tablewrap"><table>
            <thead><tr><th>Skill</th><th>Now</th><th>Requested</th></tr></thead>
            <tbody>{x.ratings.map((r: any, i: number) => (
              <tr key={i}><td>{r.skill}</td><td>{r.before ? lvl(r.before) : <span className="muted">not listed</span>}</td>
                <td><strong>{r.rating === 0 ? "Remove skill" : lvl(r.rating)}</strong></td></tr>
            ))}</tbody>
          </table></div>
        )}
        {x.roles && <p style={{ margin: "6px 0 0" }}><span className="muted">Roles:</span> {(x.roles_before ?? []).join(", ") || "none"} → <strong>{x.roles.join(", ") || "none"}</strong></p>}
        {x.domain_tags && <p style={{ margin: "6px 0 0" }}><span className="muted">Domain expertise:</span> {(x.domain_tags_before ?? []).join(", ") || "none"} → <strong>{x.domain_tags.join(", ") || "none"}</strong></p>}
        {x.background != null && <details style={{ marginTop: 6 }} open><summary className="muted" style={{ cursor: "pointer" }}>Background</summary>
          <p style={{ margin: "4px 0" }}><span className="muted">Was:</span> {x.background_before || "empty"}</p><p style={{ margin: "4px 0" }}><span className="muted">Now:</span> {x.background || "empty"}</p></details>}
        <p className="muted" style={{ margin: "6px 0 0" }}>Approving sets exactly these values on {x.employee_name}'s record. The seniority label is recomputed from the new levels.</p>
      </div>
    );
  }
  if (p.kind === "member_learning_plan") {
    return (
      <div>
        <div className="row"><strong>{x.employee_name}</strong>{x.edited ? <Pill tone="warn">edited by the member</Pill> : <Pill>as generated</Pill>}{x.mode && <Pill tone="info">{x.mode}</Pill>}
          <span className="muted">{x.weeks} weeks · {x.hours_per_week} h/week</span></div>
        <div style={{ margin: "6px 0" }}>{(x.requirements ?? []).map((r: any, i: number) => <Pill key={i} tone="info">{r.skill} → {r.level}</Pill>)}</div>
        {(x.problems ?? []).map((m: string, i: number) => <Alert key={i} tone="warn">{m}</Alert>)}
        <details><summary className="muted" style={{ cursor: "pointer" }}>Read the plan</summary><Markdown>{x.markdown}</Markdown></details>
        <p className="muted" style={{ margin: "6px 0 0" }}>Approving saves this plan on {x.employee_name}'s profile.</p>
      </div>
    );
  }
  if (p.kind === "employee_profile" || p.kind === "member_cv") {
    return (
      <div>
        <div className="row"><strong>{x.name ?? x.employee_name}</strong>{x.employee_no && <Pill>{x.employee_no}</Pill>}{x.seniority && <Pill tone="info">Seniority by rule: {x.seniority}</Pill>}{!!x.roles?.length && <span className="muted">{x.roles.join(", ")}</span>}</div>
        {x.background && <p className="muted" style={{ margin: "4px 0" }}>{x.background}</p>}
        <div>{(x.skills ?? []).map((s: any, i: number) => <Pill key={i} tone={s.needs_review ? "warn" : "ok"}>{s.raw_skill} · {s.rating}/4{s.needs_review ? " · check" : ""}</Pill>)}</div>
        <details style={{ marginTop: 4 }}><summary className="muted" style={{ cursor: "pointer" }}>Evidence quotes from the CV</summary>
          <ul style={{ margin: "4px 0", paddingLeft: 18 }}>{(x.skills ?? []).filter((s: any) => s.evidence).map((s: any, i: number) => <li key={i}><strong>{s.raw_skill}</strong>: “{s.evidence}”</li>)}</ul></details>
        <p className="muted" style={{ margin: "4px 0 0" }}>Levels are AI estimates checked by the uploader. An existing employee only gets missing skills and roles added; nothing is lowered or removed.</p>
      </div>
    );
  }
  return (
    <div>
      <div>{(x.requirements ?? []).map((r: any, i: number) => <Pill key={i} tone="info">{r.skill} ≥ {r.level}{r.min_people > 1 ? ` ×${r.min_people}` : ""}{r.domain_tags?.length ? ` · ${r.domain_tags.join(", ")}` : ""}</Pill>)}</div>
      <div className="row" style={{ marginTop: 4 }}>
        <span className="muted">Head count</span>
        {p.status === "pending" && p.can_decide
          ? <input type="number" min={1} style={{ width: 70 }} value={edit.headcount ?? x.headcount ?? ""} onChange={(e) => setEdit({ ...edit, headcount: e.target.value ? Number(e.target.value) : null })} />
          : <strong>{x.headcount ?? "—"}</strong>}
        {!!x.roles?.length && <span className="muted">Roles: {x.roles.join(", ")}</span>}
      </div>
      <div className="row" style={{ marginTop: 4 }}>
        <span className="muted">Project dates (optional)</span>
        {p.status === "pending" && p.can_decide ? (
          <>
            <input type="date" value={edit.start_date ?? x.start_date ?? ""} max={(edit.end_date ?? x.end_date) || undefined} aria-label="Project start date" onChange={(e) => setEdit({ ...edit, start_date: e.target.value || null })} />
            <input type="date" value={edit.end_date ?? x.end_date ?? ""} min={(edit.start_date ?? x.start_date) || undefined} aria-label="Project end date" onChange={(e) => setEdit({ ...edit, end_date: e.target.value || null })} />
          </>
        ) : <strong>{x.start_date || x.end_date ? `${x.start_date ?? "…"} to ${x.end_date ?? "…"}` : "not set"}</strong>}
      </div>
      {x.notes && <p className="muted" style={{ margin: "4px 0 0" }}>{x.notes}</p>}
    </div>
  );
}

function Item({ p, done }: { p: Proposal; done: () => void }) {
  const [edit, setEdit] = useState<Record<string, any>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function decide(action: "approve" | "reject") {
    setBusy(true); setErr(null);
    try {
      const r = await post<{ message?: string }>(`/api/agents/proposals/${p.id}/decision`, { action, note, edits: Object.keys(edit).length ? edit : undefined });
      setMsg(r.message ?? (action === "approve" ? "Approved and applied." : "Rejected."));
      done();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="row">
        <strong>{p.title}</strong><Pill tone="info">{KIND[p.kind]}</Pill>
        {p.confidence != null && <Pill>confidence {Math.round(p.confidence * 100)}%</Pill>}
        <span className="spacer" /><span className="muted">{p.requested_by_name ? `requested by ${p.requested_by_name}` : `${p.agent} agent`} · {p.created_at}</span>
      </div>
      <div style={{ margin: "8px 0" }}><Body p={p} edit={edit} setEdit={setEdit} /></div>
      {p.evidence && <p className="muted">Why: {p.evidence}</p>}
      {p.status !== "pending" && <p className="muted">{p.status === "auto_applied" ? "Applied automatically" : p.status[0].toUpperCase() + p.status.slice(1)} by {p.decided_by}{p.note && ` — ${p.note}`}</p>}
      {err && <Alert tone="bad">{err}</Alert>}
      {msg && <Alert tone="ok">{msg} {p.kind === "upload_fix" && <Link href="/upload">Open Upload</Link>}</Alert>}
      {p.status === "pending" && !msg && (p.can_decide ? (
        <div className="row">
          <input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 160 }} />
          <button className="btn" disabled={busy} onClick={() => decide("approve")}>Approve</button>
          <button className="btn ghost" disabled={busy} onClick={() => decide("reject")}>Reject</button>
        </div>
      ) : <p className="muted">{p.approver ? `Waiting for ${p.approver}.` : `Only a ${WHO[p.kind]} can decide this.`}</p>)}
    </div>
  );
}

export default function ApprovalsPage() {
  const { user } = useUser();
  return user?.role === "member" ? <MemberSubmissions /> : <StaffApprovals />;
}

/** A member sees only their own submissions and where each one stands. They never decide anything. */
function MemberSubmissions() {
  const list = useApi<Proposal[]>("/api/agents/proposals?status=all");
  return (
    <>
      <h1>My submissions</h1>
      <p className="sub">Everything you change goes to your team leader first. Nothing on your profile changes until it is approved.</p>
      {list.loading && <Loading />}
      {list.error && <Alert tone="bad">{list.error}</Alert>}
      {list.data?.length === 0 && <Empty>You have not sent anything yet.</Empty>}
      {list.data?.map((p) => (
        <div className="card" key={p.id}>
          <div className="row">
            <strong>{p.title}</strong><Pill tone="info">{KIND[p.kind]}</Pill>
            <Pill tone={p.status === "approved" ? "ok" : p.status === "rejected" ? "bad" : "warn"}>{STATUS_LABEL[p.status] ?? p.status}</Pill>
            <span className="spacer" /><span className="muted">{p.created_at}</span>
          </div>
          <div style={{ margin: "8px 0" }}><Body p={p} edit={{}} setEdit={() => {}} /></div>
          {p.status === "pending"
            ? <p className="muted">Waiting for {p.approver ?? "your team leader"}.</p>
            : <p className="muted">{STATUS_LABEL[p.status]} by {p.decided_by}{p.note && ` — ${p.note}`}</p>}
        </div>
      ))}
    </>
  );
}

function StaffApprovals() {
  const { refresh, user } = useUser();
  const router = useRouter();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("pending");
  const list = useApi<Proposal[]>(tab === "briefs" ? null : `/api/agents/proposals?status=${tab}`);
  const briefs = useApi<Brief[]>(tab === "briefs" ? "/api/agents/briefs" : null);
  const counts = useApi<ApprovalCounts>("/api/agents/approval-counts");
  const canCancelBrief = user?.role === "admin" || user?.role === "hr_manager";

  function openInPlanner(b: Brief) {
    try { localStorage.setItem("skillmatrix.project", JSON.stringify({ prompt: b.prompt || b.title, requirements: b.requirements, headcount: b.headcount, roles: b.roles, briefId: b.id })); } catch {}
    router.push("/projects");
  }

  return (
    <>
      <h1>Approvals</h1>
      <p className="sub">What agents proposed. HR managers decide skill mappings and upload settings; project managers and team leaders decide project briefs; HR managers approve who is assigned to a project.</p>
      {(counts.error) && <Alert tone="bad">{counts.error}</Alert>}
      <div className="tabs">{TABS.map(([k, l]) => {
        const count = k === "pending" ? counts.data?.waiting : k === "briefs" ? counts.data?.approved_briefs : undefined;
        return <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}{count != null ? ` (${count})` : ""}</button>;
      })}</div>
      {(list.loading || briefs.loading) && <Loading />}
      {(list.error || briefs.error) && <Alert tone="bad">{list.error || briefs.error}</Alert>}
      {tab !== "briefs" && list.data?.length === 0 && <Empty>Nothing here.</Empty>}
      {tab !== "briefs" && list.data?.map((p) => <Item key={p.id} p={p} done={() => { refresh(); setTimeout(list.reload, 600); counts.reload(); }} />)}
      {tab === "briefs" && briefs.data?.length === 0 && <Empty>No approved project briefs yet.</Empty>}
      {tab === "briefs" && briefs.data?.map((b) => (
        <ApprovedBrief
          key={b.id}
          brief={b}
          canCancel={canCancelBrief}
          openInPlanner={() => openInPlanner(b)}
          onCancelled={() => { briefs.reload(); counts.reload(); }}
        />
      ))}
    </>
  );
}

function ApprovedBrief({
  brief, canCancel, openInPlanner, onCancelled,
}: {
  brief: Brief;
  canCancel: boolean;
  openInPlanner: () => void;
  onCancelled: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await post(`/api/agents/briefs/${brief.id}/cancel`, { reason });
      onCancelled();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="row"><strong>{brief.title}</strong><span className="spacer" /><span className="muted">approved by {brief.approved_by} · {brief.created_at}</span></div>
      <div style={{ margin: "8px 0" }}>{brief.requirements.map((r, i) => <Pill key={i} tone="info">{r.skill} ≥ {r.level}{r.domain_tags?.length ? ` · ${r.domain_tags.join(", ")}` : ""}</Pill>)}</div>
      <p className="muted">Head count: {brief.headcount ?? "—"}{brief.roles.length > 0 && ` · Roles: ${brief.roles.join(", ")}`}{(brief.start_date || brief.end_date) && ` · Dates: ${brief.start_date ?? "…"} to ${brief.end_date ?? "…"}`}</p>
      <ProjectTeam briefId={brief.id} canRequest={false} />
      <div className="row">
        <button className="btn sm" onClick={openInPlanner}>Open in project planner</button>
        {canCancel && !editing && <button className="btn danger sm" onClick={() => setEditing(true)}>Cancel project</button>}
      </div>
      {editing && (
        <div style={{ marginTop: 12 }}>
          <label className="f" htmlFor={`cancel-reason-${brief.id}`}>Why is this approved project being dropped? (required)</label>
          <textarea
            id={`cancel-reason-${brief.id}`}
            value={reason}
            maxLength={1000}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Enter the reason for cancelling this approved project"
          />
          {error && <Alert tone="bad">{error}</Alert>}
          <div className="row">
            <button className="btn danger" disabled={busy || !reason.trim()} onClick={cancel}>
              {busy ? "Cancelling…" : "Confirm cancellation"}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => { setEditing(false); setReason(""); setError(null); }}>Keep project</button>
          </div>
        </div>
      )}
    </div>
  );
}
