"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Skill levels are 0-4. */
export const LEVELS = ["None", "Learned", "Applied", "Experienced", "Mastered"] as const;
export const levelName = (n: number | null | undefined) => (n == null ? "" : LEVELS[n] ?? String(n));

export function SeniorityBadge({ s }: { s?: { level: string; reason: string } }) {
  if (!s) return null;
  const tone = s.level === "Senior" ? "ok" : s.level === "Mid" ? "info" : s.level === "Junior" ? "warn" : undefined;
  return <span className={`pill ${tone ?? ""}`} title={s.reason}>{s.level}</span>;
}

export function Cell({ rating }: { rating: number | null | undefined }) {
  if (rating === undefined) return <div className="cell" title="Not listed" aria-label="Not listed" />;  // blank: the matrix gridlines show the cell
  if (rating === null) return <div className="cell unrated" title="Skill listed without a rating">?</div>;
  return <div className={`cell lv${rating}`} title={`Level ${rating} (${levelName(rating)}) / 4`}>{rating}</div>;  // lv0..lv4: the blue level scale in globals.css
}

export function Pill({ children, tone }: { children: React.ReactNode; tone?: "ok" | "warn" | "bad" | "info" }) {
  return <span className={`pill ${tone ?? ""}`}>{children}</span>;
}

export function StatusPill({ status }: { status: "covered" | "at_risk" | "gap" }) {
  const map = { covered: ["ok", "Covered"], at_risk: ["warn", "At risk"], gap: ["bad", "Gap"] } as const;
  return <Pill tone={map[status][0]}>{map[status][1]}</Pill>;
}

export function Bar({ value, max = 1 }: { value: number; max?: number }) {
  return <div className="bar"><i style={{ width: `${Math.min(100, (value / max) * 100)}%` }} /></div>;
}

export function Alert({ tone, children }: { tone: "warn" | "bad" | "ok" | "info"; children: React.ReactNode }) {
  return <div className={`alert ${tone}`} role={tone === "bad" ? "alert" : undefined}>{children}</div>;
}

export function Markdown({ children }: { children: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown></div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="muted">{what}…</p>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="card muted" style={{ textAlign: "center", padding: 32 }}>{children}</div>;
}
