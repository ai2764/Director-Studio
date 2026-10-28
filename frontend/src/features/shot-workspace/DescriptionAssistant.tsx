import { useEffect, useRef, useState } from "react";
import { parseError } from "../../shared/api/client";
import { getDirectorModel, setDirectorModel } from "../director/api";
import { WorkspaceMascot } from "./WorkspaceMascot";
import { StudioIcon } from "../../shared/components/StudioIcon";

export type AssistantTurn = {role: "user" | "assistant"; text: string};
export type AssistantReply = {reply: string; proposed_change?: string; context_scope?: string};

type ConversationMessage = {role: "user" | "assistant" | "error"; text: string; proposal?: string; scope?: string};

export function DescriptionAssistant({ shotId, description, disabled, ready, onRequest, onUndo, canUndo }: {
  shotId: string; description: string; disabled: boolean; ready: boolean;
  onRequest: (request: string, recentTurns: AssistantTurn[]) => Promise<AssistantReply>; onUndo: () => void; canUndo: boolean;
}) {
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  function minimize() { setOpen(false); toggleRef.current?.focus(); }
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelSwitching, setModelSwitching] = useState(false);
  const [modelError, setModelError] = useState("");
  useEffect(() => {
    if (!open) return;
    let current = true; setModelLoading(true); setModelError("");
    void getDirectorModel().then((status) => {
      if (current) { setModel(status.model || ""); setModels(status.available || []); }
    }).catch((error) => { if (current) setModelError(error instanceof Error ? error.message : String(error)); })
      .finally(() => { if (current) setModelLoading(false); });
    return () => { current = false; };
  }, [open]);
  async function changeModel(value: string) {
    if (!value || value === model || busy || disabled || modelSwitching) return;
    setModelSwitching(true); setModelError("");
    try {
      const status = await setDirectorModel(value, true);
      setModel(status.model || value); if (status.available) setModels(status.available);
    } catch (error) { setModelError(error instanceof Error ? error.message : String(error)); }
    finally { setModelSwitching(false); }
  }
  const [seconds, setSeconds] = useState(0);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  useEffect(() => {
    if (!open) return;
    let current = true;
    setHistoryLoading(true); setHistoryError(""); setMessages([]);
    void fetch(`/api/shot-workspace/shots/${shotId}/description/conversation`).then(async (response) => {
      if (!response.ok) throw new Error(await parseError(response));
      const data = await response.json() as {messages?: ConversationMessage[]};
      if (current) setMessages(data.messages || []);
    }).catch(() => { if (current) setHistoryError("Conversation could not be loaded. Reopen the assistant to retry."); })
      .finally(() => { if (current) setHistoryLoading(false); });
    return () => { current = false; };
  }, [shotId, open]);
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now(); setSeconds(0);
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages, busy, open]);
  async function send(override?: string) {
    const request = (override ?? input).trim(); if (!request || busy || disabled || historyLoading || !!historyError || modelLoading || modelSwitching || !ready) return;
    setBusy(true); setInput(""); setMessages((current) => [...current, {role:"user",text:request}]);
    try {
      const lastAssistant = messages.map((message,index) => ({message,index})).reverse().find(({message}) => message.role === "assistant");
      const lastUser = lastAssistant ? messages.slice(0,lastAssistant.index).reverse().find((message) => message.role === "user") : undefined;
      const recentTurns: AssistantTurn[] = lastAssistant && lastUser ? [
        {role:"user",text:lastUser.text.slice(0,600)}, {role:"assistant",text:lastAssistant.message.text.slice(0,600)},
      ] : [];
      const result = await onRequest(request, recentTurns);
      setMessages((current) => [...current, {role:"assistant",text:result.reply,proposal:result.proposed_change,scope:result.context_scope}]);
    } catch (error) {
      setInput(request); setMessages((current) => [...current, {role:"error",text:error instanceof Error ? error.message : String(error)}]);
    } finally { setBusy(false); }
  }
  return <>{open && <button type="button" className="description-assistant-backdrop" aria-label="Minimize assistant"
    tabIndex={-1} onClick={minimize} />}
  <aside className={`description-assistant${open ? " is-open" : ""}`} aria-label="Description assistant"
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); minimize(); } }}>
    <button ref={toggleRef} type="button" className="description-assistant-toggle" aria-expanded={open}
      aria-label={open ? "Description assistant · Minimize" : busy ? `Description · ${seconds}s` : "✦ Description assistant"}
      onClick={() => setOpen((value) => !value)}>
      <WorkspaceMascot /><span className="assistant-mobile-icon"><StudioIcon name="chat" size={18} /></span>
      <span className="assistant-desktop-label">{open ? "Description assistant · Minimize" : busy ? `Description · ${seconds}s` : "Description assistant"}</span>
      <span className="assistant-mobile-label">{open ? "Assistant · Minimize" : busy ? `Thinking · ${seconds}s` : "Assistant"}</span>
      {open && <span className="assistant-window-close"><StudioIcon name="close" size={18} /></span>}
    </button>
    {open && <div className="description-assistant-panel">
      <label className="description-assistant-model">Model<select aria-label="Description assistant model" value={model}
        title="Shared model selection for description assistance and prompt writing"
        disabled={disabled || busy || modelLoading || modelSwitching || !models.length}
        onChange={(event) => void changeModel(event.target.value)}>
        {!models.length && <option value={model}>{modelLoading ? "Loading models…" : model || "No models available"}</option>}
        {!!model && !!models.length && !models.includes(model) && <option value={model} disabled>{model} (not available)</option>}
        {models.map((name) => <option key={name} value={name}>{name}</option>)}
      </select>{modelSwitching && <small role="status">Switching model…</small>}</label>
      {modelError && <p className="description-assistant-help" role="alert">Model selection failed: {modelError}</p>}
      <p className="description-assistant-help">Discuss the shot, edit its brief, or ask me to write or revise its prompt. Prompt requests save the current brief and run the prompt writer. Reference descriptions are included. Conversation is saved for this shot.</p>
      {historyLoading && <p className="description-assistant-help" role="status">Loading conversation…</p>}
      {historyError && <p className="description-assistant-help" role="alert">{historyError}</p>}
      <div className="description-assistant-log" ref={log} role="log" aria-label="Description conversation">
        <div className="description-assistant-message assistant"><strong>Current description</strong><p>{description || "No description yet. Tell me what happens in this shot."}</p></div>
        {messages.map((message,index) => <div key={index} className={`description-assistant-message ${message.role}`}><strong>{message.role === "user" ? "You" : message.role === "error" ? "Unable to update" : "Assistant"}</strong>{message.scope && <small className="description-context-scope">{message.scope}</small>}<p>{message.text}</p>
          {message.proposal && <button type="button" className="mode-chip" disabled={disabled || busy || historyLoading || !!historyError || modelSwitching} onClick={() => void send(`Update this shot as follows: ${message.proposal}`)}>Apply suggestion</button>}</div>)}
        {busy && <p role="status">Thinking · {seconds}s</p>}
      </div>
      <form onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <textarea aria-label="Description request" rows={2} value={input} disabled={disabled || busy}
          placeholder={description.trim() ? "Discuss a shot or describe a change…" : "Describe a shot or ask for ideas…"} onChange={(event) => setInput(event.target.value)} />
        <div className="manual-shot-actions">{canUndo && <button type="button" className="mode-chip" disabled={disabled || busy} onClick={onUndo}>Undo description change</button>}
          <button type="submit" className="btn primary" disabled={disabled || busy || historyLoading || !!historyError || modelLoading || modelSwitching || !ready || !input.trim()}>{busy ? `Thinking · ${seconds}s` : "Send"}</button></div>
      </form>
    </div>}
  </aside></>;
}
