import { useEffect, useRef, useState } from "react";
import { compactDirectorContext, getDirectorRuntime, type ChatCompactionResult } from "./api";
import "./contextUsage.css";

export function ContextCompaction({ projectId, disabled, onBusyChange }: {
  projectId: string; disabled: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ChatCompactionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void getDirectorRuntime(controller.signal).then(status => {
      if (!controller.signal.aborted) setAvailable(status.runtime === "harness");
    }).catch(() => {});
    return () => { controller.abort(); operation.current?.abort(); onBusyChange(false); };
  }, [projectId, onBusyChange]);

  const compact = async () => {
    if (disabled || operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true); onBusyChange(true); setError(null); setResult(null);
    try {
      const next = await compactDirectorContext(projectId, controller.signal);
      if (!controller.signal.aborted) setResult(next);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (!controller.signal.aborted) { setBusy(false); onBusyChange(false); }
      if (operation.current === controller) operation.current = null;
    }
  };

  if (!available) return null;
  return <div className="context-compaction">
    <button type="button" className="context-compaction-button" disabled={disabled || busy} onClick={() => void compact()}
      title="Use Harness to summarize older history. This does not retry your request or generate assets.">
      {busy ? "Compacting context…" : "Compact context"}
    </button>
    {busy ? <span role="status">Saving a reusable summary. No tools will run.</span> : null}
    {result ? <p role="status">{result.compacted
      ? `Context compacted: ~${result.before_tokens.toLocaleString("en-US")} → ~${result.after_tokens.toLocaleString("en-US")} estimated tokens. Summary saved.`
      : "No safely compressible older history. Context unchanged."}
      {" "}Send your next message when ready; nothing was retried automatically.</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}
