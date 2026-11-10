"use client";
import { useRef, useState } from "react";
import { api, post } from "@/lib/api";
import type { CvPreview, CvSkill } from "@/lib/types";
import { Alert, LEVELS, Pill, SeniorityBadge } from "@/components/ui";

type Edit = { name: string; employee_no: string; roles: string; background: string; skills: CvSkill[] };

/** One CV (PDF or Word). The AI proposes, code checks the quotes and the seniority, YOU correct it, a person approves it
 *  (an HR manager or admin for staff; the member's own team leader for a member's CV, which can only ever go onto their own record). */
export default function CvUpload({ member = false }: { member?: boolean }) {
  const [cv, setCv] = useState<CvPreview | null>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function send(file: File) {
    setBusy(true); setError(null); setDone(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api<CvPreview>(member ? "/api/me/cv" : "/api/uploads/cv", { method: "POST", body: fd });
      setCv(r);
      setEdit({ name: r.candidate.name ?? "", employee_no: r.candidate.employee_no ?? "", roles: r.candidate.roles.join(", "), background: r.candidate.background, skills: r.candidate.skills });
    } catch (e) { setError((e as Error).message); setCv(null); setEdit(null); }
    finally { setBusy(false); }
  }

  const setSkill = (i: number, patch: Partial<CvSkill>) => edit && setEdit({ ...edit, skills: edit.skills.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  async function submit() {
    if (!cv || !edit) return;
    setBusy(true); setError(null);
    try {
      const common = {
        roles: edit.roles.split(",").map((r) => r.trim()).filter(Boolean), background: edit.background,
        skills: edit.skills.map((s) => ({ raw_skill: s.raw_skill, rating: s.rating, evidence: s.evidence })),
      };
      const res = await post<{ proposal_id: number; duplicate?: boolean; approver_label?: string }>(
        member ? `/api/me/cv/${cv.upload_id}/submit` : `/api/uploads/cv/${cv.upload_id}/submit`,
        member ? common : { name: edit.name.trim(), employee_no: edit.employee_no.trim() || null, ...common });
      setDone(res.duplicate ? "This CV record is already waiting for approval."
        : member ? `Sent to ${res.approver_label ?? "your team leader"} for approval (#${res.proposal_id}). Your record does not change until it is approved; follow it under My submissions.`
        : `Sent for approval (proposal #${res.proposal_id}). An HR manager or admin decides it under Approvals; nothing is in the matrix until then.`);
      setCv(null); setEdit(null);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <p className="muted">Upload one person's CV (PDF or Word .docx). The AI <strong>estimates</strong> each skill level from the text (never above 3 · Experienced) and quotes the sentence it used. The app checks that each quote is really in the CV and computes Junior / Mid / Senior by rule. You review and correct, then {member ? "your team leader approves. Only missing skills and roles are added to your own record; nothing is lowered or removed." : "an HR manager approves."}</p>
      {error && <Alert tone="bad">{error}</Alert>}
      {done && <Alert tone="ok">{done}</Alert>}
      {!cv && (
        <div className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0}
          onClick={() => input.current?.click()} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) send(f); }}>
          {busy ? "Reading the CV…" : <>Drop a CV here, or click to choose<br /><span style={{ fontSize: 12 }}>.pdf or .docx · max 10 MB</span></>}
          <input ref={input} type="file" hidden accept=".pdf,.docx" onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }} />
        </div>
      )}
      {cv && edit && (
        <>
          <div className="card">
            <div className="row"><h2 style={{ margin: 0 }}>{cv.filename}</h2><span className="spacer" /><button className="btn ghost sm" onClick={() => { setCv(null); setEdit(null); }}>Choose another CV</button></div>
            <div className="row" style={{ margin: "10px 0" }}>
              <strong>Seniority by rule:</strong> <SeniorityBadge s={cv.seniority} />
              <span className="muted">{cv.seniority_explanation} {cv.seniority_explanation_source === "ai" ? "(worded by AI from the computed numbers)" : ""}</span>
            </div>
            <p className="muted" style={{ marginTop: 0 }}>The label is computed from the levels below as estimated. If you change levels, it is recalculated when the record is approved.</p>
            {!member && cv.existing_employee && (
              <Alert tone="info">{cv.existing_employee.ambiguous
                ? `Several employees are called ${cv.existing_employee.name}. Add the employee number below so the right person is updated.`
                : `${cv.existing_employee.name} is already in the matrix. Approving will only ADD skills and roles that are missing; existing ratings are kept and nothing is removed.`}</Alert>
            )}
            <div className="grid g4">
              {!member && <><div><label className="f">Name *</label><input style={{ width: "100%" }} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></div>
              <div><label className="f">Employee number</label><input style={{ width: "100%" }} value={edit.employee_no} onChange={(e) => setEdit({ ...edit, employee_no: e.target.value })} /></div></>}
              <div style={{ gridColumn: "span 2" }}><label className="f">Roles (comma separated)</label><input style={{ width: "100%" }} value={edit.roles} onChange={(e) => setEdit({ ...edit, roles: e.target.value })} /></div>
            </div>
            <label className="f" style={{ marginTop: 10 }}>Background</label>
            <textarea style={{ width: "100%" }} rows={3} value={edit.background} onChange={(e) => setEdit({ ...edit, background: e.target.value })} />
          </div>
          {cv.candidate.warnings.length > 0 && <Alert tone="warn"><ul style={{ margin: 0, paddingLeft: 18 }}>{cv.candidate.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Alert>}
          <div className="card">
            <h2>Skills (levels estimated by AI, please check)</h2>
            <div className="tablewrap"><table>
              <thead><tr><th>Skill</th><th>Level</th><th>Evidence from the CV</th><th /></tr></thead>
              <tbody>
                {edit.skills.map((s, i) => (
                  <tr key={i}>
                    <td>{s.raw_skill}</td>
                    <td><select value={s.rating} aria-label={`Level for ${s.raw_skill}`} onChange={(e) => setSkill(i, { rating: Number(e.target.value) })}>
                      {LEVELS.map((n, v) => <option key={v} value={v}>{v} · {n}</option>)}</select></td>
                    <td>{s.verified ? <><Pill tone="ok">quote found</Pill> <span className="muted">“{s.evidence}”</span></> : <><Pill tone="warn">check</Pill> <span className="muted">{s.note || "no evidence quote"}</span></>}</td>
                    <td><button className="btn ghost sm" onClick={() => setEdit({ ...edit, skills: edit.skills.filter((_, j) => j !== i) })}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
            <p className="muted">A level of 0 (None) is not stored. Level 4 (Mastered) can only be set by you here; the AI never estimates it.</p>
          </div>
          <div className="card row">
            <span className="spacer" />
            <button className="btn" disabled={busy || (!member && !edit.name.trim()) || !edit.skills.some((s) => s.rating > 0)} onClick={submit}>{busy ? "Sending…" : "Send for approval"}</button>
          </div>
        </>
      )}
    </>
  );
}
