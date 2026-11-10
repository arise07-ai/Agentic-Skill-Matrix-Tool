"use client";
import { useState } from "react";
import { post, useApi } from "@/lib/api";
import type { MappingRow, RequestedSkill, TaxonomyEntry } from "@/lib/types";
import { Alert, Empty, Loading, Pill } from "@/components/ui";

function Row({ m, taxonomy, categories, onSaved }: { m: MappingRow; taxonomy: TaxonomyEntry[]; categories: string[]; onSaved: () => void }) {
  const [value, setValue] = useState(m.canonical_skill);
  const [category, setCategory] = useState(m.category === "Uncategorized" ? "" : m.category);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const isNew = !taxonomy.some((t) => t.name === value.trim());

  async function save() {
    setBusy(true); setErr(null);
    try { await post("/api/skills/mappings", { raw_skill: m.raw_skill, canonical: value.trim(), category: category || null }, "PATCH"); onSaved(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <tr>
      <td><strong>{m.raw_skill}</strong><div className="muted" style={{ fontSize: 12 }}>{m.occurrences} employee{m.occurrences > 1 && "s"}</div></td>
      <td>
        <input list="taxonomy-options" value={value} onChange={(e) => setValue(e.target.value)} aria-label={`Canonical skill for ${m.raw_skill}`} style={{ minWidth: 200 }} />
        {isNew && (
          <div style={{ marginTop: 6 }}>
            <span className="pill warn">new skill</span>
            <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category for new skill">
              <option value="">Category…</option>{categories.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
        )}
        {err && <div className="muted" style={{ color: "var(--bad)" }}>{err}</div>}
      </td>
      <td><Pill tone={m.needs_review ? "warn" : "ok"}>{m.method}</Pill>{m.confidence > 0 && <span className="muted"> {Math.round(m.confidence * 100)}%</span>}</td>
      <td><button className="btn sm" disabled={busy || !value.trim()} onClick={save}>{busy ? "Saving…" : m.method === "reviewed" ? "Update" : "Confirm"}</button></td>
    </tr>
  );
}

function RequestedRow({ r, taxonomy, categories, onDone }: { r: RequestedSkill; taxonomy: TaxonomyEntry[]; categories: string[]; onDone: () => void }) {
  const [canonical, setCanonical] = useState(r.name);
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const isNew = !taxonomy.some((t) => t.name === canonical.trim());

  async function act(action: "approve" | "dismiss") {
    setBusy(true); setErr(null);
    try {
      await post("/api/skills/requested", {
        name: r.name, action, canonical: canonical.trim() && canonical.trim() !== r.name ? canonical.trim() : null, category: category || null,
      });
      onDone();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <tr>
      <td>
        <strong>{r.name}</strong>
        <div className="muted" style={{ fontSize: 12 }}>asked for {r.times_requested}×{r.holders > 0 ? ` · ${r.holders} employee${r.holders > 1 ? "s" : ""} already list it` : " · nobody has it yet"}</div>
        {r.last_context && <div className="muted" style={{ fontSize: 12, maxWidth: 360 }}>“{r.last_context.slice(0, 140)}{r.last_context.length > 140 ? "…" : ""}”</div>}
      </td>
      <td>
        <input list="taxonomy-options" value={canonical} onChange={(e) => setCanonical(e.target.value)} aria-label={`Skill name for ${r.name}`} style={{ minWidth: 200 }} />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Keep the name to add it as a new skill, or pick an existing skill to treat this as another spelling of it.</div>
        {isNew && (
          <div style={{ marginTop: 6 }}>
            <span className="pill warn">new skill</span>
            <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category for new skill">
              <option value="">Category…</option>{categories.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
        )}
        {err && <div className="muted" style={{ color: "var(--bad)" }}>{err}</div>}
      </td>
      <td style={{ whiteSpace: "nowrap" }}>
        <button className="btn sm" disabled={busy || !canonical.trim()} onClick={() => act("approve")}>{busy ? "Saving…" : "Approve"}</button>{" "}
        <button className="btn ghost sm" disabled={busy} onClick={() => act("dismiss")}>Dismiss</button>
      </td>
    </tr>
  );
}

export default function StandardizePage() {
  const [tab, setTab] = useState<"needs_review" | "all" | "requested">("needs_review");
  const { data, error, loading, reload } = useApi<MappingRow[]>(tab === "requested" ? null : `/api/skills/mappings?status=${tab}`);
  const { data: requested, error: reqError, loading: reqLoading, reload: reloadReq } = useApi<RequestedSkill[]>("/api/skills/requested");
  const { data: taxonomy, reload: reloadTax } = useApi<TaxonomyEntry[]>("/api/taxonomy");
  const categories = Array.from(new Set(taxonomy?.map((t) => t.category) ?? [])).sort();

  return (
    <>
      <h1>Skill standardization</h1>
      <p className="sub">Raw skill text from uploads is mapped to a canonical taxonomy (exact → fuzzy → semantic → optional LLM). Confirm or correct uncertain mappings; your corrections are remembered for future uploads and can become fine-tuning data.</p>
      <datalist id="taxonomy-options">{taxonomy?.map((t) => <option key={t.name} value={t.name}>{t.category}</option>)}</datalist>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "needs_review"} className={tab === "needs_review" ? "on" : ""} onClick={() => setTab("needs_review")}>Needs review</button>
        <button role="tab" aria-selected={tab === "all"} className={tab === "all" ? "on" : ""} onClick={() => setTab("all")}>All mappings</button>
        <button role="tab" aria-selected={tab === "requested"} className={tab === "requested" ? "on" : ""} onClick={() => setTab("requested")}>
          Requested by projects{requested && requested.length > 0 ? ` (${requested.length})` : ""}
        </button>
      </div>
      {tab === "requested" && (
        <>
          <p className="sub">Skills that a project demand asked for but the taxonomy does not have. Nothing is added until you approve it. Approved skills join the taxonomy, so future uploads and project demands recognise them.</p>
          {reqLoading && <Loading />}
          {reqError && <Alert tone="bad">{reqError}</Alert>}
          {requested && requested.length === 0 && <Empty>No pending requests.</Empty>}
          {requested && requested.length > 0 && (
            <div className="card tablewrap">
              <table>
                <thead><tr><th>Requested skill</th><th>Add to taxonomy as</th><th /></tr></thead>
                <tbody>{requested.map((r) => <RequestedRow key={r.name} r={r} taxonomy={taxonomy ?? []} categories={categories} onDone={() => { reloadReq(); reloadTax(); }} />)}</tbody>
              </table>
            </div>
          )}
        </>
      )}
      {tab !== "requested" && loading && <Loading />}
      {tab !== "requested" && error && <Alert tone="bad">{error}</Alert>}
      {tab !== "requested" && data && data.length === 0 && <Empty>{tab === "needs_review" ? "Nothing to review. 🎉" : "No skills yet."}</Empty>}
      {tab !== "requested" && data && data.length > 0 && (
        <div className="card tablewrap">
          <table>
            <thead><tr><th>As written</th><th>Canonical skill</th><th>Method</th><th /></tr></thead>
            <tbody>{data.map((m) => <Row key={`${m.raw_skill}|${m.canonical_skill}|${m.method}`} m={m} taxonomy={taxonomy ?? []} categories={categories} onSaved={() => { reload(); reloadTax(); }} />)}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
