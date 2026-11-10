"use client";
import { useEffect, useState } from "react";

type Choice = "system" | "light" | "dark";
const KEY = "sm-theme";

/** System / Light / Dark. The choice is remembered in this browser; "System" follows the computer's setting.
 *  app/layout.tsx applies the saved choice before the first paint. */
export default function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>("system");

  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    setChoice(t === "light" || t === "dark" ? t : "system");
  }, []);

  function pick(next: Choice) {
    setChoice(next);
    const root = document.documentElement;
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {}
  }

  return (
    <div className="theme-toggle" role="group" aria-label="Colour theme">
      {(["system", "light", "dark"] as const).map((c) => (
        <button key={c} type="button" aria-pressed={choice === c} onClick={() => pick(c)}>
          {c === "system" ? "System" : c === "light" ? "Light" : "Dark"}
        </button>
      ))}
    </div>
  );
}
