"use client";
import Link from "next/link";
import { useState } from "react";
import { post, useApi } from "@/lib/api";
import type { ProjectTeamData } from "@/lib/types";
import { Alert, Empty, Loading, Pill } from "@/components/ui";
import { hoursOf } from "@/components/AssignControl";

/** Who is on an approved project and how much of their day it takes. Project managers ask for changes; HR approves them. */
export default function ProjectTeam({ briefId, canRequest, reloadKey = 0, onChanged }: { briefId: number; canRequest: boolean; reloadKey?: number; onChanged?: () => void }) {
  const team = useApi<ProjectTeamData>(`/api/assignments/project/${briefId}?k=${reloadKey}`);
  const [editing, setEditing] = useState<number | null>(null);
  const [pct, setPct] = useState(50);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [editDates, setEditDates] = useState(false);
  const [pStart, setPStart] = useState("");
  const [pEnd, setPEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function ask(body: Record<string, unknown>, path = "requests") {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await post<{ message: string }>(`/api/assignments/project/${briefId}/${path}`, body);
      setMsg(r.message); setEditing(null); setEditDates(false); team.reload(); onChanged?.();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  if (team.loading && !team.data) return <Loading />;
  if (team.error) return <Alert tone="bad">{team.error}</Alert>;
  const t = team.data;
  if (!t) return null;
  const range = (a?: string | null, b?: string | null) => (a || b ? `${a ?? "…"} to ${b ?? "…"}` : "no dates");
  const ps = t.brief.start_date, pe = t.brief.end_date;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="row">
        <strong>Assigned team</strong>
        <Pill tone="info">{t.members.length}{t.brief.headcount ? ` of ${t.brief.headcount}` : ""} people</Pill>
        <span className="muted">{t.total_pct}% of a day in total = {t.total_hours} h/day</span>
      </div>
      <div className="row" style={{ marginTop: 4 }}>
        <span className="muted">Project dates: <strong>{ps || pe ? range(ps, pe) : "not set (counts as always running)"}</strong></span>
        {canRequest && !editDates && <button className="btn ghost sm" onClick={() => { setEditDates(true); setPStart(ps ?? ""); setPEnd(pe ?? ""); }}>Change dates</button>}
      </div>
      {editDates && (
        <div className="row" style={{ gap: 6, marginTop: 6 }}>
          <label className="muted">Start <input type="date" value={pStart} max={pEnd || undefined} onChange={(e) => setPStart(e.target.value)} /></label>
          <label className="muted">End <input type="date" value={pEnd} min={pStart || undefined} onChange={(e) => setPEnd(e.target.value)} /></label>
          <button className="btn sm" disabled={busy} onClick={() => ask({ start_date: pStart || null, end_date: pEnd || null }, "dates")}>Send to HR</button>
          <button className="btn ghost sm" disabled={busy} onClick={() => setEditDates(false)}>Cancel</button>
        </div>
      )}
      {msg && <Alert tone="ok">{msg}</Alert>}
      {err && <Alert tone="bad">{err}</Alert>}
      {t.members.length === 0 ? <Empty>Nobody is assigned yet.</Empty> : (
        <div className="tablewrap"><table>
          <thead><tr><th>Person</th><th>Position</th><th>On this project</th><th>Dates</th><th>Busiest day, all projects</th>{canRequest && <th />}</tr></thead>
          <tbody>{t.members.map((m) => (
            <tr key={m.assignment_id}>
              <td><Link href={`/employees/${m.employee_id}`}>{m.name}</Link></td>
              <td>{m.position || <span className="muted">—</span>}</td>
              <td>{m.allocation_pct}% · {m.hours} h/day</td>
              <td>{range(m.period_start, m.period_end)}{(m.start_date || m.end_date) && <span className="muted"> (own dates)</span>}</td>
              <td>{m.total_pct}% · {hoursOf(m.total_pct)} h/day{m.other_pct > 0 && <span className="muted"> ({m.other_pct}% elsewhere)</span>}</td>
              {canRequest && <td>
                {editing === m.assignment_id ? (
                  <div className="row" style={{ gap: 6 }}>
                    <input type="number" min={1} max={100} style={{ width: 70 }} value={pct} aria-label={`New share of the day for ${m.name}`}
                      onChange={(e) => setPct(Math.min(100, Math.max(1, Number(e.target.value) || 1)))} />
                    <span className="muted">% = {hoursOf(pct)} h</span>
                    <input type="date" value={from} min={ps ?? undefined} max={to || pe || undefined} onChange={(e) => setFrom(e.target.value)} aria-label={`Start date for ${m.name}`} />
                    <input type="date" value={to} min={from || ps || undefined} max={pe ?? undefined} onChange={(e) => setTo(e.target.value)} aria-label={`End date for ${m.name}`} />
                    <button className="btn sm" disabled={busy} onClick={() => ask({ action: "change", employee_id: m.employee_id, position: m.position, allocation_pct: pct, start_date: from || null, end_date: to || null })}>Send to HR</button>
                    <button className="btn ghost sm" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
                  </div>
                ) : (
                  <div className="row" style={{ gap: 6 }}>
                    <button className="btn ghost sm" disabled={busy} onClick={() => { setEditing(m.assignment_id); setPct(m.allocation_pct); setFrom(m.start_date ?? ""); setTo(m.end_date ?? ""); }}>Change time</button>
                    <button className="btn danger sm" disabled={busy} onClick={() => ask({ action: "remove", employee_id: m.employee_id, position: m.position })}>Remove</button>
                  </div>
                )}
              </td>}
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {t.waiting.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <span className="muted">Waiting for HR: </span>
          {t.waiting.map((w) => (
            <Pill key={w.proposal_id} tone="warn">
              {w.action === "dates" ? `New project dates: ${range(w.period_start, w.period_end)}` : `${w.employee_name} — ${w.action === "assign" ? `add at ${w.allocation_pct}%` : w.action === "change" ? `change to ${w.allocation_pct}%` : "remove"}`}
              {w.requested_by_name ? ` · by ${w.requested_by_name}` : ""}
            </Pill>
          ))}
        </div>
      )}
    </div>
  );
}
