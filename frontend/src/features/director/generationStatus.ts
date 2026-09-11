export type GenerationPhase = "queued" | "uploading" | "generating" | "saving";

export interface GenerationJobStatus {
  job_id: string;
  pipeline_id: string;
  kind: "image" | "video";
  status: "queued" | "uploading" | "running";
  phase: GenerationPhase;
  queued_at: string;
}

export interface DirectorVramStatus {
  provider?: string;
  model?: string;
  llm_runtime?: {
    provider?: string;
    model?: string;
    ready?: boolean;
    uses_local_gpu?: boolean;
    loaded_instances?: unknown[];
    error?: string;
  };
  chat_locked: boolean;
  generation_count: number;
  generation_jobs: GenerationJobStatus[];
  owner?: "llm" | "comfy" | null;
  comfy_pipeline?: string | null;
}

export function formatGenerationElapsed(queuedAt: string, now: Date): string {
  const queuedMs = Date.parse(queuedAt);
  const elapsedSeconds = Number.isFinite(queuedMs)
    ? Math.max(0, Math.floor((now.getTime() - queuedMs) / 1000))
    : 0;
  const hours = Math.floor(elapsedSeconds / 3600);
  const minutes = Math.floor((elapsedSeconds % 3600) / 60);
  const seconds = elapsedSeconds % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

function phaseLabel(job: GenerationJobStatus): string {
  if (job.phase === "queued") return "Queued";
  if (job.phase === "uploading") return "Uploading assets";
  if (job.phase === "saving") return "Saving result";
  return job.kind === "video" ? "Generating video" : "Generating image";
}

export function generationStatusText(
  status: DirectorVramStatus,
  now: Date,
): string {
  const jobs = [...status.generation_jobs].sort(
    (left, right) => Date.parse(left.queued_at) - Date.parse(right.queued_at),
  );
  const active = jobs[0];
  if (!status.chat_locked || !active) return "";
  const elapsed = formatGenerationElapsed(active.queued_at, now);
  const waiting = Math.max(0, status.generation_count - 1);
  const suffix = waiting > 0 ? ` · ${waiting} ${waiting === 1 ? "job" : "jobs"} waiting` : "";
  return `${phaseLabel(active)} · ${elapsed}${suffix}`;
}
