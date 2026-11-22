import { useEffect, useRef, useState } from "react";
import { parseError } from "../../shared/api/client";
import { WorkspaceMascot } from "./WorkspaceMascot";
import { StudioIcon } from "../../shared/components/StudioIcon";
import { Banner, Button, Textarea, cn } from "../../shared/ui";

export type AssistantTurn = { role: "user" | "assistant"; text: string };
export type AssistantReply = { reply: string; proposed_change?: string; context_scope?: string };

type ConversationMessage = {
  role: "user" | "assistant" | "error" | "brief";
  created_at?: string;
  text: string;
  proposal?: string;
  scope?: string;
};

export function DescriptionAssistant({
  shotId,
  description,
  savedDescription,
  disabled,
  ready,
  onRequest,
  onUndo,
  canUndo,
}: {
  shotId: string;
  description: string;
  savedDescription?: string;
  disabled: boolean;
  ready: boolean;
  onRequest: (request: string, recentTurns: AssistantTurn[], signal?: AbortSignal) => Promise<AssistantReply>;
  onUndo: () => void;
  canUndo: boolean;
}) {
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  function minimize() {
    setOpen(false);
    toggleRef.current?.focus();
  }
  const [input, setInput] = useState("");
  const requestController = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    if (!open || busy) return;
    let current = true;
    setHistoryLoading(true);
    setHistoryError("");
    void fetch(`/api/shot-workspace/shots/${shotId}/description/conversation`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await parseError(response));
        const data = (await response.json()) as { messages?: ConversationMessage[] };
        if (current && data.messages?.length) setMessages(data.messages);
      })
      .catch(() => {
        if (current) setHistoryError("Conversation could not be loaded. Reopen the assistant to retry.");
      })
      .finally(() => {
        if (current) setHistoryLoading(false);
      });
    return () => {
      current = false;
    };
  }, [shotId, open, savedDescription, busy, disabled]);

  useEffect(() => {
    setMessages([]);
  }, [shotId]);

  const log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setSeconds(0);
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [messages, busy, open]);

  async function send(override?: string) {
    const request = (override ?? input).trim();
    if (!request || busy || disabled || historyLoading || !!historyError || !ready) return;
    const controller = new AbortController();
    requestController.current = controller;
    setBusy(true);
    setInput("");
    setMessages((current) => [...current, { role: "user", text: request }]);
    try {
      const lastAssistant = messages
        .map((message, index) => ({ message, index }))
        .reverse()
        .find(({ message }) => message.role === "assistant");
      const lastUser = lastAssistant
        ? messages
            .slice(0, lastAssistant.index)
            .reverse()
            .find((message) => message.role === "user")
        : undefined;
      const recentTurns: AssistantTurn[] =
        lastAssistant && lastUser
          ? [
              { role: "user", text: lastUser.text.slice(0, 600) },
              { role: "assistant", text: lastAssistant.message.text.slice(0, 600) },
            ]
          : [];
      const result = await onRequest(request, recentTurns, controller.signal);
      controller.signal.throwIfAborted();
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          text: result.reply,
          proposal: result.proposed_change,
          scope: result.context_scope,
        },
      ]);
    } catch (error) {
      setInput(request);
      setMessages((current) => [
        ...current,
        {
          role: "error",
          text: controller.signal.aborted
            ? "Request cancelled. Any changes already saved are kept."
            : error instanceof Error
              ? error.message
              : String(error),
        },
      ]);
    } finally {
      requestController.current = null;
      setBusy(false);
    }
  }

  return (
    <>
      {open && (
        <button
          type="button"
          className="fixed inset-0 z-40 cursor-default bg-black/40 backdrop-blur-[1px] md:hidden"
          aria-label="Minimize assistant"
          tabIndex={-1}
          onClick={minimize}
        />
      )}
      <aside
        className={cn(
          "fixed right-3 bottom-3 z-50 max-w-[calc(100vw-24px)] text-xs sm:right-5 sm:bottom-5",
          open
            ? "flex max-h-[min(78dvh,calc(100dvh-32px))] w-[min(390px,calc(100vw-24px))] flex-col overflow-hidden rounded-xl border border-border-subtle bg-panel text-ink shadow-[var(--shadow-popover)]"
            : "w-max",
        )}
        aria-label="Description assistant"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            minimize();
          }
        }}
      >
        <button
          ref={toggleRef}
          type="button"
          aria-expanded={open}
          aria-label={
            open
              ? "Description assistant · Minimize"
              : busy
                ? `Description · ${seconds}s`
                : "✦ Description assistant"
          }
          onClick={() => setOpen((value) => !value)}
          className={cn(
            "flex cursor-pointer items-center gap-2.5 transition-colors select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
            open
              ? "w-full shrink-0 border-b border-border-subtle bg-elevated px-3.5 py-2.5 text-left font-medium text-ink hover:bg-hover"
              : "ml-auto rounded-full border border-accent/40 bg-accent py-1.5 pr-3.5 pl-1.5 font-medium text-accent-fg shadow-[var(--shadow-popover)] hover:bg-accent-hover",
          )}
        >
          <WorkspaceMascot
            className={cn("hidden md:inline-flex", open ? "h-7 w-7" : "h-7 w-7 rounded-full border-white/20")}
          />
          <span className="inline-flex md:hidden">
            <StudioIcon name="chat" size={16} />
          </span>
          <span className="hidden md:inline">
            {open
              ? "Description assistant · Minimize"
              : busy
                ? `Description · ${seconds}s`
                : "Description assistant"}
          </span>
          <span className="inline md:hidden">
            {open ? "Assistant · Minimize" : busy ? `Thinking · ${seconds}s` : "Assistant"}
          </span>
          {open && (
            <span className="ml-auto inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink-muted hover:bg-hover hover:text-ink">
              <StudioIcon name="close" size={14} />
            </span>
          )}
        </button>

        {open && (
          <div className="flex min-h-0 flex-1 flex-col">
            <p className="m-0 shrink-0 border-b border-border-subtle bg-elevated/40 px-3.5 py-2 text-[11px] leading-relaxed text-ink-secondary">
              Discuss the shot, edit its brief, or ask me to write or revise its prompt. Requested description edits are
              applied and saved automatically. Prompt requests save the current brief and run the prompt writer.
              Reference descriptions are included. Conversation is saved for this shot.
            </p>
            {historyLoading && (
              <p className="m-0 shrink-0 px-3.5 py-2 text-[11px] text-ink-muted" role="status">
                Loading conversation…
              </p>
            )}
            {historyError && (
              <div className="shrink-0 px-3.5 pt-2">
                <Banner tone="danger">{historyError}</Banner>
              </div>
            )}
            <div
              className="flex min-h-[96px] max-h-[40dvh] flex-1 flex-col gap-2 overflow-y-auto overscroll-contain bg-canvas/50 p-3"
              ref={log}
              role="log"
              aria-label="Description conversation"
            >
              <div className="rounded-lg border border-border-subtle bg-panel p-2.5 text-xs">
                <strong className="block text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                  Current description
                </strong>
                <p className="mt-1 mb-0 whitespace-pre-wrap leading-relaxed text-ink-secondary">
                  {description || "No description yet. Tell me what happens in this shot."}
                </p>
              </div>
              {messages.map((message, index) => (
                <div
                  key={index}
                  className={cn(
                    "rounded-lg border p-2.5 text-xs break-words",
                    message.role === "user"
                      ? "ml-5 border-accent/25 bg-accent-soft text-ink"
                      : message.role === "error"
                        ? "border-status-danger/30 bg-status-danger-soft text-status-danger"
                        : "border-border-subtle bg-panel text-ink",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <strong className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                      {message.role === "user"
                        ? "You"
                        : message.role === "error"
                          ? "Unable to update"
                          : message.role === "brief"
                            ? "Shot brief"
                            : "Assistant"}
                    </strong>
                    {message.created_at && (
                      <small className="font-mono text-[10px] text-ink-muted">
                        {new Date(message.created_at).toLocaleString()}
                      </small>
                    )}
                  </div>
                  {message.scope && (
                    <small className="mt-0.5 block text-[10px] text-ink-muted">{message.scope}</small>
                  )}
                  <p className="mt-1 mb-0 whitespace-pre-wrap leading-relaxed text-ink">{message.text}</p>
                  {message.proposal && (
                    <Button
                      type="button"
                      variant="secondary"
                      size="xs"
                      className="mt-2"
                      disabled={disabled || busy || historyLoading || !!historyError}
                      onClick={() => void send(`Update this shot as follows: ${message.proposal}`)}
                    >
                      Apply suggestion
                    </Button>
                  )}
                </div>
              ))}
              {busy && (
                <p role="status" className="m-0 px-1 font-mono text-[11px] text-ink-muted">
                  Thinking · {seconds}s
                </p>
              )}
            </div>
            <form
              className="flex shrink-0 flex-col gap-2 border-t border-border-subtle bg-elevated/60 p-3"
              onSubmit={(event) => {
                event.preventDefault();
                void send();
              }}
            >
              <Textarea
                aria-label="Description request"
                rows={2}
                value={input}
                disabled={disabled || busy}
                placeholder={
                  description.trim() ? "Discuss a shot or describe a change…" : "Describe a shot or ask for ideas…"
                }
                className="min-h-[60px] max-h-[120px] text-xs"
                onChange={(event) => setInput(event.target.value)}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  {canUndo && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={disabled || busy}
                      onClick={onUndo}
                    >
                      Undo description change
                    </Button>
                  )}
                </div>
                {busy ? (
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => requestController.current?.abort()}
                  >
                    Cancel
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    disabled={disabled || historyLoading || !!historyError || !ready || !input.trim()}
                  >
                    Send
                  </Button>
                )}
              </div>
            </form>
          </div>
        )}
      </aside>
    </>
  );
}
