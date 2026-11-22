import { useEffect, useRef, useState } from "react";
import { ChevronDown, Sparkles } from "lucide-react";
import { getDirectorModel, setDirectorModel } from "../director/api";
import { Banner, Select, cn } from "../../shared/ui";

export function WorkspaceModelPicker({
  disabled,
  onSwitching,
}: {
  disabled: boolean;
  onSwitching: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState("");
  const root = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (root.current && event.target instanceof Node && !root.current.contains(event.target)) {
        root.current.open = false;
      }
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setLoading(true);
    setError("");
    void getDirectorModel()
      .then((status) => {
        if (current) {
          setModel(status.model || "");
          setModels(status.available || []);
        }
      })
      .catch((cause) => {
        if (current) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [open]);

  async function change(value: string) {
    if (!value || value === model || disabled || switching) return;
    setSwitching(true);
    onSwitching(true);
    setError("");
    try {
      const status = await setDirectorModel(value, true);
      setModel(status.model || value);
      if (status.available) setModels(status.available);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSwitching(false);
      onSwitching(false);
    }
  }

  return (
    <details
      ref={root}
      className="relative text-xs"
      onToggle={(event) => setOpen(event.currentTarget.open)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.currentTarget.open = false;
          event.currentTarget.querySelector("summary")?.focus();
        }
      }}
    >
      <summary
        className={cn(
          "inline-flex h-8 cursor-pointer list-none items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors select-none",
          "[&::-webkit-details-marker]:hidden",
          open
            ? "border-border-strong bg-hover text-ink"
            : "border-border-subtle bg-panel text-ink-secondary hover:border-border-strong hover:bg-hover hover:text-ink",
        )}
      >
        <Sparkles className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
        <span>AI model</span>
        <ChevronDown
          className={cn("h-3 w-3 text-ink-muted transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </summary>
      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-50 grid w-[min(320px,calc(100vw-32px))] gap-2.5 rounded-xl border border-border-subtle bg-panel p-3.5 shadow-[var(--shadow-popover)]">
          <label className="grid gap-1.5 text-xs font-medium text-ink">
            <span>AI model</span>
            <Select
              size="md"
              wrapperClassName="w-full"
              aria-label="Workspace AI model"
              value={model}
              disabled={disabled || loading || switching || !models.length}
              onChange={(event) => void change(event.target.value)}
            >
              {!models.length && (
                <option value={model}>
                  {loading ? "Loading models…" : model || "No models available"}
                </option>
              )}
              {!!model && !!models.length && !models.includes(model) && (
                <option value={model} disabled>
                  {model} (not available)
                </option>
              )}
              {models.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </label>
          <small className="text-[11px] leading-relaxed text-ink-muted">
            Shared across projects for chat, description refinement and prompt writing.
          </small>
          {disabled && (
            <small className="text-[11px] text-status-warn">
              Finish the current action before switching.
            </small>
          )}
          {switching && (
            <small role="status" className="text-[11px] text-ink-secondary">
              Switching model…
            </small>
          )}
          {error && <Banner tone="danger">Model selection failed: {error}</Banner>}
        </div>
      )}
    </details>
  );
}
