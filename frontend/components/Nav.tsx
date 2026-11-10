"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useUser } from "./AppFrame";
import { api, resetProjectDraft } from "@/lib/api";

export default function Nav() {
  const path = usePathname();
  const router = useRouter();
  const { user, pending } = useUser();
  const active = (href: string) => (href === "/" || href === "/me" ? path === href : path.startsWith(href));
  const isMember = user?.role === "member";

  // A member only sees their own work. The server refuses everything else too (this only hides the links).
  const memberGroups: { title: string; items: { href: string; label: string; badge?: number }[] }[] = [
    { title: "My work", items: [
      { href: "/me", label: "My profile & skills" },
      { href: "/me/cv", label: "Upload my CV" },
      { href: "/learning", label: "My learning path" },
      { href: "/approvals", label: "My submissions" },
    ] },
  ];

  const staffGroups: { title: string; items: { href: string; label: string; badge?: number }[] }[] = [
    { title: "Workforce", items: [
      { href: "/", label: "Dashboard" },
      { href: "/upload", label: "Upload data" },
      { href: "/matrix", label: "Skill matrix" },
      { href: "/employees", label: "Employees" },
      { href: "/roles", label: "Roles" },
      { href: "/standardize", label: "Standardization" },
    ] },
    { title: "Planning", items: [
      { href: "/projects", label: "Project planner" },
      { href: "/learning", label: "Learning paths" },
    ] },
    { title: "Agents", items: [
      { href: "/agents", label: "Run an agent" },
      { href: "/approvals", label: "Approvals", badge: pending },
    ] },
    { title: "System", items: [
      { href: "/settings/ai", label: "AI integration" },
      ...(user?.role === "admin" ? [{ href: "/admin", label: "Users & audit log" }] : []),
      ...(user?.role === "admin" || user?.role === "hr_manager" ? [{ href: "/members", label: "Members & team leaders" }] : []),
    ] },
  ];
  const groups = isMember ? memberGroups : staffGroups;

  async function logout() {
    resetProjectDraft();
    try { await api("/api/auth/logout", { method: "POST" }); } catch {}
    router.replace("/login");
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand__top">
          <span className="brand__mark" aria-hidden="true">
            <svg viewBox="0 0 28 28" focusable="false">
              <rect x="2" y="2" width="6" height="6" />
              <rect x="11" y="2" width="6" height="6" />
              <rect x="20" y="2" width="6" height="6" />
              <rect x="2" y="11" width="6" height="6" />
              <rect x="11" y="11" width="6" height="6" />
              <rect x="20" y="11" width="6" height="6" />
              <rect x="2" y="20" width="6" height="6" />
              <rect x="11" y="20" width="6" height="6" />
              <rect x="20" y="20" width="6" height="6" />
            </svg>
          </span>
          <span className="brand__name">Skill Matrix</span>
        </div>
      </div>
      <nav className="nav">
        {groups.map((g) => (
          <div key={g.title}>
            <div className="group">{g.title}</div>
            {g.items.map((i) => (
              <Link key={i.href} href={i.href} className={active(i.href) ? "active" : ""}>
                {i.label}{!!i.badge && <span className="badge">{i.badge}</span>}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      {user && (
        <div className="whoami">
          <strong>{user.display_name}</strong>
          <span className="muted">{user.role_label}</span>
          <button className="btn ghost sm" onClick={logout}>Log out</button>
        </div>
      )}
    </aside>
  );
}
