import { useEffect, useRef, useState } from "react";

export function DescriptionAssistant({ description, disabled, ready, onRequest, onUndo, canUndo }: {
  description: string; disabled: boolean; ready: boolean;
  onRequest: (request: string) => Promise<string>; onUndo: () => void; canUndo: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [messages, setMessages] = useState<{role: "user" | "assistant" | "error"; text: string}[]>([]);
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now(); setSeconds(0);
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages, busy, open]);
  async function send() {
    const request = input.trim(); if (!request || busy || disabled || !ready) return;
    setBusy(true); setInput(""); setMessages((current) => [...current, {role:"user",text:request}]);
    try {
      const reply = await onRequest(request);
      setMessages((current) => [...current, {role:"assistant",text:reply}]);
    } catch (error) {
      setInput(request); setMessages((current) => [...current, {role:"error",text:error instanceof Error ? error.message : String(error)}]);
    } finally { setBusy(false); }
  }
  return <aside className={`description-assistant${open ? " is-open" : ""}`} aria-label="Description assistant">
    <button type="button" className="description-assistant-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
      {open ? "Description assistant · Minimize" : busy ? `Description · ${seconds}s` : "✦ Description assistant"}
    </button>
    {open && <div className="description-assistant-panel">
      <p className="description-assistant-help">Create or refine this description. Only its current version and your latest request are sent.</p>
      <div className="description-assistant-log" ref={log} role="log" aria-label="Description conversation">
        <div className="description-assistant-message assistant"><strong>Current description</strong><p>{description || "No description yet. Tell me what happens in this shot."}</p></div>
        {messages.map((message,index) => <div key={index} className={`description-assistant-message ${message.role}`}><strong>{message.role === "user" ? "You" : message.role === "error" ? "Unable to update" : "Assistant"}</strong><p>{message.text}</p></div>)}
        {busy && <p role="status">Refining description · {seconds}s</p>}
      </div>
      <form onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <textarea aria-label="Description request" rows={2} value={input} disabled={disabled || busy}
          placeholder={description.trim() ? "What would you like to change?" : "Describe this shot…"} onChange={(event) => setInput(event.target.value)} />
        <div className="manual-shot-actions">{canUndo && <button type="button" className="mode-chip" disabled={disabled || busy} onClick={onUndo}>Undo description change</button>}
          <button type="submit" className="btn primary" disabled={disabled || busy || !ready || !input.trim()}>{busy ? `Refining · ${seconds}s` : "Send"}</button></div>
      </form>
    </div>}
  </aside>;
}
