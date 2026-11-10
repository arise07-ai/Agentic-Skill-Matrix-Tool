"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { post, useApi } from "@/lib/api";
import type { MemberProfile, Proposal } from "@/lib/types";
import { Alert, LEVELS, Loading, Markdown, Pill, SeniorityBadge } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** A member's own profile. Nothing here changes the matrix directly: "Send for approval" goes to the member's team leader. */
export default function MyProfilePage() {
  const { refresh } = useUser();
  const { data, error, loading, reload } = useApi<MemberProfile>("/api/me/profile");
  const pending = useApi<Proposal[]>("/api/agents/proposals?status=pending");
  const [levels, setLevels] = useState<Record<string, number>>({});
  const [added, setAdded] = useState<{ skill: string; rating: number }[]>([]);
  const [newSkill, setNewSkill] = useState("");
  const [newLevel, setNewLevel] = useState(2);
  const [roles, setRoles] = useState("");
  const [tags, setTags] = useState("");
  const [background, setBackground] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const emp = data?.employee;
  useEffect(() => {
    if (!emp) return;
    setLevels(Object.fromEntries(emp.skills.map((s) => [s.canonical_skill, s.rating ?? 0])));
    setRoles(emp.roles.join(", ")); setTags(emp.domain_tags.join(", ")); setBackground(emp.background ?? "");
    setAdded([]);
  }, [emp]);

  const changedRatings = useMemo(() => {
    if (!emp) return [];
    const out = emp.skills.filter((s) => (levels[s.canonical_skill] ?? 0) !== (s.rating ?? 0)).map((s) => ({ skill: s.canonical_skill, rating: levels[s.canonical_skill] ?? 0 }));
    return [...out, ...added];
  }, [emp, levels, added]);
  const profileChanged = !!emp && (
    [...list(roles)].sort().join("|") !== [...emp.roles].sort().join("|") || [...list(tags)].sort().join("|") !== [...emp.domain_tags].sort().join("|") || background.trim() !== (emp.background ?? "").trim());
  const dirty = changedRatings.length > 0 || profileChanged;

  if (loading) return <Loading />;
  if (error || !data || !emp) return <Alert tone="bad">{error ?? "Could not load your profile"}</Alert>;

  const have = new Set(emp.skills.map((s) => s.canonical_skill.toLowerCase()).concat(added.map((a) => a.skill.toLowerCase())));
  const options = data.skill_options.filter((o) => !have.has(o.name.toLowerCase()));

  function addSkill() {
    const name = newSkill.trim();
    if (!name || have.has(name.toLowerCase())) return;
    setAdded([...added, { skill: name, rating: newLevel }]); setNewSkill("");
  }

  async function send() {
    setBusy(true); setErr(null); setOk(null);
    try {
      const body: Record<string, unknown> = { ratings: changedRatings };
      if (profileChanged) { body.roles = list(roles); body.domain_tags = list(tags); body.background = background; }
      const r = await post<{ proposal_id: number; approver_label: string; duplicate?: boolean }>("/api/me/changes", body);
      setOk(r.duplicate ? "These changes are already waiting for approval."
        : `Sent to ${r.approver_label} for approval (#${r.proposal_id}). Your profile changes when they approve it.`);
      refresh(); pending.reload();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  const waiting = pending.data?.length ?? 0;
  return (
    <>
      <div className="row">
        <h1 style={{ margin: 0 }}>{emp.name}</h1><SeniorityBadge s={emp.seniority} />
      </div>
      <p className="sub">{emp.employee_no ?? "No employee number"} · {emp.roles.join(", ") || "No role"}. Changes you make here are <strong>not applied</strong> until {data.approver_label} approves them.</p>
      {emp.seniority && <p className="muted" style={{ marginTop: -8 }}>{emp.seniority.reason}</p>}
      {!data.team_leader && <Alert tone="warn">No team leader is set for your account yet, so an admin will decide your changes. An admin or HR manager can assign one.</Alert>}
      {waiting > 0 && <Alert tone="info">{waiting} of your submission{waiting > 1 ? "s are" : " is"} waiting for approval. <Link href="/approvals">See My submissions</Link></Alert>}
      {err && <Alert tone="bad">{err}</Alert>}
      {ok && <Alert tone="ok">{ok}</Alert>}

      <div className="card">
        <h2>Profile</h2>
        <div className="grid g4">
          <div style={{ gridColumn: "span 2" }}><label className="f" htmlFor="me-roles">Roles (comma separated)</label>
            <input id="me-roles" style={{ width: "100%" }} value={roles} onChange={(e) => setRoles(e.target.value)} /></div>
          <div style={{ gridColumn: "span 2" }}><label className="f" htmlFor="me-tags">Domain expertise (comma separated)</label>
            <input id="me-tags" style={{ width: "100%" }} value={tags} onChange={(e) => setTags(e.target.value)} /></div>
        </div>
        <label className="f" htmlFor="me-bg" style={{ marginTop: 10 }}>Background</label>
        <textarea id="me-bg" style={{ width: "100%" }} rows={4} maxLength={1500} value={background} onChange={(e) => setBackground(e.target.value)} />
        <p className="muted">Your name and employee number are managed by HR.</p>
      </div>

      <div className="card">
        <h2>My skill levels</h2>
        <p className="muted" style={{ marginTop: 0 }}>0 None · 1 Learned · 2 Applied · 3 Experienced · 4 Mastered. Setting a skill to 0 asks to remove it.</p>
        <div className="tablewrap"><table>
          <thead><tr><th>Skill</th><th>Category</th><th>Level</th><th /></tr></thead>
          <tbody>
            {emp.skills.map((s) => {
              const cur = levels[s.canonical_skill] ?? 0;
              const changed = cur !== (s.rating ?? 0);
              return (
                <tr key={s.canonical_skill}>
                  <td>{s.canonical_skill}</td><td className="muted">{s.category}</td>
                  <td><select value={cur} aria-label={`Level for ${s.canonical_skill}`} onChange={(e) => setLevels({ ...levels, [s.canonical_skill]: Number(e.target.value) })}>
                    {LEVELS.map((n, v) => <option key={v} value={v}>{v} · {n}</option>)}</select></td>
                  <td>{changed && <Pill tone="warn">{s.rating ?? 0} → {cur}</Pill>}</td>
                </tr>
              );
            })}
            {added.map((a, i) => (
              <tr key={`new-${a.skill}`}>
                <td>{a.skill} <Pill tone="info">new</Pill></td><td className="muted">—</td>
                <td><select value={a.rating} aria-label={`Level for ${a.skill}`} onChange={(e) => setAdded(added.map((x, j) => (j === i ? { ...x, rating: Number(e.target.value) } : x)))}>
                  {LEVELS.slice(1).map((n, v) => <option key={v + 1} value={v + 1}>{v + 1} · {n}</option>)}</select></td>
                <td><button className="btn ghost sm" onClick={() => setAdded(added.filter((_, j) => j !== i))}>Remove</button></td>
              </tr>
            ))}
            {emp.skills.length === 0 && added.length === 0 && <tr><td colSpan={4} className="muted">No skills yet. Add one below or upload your CV.</td></tr>}
          </tbody>
        </table></div>
        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label className="f" htmlFor="me-newskill">Add a skill</label>
            <input id="me-newskill" list="me-skill-options" style={{ minWidth: 240 }} placeholder="Pick from the list or type a new one" value={newSkill} onChange={(e) => setNewSkill(e.target.value)} />
            <datalist id="me-skill-options">{options.map((o) => <option key={o.name} value={o.name}>{o.category}</option>)}</datalist>
          </div>
          <div><label className="f" htmlFor="me-newlevel">Level</label>
            <select id="me-newlevel" value={newLevel} onChange={(e) => setNewLevel(Number(e.target.value))}>{LEVELS.slice(1).map((n, v) => <option key={v + 1} value={v + 1}>{v + 1} · {n}</option>)}</select></div>
          <button className="btn ghost" disabled={!newSkill.trim()} onClick={addSkill}>Add</button>
        </div>
      </div>

      <div className="card row">
        <span className="muted">{dirty ? `${changedRatings.length} skill change(s)${profileChanged ? " and profile changes" : ""} ready to send.` : "No changes yet."}</span>
        <span className="spacer" />
        <button className="btn" disabled={!dirty || busy} onClick={send}>{busy ? "Sending…" : "Send for approval"}</button>
      </div>

      {data.learning_plans.length > 0 && (
        <div className="card">
          <h2>My approved learning plans</h2>
          {data.learning_plans.map((p) => (
            <details key={p.id} style={{ marginBottom: 8 }}>
              <summary style={{ cursor: "pointer" }}><strong>{p.title}</strong> <span className="muted">· approved by {p.approved_by} · {p.created_at}</span></summary>
              <Markdown>{p.markdown}</Markdown>
            </details>
          ))}
        </div>
      )}
    </>
  );
}
