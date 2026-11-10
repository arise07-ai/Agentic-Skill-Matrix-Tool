"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Nav from "./Nav";
import ChatWidget from "./ChatWidget";
import PageLoading from "./PageLoading";
import ThemeToggle from "./ThemeToggle";
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
  const [drawerOpen, setDrawerOpen] = useState(false); // small screens: the sidebar as a drawer
  const initialized = useRef(false);

  useEffect(() => { setDrawerOpen(false); }, [path]); // a drawer closes when the page changes
  useEffect(() => { // ...and with the Escape key
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawerOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  /** Desktop: hide or show the sidebar, remembered in this browser (app/layout.tsx applies it before the first paint).
   *  Small screens: open or close the drawer. */
  function toggleNav() {
    if (window.matchMedia("(max-width: 820px)").matches) { setDrawerOpen((o) => !o); return; }
    const root = document.documentElement;
    const hide = root.dataset.nav !== "hidden";
    if (hide) root.dataset.nav = "hidden"; else delete root.dataset.nav;
    try { localStorage.setItem("sm-nav", hide ? "hidden" : "shown"); } catch {}
  }

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
      <div className={`shell${drawerOpen ? " drawer-open" : ""}`}>
        <Nav />
        <div className="content">
          <header className="topbar">
            <button type="button" className="icon-btn" onClick={toggleNav} aria-label="Show or hide the menu">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" /><path d="M6 2.75v10.5" />
              </svg>
            </button>
            <span className="spacer" />
            <ThemeToggle />
          </header>
          <main className="main">{children}</main>
        </div>
        <div className="scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
      </div>
      {user.role !== "member" && <ChatWidget />}
    </AuthCtx.Provider>
  );
}
