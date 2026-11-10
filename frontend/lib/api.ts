"use client";
import { useCallback, useEffect, useState } from "react";
import type { Requirement } from "./types";

export const SIGNED_OUT_KEY = "sm_signed_out_reason";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {}
    if (res.status === 401 && !path.startsWith("/api/auth/login") && typeof window !== "undefined" && window.location.pathname !== "/login") {
      // the session ended or never started; the login page shows why (e.g. "signed in on another device")
      try { if (msg && msg !== "Please log in") sessionStorage.setItem(SIGNED_OUT_KEY, msg); } catch {}
      window.location.href = "/login";
    }
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

/** A server time in UTC ("YYYY-MM-DD HH:MM:SS") shown in the viewer's own time zone. */
export function localTime(utc: string | null | undefined): string {
  if (!utc) return "";
  const d = new Date(utc.replace(" ", "T") + "Z");
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function post<T>(path: string, body: unknown, method = "POST"): Promise<T> {
  return api<T>(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const load = useCallback(() => {
    if (!path) return;
    setLoading(true);
    api<T>(path)
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [path]);
  useEffect(load, [load]);
  return { data, error, loading, reload: load };
}

/** Project demand shared between the Project Planner and Learning Path pages (kept in this browser only). */
export type Project = { prompt: string; requirements: Requirement[]; headcount: number | null; roles: string[]; roleSeniority: Record<string, string>; briefId?: number | null };
const KEY = "skillmatrix.project";
const EMPTY: Project = { prompt: "", requirements: [], headcount: null, roles: [], roleSeniority: {} };

export function resetProjectDraft() {
  try { localStorage.removeItem(KEY); } catch {}
}

export function useProject() {
  const [project, setProject] = useState<Project>(EMPTY);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) setProject({ ...EMPTY, ...JSON.parse(raw) });
    } catch {}
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    try { localStorage.setItem(KEY, JSON.stringify(project)); } catch {}
  }, [project, ready]);
  return [project, setProject, ready] as const;
}
