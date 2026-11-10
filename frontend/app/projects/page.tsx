"use client";
import Link from "next/link";
import { useState } from "react";
import { post, useApi, useProject } from "@/lib/api";
import type { Brief, Candidate, Capacity, CapacityMap, GapReport, Requirement, StaffingResult } from "@/lib/types";
import { Alert, Bar, LEVELS, Markdown, Pill, SeniorityBadge, StatusPill } from "@/components/ui";
import LearningPlanApproval from "@/components/LearningPlanApproval";
import RequirementsEditor from "@/components/RequirementsEditor";
import { AssignControl, CapacityPill } from "@/components/AssignControl";
import ProjectTeam from "@/components/ProjectTeam";
import { useUser } from "@/components/AppFrame";

type Assign = { briefId: number | null | undefined; start?: string | null; end?: string | null; canRequest: boolean; capacity: Record<string, Capacity>; sent: () => void };

type Extracted = { requirements: Requirement[]; headcount: number | null; roles: string[]; mode: string };

function learningHref(employeeId: number, requirements: Requirement[]) {
  const params = new URLSearchParams({
    employee: String(employeeId),
    requirements: JSON.stringify(requirements.map(({ skill, level, domain_tags }) => ({ skill, level, domain_tags }))),
  });
  return `/learning?${params.toString()}`;
}

function requirementsForSkills(skills: { skill: string; level: number }[], requirements: Requirement[]) {
  return skills.map(({ skill, level }) => {
    const requirement = requirements.find((item) => item.skill === skill);
    return requirement ? { ...requirement, level } : { skill, level, weight: 1, min_people: 1 };
  });
}

export default function ProjectsPage() {
  const [project, setProject, ready] = useProject();
  const { user } = useUser();
  const briefs = useApi<Brief[]>("/api/agents/briefs");
  const [capKey, setCapKey] = useState(0);
  const capacity = useApi<CapacityMap>(`/api/assignments/capacity?${project.briefId ? `brief_id=${project.briefId}&` : ""}k=${capKey}`);
  const chosen = briefs.data?.find((b) => b.id === project.briefId);
  const chosenBrief = chosen;
  const canRequest = user?.role === "project_manager" || user?.role === "admin";
  const assign: Assign = { briefId: project.briefId, start: chosenBrief?.start_date, end: chosenBrief?.end_date, canRequest, capacity: capacity.data?.employees ?? {}, sent: () => setCapKey((k) => k + 1) };
  const [tab, setTab] = useState<"gaps" | "staffing">("gaps");
  const [gaps, setGaps] = useState<GapReport | null>(null);
  const [staff, setStaff] = useState<StaffingResult | null>(null);
  const [planDecisions, setPlanDecisions] = useState<Record<number, "approved" | "declined">>({});
  const [mode, setMode] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [weeks, setWeeks] = useState(4);
  const [hours, setHours] = useState(5);

  const body = () => ({
    prompt: project.prompt,
    requirements: project.requirements.filter((r) => r.skill.trim()),
    headcount: project.headcount, roles: project.roles, role_seniority: project.roleSeniority, top_n: 15,
    learning_weeks: weeks, learning_hours_per_week: hours,
  });

  async function run<T>(label: string, fn: () => Promise<T>, apply: (t: T) => void) {
    setBusy(label); setError(null);
    try { apply(await fn()); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  const extract = () => run("extract", () => post<Extracted>("/api/projects/requirements", { prompt: project.prompt }), (r) => {
    setProject({ ...project, requirements: r.requirements, headcount: r.headcount, roles: r.roles });
    setMode(r.mode);
    setGaps(null); setStaff(null);
  });
  const analyzeGaps = () => run("gaps", () => post<GapReport>("/api/projects/gaps", body()), (r) => { setGaps(r); setTab("gaps"); });
  const findStaff = () => run("staff", () => post<StaffingResult>("/api/projects/staffing", body()), (r) => {
    setStaff(r); setPlanDecisions({}); setTab("staffing");
  });
  const both = async () => { await analyzeGaps(); await findStaff(); setTab("gaps"); };

  if (!ready) return null;
  const hasReqs = project.requirements.some((r) => r.skill.trim());

  return (
    <>
      <h1>Project planner</h1>
      <p className="sub">Describe the project demand. Required skills are extracted for you to confirm, then gaps are predicted and candidates are ranked with semantic search.</p>
      {error && <Alert tone="bad">{error}</Alert>}

      <div className="card">
        <h2>1 · Project demand</h2>
        <label className="f" htmlFor="prompt">Describe the project, the skills it needs and how many people</label>
        <textarea id="prompt" value={project.prompt} placeholder="e.g. We need 3 engineers for a data platform: Python (4), Postgres, Docker, advanced Kubernetes. A project manager with Agile experience should lead."
          onChange={(e) => setProject({ ...project, prompt: e.target.value })} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" onClick={extract} disabled={!project.prompt.trim() || !!busy}>{busy === "extract" ? "Extracting…" : "Extract required skills"}</button>
          {mode && <Pill tone={mode === "llm" ? "ok" : "info"}>{mode === "llm" ? "extracted by LLM" : "keyword extraction (LLM not configured)"}</Pill>}
        </div>
      </div>

      <div className="card">
        <h2>2 · Required skills <span className="muted" style={{ fontWeight: 400 }}>(edit before running)</span></h2>
        <RequirementsEditor value={project.requirements} onChange={(requirements) => setProject({ ...project, requirements })} />
        <div className="row" style={{ marginTop: 14 }}>
          <div><label className="f">Team size</label>
            <input type="number" min={1} style={{ width: 90 }} value={project.headcount ?? ""} placeholder="auto"
              onChange={(e) => setProject({ ...project, headcount: e.target.value ? Number(e.target.value) : null })} /></div>
          <div style={{ flex: 1, minWidth: 220 }}><label className="f">Preferred roles (comma separated)</label>
            <input style={{ width: "100%" }} value={project.roles.join(", ")}
              onChange={(e) => setProject({ ...project, roles: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></div>
          {project.roles.length > 0 && (
            <div><label className="f">Seniority wanted (optional)</label>
              <div className="row" style={{ gap: 8 }}>
                {project.roles.map((role) => (
                  <label key={role} className="row" style={{ gap: 4 }}>
                    <span className="muted">{role}</span>
                    <select aria-label={`Seniority wanted for ${role}`} value={project.roleSeniority[role] ?? ""}
                      onChange={(e) => {
                        const next = { ...project.roleSeniority };
                        if (e.target.value) next[role] = e.target.value; else delete next[role];
                        setProject({ ...project, roleSeniority: next });
                      }}>
                      <option value="">Any</option><option>Senior</option><option>Mid</option><option>Junior</option>
                    </select>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div><label className="f">Study plan weeks</label>
            <input type="number" min={1} max={52} style={{ width: 90 }} value={weeks} onChange={(e) => setWeeks(Math.min(52, Math.max(1, Number(e.target.value) || 1)))} /></div>
          <div><label className="f">Study hours / week</label>
            <input type="number" min={1} max={60} style={{ width: 90 }} value={hours} onChange={(e) => setHours(Math.min(60, Math.max(1, Number(e.target.value) || 1)))} /></div>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn" disabled={!hasReqs || !!busy} onClick={both}>{busy ? "Analyzing…" : "Analyze gaps & find staffing"}</button>
          <button className="btn ghost" disabled={!hasReqs || !!busy} onClick={analyzeGaps}>Gaps only</button>
          <button className="btn ghost" disabled={(!hasReqs && !project.prompt.trim()) || !!busy} onClick={findStaff}>Staffing only</button>
        </div>
      </div>

      <div className="card">
        <h2>3 · Project to assign people to</h2>
        <p className="muted" style={{ marginTop: 0 }}>Pick an approved project to see who is on it and to assign people from the results below. A project manager asks, HR approves. Each person can give up to 8 hours a day (100%) on any single day; projects on different dates do not add up. Dates are optional.</p>
        <select aria-label="Approved project" value={project.briefId ?? ""} style={{ minWidth: 260 }}
          onChange={(e) => setProject({ ...project, briefId: e.target.value ? Number(e.target.value) : null })}>
          <option value="">No project selected</option>
          {briefs.data?.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
        </select>
        {project.briefId && !chosen && !briefs.loading && <Alert tone="warn">That project is no longer approved. Pick another one.</Alert>}
        {chosen && <ProjectTeam briefId={chosen.id} canRequest={canRequest} reloadKey={capKey} onChanged={() => setCapKey((k) => k + 1)} />}
        {!canRequest && chosen && <p className="muted">Only a project manager can ask for assignments.</p>}
      </div>

      {(gaps || staff) && (
        <>
          <div className="tabs" role="tablist">
            <button role="tab" className={tab === "gaps" ? "on" : ""} onClick={() => setTab("gaps")} disabled={!gaps}>Predicted gaps</button>
            <button role="tab" className={tab === "staffing" ? "on" : ""} onClick={() => setTab("staffing")} disabled={!staff}>Suggested staffing</button>
          </div>
          {tab === "gaps" && gaps && <Gaps report={gaps} requirements={project.requirements} />}
          {tab === "staffing" && staff && <Staffing
            result={staff}
            requirements={project.requirements}
            planDecisions={planDecisions}
            onPlanDecision={(id, decision) => setPlanDecisions((current) => ({ ...current, [id]: decision }))}
            assign={assign}
          />}
        </>
      )}
    </>
  );
}

function Gaps({ report, requirements }: { report: GapReport; requirements: Requirement[] }) {
  const s = report.summary;
  return (
    <>
      {report.narrative?.text && <Alert tone="info"><strong>Forecast narrative</strong> <span className="muted">(written by AI from the numbers below, checked by the app)</span><p style={{ margin: "4px 0 0" }}>{report.narrative.text}</p></Alert>}
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card stat"><div className="n">{Math.round(s.readiness * 100)}%</div><div className="l">Skill readiness</div></div>
        <div className="card stat"><div className="n" style={{ color: "var(--ok)" }}>{s.covered}</div><div className="l">Covered</div></div>
        <div className="card stat"><div className="n" style={{ color: "var(--warn)" }}>{s.at_risk}</div><div className="l">At risk (close to covered)</div></div>
        <div className="card stat"><div className="n" style={{ color: "var(--bad)" }}>{s.gap}</div><div className="l">Gaps</div></div>
      </div>
      {report.items.map((g) => (
        <div className="card" key={`${g.skill}:${(g.domain_tags ?? []).join("|")}`}>
          <div className="row">
            <h2 style={{ margin: 0 }}>{g.skill}</h2><StatusPill status={g.status} />
            <span className="muted">needs {g.min_people} × level {g.level}+ ({LEVELS[g.level]})</span>
            {g.domain_tags?.map((tag) => <Pill key={tag} tone="info">requires {tag}</Pill>)}
            <span className="spacer" />
            <span className="muted" style={{ fontSize: 12.5 }}>risk {Math.round(g.risk * 100)}%</span>
            <div style={{ width: 90 }}><Bar value={g.risk} /></div>
          </div>
          <div className="grid g2" style={{ marginTop: 10 }}>
            <div>
              <h3>Meet the bar ({g.holders.length})</h3>
              {g.holders.length ? g.holders.map((p) => <Link key={p.id} href={`/employees/${p.id}`} className="pill ok">{p.name} · {p.rating}</Link>) : <span className="muted">Nobody yet</span>}
              {g.near.length > 0 && <><h3 style={{ marginTop: 10 }}>One level short</h3>{g.near.map((p) => <Link key={p.id} href={`/employees/${p.id}`} className="pill warn">{p.name} · {p.rating}</Link>)}</>}
            </div>
            <div>
              <h3>Best upskilling candidates</h3>
              {g.upskill.length === 0 ? <span className="muted">{g.status === "covered" ? "Not needed" : "No close candidates"}</span> : (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {g.upskill.map((u) => (
                    <li key={u.id}><Link href={`/employees/${u.id}`}>{u.name}</Link> — {u.rating ? `rated ${u.rating}` : "not rated"}
                      {u.adjacent_skill && <span className="muted"> · related strength: {u.adjacent_skill}</span>}{" "}
                      <Link
                        href={learningHref(u.id, requirementsForSkills([{ skill: g.skill, level: g.level }], requirements))}
                        style={{ fontSize: 12.5 }}
                      >learning path</Link></li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      ))}
    </>
  );
}

function Staffing({
  result, requirements, planDecisions, onPlanDecision, assign,
}: {
  assign: Assign;
  result: StaffingResult;
  requirements: Requirement[];
  planDecisions: Record<number, "approved" | "declined">;
  onPlanDecision: (employeeId: number, decision: "approved" | "declined") => void;
}) {
  const team = new Set(result.team);
  const nobody = result.nobody ?? [];
  const limited = result.team_limited ?? [];
  const learning = result.learning ?? [];
  const candidatesById = new Map(result.candidates.map((candidate) => [candidate.id, candidate]));
  return (
    <>
      {assign.canRequest && !assign.briefId && <Alert tone="info">To assign people from these results, pick an approved project in step 3 above.</Alert>}
      {!result.semantic_mode.startsWith("vector") && <Alert tone="warn">Semantic search was unavailable (vector DB error), so ranking uses skills and roles only.</Alert>}
      {result.summary?.text && <Alert tone="info"><strong>Why this team</strong> <span className="muted">(written by AI from the verified facts, checked by the app)</span><p style={{ margin: "4px 0 0" }}>{result.summary.text}</p></Alert>}
      {result.uncovered.length === 0 && <Alert tone="ok">The team of {result.team.length} covers every required skill.</Alert>}
      {limited.length > 0 && <Alert tone="warn">The team size ({result.team_size}) is too small to include people for: {limited.join(", ")}. Employees who have these skills exist — increase the team size.</Alert>}
      {nobody.length > 0 && <Alert tone="bad">Nobody in the company currently reaches the requested level for: {nobody.join(", ")}. Upskilling is suggested below.</Alert>}
      {result.team_details && result.team_details.length > 0 && (
        <section className="card staffing-section staffing-section--team" aria-labelledby="suggested-team-title">
          <div className="staffing-section__heading">
            <div>
              <h2 id="suggested-team-title">Suggested Team</h2>
              <p className="muted">Selected to cover the project’s required skills, up to the requested team size of {result.team_size}.</p>
            </div>
            <Pill tone="info">{result.team_details.length} suggested</Pill>
          </div>
          <div className="staffing-cards">
            {result.team_details.map((d) => {
              const candidate = candidatesById.get(d.id);
              const matched = candidate?.matched ?? [];
              const gaps = candidate?.gaps ?? [];
              return (
                <article className="staffing-person" key={d.id}>
                  <div className="staffing-person__heading">
                    <div>
                      <h3><Link href={`/employees/${d.id}`}>{d.name}</Link> <SeniorityBadge s={d.seniority} /></h3>
                      <p className="muted">{d.roles.join(" · ") || "Role not specified"}</p>
                    </div>
                    {d.role === "extra" && <Pill tone="info">Additional capacity</Pill>}
                  </div>
                  {d.role === "extra" ? (
                    <p className="staffing-person__reason">Added after the required skill coverage was met.</p>
                  ) : (
                    <>
                      <p className="staffing-person__reason">
                        <strong>Selected to cover:</strong>{" "}
                        {d.adds.length ? d.adds.join(", ") : "No additional requirement in this team slot"}
                      </p>
                      {!!d.role_matches?.length && (
                        <div className="staffing-person__evidence">
                          <strong>Role match; rating still below target</strong>
                          <ul>{d.role_matches.map((skill) => <li key={skill}>{skill}</li>)}</ul>
                        </div>
                      )}
                      {matched.length > 0 && (
                        <div className="staffing-person__evidence">
                          <strong>Meets target</strong>
                          <ul>{matched.map((item) => <li key={item.skill}>{item.skill}: {item.have}/{item.need}</li>)}</ul>
                        </div>
                      )}
                      {gaps.length > 0 && (
                        <div className="staffing-person__evidence staffing-person__evidence--gap">
                          <strong>Below target</strong>
                          <ul>{gaps.map((item) => <li key={item.skill}>{item.skill}: {item.have ? `${item.have}/${item.need}` : "None"}</li>)}</ul>
                        </div>
                      )}
                    </>
                  )}
                  {d.below_target.length > 0 ? (
                    <Link
                      className="btn ghost sm"
                      href={learningHref(d.id, requirementsForSkills(
                        d.below_target.map((skill) => ({
                          skill,
                          level: requirements.find((item) => item.skill === skill)?.level ?? 2,
                        })),
                        requirements,
                      ))}
                    >Create learning path</Link>
                  ) : <span className="muted">No learning gaps against these requirements.</span>}
                <div style={{ marginTop: 8 }}>
                  <CapacityPill cap={assign.capacity[String(d.id)]} />
                  <div style={{ marginTop: 6 }}>
                    <AssignControl briefId={assign.briefId} canRequest={assign.canRequest} employeeId={d.id} name={d.name} position={d.roles[0] ?? ""}
                      cap={assign.capacity[String(d.id)]} onSent={assign.sent} projectStart={assign.start} projectEnd={assign.end} />
                  </div>
                </div>
                </article>
              );
            })}
          </div>
        </section>
      )}
      {(result.positions ?? []).length > 0 && (
        <section className="card staffing-section" aria-labelledby="positions-title">
          <div className="staffing-section__heading">
            <div>
              <h2 id="positions-title">Candidates by position</h2>
              <p className="muted">One position per preferred role. Each person is listed under the position where they fit best. If someone falls short of a required level, open a learning path with their gaps already filled in.</p>
            </div>
          </div>
          {(result.positions ?? []).map((pos) => (
            <div key={pos.role} style={{ marginBottom: 16 }}>
              <div className="row"><h3 style={{ margin: 0 }}>{pos.role}{pos.wanted_seniority ? ` · ${pos.wanted_seniority}` : ""}</h3>
                <Pill tone={pos.ready_now > 0 ? "ok" : "warn"}>{pos.ready_now} ready now</Pill>
                <span className="muted">{pos.holders} {pos.holders === 1 ? "person holds" : "people hold"} this role</span></div>
              {pos.note && <p className="muted" style={{ margin: "4px 0" }}>{pos.note}</p>}
              {pos.candidates.length === 0 ? <p className="muted">No candidate has a required skill for this position.</p> : (
                <div className="tablewrap"><table>
                  <thead><tr><th>Candidate</th><th>Fit</th><th>Requirements met</th><th>Below target</th><th>Time free</th><th /></tr></thead>
                  <tbody>{pos.candidates.map((c) => (
                    <tr key={c.id}>
                      <td><Link href={`/employees/${c.id}`}>{c.name}</Link> <SeniorityBadge s={c.seniority} />{c.seniority_match === false && <span className="muted"> (asked {pos.wanted_seniority})</span>} {c.in_team && <Pill tone="info">in suggested team</Pill>}
                        <div className="muted">{c.roles.join(" · ") || "No role"}{c.role_match ? "" : " · different role"}</div></td>
                      <td>{Math.round(c.score * 100)}%</td>
                      <td>{c.meets}/{c.total}</td>
                      <td>{c.gaps.length === 0 ? <Pill tone="ok">meets all</Pill> : c.gaps.map((g) => <Pill key={g.skill} tone="warn">{g.skill} {g.have ? `${g.have}/${g.need}` : "None"}</Pill>)}</td>
                      <td><CapacityPill cap={assign.capacity[String(c.id)]} /></td>
                      <td>
                        <div className="row" style={{ gap: 6, alignItems: "flex-start" }}>
                          <AssignControl briefId={assign.briefId} canRequest={assign.canRequest} employeeId={c.id} name={c.name} position={pos.role}
                            cap={assign.capacity[String(c.id)]} onSent={assign.sent} projectStart={assign.start} projectEnd={assign.end} />
                          {c.short && <Link className="btn ghost sm" href={`/learning?employee=${c.id}&requirements=${encodeURIComponent(JSON.stringify(c.learning_requirements))}`}>Learning path</Link>}
                        </div>
                      </td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </div>
          ))}
        </section>
      )}
      {result.positions_hint && <Alert tone="info">{result.positions_hint}</Alert>}
      {learning.length > 0 && (
        <section className="card staffing-section staffing-section--learning" aria-labelledby="suggested-learning-title">
          <div className="staffing-section__heading">
            <div>
              <h2 id="suggested-learning-title">Suggested Learning</h2>
              <p className="muted">These employees are recommended to develop skills that currently have no qualified team member. Matching roles are considered, but do not replace the required skill rating.</p>
            </div>
          </div>
          <div className="staffing-cards">
            {learning.map((L) => (
              <article className="staffing-person staffing-person--learning" key={L.employee.id}>
                <div className="staffing-person__heading">
                  <div>
                    <h3><Link href={`/employees/${L.employee.id}`}>{L.employee.name}</Link></h3>
                    <p className="muted">{L.employee.roles.join(" · ") || "Role not specified"}{team.has(L.employee.id) ? " · Suggested team member" : " · Not currently in the suggested team"}</p>
                  </div>
                  <Link
                    className="btn"
                    href={learningHref(L.employee.id, L.skills.map(({ skill, target, domain_tags }) => ({
                      skill, level: target, weight: 1, min_people: 1, domain_tags,
                    })))}
                  >Create learning path</Link>
                </div>
                <div className="staffing-learning-skills">
                  {L.skills.map((x) => (
                    <div className="staffing-learning-skill" key={x.skill}>
                      <h4>{x.skill} <span className="muted">· target {x.target}/4</span></h4>
                      <p className="staffing-person__reason">
                        Current rating: {x.current ? `${x.current}/4` : "not rated"}
                        {x.related_skill && ` · Related strength: ${x.related_skill} (${x.related_rating}/4)`}
                      </p>
                      <p className="staffing-person__basis">
                        {x.related_skill
                          ? `Chosen because ${x.related_skill} is a related skill this person already has.`
                          : x.current > 0
                            ? `Chosen because this person already has a ${x.current}/4 starting level in ${x.skill}.`
                            : "No current or related skill rating was found; this person was ranked by overall project fit."}
                      </p>
                      {x.domain_tags?.length ? <div className="row">{x.domain_tags.map((tag) => <Pill key={tag} tone="info">Requires {tag}</Pill>)}</div> : null}
                    </div>
                  ))}
                </div>
                {L.error && <Alert tone="warn">{L.error}</Alert>}
                {L.plan && <>
                  {L.plan.warnings.map((w) => <Alert key={w} tone="warn">{w}</Alert>)}
                  {L.plan.approval_required && !planDecisions[L.employee.id] ? (
                    <LearningPlanApproval
                      result={L.plan}
                      onApprove={() => onPlanDecision(L.employee.id, "approved")}
                      onDecline={() => onPlanDecision(L.employee.id, "declined")}
                    />
                  ) : (
                    <details open><summary style={{ cursor: "pointer", fontWeight: 600 }}>Study plan</summary>
                      <Markdown>{planDecisions[L.employee.id] === "declined" ? L.plan.template_markdown ?? L.plan.markdown : L.plan.markdown}</Markdown>
                    </details>
                  )}
                </>}
              </article>
            ))}
          </div>
        </section>
      )}
      <details className="card">
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>All ranked candidates ({result.candidates.length})</summary>
        <div className="tablewrap" style={{ marginTop: 10 }}>
          <table>
            <thead><tr><th>#</th><th>Candidate</th><th>Fit</th><th>Skills</th><th>Why</th><th></th></tr></thead>
            <tbody>
              {result.candidates.map((c: Candidate, i) => (
                <tr key={c.id} style={team.has(c.id) ? { background: "var(--accent-soft)" } : undefined}>
                  <td>{i + 1}</td>
                  <td><Link href={`/employees/${c.id}`}>{c.name}</Link> <SeniorityBadge s={c.seniority} />{team.has(c.id) && <Pill tone="info">in team</Pill>}<div className="muted" style={{ fontSize: 12 }}>{c.roles.join(", ")}</div></td>
                  <td style={{ minWidth: 110 }}>{Math.round(c.score * 100)}%<Bar value={c.score} /></td>
                  <td>{c.matched.map((m) => <Pill key={m.skill} tone="ok">{m.skill} {m.have}/{m.need}{m.domain_tags?.length ? ` · ${m.domain_tags.join(", ")}` : ""}</Pill>)}{c.gaps.map((m) => <Pill key={m.skill} tone="warn">{m.skill} {m.have}/{m.need}{m.domain_tags?.length ? ` · needs ${m.domain_tags.join(", ")}` : ""}</Pill>)}</td>
                  <td className="muted" style={{ maxWidth: 320 }}>{c.rationale}</td>
                  <td>{c.gaps.length > 0
                    ? <Link href={learningHref(c.id, c.gaps.map((gap) => ({
                      skill: gap.skill, level: gap.need, weight: 1, min_people: 1, domain_tags: gap.domain_tags,
                    })))}>Add learning path</Link>
                    : <span className="muted">No skill gaps</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
