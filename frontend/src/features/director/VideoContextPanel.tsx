import { useEffect, useState } from "react";
import { fetchH3Profiles } from "../../shared/api/client";
import type { Shot, ShotVideoContext } from "../../shared/api/types";
import { VideoDependencyIndicator } from "./VideoDependencyIndicator";
import {
  getShot,
  getVideoJob,
  saveVideoContext,
  uploadVideoContext,
  type VideoContextSave,
  type VideoJobRecord,
} from "./api";

const WINDOW_FRAMES = [5, 22, 39, 56] as const;
type WindowFrames = (typeof WINDOW_FRAMES)[number];

interface Version {
  jobId: string;
  outputKey: string;
  url: string;
}

export function continuationFocus(actions: string[], shots: Shot[]): string | null {
  for (const action of actions) {
    if (!action.startsWith("configure_video_context:")) continue;
    const id = action.slice("configure_video_context:".length);
    const shot = shots.find((item) => item.id === id);
    if (shot?.video_context && shot.video_context.mode !== "off") return id;
  }
  return null;
}

function contextWindowLabel(frames: number): string {
  return `${frames} frames · ${(frames / 24).toFixed(2)} s`;
}

function sourceOf(shots: Shot[], shot: Shot): Shot | null {
  if (shot.video_context?.source_shot_id) {
    return shots.find((item) => item.id === shot.video_context?.source_shot_id) || null;
  }
  const index = shots.findIndex((item) => item.id === shot.id);
  return index > 0 ? shots[index - 1] : null;
}

function versionIds(shot: Shot): string[] {
  const raw = shot.meta?.superseded_h3_job_ids;
  const older = Array.isArray(raw)
    ? raw.filter((item): item is string => typeof item === "string")
    : [];
  return [...new Set([...(shot.h3_job_id ? [shot.h3_job_id] : []), ...older])];
}

function legalWindow(value: number | null | undefined): WindowFrames {
  return value === 5 || value === 22 || value === 39 || value === 56 ? value : 22;
}

function videoKeys(job: VideoJobRecord): string[] {
  return Object.entries(job.outputs).flatMap(([key, slot]) => {
    const name = `${slot?.filename || ""} ${slot?.url || ""}`.toLowerCase();
    const video = key === "video"
      || key === "video_raw"
      || name.endsWith(".mp4")
      || name.endsWith(".mov")
      || name.endsWith(".webm");
    return video ? [key] : [];
  });
}

function activeContext(context: ShotVideoContext | null | undefined): context is ShotVideoContext {
  return Boolean(context && context.mode !== "off");
}

export function VideoContextPanel({
  shot,
  shots,
  expanded = false,
  onShotUpdated,
}: {
  shot: Shot;
  shots: Shot[];
  expanded?: boolean;
  onShotUpdated?: (shot: Shot) => void;
}) {
  const context = shot.video_context;
  const active = activeContext(context);
  const previous = sourceOf(shots, shot);
  const [open, setOpen] = useState(expanded);
  const [profile, setProfile] = useState<"builtin" | "custom" | null>(null);
  const [frames, setFrames] = useState(String(legalWindow(context?.context_frames)));
  const [carry, setCarry] = useState(Boolean(context?.carry_audio));
  const [jobs, setJobs] = useState<VideoJobRecord[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [uploadFile, setUploadFile] = useState<{ id: string; filename: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (expanded) setOpen(true);
  }, [expanded, shot.id]);

  useEffect(() => {
    setFrames(String(legalWindow(shot.video_context?.context_frames)));
    setCarry(Boolean(shot.video_context?.carry_audio));
  }, [shot]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    fetchH3Profiles()
      .then((profiles) => {
        if (!cancelled) setProfile(profiles.active.source === "custom" ? "custom" : "builtin");
      })
      .catch(() => {
        if (!cancelled) setProfile("builtin");
      });
    return () => {
      cancelled = true;
    };
  }, [open, active]);

  useEffect(() => {
    setJobs([]);
    setVersions([]);
    if (!active || context?.mode !== "previous_shot") return;
    if (!previous) return;
    const ids = [...new Set([...versionIds(previous), ...(context.source_job_id ? [context.source_job_id] : [])])];
    let cancelled = false;
    void (async () => {
      const loaded: VideoJobRecord[] = [];
      for (const id of ids) {
        try {
          const job = await getVideoJob(id);
          if (job) loaded.push(job);
        } catch {
          // A missing historical job stays out of the version list.
        }
      }
      if (cancelled) return;
      const next: Version[] = [];
      for (const job of loaded) {
        if (job.status !== "succeeded") continue;
        for (const key of videoKeys(job)) {
          next.push({
            jobId: job.id,
            outputKey: key,
            url: job.outputs[key]?.url || "",
          });
        }
      }
      setJobs(loaded);
      setVersions(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [active, previous, context?.mode, context?.source_job_id]);

  const resolvedJobId = context?.mode === "previous_shot"
    ? (context.source_job_id || previous?.h3_job_id || "")
    : "";
  const currentVersions = versions.filter((version) => version.jobId === resolvedJobId);
  const resolvedKey = context?.mode === "previous_shot"
    ? (context.source_output_key || (currentVersions.length === 1 ? currentVersions[0].outputKey : "")) : "";
  const waitingForSource = context?.mode === "previous_shot" && previous && (
    !resolvedJobId || jobs.some((job) => job.id === resolvedJobId
      && ["queued", "uploading", "running"].includes(job.status))
  );
  const blocks = sourceBlocks(context, previous, jobs);
  const preview = previewUrl(shot, versions, uploadFile, previous?.h3_job_id);
  const selectedValue = versions.some((version) => (
    version.jobId === resolvedJobId && version.outputKey === resolvedKey
  ))
    ? `${resolvedJobId}:${resolvedKey}`
    : "";

  function payload(body: VideoContextSave, windowFrames = legalWindow(context?.context_frames), audio = carry): VideoContextSave {
    if (profile === "custom") return body;
    return { ...body, context_frames: windowFrames, carry_audio: audio };
  }

  async function commit(body: VideoContextSave) {
    setBusy(true);
    setError(null);
    try {
      await saveVideoContext(shot.id, body);
      onShotUpdated?.(await getShot(shot.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function onUpload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const record = await uploadVideoContext(shot.project_id, file);
      setUploadFile({ id: record.upload_id, filename: record.filename });
      await saveVideoContext(shot.id, payload({
        mode: "external_upload",
        upload_id: record.upload_id,
      }));
      onShotUpdated?.(await getShot(shot.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  function updateWindow() {
    const next = Number(frames);
    if (!WINDOW_FRAMES.includes(next as WindowFrames)) {
      setError("Use 5, 22, 39 or 56 frames.");
      return;
    }
    setError(null);
    if (!context || next === legalWindow(context.context_frames)) return;
    const source: VideoContextSave = context.mode === "previous_shot" ? {
      mode: "previous_shot",
      ...(context.source_shot_id ? { source_shot_id: context.source_shot_id } : {}),
      ...(context.source_job_id ? { source_job_id: context.source_job_id } : {}),
      ...(context.source_output_key ? { source_output_key: context.source_output_key } : {}),
    } : { mode: "external_upload", ...(context.upload_id ? { upload_id: context.upload_id } : {}) };
    void commit(payload(source, next as WindowFrames, carry));
  }

  if (!active) return null;

  return (
    <section className="video-context-panel" role="region" aria-label="Video continuation">
      <VideoDependencyIndicator shot={shot} shots={shots} />
      <div className="video-context-window">
        {profile === "builtin" ? <label>
          Context window
          <input type="number" aria-label="Context window" min={5} max={56} step={17}
            value={frames} disabled={busy} onChange={(event) => setFrames(event.target.value)}
            onBlur={updateWindow} onKeyDown={(event) => {
              if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
            }} />
        </label> : null}
        {profile === "custom" ? <label>
          Context window
          <input aria-label="Context window" readOnly value="工作流配置" />
        </label> : null}
        {profile === "builtin" ? <small>{contextWindowLabel(legalWindow(context?.context_frames))}</small> : null}
      </div>
      {blocks.map((reason) => <p key={reason} className="video-context-blocked">{reason}</p>)}
      {waitingForSource ? <p role="status">Continuation plan saved. Waiting for the source video.</p> : null}
      {error ? <p className="video-context-blocked" role="alert">{error}</p> : null}
      <details
        className="video-context-settings"
        open={open}
        onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary>Continuation settings</summary>
        {open ? <div className="video-context-fields">
      {resolvedJobId ? (
        <p className="video-context-fact">
          <span>Job</span>
          <strong aria-label="Resolved job">{resolvedJobId}</strong>
        </p>
      ) : null}
      {resolvedKey ? (
        <p className="video-context-fact"><span>Artifact</span><strong>{resolvedKey}</strong></p>
      ) : null}
      {context?.mode === "external_upload" && context.upload_id ? (
        <p className="video-context-fact">
          <span>Upload</span>
          <strong aria-label="Resolved upload">{context.upload_id}</strong>
        </p>
      ) : null}
      {preview ? (
        <video controls playsInline preload="metadata" src={preview} />
      ) : null}
          {context.mode === "previous_shot" ? <label>
            Source shot
            <select aria-label="Source shot" value={previous?.id || ""} disabled={busy}
              onChange={(event) => {
                if (!event.target.value) return;
                void commit(payload({ mode: "previous_shot", source_shot_id: event.target.value }));
              }}>
              {!previous ? <option value="">Source shot unavailable</option> : null}
              {shots.slice(0, Math.max(0, shots.findIndex((item) => item.id === shot.id))).map((item, index) => (
                <option key={item.id} value={item.id}>Shot {index + 1} · {item.title}</option>
              ))}
            </select>
          </label> : null}
          <label>
            Source video version
            <select
              aria-label="Source video version"
              value={selectedValue}
              disabled={busy || versions.length === 0}
              onChange={(event) => {
                const [jobId, outputKey] = event.target.value.split(":");
                if (!jobId || !outputKey) return;
                void commit(payload({
                  mode: "previous_shot",
                  ...(previous ? { source_shot_id: previous.id } : {}),
                  source_job_id: jobId,
                  source_output_key: outputKey,
                }));
              }}
            >
              {selectedValue === "" ? <option value="">Select a video</option> : null}
              {versions.map((version) => (
                <option key={`${version.jobId}:${version.outputKey}`} value={`${version.jobId}:${version.outputKey}`}>
                  {version.jobId} · {version.outputKey}
                </option>
              ))}
            </select>
          </label>
          {profile === "builtin" ? (
            <label>
              <input
                type="checkbox"
                aria-label="Carry source audio"
                checked={carry}
                disabled={busy || !active}
                onChange={(event) => {
                  const next = event.target.checked;
                  setCarry(next);
                  if (!active || !context) return;
                  if (context.mode === "previous_shot") {
                    void commit(payload({
                      mode: "previous_shot",
                      ...(context.source_shot_id ? { source_shot_id: context.source_shot_id } : {}),
                      ...(context.source_job_id ? { source_job_id: context.source_job_id } : {}),
                      ...(context.source_output_key ? { source_output_key: context.source_output_key } : {}),
                    }, legalWindow(context.context_frames), next));
                  } else if (context.mode === "external_upload" && context.upload_id) {
                    void commit(payload({
                      mode: "external_upload",
                      upload_id: context.upload_id,
                    }, legalWindow(context.context_frames), next));
                  }
                }}
              />
              Carry source audio
            </label>
          ) : null}
          <label>
            Upload continuation video
            <input
              type="file"
              aria-label="Upload continuation video"
              accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                void onUpload(file);
              }}
            />
          </label>
          {active ? (
            <button type="button" disabled={busy} onClick={() => void commit({ mode: "off" })}>
              Turn continuation off
            </button>
          ) : null}
        </div> : null}
      </details>
    </section>
  );
}

function sourceBlocks(
  context: ShotVideoContext | null | undefined,
  previous: Shot | null,
  jobs: VideoJobRecord[],
): string[] {
  if (context?.mode !== "previous_shot") return [];
  if (!previous) return [context.source_shot_id ? "Selected source shot was not found" : "This shot has no previous shot"];
  const resolvedId = context.source_job_id || previous.h3_job_id || "";
  if (!resolvedId) return [];
  if (jobs.length === 0) return [];
  const job = jobs.find((item) => item.id === resolvedId);
  if (!job) return ["Context job does not belong to the source shot"];
  if (["queued", "uploading", "running"].includes(job.status)) return [];
  if (job.status !== "succeeded") return [`Source shot job is ${job.status}`];
  const outputs = videoKeys(job);
  if (outputs.length === 0) return ["Source shot has no video artifact"];
  if (outputs.length > 1 && !outputs.includes(context.source_output_key || "")) {
    return ["Select one video artifact from the source shot"];
  }
  return [];
}

function previewUrl(
  shot: Shot,
  versions: Version[],
  uploadFile: { id: string; filename: string } | null,
  currentSourceJobId?: string | null,
): string {
  const context = shot.video_context;
  if (
    context?.mode === "external_upload"
    && uploadFile
    && uploadFile.id === context.upload_id
  ) {
    return `/api/files/projects/${shot.project_id}/video_context_uploads/${uploadFile.filename}`;
  }
  if (context?.mode !== "previous_shot") return "";
  const jobId = context.source_job_id || currentSourceJobId || "";
  const match = versions.find((version) => (
    version.jobId === jobId && version.outputKey === (context.source_output_key || version.outputKey) && version.url
  ));
  if (!context.source_output_key) {
    const matches = versions.filter((version) => version.jobId === jobId && version.url);
    return matches.length === 1 ? matches[0].url : "";
  }
  return match?.url || "";
}
