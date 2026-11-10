"use client";
import { useState } from "react";
import { localTime, post, useApi } from "@/lib/api";
import type { AuditRow, LockedAccount, OpenSession, Role, User } from "@/lib/types";
import { Alert, Loading, Pill } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

const ROLES: [Role, string][] = [["project_manager", "Project Manager"], ["team_leader", "Team Leader"], ["hr_manager", "HR Manager"], ["admin", "Administrator"]];

export default function AdminPage() {
  const { user } = useUser();
  const users = useApi<User[]>(user?.role === "admin" ? "/api/auth/users" : null);
  const audit = useApi<AuditRow[]>(user?.role === "admin" ? "/api/auth/audit?limit=200" : null);
  const sessions = useApi<OpenSession[]>(user?.role === "admin" ? "/api/auth/sessions" : null);
  const locked = useApi<LockedAccount[]>(user?.role === "admin" ? "/api/auth/lockouts" : null);
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
  async function endSession(s: OpenSession) {
    const self = s.user_id === user?.id;
    if (!confirm(self ? "End your own session? You will be signed out." : `Sign ${s.display_name} out now?`)) return;
    try {
      await post(`/api/auth/sessions/${encodeURIComponent(s.id)}/end`, {});
      setMsg({ tone: "ok", text: `${s.display_name} was signed out.` }); sessions.reload(); audit.reload();
    } catch (e) { setMsg({ tone: "bad", text: (e as Error).message }); }
  }
  async function unlock(l: LockedAccount) {
    try {
      await post("/api/auth/lockouts/unlock", { username: l.username });
      setMsg({ tone: "ok", text: `${l.username} can sign in again.` }); locked.reload(); audit.reload();
    } catch (e) { setMsg({ tone: "bad", text: (e as Error).message }); }
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

      {!!locked.data?.length && (
        <div className="card">
          <h2>Locked sign-ins</h2>
          <p className="muted">After 5 wrong passwords within 15 minutes a username is locked for 15 minutes. Unlock it if you know the person.</p>
          <div className="tablewrap"><table>
            <thead><tr><th>Username</th><th>Account</th><th>Locked until</th><th /></tr></thead>
            <tbody>
              {locked.data.map((l) => (
                <tr key={l.username}>
                  <td>{l.username}</td>
                  <td>{l.user_id ? l.display_name : <Pill tone="warn">No such account</Pill>}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{localTime(l.locked_until)}</td>
                  <td><button className="btn ghost sm" onClick={() => unlock(l)}>Unlock</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      )}

      <div className="card">
        <h2>Active sessions</h2>
        <p className="muted">Each user can be signed in on one device at a time; signing in somewhere else ends the earlier session. Times are in your time zone.</p>
        {sessions.loading && <Loading />}
        {sessions.error && <Alert tone="bad">{sessions.error}</Alert>}
        {sessions.data && sessions.data.length === 0 && <p className="muted">Nobody is signed in.</p>}
        {!!sessions.data?.length && (
          <div className="tablewrap"><table>
            <thead><tr><th>User</th><th>Role</th><th>Device</th><th>Signed in</th><th>Last activity</th><th /></tr></thead>
            <tbody>
              {sessions.data.map((s) => (
                <tr key={s.id}>
                  <td>{s.display_name}<br /><span className="muted">{s.username}</span></td>
                  <td>{s.role}</td><td>{s.device}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{localTime(s.created_at)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{localTime(s.last_seen_at)}</td>
                  <td><button className="btn ghost sm" onClick={() => endSession(s)}>{s.user_id === user?.id ? "End (you)" : "Sign out"}</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
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
