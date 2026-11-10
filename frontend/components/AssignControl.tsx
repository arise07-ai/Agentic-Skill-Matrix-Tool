"use client";
import { useState } from "react";
import { post } from "@/lib/api";
import type { Capacity } from "@/lib/types";
import { Alert, Pill } from "@/components/ui";

/** Allocation is a percent of an 8-hour working day. */
export const DAY_HOURS = 8;
export const hoursOf = (pct: number) => Math.round(pct * DAY_HOURS) / 100;

/** How much of this person's day is still free. */
export function CapacityPill({ cap }: { cap?: Capacity }) {
  const free = cap?.free_pct ?? 100;
  const tone = free === 0 ? "bad" : free <= 30 ? "warn" : "ok";
  const when = (a?: string | null, b?: string | null) => (a || b ? ` (${a ?? "…"} to ${b ?? "…"})` : "");
  const title = (cap?.window ? `Busiest day in ${cap.window}\n` : "") + (cap?.projects.length
    ? cap.projects.map((p) => `${p.title}: ${p.allocation_pct}% (${p.hours} h/day)${when(p.start, p.end)}`).join("\n")
    : "Not on any approved project in this period");
  return (
    <span title={title}>
      <Pill tone={tone}>{free === 0 ? "Fully booked" : `${free}% free · ${hoursOf(free)} h/day`}</Pill>
      {!!cap?.pending_pct && <span className="muted"> (+{cap.pending_pct}% waiting for HR)</span>}
    </span>
  );
}

/** A project manager asks HR to put this person on the chosen project. Nothing is saved until HR approves. */
export function AssignControl({ briefId, canRequest, employeeId, name, position, cap, onSent, projectStart, projectEnd }: {
  briefId: number | null | undefined; canRequest: boolean; employeeId: number; name: string; position: string; cap?: Capacity; onSent?: () => void;
  projectStart?: string | null; projectEnd?: string | null;
}) {
  const room = cap?.free_after_pending_pct ?? 100;
  const [open, setOpen] = useState(false);
  const [pct, setPct] = useState(Math.min(50, Math.max(room, 1)));
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!canRequest || !briefId) return null;
  if (msg) return <span className="muted">{msg}</span>;
  if (room === 0) return <span className="muted">No free time</span>;
  if (!open) return <button className="btn sm" onClick={() => setOpen(true)}>Assign</button>;

  async function send() {
    setBusy(true); setErr(null);
    try {
      const r = await post<{ message: string; duplicate?: boolean }>(`/api/assignments/project/${briefId}/requests`,
        { action: "assign", employee_id: employeeId, position, allocation_pct: pct, start_date: start || null, end_date: end || null });
      setMsg(r.duplicate ? "Already waiting for HR." : "Sent to HR for approval.");
      onSent?.();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div style={{ minWidth: 220 }}>
      <label className="f" htmlFor={`alloc-${employeeId}-${position}`}>{name}: share of the 8-hour day</label>
      <div className="row" style={{ gap: 6 }}>
        <input id={`alloc-${employeeId}-${position}`} type="number" min={1} max={room} style={{ width: 70 }} value={pct}
          onChange={(e) => setPct(Math.min(100, Math.max(1, Number(e.target.value) || 1)))} />
        <span className="muted">% = {hoursOf(pct)} h/day (up to {room}%)</span>
      </div>
      <div className="row" style={{ gap: 6, marginTop: 6 }}>
        <label className="muted">From <input type="date" value={start} min={projectStart ?? undefined} max={end || projectEnd || undefined} onChange={(e) => setStart(e.target.value)} aria-label={`Start date for ${name} (optional)`} /></label>
        <label className="muted">to <input type="date" value={end} min={start || projectStart || undefined} max={projectEnd ?? undefined} onChange={(e) => setEnd(e.target.value)} aria-label={`End date for ${name} (optional)`} /></label>
      </div>
      <p className="muted" style={{ margin: "4px 0 0" }}>Dates are optional. Blank follows the project{projectStart || projectEnd ? ` (${projectStart ?? "…"} to ${projectEnd ?? "…"})` : ""}.</p>
      {err && <Alert tone="bad">{err}</Alert>}
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn sm" disabled={busy || pct > room} onClick={send}>{busy ? "Sending…" : "Send to HR"}</button>
        <button className="btn ghost sm" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}
