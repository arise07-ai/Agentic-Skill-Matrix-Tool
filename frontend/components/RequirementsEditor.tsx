"use client";
import type { Requirement, TaxonomyEntry } from "@/lib/types";
import { useApi } from "@/lib/api";
import { LEVELS } from "@/components/ui";

export default function RequirementsEditor({ value, onChange, taxonomyPath = "/api/taxonomy" }: {
  value: Requirement[]; onChange: (v: Requirement[]) => void; taxonomyPath?: string; // members use /api/me/taxonomy
}) {
  const { data: taxonomy } = useApi<TaxonomyEntry[]>(taxonomyPath);
  const skillNames = new Set(taxonomy?.map((t) => t.name) ?? []);
  const set = (i: number, patch: Partial<Requirement>) => onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div>
      {value.length === 0 && <p className="muted">No required skills yet. Extract them from the description above or add them manually.</p>}
      {value.length > 0 && (
        <div className="tablewrap">
          <table>
            <thead><tr><th>Skill</th><th>Min level (1–4)</th><th>Min. people</th><th>Weight</th><th>Required domain tags</th><th /></tr></thead>
            <tbody>
              {value.map((r, i) => (
                <tr key={i}>
                  <td>
                    <select
                      value={skillNames.has(r.skill) ? r.skill : "__custom__"}
                      aria-label="Skill"
                      onChange={(e) => {
                        if (e.target.value !== "__custom__") set(i, { skill: e.target.value });
                      }}
                    >
                      <option value="__custom__">Custom skill…</option>
                      {taxonomy?.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                    </select>
                    {!skillNames.has(r.skill) && (
                      <input
                        value={r.skill}
                        aria-label="Custom skill"
                        placeholder="Enter a skill"
                        style={{ width: "100%", marginTop: 4 }}
                        onChange={(e) => set(i, { skill: e.target.value })}
                      />
                    )}
                    {r.known === false && <span className="pill warn" style={{ marginLeft: 6 }} title="Not in the taxonomy">new</span>}
                  </td>
                  <td>
                    <select value={r.level} aria-label="Minimum level" onChange={(e) => set(i, { level: Number(e.target.value) })}>
                      {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} · {LEVELS[n]}</option>)}
                    </select>
                  </td>
                  <td><input type="number" min={1} max={100} style={{ width: 70 }} value={r.min_people} aria-label="Minimum people" onChange={(e) => set(i, { min_people: Math.max(1, Number(e.target.value) || 1) })} /></td>
                  <td><input type="number" min={0.5} max={5} step={0.5} style={{ width: 70 }} value={r.weight} aria-label="Weight" onChange={(e) => set(i, { weight: Math.max(0.5, Number(e.target.value) || 1) })} /></td>
                  <td><input value={(r.domain_tags ?? []).join(", ")} aria-label="Required domain tags" placeholder="e.g. Mainframe/Batch Ops" onChange={(e) => set(i, { domain_tags: e.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) })} /></td>
                  <td><button className="btn ghost sm" onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label={`Remove ${r.skill}`}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ marginTop: 10 }}>
        <button className="btn ghost sm" onClick={() => onChange([...value, { skill: "", level: 2, weight: 1, min_people: 1 }])}>+ Add skill</button>
      </div>
    </div>
  );
}
