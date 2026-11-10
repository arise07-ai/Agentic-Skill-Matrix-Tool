"use client";
import { useState } from "react";
import { post, useApi } from "@/lib/api";
import type { AuditRow, Role, User } from "@/lib/types";
import { Alert, Loading, Pill } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

const ROLES: [Role, string][] = [["project_manager", "Project Manager"], ["team_leader", "Team Leader"], ["hr_manager", "HR Manager"], ["admin", "Administrator"]];

export default function AdminPage() {
  const { user } = useUser();
  const users = useApi<User[]>(user?.role === "admin" ? "/api/auth/users" : null);
  const audit = useApi<AuditRow[]>(user?.role === "admin" ? "/api/auth/audit?limit=200" : null);
  const [f, setF] = useState({ username: "", password: "", role: "project_manager" as Role, display_name: "" });
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  if (user && user.role !== "admin") return <Alert tone="bad">Only an administrator can open this page.</Alert>;

  async function patch(id: number, body: Record<string, unknown>, ok: string) {
    try { await post(`/api/auth/users/${id}`, body, "PATCH"); setMsg({ tone: "ok", text: ok }); users.reload(); }
    catch (e) { setMsg({ tone: "bad", text: (e as Error).message }); }
  }
  async function create(e: React.FormEvent) {
    e.preventDefault();
    try { await post("/api/auth/users", f); setMsg({ tone: "ok", text: `User ${f.username} created.` }); setF({ ...f, username: "", password: "", display_name: "" }); users.reload(); audit.reload(); }
    catch (err) { setMsg({ tone: "bad", text: (err as Error).message }); }
  }
  function resetPassword(u: User) {
    const p = prompt(`New password for ${u.username} (at least 10 characters):`);
    if (p) patch(u.id, { password: p }, `Password changed for ${u.username}. Their sessions were ended.`);
  }

  return (
    <>
      <h1>Users & audit log</h1>
      <p className="sub">Only administrators see this page. Project managers, team leaders and HR managers can use every page; what differs is who may approve which agent proposal.</p>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

      <div className="card">
        <h2>Add a user</h2>
        <form className="row" onSubmit={create}>
          <input placeholder="Username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
          <input placeholder="Display name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <input type="password" placeholder="Password (10+ characters)" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <button className="btn" disabled={!f.username || f.password.length < 10}>Create</button>
        </form>
      </div>

      <div className="card">
        <h2>Users</h2>
        {users.loading && <Loading />}
        {users.error && <Alert tone="bad">{users.error}</Alert>}
        <div className="tablewrap"><table>
          <thead><tr><th>Username</th><th>Name</th><th>Role</th><th>Status</th><th /></tr></thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td><td>{u.display_name}</td>
                <td><select value={u.role} onChange={(e) => patch(u.id, { role: e.target.value }, `Role of ${u.username} changed.`)}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                <td>{u.active ? <Pill tone="ok">Active</Pill> : <Pill tone="bad">Disabled</Pill>}</td>
                <td className="row">
                  <button className="btn ghost sm" onClick={() => patch(u.id, { active: !u.active }, `${u.username} ${u.active ? "disabled" : "enabled"}.`)}>{u.active ? "Disable" : "Enable"}</button>
                  <button className="btn ghost sm" onClick={() => resetPassword(u)}>Reset password</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>

      <div className="card">
        <h2>Audit log (latest 200)</h2>
        {audit.loading && <Loading />}
        <div className="tablewrap"><table>
          <thead><tr><th>When (UTC)</th><th>Who</th><th>What</th><th>Details</th></tr></thead>
          <tbody>
            {audit.data?.map((a) => (
              <tr key={a.id}><td style={{ whiteSpace: "nowrap" }}>{a.ts}</td><td>{a.username}<br /><span className="muted">{a.role}</span></td><td>{a.action}</td>
                <td><code style={{ fontSize: 12, wordBreak: "break-word" }}>{JSON.stringify(a.detail)}</code></td></tr>
            ))}
          </tbody>
        </table></div>
      </div>
    </>
  );
}
