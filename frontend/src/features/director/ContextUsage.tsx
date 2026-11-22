import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, X } from "lucide-react";
import { cn, StatusDot } from "../../shared/ui";
import type { ContextUsage } from "./api";

const number = (value: number | null) =>
  value == null ? "—" : value.toLocaleString("en-US");

const input = (call: ContextUsage) =>
  call.input_tokens == null
    ? `~${number(call.estimated_input_tokens)}`
    : number(call.input_tokens);

const status = (call: ContextUsage) =>
  ({
    running: call.purpose === "compaction" ? "Compacting history" : "Waiting for model",
    completed: "Completed",
    output_truncated: "Output truncated",
    context_overflow: "Context overflow",
    failed: "Request failed",
    cancelled: "Cancelled",
  })[call.status];

export function ContextUsagePanel({
  calls,
  children,
  compact = false,
}: {
  calls: ContextUsage[];
  children?: ReactNode;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const last = calls.at(-1);
  const prompt = last ? (last.input_tokens ?? last.estimated_input_tokens) : 0;
  const high = Boolean(last?.input_budget != null && prompt >= last.input_budget * 0.8);
  const failed = Boolean(
    last && ["output_truncated", "context_overflow", "failed"].includes(last.status),
  );
  const tone = failed ? "error" : high ? "warning" : "normal";
  const dotTone = failed ? "danger" : high ? "warn" : last ? "ok" : "neutral";
  const ratio = last?.context_window ? prompt / last.context_window : null;
  const summary = last
    ? `Context · ${input(last)}${last.context_window ? ` / ${number(last.context_window)}` : " tokens"} · ${status(last)}${high && !failed ? " · High pressure" : ""}`
    : "Context · Usage and compaction";
  const visibleSummary = compact
    ? last?.context_window
      ? `Context ${Math.round((prompt / last.context_window) * 100)}%`
      : last
        ? `Context ${input(last)}`
        : "Context"
    : summary;

  const close = useCallback(() => {
    triggerRef.current?.focus();
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [close, open]);

  return (
    <div className={cn("context-usage min-w-0 font-mono text-[11px] tabular-nums", tone, compact && "compact")}>
      <button
        ref={triggerRef}
        type="button"
        className={cn(
          "context-usage-trigger inline-flex h-7 items-center justify-between gap-1.5 rounded-md border px-2 text-left transition-colors cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          compact ? "w-auto max-w-[140px]" : "w-auto max-w-[260px]",
          failed
            ? "border-status-danger/35 bg-status-danger-soft text-status-danger hover:border-status-danger/60"
            : high
              ? "border-status-warn/35 bg-status-warn-soft text-status-warn hover:border-status-warn/60"
              : "border-border-subtle bg-elevated/80 text-ink-secondary hover:border-border-strong hover:text-ink",
        )}
        aria-label={summary}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <StatusDot tone={dotTone} pulse={last?.status === "running"} />
        <span className="context-usage-trigger-label min-w-0 flex-1 truncate">
          {visibleSummary}
        </span>
        <ArrowUpRight
          aria-hidden="true"
          className="context-usage-trigger-icon h-3 w-3 shrink-0 opacity-65"
        />
      </button>

      {open ? (
        <div
          className="context-dialog-backdrop fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 backdrop-blur-[2px] sm:items-center sm:p-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) close();
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="context-dialog-title"
            className="context-dialog flex max-h-[min(760px,calc(100dvh-1rem))] w-full flex-col overflow-hidden rounded-t-2xl border border-border-subtle bg-panel font-sans text-ink shadow-[var(--shadow-popover)] sm:w-[min(680px,100%)] sm:rounded-xl"
          >
            <header className="context-dialog-header flex items-start justify-between gap-4 border-b border-border-subtle px-5 py-3.5">
              <div>
                <span className="context-dialog-eyebrow text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                  Director diagnostics
                </span>
                <h3
                  id="context-dialog-title"
                  className="mt-0.5 text-sm font-semibold tracking-[-0.01em] text-ink"
                >
                  Context details
                </h3>
              </div>
              <button
                ref={closeRef}
                type="button"
                className="context-dialog-close inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md border border-border-subtle bg-transparent text-ink-muted transition-colors hover:bg-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                aria-label="Close context details"
                onClick={close}
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </header>

            <div className="context-dialog-scroll min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 text-xs">
              {last ? (
                <>
                  <div
                    className="context-usage-metrics flex flex-wrap items-center justify-between gap-2 rounded-md border border-border-subtle bg-elevated/60 px-3 py-2 font-mono text-[11px] font-medium text-ink tabular-nums"
                    aria-live="polite"
                  >
                    <span>
                      Input {input(last)} ·{" "}
                      {last.input_tokens == null ? "estimated text" : "provider reported"}
                    </span>
                    <span>
                      Output {number(last.output_tokens)} / {number(last.output_limit)}
                    </span>
                    <span>
                      Capacity {number(last.context_window)} ·{" "}
                      {last.capacity_source === "provider_reported"
                        ? "provider reported"
                        : last.capacity_source === "configured_fallback"
                          ? "configured fallback"
                          : "not reported"}
                    </span>
                  </div>
                  {ratio != null ? (
                    <div
                      role="meter"
                      aria-label="Input context usage"
                      aria-valuemin={0}
                      aria-valuemax={last.context_window!}
                      aria-valuenow={Math.min(prompt, last.context_window!)}
                      aria-valuetext={`${input(last)} / ${number(last.context_window)} input tokens`}
                      className="context-usage-meter h-1.5 w-full overflow-hidden rounded-full bg-border-subtle"
                    >
                      <span
                        className={cn(
                          "block h-full rounded-full transition-all",
                          failed
                            ? "bg-status-danger"
                            : high
                              ? "bg-status-warn"
                              : "bg-status-ok",
                        )}
                        style={{ width: `${Math.min(100, ratio * 100)}%` }}
                      />
                    </div>
                  ) : (
                    <p className="text-ink-secondary">
                      Context capacity not reported by this provider.
                    </p>
                  )}
                  <p className="context-usage-note leading-relaxed text-ink-muted">
                    Input budget {number(last.input_budget)} · Output reserve{" "}
                    {number(last.output_limit)} tokens. ~ is a text estimate (4 chars/token),
                    including system, project, history and tools. Not an exact tokenizer count;
                    excludes image tokens. Native calls are non-streaming: actual counts arrive
                    when the model returns.
                  </p>
                  {last.status === "output_truncated" ? (
                    <p className="rounded-md border border-status-warn/30 bg-status-warn-soft px-3 py-2 text-status-warn">
                      Output was cut off before completion. This alone does not prove input context
                      overflow.
                    </p>
                  ) : null}
                  <div className="context-usage-calls divide-y divide-border-subtle rounded-lg border border-border-subtle bg-elevated/35">
                    {[...calls].reverse().map((call) => (
                      <article
                        key={call.call_id}
                        className="context-usage-call grid gap-1 px-3.5 py-2.5 text-[11px] text-ink-secondary break-words"
                      >
                        <strong className="font-semibold text-ink">
                          #{call.sequence} ·{" "}
                          {call.purpose === "compaction" ? "History compaction" : "Model turn"} ·{" "}
                          {status(call)}
                        </strong>
                        <span className="font-mono text-ink-muted">
                          {call.provider} · {call.model}
                        </span>
                        <span className="font-mono tabular-nums">
                          Input {input(call)} · Output {number(call.output_tokens)} /{" "}
                          {number(call.output_limit)} ·{" "}
                          {call.status === "running"
                            ? "In progress"
                            : `${(call.elapsed_ms / 1000).toFixed(1)}s`}
                        </span>
                        <span>
                          Thinking {number(call.thinking_chars)} chars · Text{" "}
                          {number(call.content_chars)} chars · Tool calls {number(call.tool_calls)}
                        </span>
                        <span>
                          Reasoning tokens {number(call.reasoning_tokens)}
                          {call.reasoning_tokens != null
                            ? " (included in output)"
                            : " (not reported)"}{" "}
                          · Images {call.image_count} · Finish {call.finish_reason || "—"}
                        </span>
                        <span className="context-usage-note font-mono text-[10px] text-ink-muted">
                          Estimated text: system/project ~{number(call.estimated_parts.system)} ·
                          conversation ~{number(call.estimated_parts.conversation)} · tools ~
                          {number(call.estimated_parts.tools)} · format ~
                          {number(call.estimated_parts.format)}
                        </span>
                      </article>
                    ))}
                  </div>
                  <p className="context-usage-note text-ink-muted">
                    Latest {calls.length} calls in this turn. Kept after errors until the next
                    message or project/model change.
                  </p>
                </>
              ) : (
                <p className="context-usage-empty text-ink-muted">
                  Usage details appear when a model call starts.
                </p>
              )}
            </div>

            {children ? (
              <footer className="context-dialog-actions shrink-0 border-t border-border-subtle bg-elevated/50 px-5 py-3">
                {children}
              </footer>
            ) : null}
          </section>
        </div>
      ) : null}
    </div>
  );
}
