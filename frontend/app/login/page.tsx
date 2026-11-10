"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { post, resetProjectDraft } from "@/lib/api";
import { Alert } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await post("/api/auth/login", { username, password }); resetProjectDraft(); router.replace("/"); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <span className="login-mark" aria-hidden="true">
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
          <span>Skill Matrix</span>
        </div>
        <div className="login-heading">
          <p className="login-eyebrow">YOUR WORKSPACE</p>
          <h1>Welcome back</h1>
          <p>Sign in with the account an administrator created for you.</p>
        </div>
        {error && <Alert tone="bad">{error}</Alert>}
        <div className="login-fields">
          <label className="f" htmlFor="u">Username</label>
          <input id="u" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
          <label className="f" htmlFor="p">Password</label>
          <input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button className="btn login-submit" disabled={busy || !username || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="login-foot">Access is managed by your administrator.</p>
      </form>
    </main>
  );
}
