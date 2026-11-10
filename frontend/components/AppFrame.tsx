"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Nav from "./Nav";
import ChatWidget from "./ChatWidget";
import PageLoading from "./PageLoading";
import { api, resetProjectDraft } from "@/lib/api";
import type { AgentsStatus, User } from "@/lib/types";

type Ctx = { user: User | null; pending: number; refresh: () => void };
const AuthCtx = createContext<Ctx>({ user: null, pending: 0, refresh: () => {} });
export const useUser = () => useContext(AuthCtx);

// The only pages a member may open. The server refuses everything else as well.
const MEMBER_PAGES = ["/me", "/learning", "/approvals"];

/** Checks the login on every page. The login page itself is shown without the menu. */
export default function AppFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [pending, setPending] = useState(0);
  const [checked, setChecked] = useState(false);
  const initialized = useRef(false);

  const refresh = useCallback(() => {
    api<AgentsStatus>("/api/agents/status").then((s) => setPending(s.pending.mine)).catch(() => {});
  }, []);

  useEffect(() => {
    if (path === "/login") { setChecked(true); return; }
    if (!initialized.current) {
      resetProjectDraft();
      initialized.current = true;
    }
    api<User>("/api/auth/me")
      .then((u) => { setUser(u); refresh(); })
      .catch(() => { setUser(null); router.replace("/login"); })
      .finally(() => setChecked(true));
  }, [path, router, refresh]);

  const memberBlocked = user?.role === "member" && !MEMBER_PAGES.some((p) => path === p || path.startsWith(p + "/"));
  useEffect(() => { if (memberBlocked) router.replace("/me"); }, [memberBlocked, router]);

  if (path === "/login" || path === "/loading-preview") return <>{children}</>;
  if (!checked || !user || memberBlocked) return <PageLoading />;
  return (
    <AuthCtx.Provider value={{ user, pending, refresh }}>
      <div className="shell">
        <Nav />
        <main className="main">{children}</main>
      </div>
      {user.role !== "member" && <ChatWidget />}
    </AuthCtx.Provider>
  );
}
