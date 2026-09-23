import { useEffect, useRef, useState } from "react";
import {
  getManagedRun, planManagedRun, startManagedRun, stopManagedRun,
  type H3Provider, type LocalH3Resolution, type ManagedRun,
} from "./api";

interface Props {
  projectId: string;
  shots: { id: string; title: string; script_beat?: string; camera_motion?: string }[];
  provider: H3Provider;
  presets: LocalH3Resolution[];
  onProjectChanged: () => void;
  onStateChange?: (active: boolean) => void;
}

export function ManagedRunControls({ projectId, shots, provider, presets, onProjectChanged, onStateChange }: Props) {
  const [run, setRun] = useState<ManagedRun | null>(null);
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showPlanHelp, setShowPlanHelp] = useState(false);
  const lastProgress = useRef("");
  const onChanged = useRef(onProjectChanged);
  onChanged.current = onProjectChanged;
  const onStateChanged = useRef(onStateChange);
  onStateChanged.current = onStateChange;

  useEffect(() => {
    onStateChanged.current?.(run?.state === "active" || run?.state === "stopping");
  }, [run?.state]);

  useEffect(() => {
    let cancelled = false;
    setRun(null);
    setResolution("");
    getManagedRun(projectId).then((next) => {
      if (!cancelled) {
        setRun(next);
        if (next?.resolution_preset) setResolution(next.resolution_preset);
      }
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    if (run?.state !== "active" && run?.state !== "stopping") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      getManagedRun(projectId).then((next) => {
        if (cancelled || !next) return;
        setRun(next);
        const progress = `${next.run_id}:${next.state}:${next.current_index}:${next.current_job_id}`;
        if (progress !== lastProgress.current) {
          lastProgress.current = progress;
          onChanged.current();
        }
      }).catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    }, 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [projectId, run?.state]);

  async function perform(action: () => Promise<ManagedRun>) {
    setBusy(true);
    setError("");
    try {
      const next = await action();
      setRun(next);
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

  return (
    <section className="managed-run-card" aria-label="Managed local H3 run">
      <div className="managed-run-heading">
        <div>
          <span className="mobile-eyebrow">Local · ComfyUI</span>
          <h2>Managed run</h2>
        </div>
        {run ? <span className={`status-chip status-${run.state}`}>{run.state}</span> : null}
      </div>
      {error ? <div className="banner error" role="alert">{error}</div> : null}
      {run?.paused_reason ? <div className="banner" role="status">{run.paused_reason}</div> : null}
      {run?.steps.length ? (
        <details className="managed-run-details" key={projectId}>
          <summary>Plan details</summary>
          <p className="muted tiny">Review the shot order and continuity handoffs. Stop at any time.</p>
          <ol className="managed-run-plan" aria-label="Managed run plan">
            {run.steps.map((step, index) => (
              <li key={step.shot_id} className={index < run.current_index ? "done" : index === run.current_index ? "current" : ""}>
                <span className="managed-run-number">{String(index + 1).padStart(2, "0")}</span>
                <span><strong>{shotName(step.shot_id)}</strong>
                  {run.state === "draft" ? <small>{shots.find((shot) => shot.id === step.shot_id)?.script_beat}</small> : null}
                  {run.state === "draft" && shots.find((shot) => shot.id === step.shot_id)?.camera_motion
                    ? <small>Camera · {shots.find((shot) => shot.id === step.shot_id)?.camera_motion}</small> : null}
                  {step.tail_from_shot_id ? <small>Tail from {shotName(step.tail_from_shot_id)} · {step.tail_reason}</small> : null}
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      {run?.state === "draft" ? <p className="muted tiny">Higher resolutions, especially 1080, may exceed available VRAM.</p> : null}
      {run?.state === "draft" ? (
        <label className="field-label managed-run-resolution">
          One resolution for all shots
          <select className="field-input" aria-label="Managed run resolution" value={resolution}
            disabled={busy} onChange={(event) => setResolution(event.target.value)}>
            <option value="">Choose resolution…</option>
            {presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
          </select>
        </label>
      ) : run?.resolution_preset ? (
        <p className="muted tiny">Resolution · {presets.find((preset) => preset.id === run.resolution_preset)?.label || run.resolution_preset}</p>
      ) : null}
      <div className="managed-run-actions">
        {!active ? <div className="managed-run-plan-action">
          <button type="button" className="btn secondary" disabled={busy}
            onClick={() => void perform(() => planManagedRun(projectId))}>Plan managed run</button>
          <button type="button" className="managed-run-info" aria-label="About Plan managed run"
            aria-expanded={showPlanHelp} aria-controls="managed-run-plan-help"
            onClick={() => setShowPlanHelp((shown) => !shown)}>i</button>
        </div> : null}
        {run?.state === "draft" ? <button type="button" className="btn primary"
          disabled={busy || !resolution || !presets.some((preset) => preset.id === resolution)}
          onClick={() => void perform(() => startManagedRun(projectId, run.run_id, resolution))}>Start managed run</button> : null}
        {active ? <button type="button" className="btn danger" disabled={busy}
          onClick={() => void perform(() => stopManagedRun(projectId, run.run_id))}>
          {run.state === "stopping" ? "Retry stop" : "Stop managed run"}
        </button> : null}
      </div>
      {!active && showPlanHelp ? <p id="managed-run-plan-help" className="muted tiny managed-run-help">
        Plan reviews the existing Shots and chooses tail-frame handoffs. It saves a draft for you to review. No video starts until you select a resolution and click Start managed run.
      </p> : null}
    </section>
  );
}
