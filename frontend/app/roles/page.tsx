"use client";
import Link from "next/link";
import { useState } from "react";
import { api, post, useApi } from "@/lib/api";
import type { RoleCount, RoleMatchJob } from "@/lib/types";
import { Alert, Empty, Loading, Pill } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

/** The roles in use, with spelling variants. The AI step proposes fixes; an HR manager or the employee's team leader approves them. */
export default function RolesPage() {
  const { user, refresh } = useUser();
  const { data, error, loading, reload } = useApi<RoleCount[]>("/api/roles/overview");
  const [job, setJob] = useState<RoleMatchJob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const canBulk = user?.role === "admin" || user?.role === "hr_manager";

  async function matchAll() {
    setErr(null); setJob(null);
    try {
      const { job_id } = await post<{ job_id: string }>("/api/roles/match", { employee_ids: null });
      for (;;) {
        await new Promise((r) => setTimeout(r, 2000));
        const j = await api<RoleMatchJob>(`/api/roles/match/${job_id}`);
        setJob(j);
        if (j.status !== "running") { refresh(); reload(); break; }
      }
    } catch (e) { setErr((e as Error).message); }
  }

  if (loading) return <Loading />;
  if (error || !data) return <Alert tone="bad">{error ?? "Could not load roles"}</Alert>;
  const messy = data.filter((r) => r.variants.length > 1).length;
  const running = job?.status === "running";

  return (
    <>
      <h1>Roles</h1>
      <p className="sub">Every role in use. The AI compares each employee's roles with their skills and proposes: one spelling per role, roles their skills clearly support, and roles they do not.
        Code checks every proposal against what other holders of the role have; an HR manager or the employee's team leader approves each one under <Link href="/approvals">Approvals</Link>.</p>
      {err && <Alert tone="bad">{err}</Alert>}
      <div className="card row">
        <span>{data.length} roles{messy > 0 && <> · <Pill tone="warn">{messy} spelled in more than one way</Pill></>}</span><span className="spacer" />
        {canBulk
          ? <button className="btn" disabled={running} onClick={matchAll}>{running ? `Matching… ${job?.done ?? 0}/${job?.total ?? 0}` : "Match all employees with AI"}</button>
          : <span className="muted">Open an employee to match their roles. Matching everyone needs an HR manager or admin.</span>}
      </div>
      {job && job.status === "done" && (
        <Alert tone={job.proposals ? "ok" : "info"}>
          {job.proposals} role proposal{job.proposals === 1 ? "" : "s"} sent to <Link href="/approvals">Approvals</Link>.
          {job.skipped_pending > 0 && ` ${job.skipped_pending} employee(s) already had one waiting.`}
          {job.dropped > 0 && ` ${job.dropped} AI suggestion(s) were dropped because the data did not support them.`}
          {job.errors > 0 && ` ${job.errors} batch(es) failed; run it again.`} {job.notes.join(" ")}
        </Alert>
      )}
      {job?.status === "failed" && <Alert tone="bad">{job.error}</Alert>}
      {data.length === 0 ? <Empty>No roles yet. Upload employees first.</Empty> : (
        <div className="card tablewrap"><table>
          <thead><tr><th>Role</th><th>People</th><th>Also written as</th></tr></thead>
          <tbody>{data.map((r) => (
            <tr key={r.role}><td>{r.role}</td><td>{r.holders}</td>
              <td>{r.variants.slice(1).map((v) => <Pill key={v.spelling} tone="warn">{v.spelling} ({v.count})</Pill>)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
