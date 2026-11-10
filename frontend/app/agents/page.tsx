"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, post, useApi } from "@/lib/api";
import type { AgentsStatus } from "@/lib/types";
import { Alert, Loading } from "@/components/ui";
import RunPanel from "@/components/RunPanel";
import AgentPortrait from "@/components/AgentPortrait";

type Upload = { id: number; filename: string; kind: string; status: string; created_at: string };

export default function AgentsPage() {
  const status = useApi<AgentsStatus>("/api/agents/status");
  const uploads = useApi<Upload[]>("/api/uploads");
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [uploadId, setUploadId] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!uploadId && uploads.data?.length) setUploadId(String(uploads.data.find((u) => u.status !== "imported")?.id ?? uploads.data[0].id));
  }, [uploads.data, uploadId]);

  async function start(body: Record<string, unknown>) {
    setBusy(true); setError(null); setRunId(null);
    try { setRunId((await post<{ run_id: string }>("/api/agents/runs", body)).run_id); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const on = !!status.data?.available;
  return (
    <>
      <div className="page-heading"><div><span className="eyebrow">ASSISTED WORKFLOWS / AGENT DESK</span><h1>Run an agent</h1><p className="sub">Focused operators for repeatable work. Each reviews evidence and proposes changes for human approval.</p></div></div>
      <div className="agent-note"><span className="agent-note-mark">i</span><span>Nothing changes until a person approves it under Approvals. Only exact matches and earlier decisions are applied automatically.</span><span className="spacer" /><Link href="/approvals">Review queue →</Link></div>
      {status.loading && <Loading />}
      {status.error && <Alert tone="bad">{status.error}</Alert>}
      {status.data && !on && <Alert tone="warn">{status.data.message}</Alert>}
      {error && <Alert tone="bad">{error}</Alert>}

      <div className="agent-grid">
        <section className="agent-card">
          <div className="agent-card-top"><AgentPortrait kind="curator" /><span className="agent-index">01 / TAXONOMY</span></div>
          <h2>Skill standardization</h2>
          <p className="muted">Goes through the spellings waiting for review. Exact matches and earlier decisions are applied; for the rest it checks usage{status.data?.web_lookup ? " and the web" : ""} and proposes a mapping. HR managers approve.</p>
          <div className="agent-card-action"><button className="btn" disabled={!on || busy} onClick={() => start({ agent: "standardization" })}>Run standardization <span aria-hidden="true">→</span></button></div>
        </section>
        <section className="agent-card">
          <div className="agent-card-top"><AgentPortrait kind="intake" /><span className="agent-index">02 / PROJECTS</span></div>
          <h2>Project intake</h2>
          <p className="muted">Describe a project in your own words. The agent asks if something is missing and proposes a structured brief. Project managers and team leaders approve.</p>
          <textarea className="agent-input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Describe the outcome, skills, team size, or timing…" />
          <div className="agent-card-action"><button className="btn" disabled={!on || busy || text.trim().length < 10} onClick={() => start({ agent: "intake", text })}>Start project intake <span aria-hidden="true">→</span></button></div>
        </section>
        <section className="agent-card">
          <div className="agent-card-top"><AgentPortrait kind="inspector" /><span className="agent-index">03 / UPLOADS</span></div>
          <h2>Upload check</h2>
          <p className="muted">Checks that an uploaded file was read correctly before you import it, and can propose better settings. HR managers approve.</p>
          <div className="row">
            <select value={uploadId} onChange={(e) => setUploadId(e.target.value)} style={{ minWidth: 220 }}>
              {!uploads.data?.length && <option value="">No uploads yet</option>}
              {uploads.data?.map((u) => <option key={u.id} value={u.id}>{u.filename} ({u.status})</option>)}
            </select>
            <button className="btn" disabled={!on || busy || !uploadId} onClick={() => start({ agent: "upload", upload_id: Number(uploadId) })}>Inspect upload <span aria-hidden="true">→</span></button>
          </div>
        </section>
      </div>

      {runId && <RunPanel runId={runId} onFinished={() => status.reload()} />}
    </>
  );
}
