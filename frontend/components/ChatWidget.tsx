"use client";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, post, useApi } from "@/lib/api";
import type { AgentRun, AgentsStatus } from "@/lib/types";
import { Markdown } from "@/components/ui";
import AgentPortrait from "@/components/AgentPortrait";

type Msg = { role: "user" | "assistant"; content: string };

export default function ChatWidget() {
  const status = useApi<AgentsStatus>("/api/agents/status");
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, live, open]);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = text.trim();
    if (!question || busy) return;
    const history: Msg[] = [...msgs, { role: "user", content: question }];
    setMsgs(history);
    setText("");
    setBusy(true);
    setError(null);
    setLive("I’m putting together an answer…");
    try {
      const { run_id } = await post<{ run_id: string }>("/api/agents/runs", { agent: "chat", messages: history });
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        const run = await api<AgentRun>(`/api/agents/runs/${run_id}`);
        if (run.status === "done") {
          setMsgs([...history, { role: "assistant", content: run.result?.answer ?? "No answer was returned." }]);
          return;
        }
        if (run.status === "error") {
          setError(run.error ?? "The assistant stopped unexpectedly.");
          return;
        }
      }
      setError("This is taking longer than expected. Please try again.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setLive(null);
    }
  }

  const available = !!status.data?.available;
  return (
    <aside className="chat-widget" aria-label="Ask the matrix assistant">
      {open && (
        <section className="chat-window" aria-label="Ask the matrix chat">
          <header className="chat-head">
            <AgentPortrait kind="analyst" compact />
            <div className="chat-head-actions">
              {!!msgs.length && <button className="chat-icon-btn" onClick={() => { setMsgs([]); setError(null); }} aria-label="Clear conversation">Clear</button>}
              <button className="chat-icon-btn close" onClick={() => setOpen(false)} aria-label="Close chat">×</button>
            </div>
          </header>
          {!available && status.data && <div className="chat-unavailable">{status.data.message}</div>}
          <div className="chat-messages" aria-live="polite">
            {msgs.length === 0 && (
              <div className="chat-welcome">
                <span className="eyebrow">THE ANALYST / WORKFORCE DATA</span>
                <p>Direct answers, grounded in your team’s skills and records.</p>
                <span className="chat-example">Who has Python at level 4 or higher?</span>
                <span className="chat-example">Which testers know Java?</span>
              </div>
            )}
            {msgs.map((message, index) => (
              <div key={index} className={`msg ${message.role}`}>
                {message.role === "assistant" ? <Markdown>{message.content}</Markdown> : message.content}
              </div>
            ))}
            {live && <div className="msg" role="status"><span className="spin" />{live}</div>}
            {error && <div className="chat-error" role="alert">{error}</div>}
            <div ref={end} />
          </div>
          <form className="chat-compose" onSubmit={ask}>
            <input
              aria-label="Ask a question"
              value={text}
              disabled={!available || busy}
              placeholder="Ask about your workforce…"
              onChange={(event) => setText(event.target.value)}
            />
            <button className="btn" type="submit" disabled={!available || busy || !text.trim()}>Send</button>
          </form>
        </section>
      )}
      <button className={`chat-launcher ${open ? "is-open" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open} aria-label={open ? "Close Ask the matrix" : "Open Ask the matrix"}>
        <span className="chat-launcher-mark" aria-hidden="true">{open ? "×" : "?"}</span>
        {!open && <span>Ask matrix</span>}
      </button>
    </aside>
  );
}
