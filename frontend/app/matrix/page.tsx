"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import type { MatrixData } from "@/lib/types";
import { Cell, Empty, Loading, Alert } from "@/components/ui";

export default function MatrixPage() {
  const [category, setCategory] = useState("");
  const [role, setRole] = useState("");
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (role) params.set("role", role);
  const qs = params.toString();
  const { data, error, loading } = useApi<MatrixData>(`/api/matrix${qs ? `?${qs}` : ""}`);
  const { data: roles } = useApi<string[]>("/api/roles");
  const { data: all } = useApi<MatrixData>("/api/matrix");
  const categories = useMemo(() => Array.from(new Set(all?.skills.map((s) => s.category) ?? [])).sort(), [all]);

  return (
    <>
      <h1>Skill matrix</h1>
      <p className="sub">Standardized skills across the team. Hover a cell for details; click a name to open the profile.</p>
      <div className="row" style={{ marginBottom: 14 }}>
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          <option value="">All categories</option>{categories.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
          <option value="">All roles</option>{roles?.map((r) => <option key={r}>{r}</option>)}
        </select>
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 12.5 }}>Legend:</span>
        {[1, 2, 3, 4].map((n) => <Cell key={n} rating={n} />)}
        <Cell rating={null} /><span className="muted" style={{ fontSize: 12.5 }}>unrated</span>
      </div>
      {loading && <Loading />}
      {error && <Alert tone="bad">{error}</Alert>}
      {data && data.employees.length === 0 && <Empty>No employees yet. <Link href="/upload">Upload data</Link></Empty>}
      {data && data.employees.length > 0 && (
        <div className="card tablewrap">
          <table className="heat">
            <thead>
              <tr>
                <th className="who" />
                {data.skills.map((s) => <th key={`${s.category}:${s.name}`} title={`${s.category} · ${s.holders} holder(s)`}><div>{s.name}</div></th>)}
              </tr>
            </thead>
            <tbody>
              {data.employees.map((e) => (
                <tr key={e.id}>
                  <td className="who" title={`${e.name}${e.roles.length ? " · " + e.roles.join(", ") : ""}`}><Link href={`/employees/${e.id}`}>{e.name}</Link><div className="muted" style={{ fontSize: 11.5 }}>{e.roles.join(", ")}</div></td>
                  {data.skills.map((s) => <td key={`${s.category}:${s.name}`}><Cell rating={data.cells[String(e.id)]?.[s.name]} /></td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
