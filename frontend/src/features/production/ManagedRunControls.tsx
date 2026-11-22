import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Info, Play, X } from "lucide-react";
import {
  Badge,
  Banner,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Select,
  StatusDot,
  cn,
  type StatusDotProps,
} from "../../shared/ui";
import {
  getManagedRun,
  planManagedRun,
  runManagedSelection,
  stopManagedRun,
  type H3Provider,
  type LocalH3Resolution,
  type ManagedRun,
} from "./api";

interface Props {
  projectId: string;
  shots: { id: string; title: string; script_beat?: string; camera_motion?: string }[];
  provider: H3Provider;
  presets: LocalH3Resolution[];
  onProjectChanged: () => void;
  onStateChange?: (active: boolean) => void;
}

function defaultSelection(run: ManagedRun): Set<string> {
  let desired: Set<string>;
  if (run.state === "draft") {
    desired = new Set(
      run.selected_shot_ids?.length
        ? run.selected_shot_ids
        : run.steps.map((step) => step.shot_id),
    );
  } else if (run.state === "paused" || run.state === "stopped") {
    desired = new Set(run.pending_shot_ids ?? []);
  } else {
    desired = new Set();
  }
  return validSelection(run, desired);
}

function validSelection(run: ManagedRun, desired: Set<string>): Set<string> {
  const valid = new Set<string>();
  for (const step of run.steps) {
    if (!desired.has(step.shot_id)) continue;
    const sourceId = step.tail_from_shot_id;
    if (!sourceId || run.completed_job_ids[sourceId] || valid.has(sourceId)) {
      valid.add(step.shot_id);
    }
  }
  return valid;
}

function unavailableSource(run: ManagedRun, shotId: string, selected: Set<string>): string | null {
  const stepIndex = run.steps.findIndex((step) => step.shot_id === shotId);
  const sourceId = run.steps[stepIndex]?.tail_from_shot_id;
  if (!sourceId || run.completed_job_ids[sourceId]) return null;
  const sourceIndex = run.steps.findIndex((step) => step.shot_id === sourceId);
  return sourceIndex >= 0 && sourceIndex < stepIndex && selected.has(sourceId) ? null : sourceId;
}

function displayableRun(run: ManagedRun | null): ManagedRun | null {
  if (!run?.is_stale) return run;
  return run.state === "active" || run.state === "stopping" ? run : null;
}

function runTone(state: ManagedRun["state"]): NonNullable<StatusDotProps["tone"]> {
  switch (state) {
    case "active":
      return "info";
    case "stopping":
    case "paused":
      return "warn";
    case "completed":
      return "ok";
    case "stopped":
      return "danger";
    default:
      return "neutral";
  }
}

export function ManagedRunControls({
  projectId,
  shots,
  provider,
  presets,
  onProjectChanged,
  onStateChange,
}: Props) {
  const [run, setRun] = useState<ManagedRun | null>(null);
  const [open, setOpen] = useState(false);
  const [resolution, setResolution] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showPlanHelp, setShowPlanHelp] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const lastProgress = useRef("");
  const requestVersion = useRef(0);
  const shotRevision = JSON.stringify(shots);
  const onChanged = useRef(onProjectChanged);
  onChanged.current = onProjectChanged;
  const onStateChanged = useRef(onStateChange);
  onStateChanged.current = onStateChange;

  const closeManager = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    onStateChanged.current?.(run?.state === "active" || run?.state === "stopping");
  }, [run?.state]);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeManager();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, closeManager]);

  useLayoutEffect(() => {
    if (!run) {
      setSelected(new Set());
      return;
    }
    setSelected(defaultSelection(run));
    if (run.resolution_preset) setResolution(run.resolution_preset);
  }, [run?.run_id, run?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setRun(null);
    setResolution("");
    setSelected(new Set());
    setError("");
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    const version = ++requestVersion.current;
    getManagedRun(projectId)
      .then((next) => {
        if (!cancelled && version === requestVersion.current) {
          const displayRun = displayableRun(next);
          setRun(displayRun);
          setError("");
          if (displayRun?.resolution_preset) setResolution(displayRun.resolution_preset);
        }
      })
      .catch((cause) => {
        if (!cancelled && version === requestVersion.current) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, shotRevision, open]);

  useEffect(() => {
    if (run?.state !== "active" && run?.state !== "stopping") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      const version = ++requestVersion.current;
      getManagedRun(projectId)
        .then((next) => {
          if (cancelled || version !== requestVersion.current) return;
          setRun(displayableRun(next));
          setError("");
          if (!next) return;
          const progress = `${next.run_id}:${next.state}:${next.current_index}:${next.current_job_id}`;
          if (progress !== lastProgress.current) {
            lastProgress.current = progress;
            onChanged.current();
          }
        })
        .catch((cause) => {
          if (!cancelled && version === requestVersion.current) {
            setError(cause instanceof Error ? cause.message : String(cause));
          }
        });
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, run?.state]);

  async function perform(action: () => Promise<ManagedRun>) {
    ++requestVersion.current;
    setBusy(true);
    setError("");
    try {
      const next = await action();
      ++requestVersion.current;
      const displayRun = displayableRun(next);
      setRun(displayRun);
      if (displayRun?.resolution_preset) setResolution(displayRun.resolution_preset);
      onChanged.current();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const active = run?.state === "active" || run?.state === "stopping";
  if ((provider !== "local" && !active) || shots.length === 0) return null;
  const shotName = (id: string) => shots.find((shot) => shot.id === id)?.title || id;
  const resumable = run?.state === "paused" || run?.state === "stopped";
  const chosen =
    run?.steps.filter((step) => selected.has(step.shot_id)).map((step) => step.shot_id) ?? [];
  const resolutionValid = Boolean(
    resolution && presets.some((preset) => preset.id === resolution),
  );
  const selectionDisabled = busy || active || Boolean(run?.is_stale);

  function toggleShot(shotId: string) {
    if (!run) return;
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return validSelection(run, next);
    });
  }

  const remaining = run?.pending_shot_ids?.length ?? 0;
  const triggerSummary = !run
    ? "Managed run"
    : `${run.state}${remaining ? ` · ${remaining} remaining` : ""}`;
  const tone = run ? runTone(run.state) : "neutral";

  return (
    <div className="managed-run-launch flex items-center">
      <Button
        ref={triggerRef}
        type="button"
        variant="secondary"
        size="sm"
        className="managed-run-trigger h-8 gap-2"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Play className="h-3 w-3 text-accent" aria-hidden="true" />
        <span>Managed run</span>
        {run ? (
          <Badge tone={tone} className={cn("managed-run-trigger-state", `status-${run.state}`)}>
            <StatusDot tone={tone} pulse={active} />
            {triggerSummary}
          </Badge>
        ) : null}
      </Button>

      {open ? (
        <Dialog
          open={open}
          onOpenChange={(next) => {
            if (!next) closeManager();
          }}
        >
        <DialogContent
          hideClose
          className="managed-run-dialog max-w-xl gap-0 overflow-hidden p-0"
          aria-labelledby="managed-run-title"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            closeRef.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            triggerRef.current?.focus();
          }}
        >
          <DialogHeader className="managed-run-dialog-header flex-row items-center justify-between border-b border-border-subtle bg-elevated/40 px-5 py-3.5 pr-5">
            <div className="flex flex-col gap-0.5">
              <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-ink-muted">
                Local · ComfyUI
              </span>
              <DialogTitle id="managed-run-title">Managed run</DialogTitle>
              <DialogDescription className="sr-only">
                Plan and execute sequential local H3 video generation across selected shots.
              </DialogDescription>
            </div>
            <div className="managed-run-dialog-status flex items-center gap-2">
              {run ? (
                <Badge tone={tone} className={cn("status-chip", `status-${run.state}`)}>
                  <StatusDot tone={tone} pulse={active} />
                  {run.state}
                </Badge>
              ) : null}
              <Button
                ref={closeRef}
                type="button"
                variant="ghost"
                size="icon"
                className="managed-run-close"
                aria-label="Close managed run"
                onClick={closeManager}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          </DialogHeader>

          <div className="managed-run-dialog-body flex max-h-[75dvh] flex-col gap-3.5 overflow-y-auto p-5">
            {error ? <Banner tone="danger">{error}</Banner> : null}
            {run?.is_stale && run.stale_reason ? (
              <Banner tone="danger">{run.stale_reason}</Banner>
            ) : null}
            {run?.paused_reason ? (
              <Banner tone="neutral" role="status">
                {run.paused_reason}
              </Banner>
            ) : null}

            {run?.steps.length ? (
              <details
                className="managed-run-details rounded-lg border border-border-subtle bg-elevated/40 p-3"
                key={`${projectId}:${run.run_id}`}
                open={!active}
              >
                <summary className="cursor-pointer select-none text-xs font-semibold text-ink">
                  Plan details
                </summary>
                <p className="mt-1.5 text-[11px] text-ink-muted">
                  Choose any Shots to run. Generated Shots can be selected again.
                </p>
                <ol
                  className="managed-run-plan mt-2.5 flex flex-col gap-1.5"
                  aria-label="Managed run plan"
                >
                  {run.steps.map((step, index) => {
                    const missingSourceId = unavailableSource(run, step.shot_id, selected);
                    const isDone = index < run.current_index;
                    const isCurrent = index === run.current_index;
                    const shotMeta = shots.find((shot) => shot.id === step.shot_id);
                    return (
                      <li
                        key={step.shot_id}
                        className={cn(
                          "rounded-md border px-3 py-2 transition-colors",
                          isCurrent
                            ? "current border-accent/40 bg-accent-soft/40"
                            : isDone
                              ? "done border-border-subtle bg-panel/60"
                              : "border-border-subtle bg-panel",
                        )}
                      >
                        <label className="managed-run-shot-choice flex cursor-pointer items-start gap-2.5">
                          <input
                            type="checkbox"
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
                            checked={selected.has(step.shot_id)}
                            aria-label={`${shotName(step.shot_id)} Shot ${index + 1}`}
                            disabled={selectionDisabled || Boolean(missingSourceId)}
                            onChange={() => toggleShot(step.shot_id)}
                          />
                          <span className="managed-run-number font-mono text-[11px] font-medium text-ink-muted tabular-nums">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="managed-run-shot-copy flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="flex flex-wrap items-center gap-2">
                              <strong className="text-xs font-semibold text-ink">
                                {shotName(step.shot_id)}
                              </strong>
                              {(run.completed_job_ids ?? {})[step.shot_id] ? (
                                <Badge tone="ok" className="managed-run-generated">
                                  Generated
                                </Badge>
                              ) : null}
                            </span>
                            {run.state === "draft" && shotMeta?.script_beat ? (
                              <small className="text-[11px] text-ink-secondary">
                                {shotMeta.script_beat}
                              </small>
                            ) : null}
                            {run.state === "draft" && shotMeta?.camera_motion ? (
                              <small className="text-[11px] text-ink-muted">
                                Camera · {shotMeta.camera_motion}
                              </small>
                            ) : null}
                            {step.tail_from_shot_id ? (
                              <small className="text-[11px] text-ink-muted">
                                Tail from {shotName(step.tail_from_shot_id)} · {step.tail_reason}
                              </small>
                            ) : null}
                            {missingSourceId ? (
                              <small className="managed-run-warning text-[11px] font-medium text-status-warn">
                                Select {shotName(missingSourceId)} first or generate it successfully.
                              </small>
                            ) : null}
                            {(run.skipped_shots ?? {})[step.shot_id] ? (
                              <small className="managed-run-warning text-[11px] font-medium text-status-warn">
                                {run.skipped_shots[step.shot_id]}
                              </small>
                            ) : null}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ol>
              </details>
            ) : null}

            {run?.state === "draft" ? (
              <p className="text-[11px] text-ink-muted">
                Higher resolutions, especially 1080, may exceed available VRAM.
              </p>
            ) : null}

            {run?.state === "draft" && !run.is_stale ? (
              <label className="managed-run-resolution flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                One resolution for all shots
                <Select
                  aria-label="Managed run resolution"
                  value={resolution}
                  disabled={busy}
                  wrapperClassName="w-full"
                  onChange={(event) => setResolution(event.target.value)}
                >
                  <option value="">Choose resolution…</option>
                  {presets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.label}
                    </option>
                  ))}
                </Select>
              </label>
            ) : run?.resolution_preset ? (
              <p className="text-[11px] text-ink-muted">
                Resolution ·{" "}
                {presets.find((preset) => preset.id === run.resolution_preset)?.label ||
                  run.resolution_preset}
              </p>
            ) : null}

            <div className="managed-run-actions flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle pt-3">
              {!active ? (
                <div className="managed-run-plan-action flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={busy}
                    onClick={() => void perform(() => planManagedRun(projectId))}
                  >
                    {run?.is_stale ? "Plan again" : "Plan managed run"}
                  </Button>
                  {!run?.is_stale ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="managed-run-info h-7 w-7 text-ink-muted"
                      aria-label="About Plan managed run"
                      aria-expanded={showPlanHelp}
                      aria-controls="managed-run-plan-help"
                      onClick={() => setShowPlanHelp((shown) => !shown)}
                    >
                      <Info className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  ) : null}
                </div>
              ) : (
                <div />
              )}

              <div className="flex items-center gap-2">
                {run && !active && !run.is_stale ? (
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={
                      busy ||
                      chosen.length === 0 ||
                      (run.state === "draft" && !resolutionValid)
                    }
                    onClick={() =>
                      void perform(() =>
                        runManagedSelection(
                          projectId,
                          run.run_id,
                          chosen,
                          run.state === "draft" ? resolution : undefined,
                        ),
                      )
                    }
                  >
                    {resumable ? "Resume selected" : "Run selected"}
                  </Button>
                ) : null}
                {active ? (
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    disabled={busy}
                    onClick={() => void perform(() => stopManagedRun(projectId, run.run_id))}
                  >
                    {run.state === "stopping" ? "Retry stop" : "Stop managed run"}
                  </Button>
                ) : null}
              </div>
            </div>

            {!active && showPlanHelp ? (
              <p
                id="managed-run-plan-help"
                className="managed-run-help rounded-md border border-border-subtle bg-elevated/60 px-3 py-2 text-[11px] leading-relaxed text-ink-secondary"
              >
                Plan reviews the existing Shots and chooses tail-frame handoffs. It saves a draft for
                you to review. No video starts until you select a resolution and click Start managed
                run.
              </p>
            ) : null}
          </div>
        </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}
