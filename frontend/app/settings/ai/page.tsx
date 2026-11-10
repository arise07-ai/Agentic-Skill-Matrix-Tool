"use client";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import type { AiStatus } from "@/lib/types";
import { Alert, Loading, Pill } from "@/components/ui";
import { useUser } from "@/components/AppFrame";

export default function AiSettingsPage() {
  const { user } = useUser();
  const { data, error, loading, reload } = useApi<AiStatus>("/api/ai/status");
  const [msg, setMsg] = useState<string | null>(null);

  async function reindex() {
    setMsg(null);
    try { const r = await api<{ employees_indexed: number }>("/api/admin/reindex", { method: "POST" }); setMsg(`Re-indexed ${r.employees_indexed} employees and the taxonomy.`); reload(); }
    catch (e) { setMsg(`Re-index failed: ${(e as Error).message}`); }
  }
  async function wipe() {
    if (!confirm("Delete ALL employees and skills? The taxonomy is kept.")) return;
    await api("/api/admin/data", { method: "DELETE" });
    setMsg("All employee data deleted.");
  }

  return (
    <>
      <h1>AI Integration</h1>
      {loading && <Loading />}
      {error && <Alert tone="bad">{error}</Alert>}
      {data && (
        <>
          <Alert tone={data.ai_mode === "llm" ? "ok" : "info"}>
            {data.ai_mode === "llm"
              ? "LLM is configured: requirement extraction, semantic standardization fallback and study plans use it."
              : "No LLM configured: the app runs on heuristic fallbacks (keyword extraction, template study plans, placeholder embeddings). Set OPENAI_API_KEY to switch on the AI features."}
          </Alert>
          <div className="card tablewrap">
            <table className="ai-integration-table">
              <colgroup><col className="ai-integration-table__component" /><col /></colgroup>
              <thead><tr><th>Component</th><th>Status</th></tr></thead>
              <tbody>
                {data.components.map((c) => (
                  <tr key={c.key}>
                    <td><strong>{c.label}</strong></td>
                    <td><Pill tone={c.configured ? "ok" : "warn"}>{c.configured ? "configured" : "not configured"}</Pill><div className="muted">{c.mode}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="card">
        <h2>Maintenance</h2>
        {msg && <Alert tone="info">{msg}</Alert>}
        <p className="muted">Re-index after changing the embedding model or Chroma location. Vectors are stored per embedding model, so old ones are never mixed in.</p>
        {user?.role === "admin" ? ( // the server allows both to the admin only
          <div className="row">
            <button className="btn" onClick={reindex}>Re-index vectors</button>
            <button className="btn danger" onClick={wipe}>Delete all employee data</button>
          </div>
        ) : <p className="muted">Only an administrator can re-index or delete data.</p>}
      </div>
    </>
  );
}
