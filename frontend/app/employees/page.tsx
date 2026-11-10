"use client";
import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import type { EmployeeRow } from "@/lib/types";
import { Alert, Empty, Loading, Pill, SeniorityBadge } from "@/components/ui";

export default function EmployeesPage() {
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (role) params.set("role", role);
  const { data, error, loading } = useApi<EmployeeRow[]>(`/api/employees?${params.toString()}`);
  const { data: roles } = useApi<string[]>("/api/roles");

  return (
    <>
      <h1>Employees</h1>
      <p className="sub">Search by name, employee number or role.</p>
      <div className="row" style={{ marginBottom: 14 }}>
        <input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search employees" style={{ minWidth: 260 }} />
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
          <option value="">All roles</option>{roles?.map((r) => <option key={r}>{r}</option>)}
        </select>
      </div>
      {loading && <Loading />}
      {error && <Alert tone="bad">{error}</Alert>}
      {data && data.length === 0 && <Empty>No employees match. <Link href="/upload">Upload data</Link></Empty>}
      {data && data.length > 0 && (
        <div className="card tablewrap">
          <table>
            <thead><tr><th>Name</th><th>Employee no.</th><th>Roles</th><th>Seniority</th><th>Skills</th><th>Avg level (0–4)</th></tr></thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.id}>
                  <td><Link href={`/employees/${e.id}`}>{e.name}</Link></td>
                  <td>{e.employee_no ?? <span className="muted">—</span>}</td>
                  <td>{e.roles.map((r) => <Pill key={r}>{r}</Pill>)}</td>
                  <td><SeniorityBadge s={e.seniority} /></td><td>{e.skill_count}</td><td>{e.avg_rating ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
