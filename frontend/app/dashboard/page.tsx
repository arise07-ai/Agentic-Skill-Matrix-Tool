"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import type { Dashboard } from "@/lib/types";
import { Alert, Bar, Empty, Loading } from "@/components/ui";

export default function DashboardPage() {
  const { data, error, loading } = useApi<Dashboard>("/api/dashboard");
  if (loading) return <Loading />;
  if (error) return <Alert tone="bad">Could not reach the API: {error}. Is the backend running?</Alert>;
  if (!data) return null;

  if (data.employees === 0) {
    return (
      <>
        <div className="page-heading"><div><span className="eyebrow">WORKFORCE INTELLIGENCE / OVERVIEW</span><h1>Dashboard</h1><p className="sub">A concise view of team capability and coverage.</p></div></div>
        <Empty>
          <h2>No data yet</h2>
          <p>Upload an Excel or PDF file with Name, Employee Number, Roles, Skills, Skill Ratings and Background.</p>
          <Link className="btn" href="/upload">Upload data</Link>
        </Empty>
      </>
    );
  }
  const maxHolders = Math.max(...data.top_skills.map((s) => s.holders), 1);
  const maxDist = Math.max(...data.rating_distribution.map((d) => d.count), 1);
  const totalRatings = data.rating_distribution.reduce((sum, item) => sum + item.count, 0);
  return (
    <div className="dashboard">
      <div className="page-heading">
        <div><span className="eyebrow">WORKFORCE INTELLIGENCE / OVERVIEW</span><h1>Dashboard</h1><p className="sub">A concise view of team capability and coverage.</p></div>
        <Link className="btn ghost" href="/matrix">Open skill matrix <span aria-hidden="true">→</span></Link>
      </div>
      {data.needs_review > 0 && (
        <Alert tone="warn" >
          {data.needs_review} skill entries were mapped with low certainty or not mapped. <Link href="/standardize">Review standardization →</Link>
        </Alert>
      )}
      <div className="dashboard-kpis">
        <div className="dashboard-kpi"><div className="kpi-label">PEOPLE</div><div className="kpi-value">{data.employees.toLocaleString()}</div><div className="kpi-note">in current workforce</div></div>
        <div className="dashboard-kpi"><div className="kpi-label">STANDARDIZED SKILLS</div><div className="kpi-value">{data.skills.toLocaleString()}</div><div className="kpi-note">distinct capabilities</div></div>
        <div className="dashboard-kpi"><div className="kpi-label">MEAN PROFICIENCY</div><div className="kpi-value">{data.avg_rating ?? "–"}<small>{data.avg_rating !== null ? " / 4" : ""}</small></div><div className="kpi-note">across rated skills</div></div>
        <div className="dashboard-kpi"><div className="kpi-label">UNRATED ENTRIES</div><div className="kpi-value">{data.unrated.toLocaleString()}</div><div className="kpi-note">ratings to complete</div></div>
      </div>
      <div className="dashboard-grid">
        <section className="dashboard-section skills-section">
          <div className="section-heading"><div><span className="eyebrow">CAPABILITY DISTRIBUTION</span><h2>Most common skills</h2></div><span className="section-meta">TOP {data.top_skills.length}</span></div>
          <div className="tablewrap"><table className="dense-table"><thead><tr><th>Skill / category</th><th>Workforce reach</th><th className="number">People</th><th className="number">Mean</th></tr></thead><tbody>
            {data.top_skills.map((s) => (
              <tr key={s.skill}>
                <td><strong>{s.skill}</strong><div className="muted skill-category">{s.category}</div></td>
                <td className="reach-cell"><Bar value={s.holders} max={maxHolders} /></td>
                <td className="number">{s.holders}</td>
                <td className="number">{s.avg_rating ?? "–"}</td>
              </tr>
            ))}
          </tbody></table></div>
          {!data.top_skills.length && <p className="muted empty-note">No skill data to display.</p>}
        </section>
        <section className="dashboard-section watch-section">
          <div className="section-heading"><div><span className="eyebrow">COVERAGE RISK</span><h2>Single points of failure</h2></div><span className="section-meta">{data.single_point_of_failure.length} SKILLS</span></div>
          <p className="section-description">Level 3+ (Experienced or Mastered) capability held by one person.</p>
          {data.single_point_of_failure.length === 0 ? <p className="muted empty-note">No single-holder critical skills identified.</p> : (
            <div className="risk-list">{data.single_point_of_failure.map((s) => <div className="risk-row" key={s.skill}><span className="risk-mark">!</span><div><strong>{s.skill}</strong><span className="muted">{s.category}</span></div><span className="risk-count">{s.holders} holder</span></div>)}</div>
          )}
        </section>
        <section className="dashboard-section distribution-section">
          <div className="section-heading"><div><span className="eyebrow">SKILL MATURITY</span><h2>Rating distribution</h2></div><span className="section-meta">{totalRatings.toLocaleString()} RATINGS</span></div>
          <div className="distribution-list">{data.rating_distribution.map((d) => (
            <div className="distribution-row" key={d.rating}><span className="distribution-level">L{d.rating}</span><Bar value={d.count} max={maxDist} /><span className="number">{d.count.toLocaleString()}</span></div>
          ))}</div>
          <div className="distribution-legend"><span>1 · Learned</span><span>4 · Mastered</span></div>
        </section>
        <section className="dashboard-section category-section">
          <div className="section-heading"><div><span className="eyebrow">TAXONOMY</span><h2>Categories</h2></div><span className="section-meta">{data.categories.length} GROUPS</span></div>
          <div className="tablewrap"><table className="dense-table"><thead><tr><th>Category</th><th className="number">Skills</th><th className="number">Mean rating</th></tr></thead>
            <tbody>{data.categories.map((c) => <tr key={c.category}><td>{c.category}</td><td className="number">{c.skills}</td><td className="number">{c.avg_rating ?? "–"}</td></tr>)}</tbody>
          </table></div>
        </section>
      </div>
    </div>
  );
}
