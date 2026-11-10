"use client";
import { useState } from "react";
import { post, useApi } from "@/lib/api";
import type { EmployeeRow, MemberAccount, TeamLeaderOption } from "@/lib/types";
import { Alert, Empty, Loading, Pill } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

type MembersData = { members: MemberAccount[]; team_leaders: TeamLeaderOption[] };

/** Member accounts. The admin creates them and links each to ONE employee; the admin or an HR manager picks the team leader who approves
 *  that member's changes. With no team leader (or a disabled one) the admin decides. */
export default function MembersPage() {
  const { user } = useUser();
  const isAdmin = user?.role === "admin";
  const { data, error, loading, reload } = useApi<MembersData>("/api/auth/members");
  const employees = useApi<EmployeeRow[]>(isAdmin ? "/api/employees" : null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ username: "", display_name: "", password: "", employee_id: "", team_leader_id: "" });

  if (user && user.role !== "admin" && user.role !== "hr_manager") return <Alert tone="bad">Only an admin or an HR manager can open this page.</Alert>;
  if (loading) return <Loading />;
  if (error || !data) return <Alert tone="bad">{error ?? "Could not load members"}</Alert>;

  const linked = new Set(data.members.map((m) => m.employee_id));
  const free = (employees.data ?? []).filter((e) => !linked.has(e.id));

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const setLeader = (m: MemberAccount, value: string) =>
    run(() => post(`/api/auth/members/${m.id}/team-leader`, { team_leader_id: value ? Number(value) : null }, "PATCH"), `Team leader saved for ${m.display_name}.`);
  const setEmployee = (m: MemberAccount, value: string) =>
    run(() => post(`/api/auth/users/${m.id}`, { employee_id: Number(value) }, "PATCH"), `${m.display_name} is now linked to another employee.`);
  const create = () => run(async () => {
    await post("/api/auth/users", {
      username: f.username.trim(), display_name: f.display_name.trim(), password: f.password, role: "member",
      employee_id: Number(f.employee_id), team_leader_id: f.team_leader_id ? Number(f.team_leader_id) : null,
    });
    setF({ username: "", display_name: "", password: "", employee_id: "", team_leader_id: "" });
  }, "Member account created.");

  return (
    <>
      <h1>Members & team leaders</h1>
      <p className="sub">A member can sign in, but only works on their own record: upload their CV, edit their skill levels and profile, and build a learning path.
        Every change waits for the team leader chosen here. Without one, an admin decides.</p>
      {err && <Alert tone="bad">{err}</Alert>}
      {msg && <Alert tone="ok">{msg}</Alert>}

      <div className="card">
        <h2>Member accounts</h2>
        {data.members.length === 0 && <Empty>No member accounts yet.</Empty>}
        {data.members.length > 0 && (
          <div className="tablewrap"><table>
            <thead><tr><th>Account</th><th>Employee record</th><th>Team leader (approves their changes)</th><th /></tr></thead>
            <tbody>
              {data.members.map((m) => (
                <tr key={m.id}>
                  <td>{m.display_name} <span className="muted">({m.username})</span></td>
                  <td>{isAdmin
                    ? <select value={m.employee_id ?? ""} disabled={busy} aria-label={`Employee for ${m.username}`} onChange={(e) => setEmployee(m, e.target.value)}>
                        {m.employee_id && <option value={m.employee_id}>{m.employee_name}</option>}
                        {free.map((e) => <option key={e.id} value={e.id}>{e.name}{e.employee_no ? ` · ${e.employee_no}` : ""}</option>)}
                      </select>
                    : m.employee_name}</td>
                  <td><select value={m.team_leader_id ?? ""} disabled={busy} aria-label={`Team leader for ${m.username}`} onChange={(e) => setLeader(m, e.target.value)}>
                    <option value="">— none: an admin decides —</option>
                    {data.team_leaders.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    {m.team_leader_id && !data.team_leaders.some((t) => t.id === m.team_leader_id) && <option value={m.team_leader_id}>{m.team_leader_name} (disabled)</option>}
                  </select></td>
                  <td>{!m.active && <Pill tone="bad">disabled</Pill>}{m.active && !m.team_leader_id && <Pill tone="warn">admin decides</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        {data.team_leaders.length === 0 && <p className="muted">There is no active Team Leader account yet. Create one under Users & audit log first.</p>}
      </div>

      {isAdmin && (
        <div className="card">
          <h2>Create a member account</h2>
          <div className="grid g4">
            <div><label className="f" htmlFor="m-user">Username</label><input id="m-user" style={{ width: "100%" }} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></div>
            <div><label className="f" htmlFor="m-name">Display name</label><input id="m-name" style={{ width: "100%" }} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></div>
            <div><label className="f" htmlFor="m-pass">Password (min. 10 characters)</label><input id="m-pass" type="password" autoComplete="new-password" style={{ width: "100%" }} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></div>
            <div><label className="f" htmlFor="m-emp">Linked employee *</label>
              <select id="m-emp" style={{ width: "100%" }} value={f.employee_id} onChange={(e) => setF({ ...f, employee_id: e.target.value })}>
                <option value="">Select…</option>{free.map((e) => <option key={e.id} value={e.id}>{e.name}{e.employee_no ? ` · ${e.employee_no}` : ""}</option>)}</select></div>
            <div><label className="f" htmlFor="m-tl">Team leader</label>
              <select id="m-tl" style={{ width: "100%" }} value={f.team_leader_id} onChange={(e) => setF({ ...f, team_leader_id: e.target.value })}>
                <option value="">— none: an admin decides —</option>{data.team_leaders.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></div>
          </div>
          <p className="muted">Only employees who are not yet linked to an account are listed. Employees come from an upload or an approved CV.</p>
          <button className="btn" disabled={busy || !f.username.trim() || f.password.length < 10 || !f.employee_id} onClick={create}>Create member</button>
        </div>
      )}
    </>
  );
}
