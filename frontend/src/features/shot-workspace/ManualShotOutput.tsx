import { useEffect, useRef, useState } from "react";
import type { Shot } from "../../shared/api/types";
import { promptReady } from "../../shared/shotWorkflowStatus";
import type { PromptCheck } from "./promptCheck";
import { WorkspaceMascot } from "./WorkspaceMascot";
import { cancelH3Job, getH3Job, getH3ProviderStatus, getLocalH3Resolutions, getShot, submitShot,
  type H3JobRecord, type H3Provider, type LocalH3Resolution } from "../production/api";

const active = (status?: string) => ["queued", "uploading", "running"].includes(status || "");
const labels: Record<string, string> = { queued: "Queued", uploading: "Uploading", running: "Generating", succeeded: "Completed", failed: "Failed", cancelled: "Cancelled" };

export function ManualShotOutput({ shot, disabled, onSaved, check, promptWriting = false }: {
  shot: Shot; disabled: boolean; onSaved: (shot: Shot) => void;
  check?: PromptCheck | null; promptWriting?: boolean;
}) {
  const [job, setJob] = useState<H3JobRecord | null>(null);
  const [provider, setProvider] = useState<H3Provider>("local");
  const [minimax, setMinimax] = useState(false);
  const [resolutions, setResolutions] = useState<LocalH3Resolution[]>([]);
  const [resolution, setResolution] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const mounted = useRef(true);
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  useEffect(() => {
    mounted.current = true;
    void getH3ProviderStatus().then((status) => {
      if (mounted.current) { setProvider(status.default_provider); setMinimax(status.minimax_configured); }
    }).catch((cause) => { if (mounted.current) setError(String(cause)); });
    void getLocalH3Resolutions().then((data) => {
      if (mounted.current) setResolutions(data.presets);
    }).catch((cause) => { if (mounted.current) setError(String(cause)); });
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    setJob(null); setError("");
    if (!shot.h3_job_id) return;
    const jobId = shot.h3_job_id;
    async function poll() {
      try {
        const result = await getH3Job(jobId!);
        if (!current) return;
        setJob(result); setNow(Date.now()); setError("");
        if (active(result.status)) timer = setTimeout(poll, 1500);
        else {
          const latest = await getShot(shot.id);
          if (current && latest.h3_job_id === jobId) onSavedRef.current(latest);
        }
      } catch (cause) {
        if (current) { setError(String(cause)); timer = setTimeout(poll, 3000); }
      }
    }
    void poll();
    return () => { current = false; clearTimeout(timer); };
  }, [shot.id, shot.h3_job_id]);
  const running = active(job?.status) || (!job && active(shot.status));
  const ready = shot.refs.length > 0 && promptReady(shot.prompt_sections) && shot.meta?.material_review_pending !== true
    && (!check || check.valid);
  const video = job?.outputs.video?.url || job?.outputs.master?.url;
  const elapsed = job ? Math.max(0, Math.floor(((active(job.status) ? now : Date.parse(job.updated_at)) - Date.parse(job.created_at)) / 1000)) : 0;
  async function generate() {
    if (disabled || busy || running || !ready) return;
    setBusy(true); setError("");
    try {
      const preset = provider === "local" && shot.video_context?.mode !== "previous_shot"
        ? resolutions.find((item) => item.id === resolution) : undefined;
      const saved = await submitShot(shot.id, provider, preset && { width: preset.width, height: preset.height });
      // The job belongs to this shot even if the user has selected another one.
      onSavedRef.current(saved);
    } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function cancel() {
    if (!shot.h3_job_id || busy) return;
    setBusy(true); setError("");
    try {
      const result = await cancelH3Job(shot.h3_job_id);
      const latest = await getShot(shot.id);
      onSavedRef.current(latest);
      if (mounted.current) setJob(result);
    } catch (cause) { if (mounted.current) setError(String(cause)); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <aside className="manual-output" aria-label="Video output">
    <header><div className="workspace-output-title"><WorkspaceMascot role="director" /><strong>Video output</strong></div><small>{shot.title}</small></header>
    <div className="manual-output-preview">
      {video ? <video key={video} src={video} controls preload="metadata" /> : <p>{running ? "Generating video. It will appear here when ready." : "Your generated video will appear here."}</p>}
    </div>
    {job && <p role="status">{labels[job.status] || job.status} · {elapsed}s</p>}
    {running && <progress aria-label="Generating video" />}
    {job?.error && <p role="alert" className="banner error">{job.error}</p>}
    {error && <p role="alert" className="banner error">{error}</p>}
    {!promptWriting && check?.stale && <p className="manual-prompt-warning" role="alert">This prompt is out of date. Rewrite it before generating a video.</p>}
    {!promptWriting && check && !check.valid && !check.stale && Object.values(shot.prompt_sections).some(Boolean)
      && <p className="manual-prompt-warning" role="alert">Prompt checks failed. See the issues beside the prompt.</p>}
    <label>Provider<select aria-label="Provider" value={provider} disabled={busy || running} onChange={(e) => setProvider(e.target.value as H3Provider)}>
      <option value="local">ComfyUI</option>{minimax && <option value="minimax">MiniMax</option>}
    </select></label>
    {provider === "local" && <label>Size<select aria-label="Video size" value={resolution} disabled={busy || running || shot.video_context?.mode === "previous_shot"} onChange={(e) => setResolution(e.target.value)}>
      <option value="auto">{shot.video_context?.mode === "previous_shot" ? "Match source video" : "Workflow default"}</option>
      {resolutions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select></label>}
    <button className="btn primary" disabled={disabled || busy || running || !ready} onClick={() => void generate()}>
      {busy ? "Working…" : running ? "Generating…" : video ? "Regenerate video" : "Generate video"}</button>
    {running && <button className="btn secondary" disabled={busy} onClick={() => void cancel()}>Cancel generation</button>}
    {!ready && <small>Link references and write a complete prompt to generate a video.</small>}
    {disabled && <small>Save your changes or wait for the current task to finish.</small>}
    {video && <a href={video} target="_blank" rel="noopener noreferrer">Open video</a>}
  </aside>;
}
