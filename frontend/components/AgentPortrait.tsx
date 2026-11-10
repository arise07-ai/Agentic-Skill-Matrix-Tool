type AgentKind = "curator" | "intake" | "inspector" | "analyst" | "professor";

const PROFILE: Record<AgentKind, { label: string; name: string }> = {
  curator: { label: "THE CURATOR", name: "Methodical · precise · evidence-led" },
  intake: { label: "THE INTERVIEWER", name: "Curious · structured · practical" },
  inspector: { label: "THE INSPECTOR", name: "Careful · quiet · detail-focused" },
  analyst: { label: "THE ANALYST", name: "Direct · grounded · workforce-aware" },
  professor: { label: "THE PROFESSOR", name: "Academic · patient · evidence-led" },
};

export default function AgentPortrait({ kind, compact = false }: { kind: AgentKind; compact?: boolean }) {
  return (
    <div className={`agent-identity ${compact ? "compact" : ""}`}>
      <svg className={`agent-portrait portrait-${kind}`} viewBox="0 0 84 84" role="img" aria-label={`${PROFILE[kind].label} robot`}>
        <rect x="1" y="1" width="82" height="82" fill="var(--portrait-bg)" stroke="var(--line)" />
        <path d="M25 62v-7h34v7l10 8v12H15V70z" fill="var(--portrait-coat)" stroke="var(--portrait-ink)" strokeWidth="1.5" />
        <path d="M35 56h14v8H35z" fill="var(--portrait-metal)" stroke="var(--portrait-ink)" strokeWidth="1.2" />
        <path d="M23 25q0-5 6-5h26q6 0 6 5v25q0 7-7 7H30q-7 0-7-7z" fill="var(--portrait-metal)" stroke="var(--portrait-ink)" strokeWidth="1.5" />
        <path d="M28 29q0-3 4-3h20q4 0 4 3v15q0 4-4 4H32q-4 0-4-4z" fill="var(--portrait-screen)" stroke="var(--portrait-ink)" strokeWidth="1.2" />
        <path d="M17 31h6v12h-6zm44 0h6v12h-6z" fill="var(--portrait-metal)" stroke="var(--portrait-ink)" strokeWidth="1.5" />
        <circle cx="20" cy="37" r="1.5" fill="var(--accent)" /><circle cx="64" cy="37" r="1.5" fill="var(--accent)" />
        {kind === "curator" && <>
          <path d="M42 20v-7" stroke="var(--portrait-ink)" strokeWidth="2" />
          <path d="M42 8l4 4-4 4-4-4z" fill="var(--accent)" stroke="var(--portrait-ink)" />
          <circle cx="36" cy="35" r="2" fill="var(--accent)" /><circle cx="48" cy="35" r="2" fill="var(--accent)" />
          <path d="M36 42h12" stroke="var(--accent)" strokeWidth="1.5" />
          <path d="M35 67h14m-11 5h8" stroke="var(--accent)" strokeWidth="1.5" />
        </>}
        {kind === "intake" && <>
          <path d="M34 36q2-4 5 0m7 0q2-4 5 0" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          <path d="M36 42q6 6 12 0" fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M42 20v-5m-4 0h8" stroke="var(--portrait-ink)" strokeWidth="1.5" />
          <circle cx="42" cy="12" r="2" fill="var(--accent)" />
          <path d="M34 69h16m-8-5v10" stroke="var(--accent)" strokeWidth="1.5" />
        </>}
        {kind === "inspector" && <>
          <path d="M34 35h5m7 0h5" stroke="var(--accent)" strokeWidth="2" strokeLinecap="square" />
          <path d="M36 43h12" stroke="var(--portrait-ink)" strokeWidth="1.5" />
          <circle cx="61" cy="51" r="7" fill="none" stroke="var(--accent)" strokeWidth="2" />
          <path d="M66 56l5 5" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          <path d="M33 69h18m-18 4h11" stroke="var(--accent)" strokeWidth="1.5" />
        </>}
        {kind === "analyst" && <>
          <path d="M34 36h5m7 0h5" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          <path d="M36 42h12" stroke="var(--accent)" strokeWidth="1.5" />
          <path d="M35 44l-2 2m16-2 2 2" stroke="var(--accent)" strokeWidth="1" />
          <path d="M34 73v-5m7 5V64m7 9v-7m7 7V61" stroke="var(--accent)" strokeWidth="2" />
        </>}
        {kind === "professor" && <>
          <path d="M21 25l21-10 21 10-21 10z" fill="var(--portrait-cap)" stroke="var(--portrait-ink)" strokeWidth="1.5" />
          <path d="M29 29v7q13 9 26 0v-7" fill="var(--portrait-cap)" stroke="var(--portrait-ink)" strokeWidth="1.2" />
          <path d="M62 25v12" stroke="var(--accent)" strokeWidth="1.5" />
          <circle cx="62" cy="39" r="2" fill="var(--accent)" />
          <rect x="30" y="32" width="9" height="7" rx="2" fill="none" stroke="var(--accent)" strokeWidth="1.4" />
          <rect x="45" y="32" width="9" height="7" rx="2" fill="none" stroke="var(--accent)" strokeWidth="1.4" />
          <path d="M39 35h6m-15 34h10v8H31zm12 0h10v8H43z" fill="none" stroke="var(--accent)" strokeWidth="1.4" />
        </>}
        <path d="M11 76h7m48 0h7" stroke="var(--accent)" strokeWidth="1" opacity=".8" />
      </svg>
      <div className="agent-identity-copy"><span className="eyebrow">{PROFILE[kind].label}</span><span>{PROFILE[kind].name}</span></div>
    </div>
  );
}
