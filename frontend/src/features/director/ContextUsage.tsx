import type { ContextUsage } from "./api";
import "./contextUsage.css";

const number = (value: number | null) => value == null ? "—" : value.toLocaleString("en-US");
const input = (call: ContextUsage) => call.input_tokens == null
  ? `~${number(call.estimated_input_tokens)}` : number(call.input_tokens);
const status = (call: ContextUsage) => ({
  running: call.purpose === "compaction" ? "Compacting history" : "Waiting for model",
  completed: "Completed",
  output_truncated: "Output truncated",
  context_overflow: "Context overflow",
  failed: "Request failed",
  cancelled: "Cancelled",
})[call.status];

export function ContextUsagePanel({ calls }: { calls: ContextUsage[] }) {
  const last = calls.at(-1);
  if (!last) return <div className="context-usage-empty">Context · Usage appears when a model call starts</div>;
  const prompt = last.input_tokens ?? last.estimated_input_tokens;
  const high = last.input_budget != null && prompt >= last.input_budget * 0.8;
  const failed = ["output_truncated", "context_overflow", "failed"].includes(last.status);
  const tone = failed ? "error" : high ? "warning" : "normal";
  const ratio = last.context_window ? prompt / last.context_window : null;

  return (
    <details className={`context-usage ${tone}`}>
      <summary>
        <span>Context · {input(last)}{last.context_window ? ` / ${number(last.context_window)}` : " tokens"}</span>
        <span className="context-usage-status">{status(last)}{high && !failed ? " · High context pressure" : ""}</span>
      </summary>
      <div className="context-usage-body">
        <div className="context-usage-metrics" aria-live="polite">
          <span>Input {input(last)} · {last.input_tokens == null ? "estimated text" : "provider reported"}</span>
          <span>Output {number(last.output_tokens)} / {number(last.output_limit)}</span>
        </div>
        {ratio != null ? (
          <div role="meter" aria-label="Input context usage" aria-valuemin={0} aria-valuemax={last.context_window!}
            aria-valuenow={Math.min(prompt, last.context_window!)} aria-valuetext={`${input(last)} / ${number(last.context_window)} input tokens`}
            className="context-usage-meter">
            <span style={{ width: `${Math.min(100, ratio * 100)}%` }} />
          </div>
        ) : <p>Context capacity not reported by this provider.</p>}
        <p className="context-usage-note">
          Input budget {number(last.input_budget)} · Output reserve {number(last.output_limit)} tokens.
          {" "}~ is a text estimate (4 chars/token), including system, project, history and tools.
          {" "}Not an exact tokenizer count; excludes image tokens.
          {" "}Native calls are non-streaming: actual counts arrive when the model returns.
        </p>
        {last.status === "output_truncated" ? <p>Output was cut off before completion. This alone does not prove input context overflow.</p> : null}
        <div className="context-usage-calls">
          {[...calls].reverse().map((call) => (
            <article key={call.call_id} className="context-usage-call">
              <strong>#{call.sequence} · {call.purpose === "compaction" ? "History compaction" : "Model turn"} · {status(call)}</strong>
              <span>{call.provider} · {call.model}</span>
              <span>Input {input(call)} · Output {number(call.output_tokens)} / {number(call.output_limit)} · {call.status === "running" ? "In progress" : `${(call.elapsed_ms / 1000).toFixed(1)}s`}</span>
              <span>Thinking {number(call.thinking_chars)} chars · Text {number(call.content_chars)} chars · Tool calls {number(call.tool_calls)}</span>
              <span>Reasoning tokens {number(call.reasoning_tokens)}{call.reasoning_tokens != null ? " (included in output)" : " (not reported)"} · Images {call.image_count} · Finish {call.finish_reason || "—"}</span>
              <span className="context-usage-note">Estimated text: system/project ~{number(call.estimated_parts.system)} · conversation ~{number(call.estimated_parts.conversation)} · tools ~{number(call.estimated_parts.tools)} · format ~{number(call.estimated_parts.format)}</span>
            </article>
          ))}
        </div>
        <p className="context-usage-note">Latest {calls.length} calls in this turn. Kept after errors until the next message or project/model change.</p>
      </div>
    </details>
  );
}
