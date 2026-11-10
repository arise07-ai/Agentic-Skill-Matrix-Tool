"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { api, post } from "@/lib/api";
import type { UploadPreview } from "@/lib/types";
import { Alert, Pill, SeniorityBadge } from "@/components/ui";
import CvUpload from "@/components/CvUpload";
import RunPanel from "@/components/RunPanel";

const FIELDS: { key: string; label: string; required?: boolean }[] = [
  { key: "name", label: "Name", required: true },
  { key: "employee_no", label: "Employee number" },
  { key: "roles", label: "Roles" },
  { key: "skills", label: "Skills", required: true },
  { key: "ratings", label: "Skill ratings" },
  { key: "background", label: "Background" },
  { key: "domain_tags", label: "Domain tags" },
];

type CommitResult = { created: number; updated: number; skills_imported: number; needs_review: number; index_error: string | null; skipped_level_0?: number; seniority_counts?: Record<string, number> };

export default function UploadPage() {
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [tab, setTab] = useState<"file" | "cv">("file");

  async function send(file: File) {
    setBusy(true); setError(null); setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      setPreview(await api<UploadPreview>("/api/uploads", { method: "POST", body: fd }));
    } catch (e) { setError((e as Error).message); setPreview(null); }
    finally { setBusy(false); }
  }

  async function reparse(patch: { sheet?: string; layout?: string; mapping?: Record<string, string> }) {
    if (!preview) return;
    setBusy(true); setError(null);
    try { setPreview(await post<UploadPreview>(`/api/uploads/${preview.upload_id}/reparse`, patch)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function checkWithAgent() {
    if (!preview) return;
    setError(null); setRunId(null);
    try { setRunId((await post<{ run_id: string }>("/api/agents/runs", { agent: "upload", upload_id: preview.upload_id })).run_id); }
    catch (e) { setError((e as Error).message); }
  }

  async function commit() {
    if (!preview) return;
    if (mode === "replace" && !confirm("Replace ALL existing employees with this file?")) return;
    setBusy(true); setError(null);
    try { setResult(await post<CommitResult>(`/api/uploads/${preview.upload_id}/commit`, { mode })); setPreview(null); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const setMap = (field: string, col: string) => {
    if (!preview) return;
    const m = { ...preview.mapping };
    if (col) m[field] = col; else delete m[field];
    reparse({ mapping: m });
  };

  return (
    <>
      <h1>Upload data</h1>
      <p className="sub">Excel (.xlsx) or PDF with Name, Employee Number, Roles, Skills, Skill Ratings and Background. Optionally include a Domain Tags column for explicit expertise tags (for example, Mainframe/Batch Ops). Skill levels use a 0–4 scale: 0 None, 1 Learned, 2 Applied, 3 Experienced, 4 Mastered. Each employee also gets a Junior, Mid or Senior label, computed from the levels.</p>
      <div className="row" style={{ marginBottom: 14 }}>
        <button className={`btn ${tab === "file" ? "" : "ghost"}`} onClick={() => setTab("file")}>Employee file (Excel / PDF)</button>
        <button className={`btn ${tab === "cv" ? "" : "ghost"}`} onClick={() => setTab("cv")}>Single CV (PDF / Word)</button>
      </div>
      {tab === "cv" && <CvUpload />}
      {tab === "file" && <>

      {error && <Alert tone="bad">{error}</Alert>}
      {result && (
        <Alert tone="ok">
          Imported: {result.created} new, {result.updated} updated employees, {result.skills_imported} skills.{" "}
          {result.seniority_counts && <>Seniority: {Object.entries(result.seniority_counts).map(([k, v]) => `${v} ${k}`).join(", ")}. </>}
          {!!result.skipped_level_0 && <>{result.skipped_level_0} skill entries rated 0 (None) were not stored. </>}
          {result.needs_review > 0 && <>{result.needs_review} skills need review — <Link href="/standardize">open Standardization</Link>. </>}
          <Link href="/matrix">View the skill matrix →</Link>
          {result.index_error && <div>Vector indexing failed ({result.index_error}); staffing similarity will be unavailable until you re-index in AI integration.</div>}
        </Alert>
      )}

      {!preview && (
        <div
          className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0}
          onClick={() => input.current?.click()} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) send(f); }}
        >
          {busy ? "Reading file…" : <>Drop a file here, or click to choose<br /><span style={{ fontSize: 12 }}>.xlsx, .xlsm, .pdf · max 10 MB</span></>}
          <input ref={input} type="file" hidden accept=".xlsx,.xlsm,.pdf" onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }} />
        </div>
      )}

      {preview && (
        <>
          <div className="card">
            <div className="row">
              <h2 style={{ margin: 0 }}>{preview.filename}</h2>
              <Pill tone="info">{preview.source}</Pill>
              <span className="spacer" />
              <button className="btn ghost sm" onClick={() => setPreview(null)}>Choose another file</button>
            </div>
            <p className="muted" style={{ marginTop: 8 }}>{preview.records_count} employees · {preview.skills_count} skill entries detected
              {preview.seniority_counts && <> · {Object.entries(preview.seniority_counts).map(([k, v]) => `${v} ${k}`).join(", ")}</>}</p>
            {!!preview.out_of_scale_warnings && <Alert tone="warn">{preview.out_of_scale_warnings} rating(s) are outside the 0–4 scale (for example 5, or 4/5 from the old scale). They are <strong>not imported</strong>: those skills stay unrated. Fix the file and upload it again.</Alert>}

            {preview.kind === "excel" && (
              <div className="row" style={{ marginBottom: 12 }}>
                {preview.sheets.length > 1 && (
                  <div><label className="f">Sheet</label>
                    <select value={preview.sheet ?? ""} onChange={(e) => reparse({ sheet: e.target.value })}>{preview.sheets.map((s) => <option key={s}>{s}</option>)}</select></div>
                )}
                <div><label className="f">Layout</label>
                  <select value={preview.layout} onChange={(e) => reparse({ layout: e.target.value })}>
                    <option value="wide">One row per employee</option>
                    <option value="long">One row per employee–skill</option>
                  </select></div>
              </div>
            )}

            {preview.columns.length > 0 && (
              <>
                <h3>Column mapping</h3>
                <div className="grid g4" style={{ marginBottom: 8 }}>
                  {FIELDS.map((f) => (
                    <div key={f.key}>
                      <label className="f">{f.label}{f.required && " *"}</label>
                      <select value={preview.mapping[f.key] ?? ""} onChange={(e) => setMap(f.key, e.target.value)} disabled={busy} style={{ width: "100%" }}>
                        <option value="">— not in file —</option>
                        {preview.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>

          {preview.warnings.length > 0 && (
            <Alert tone="warn">
              <strong>{preview.warnings.length} warning{preview.warnings.length > 1 && "s"}</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{preview.warnings.slice(0, 8).map((w, i) => <li key={i}>{w}</li>)}</ul>
              {preview.warnings.length > 8 && <div>…and {preview.warnings.length - 8} more</div>}
            </Alert>
          )}

          <div className="card">
            <h2>Preview (first {Math.min(10, preview.records.length)} employees)</h2>
            <div className="tablewrap"><table>
              <thead><tr><th>Employee no.</th><th>Name</th><th>Roles</th><th>Domain tags</th><th>Seniority</th><th>Skills (raw → level 0–4)</th></tr></thead>
              <tbody>
                {preview.records.slice(0, 10).map((r, i) => (
                  <tr key={i}>
                    <td>{r.employee_no ?? <span className="muted">—</span>}</td><td>{r.name}</td><td>{r.roles.join(", ")}</td><td>{r.domain_tags.join(", ")}</td><td><SeniorityBadge s={r.seniority} /></td>
                    <td>{r.skills.map((s, j) => <span key={j} className={`pill ${s.rating == null ? "warn" : ""}`}>{s.raw_skill}{s.rating != null ? ` · ${s.rating}` : " · ?"}</span>)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </div>

          {runId && <RunPanel runId={runId} />}
          {runId && <p className="muted">If the agent proposes better settings, an HR manager approves them under Approvals; then press “Refresh preview” here.</p>}

          <div className="card row">
            <div>
              <label className="f">If an employee already exists</label>
              <select value={mode} onChange={(e) => setMode(e.target.value as "merge" | "replace")}>
                <option value="merge">Update existing, add new (merge)</option>
                <option value="replace">Replace all existing data</option>
              </select>
            </div>
            <span className="spacer" />
            <button className="btn ghost" disabled={busy} onClick={() => reparse({})} title="Read the file again with the saved settings, for example after an approved agent proposal.">Refresh preview</button>
            <button className="btn ghost" disabled={busy} onClick={checkWithAgent} title="An agent reads the result and looks for mistakes. Optional.">Check with agent</button>
            <button className="btn" disabled={busy || preview.records_count === 0} onClick={commit}>
              {busy ? "Working…" : `Import ${preview.records_count} employees`}
            </button>
          </div>
        </>
      )}
      </>}
    </>
  );
}
