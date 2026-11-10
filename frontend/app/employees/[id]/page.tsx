"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api, post, useApi } from "@/lib/api";
import type { EmployeeDetail, RoleMatchJob } from "@/lib/types";
import { Alert, Bar, Loading, Pill, SeniorityBadge, levelName } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

const ROLE_EDITORS = ["admin", "hr_manager", "project_manager", "team_leader"];

export default function EmployeePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: e, error, loading, reload } = useApi<EmployeeDetail>(`/api/employees/${id}`);
  const { user, refresh } = useUser();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  const access = useApi<{ can_edit: boolean }>(user && ROLE_EDITORS.includes(user.role) ? `/api/roles/employee/${id}/access` : null);
  const canEditRoles = !!access.data?.can_edit;  // a team leader only for their own members; the server checks too

  async function saveRoles() {
    setBusy(true); setNote(null);
    try {
      await api(`/api/roles/employee/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roles: draft.split(",").map((r) => r.trim()).filter(Boolean) }) });
      setEditing(false); setNote({ tone: "ok", text: "Roles saved." }); reload();
    } catch (err) { setNote({ tone: "bad", text: (err as Error).message }); } finally { setBusy(false); }
  }

  async function matchWithAi() {
    setBusy(true); setNote({ tone: "info", text: "The AI is comparing the roles with the skills…" });
    try {
      const { job_id } = await post<{ job_id: string }>("/api/roles/match", { employee_ids: [Number(id)] });
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const j = await api<RoleMatchJob>(`/api/roles/match/${job_id}`);
        if (j.status === "failed") throw new Error(j.error ?? "Role matching failed");
        if (j.status === "done") {
          refresh();
          setNote(j.proposals > 0 ? { tone: "ok", text: "A role proposal was sent to Approvals. An HR manager or this employee's team leader decides it." }
            : { tone: "info", text: j.skipped_pending > 0 ? "A role proposal for this employee is already waiting in Approvals." : (j.notes[0] ?? "The roles already match the skills. Nothing to propose.") });
          return;
        }
      }
      setNote({ tone: "info", text: "Still running. Check Approvals in a minute." });
    } catch (err) { setNote({ tone: "bad", text: (err as Error).message }); } finally { setBusy(false); }
  }

  if (loading) return <Loading />;
  if (error || !e) return <Alert tone="bad">{error ?? "Not found"}</Alert>;

  const byCat = e.skills.reduce<Record<string, typeof e.skills>>((acc, s) => ((acc[s.category] ||= []).push(s), acc), {});

  async function remove() {
    if (!confirm(`Delete ${e!.name}?`)) return;
    await api(`/api/employees/${id}`, { method: "DELETE" });
    router.push("/employees");
  }

  return (
    <>
      <p><Link href="/employees">← Employees</Link></p>
      <div className="row">
        <div>
          <h1>{e.name} <SeniorityBadge s={e.seniority} /></h1>
          <p className="sub">{e.employee_no ?? "No employee number"} · {e.roles.join(", ") || "No role"}</p>
          {e.seniority && <p className="muted" style={{ marginTop: -8 }}>{e.seniority.reason}</p>}
        </div>
        <span className="spacer" />
        <Link className="btn" href={`/learning?employee=${e.id}`}>Learning path</Link>
        <button className="btn ghost" onClick={remove}>Delete</button>
      </div>
      <div className="card">
        <div className="row"><h2 style={{ margin: 0 }}>Roles</h2><span className="spacer" />
          {canEditRoles && !editing && <button className="btn ghost sm" onClick={() => { setDraft(e.roles.join(", ")); setEditing(true); }}>Edit roles</button>}
          {canEditRoles && <button className="btn sm" disabled={busy} onClick={matchWithAi}>Match with AI</button>}
        </div>
        {note && <Alert tone={note.tone}>{note.text}</Alert>}
        {editing ? (
          <div className="row" style={{ marginTop: 8 }}>
            <input aria-label="Roles, comma separated" style={{ flex: 1, minWidth: 240 }} value={draft} onChange={(ev) => setDraft(ev.target.value)} placeholder="e.g. Developer, Tester" />
            <button className="btn" disabled={busy} onClick={saveRoles}>Save</button>
            <button className="btn ghost" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </div>
        ) : (
          <div className="row" style={{ marginTop: 8 }}>{e.roles.length ? e.roles.map((r) => <Pill key={r} tone="info">{r}</Pill>) : <span className="muted">No role</span>}</div>
        )}
        {canEditRoles && <p className="muted" style={{ marginBottom: 0 }}>Your edits apply at once and are logged. "Match with AI" only proposes: the AI compares roles with skills, code checks it, and an HR manager or this employee's team leader approves.</p>}
      </div>
      {e.domain_tags.length > 0 && <div className="card"><h2>Domain expertise</h2><div className="row">{e.domain_tags.map((tag) => <Pill key={tag} tone="info">{tag}</Pill>)}</div></div>}
      {e.background && <div className="card"><h2>Background</h2><p style={{ margin: 0 }}>{e.background}</p></div>}
      {Object.entries(byCat).map(([cat, skills]) => (
        <div className="card" key={cat}>
          <h2>{cat}</h2>
          <div className="tablewrap"><table>
            <thead><tr><th>Skill</th><th style={{ width: "30%" }}>Rating</th><th>Source text</th><th>Mapping</th></tr></thead>
            <tbody>
              {skills.map((s) => (
                <tr key={s.canonical_skill}>
                  <td>{s.canonical_skill}</td>
                  <td>{s.rating == null ? <Pill tone="warn">unrated</Pill> : <div className="row"><Bar value={s.rating} max={4} /><span>{s.rating}/4 · {levelName(s.rating)}</span></div>}</td>
                  <td className="muted">{s.raw_skill === s.canonical_skill ? "—" : s.raw_skill}</td>
                  <td><Pill tone={s.needs_review ? "warn" : "ok"}>{s.method}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      ))}
    </>
  );
}
