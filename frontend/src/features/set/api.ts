import { parseError } from "../../shared/api/client";
import type { JobStatus, OutputSlot } from "../../shared/api/types";

export type { JobStatus, OutputSlot };

export interface SceneJobRecord {
  id: string;
  status: JobStatus;
  name: string;
  notes: string;
  angle_prompts: string;
  prepend_text: string;
  append_text: string;
  used_angles: string[];
  /** Local file stems: {scene_name}_{view}, e.g. Audition_Room_01_left_side_view_h270_v0 */
  output_stems: string[];
  seed: number | null;
  fixed_seed: boolean;
  error: string | null;
  comfy_prompt_id: string | null;
  created_at: string;
  updated_at: string;
  outputs: Record<string, OutputSlot>;
  output_order: string[];
  input_previews: Record<string, string>;
  scene_id: string | null;
  pipeline_id?: string;
}

export interface SceneRecord {
  id: string;
  name: string;
  notes: string;
  angle_prompts: string;
  used_angles: string[];
  seed: number | null;
  job_id: string;
  created_at: string;
  files: Record<string, string | null>;
  urls: Record<string, string>;
  meta?: Record<string, unknown>;
  project_id?: string | null;
}

export interface SceneMetaDefaults {
  default_angles: string;
  default_prepend?: string;
  default_append?: string;
  max_upload_mb: number;
  fields?: { id: string; label: string; required?: boolean; hint?: string }[];
  description?: string;
}

export async function fetchSceneDefaults(): Promise<SceneMetaDefaults> {
  const res = await fetch("/api/meta/scene-defaults");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function generateScene(form: FormData): Promise<SceneJobRecord> {
  const res = await fetch("/api/scenes/generate", { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getSceneJob(jobId: string): Promise<SceneJobRecord> {
  const res = await fetch(`/api/scenes/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function cancelSceneJob(jobId: string): Promise<SceneJobRecord> {
  const res = await fetch(`/api/scenes/jobs/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function saveSceneJob(
  jobId: string,
  body?: { name?: string; notes?: string; project_id?: string | null },
): Promise<SceneRecord> {
  const res = await fetch(`/api/scenes/jobs/${jobId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listScenes(projectId?: string | null): Promise<SceneRecord[]> {
  const qs = projectId
    ? `?project_id=${encodeURIComponent(projectId)}`
    : "";
  const res = await fetch(`/api/scenes${qs}`);
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return data.items || [];
}

export async function listSceneJobs(
  projectId?: string | null,
  limit = 20,
): Promise<SceneJobRecord[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (projectId) params.set("project_id", projectId);
  const res = await fetch(`/api/scenes/jobs?${params}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
