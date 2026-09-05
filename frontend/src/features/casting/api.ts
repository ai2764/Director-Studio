import { parseError } from "../../shared/api/client";
import type { JobStatus, OutputSlot } from "../../shared/api/types";

export type { JobStatus, OutputSlot };
/** Derived from uploads for display only (workflow auto-routes). */
export type ActorMode = "text" | "reference" | "wardrobe";

export interface JobOutputs {
  wardrobe_ref?: OutputSlot | null;
  master?: OutputSlot | null;
  bust_threeview?: OutputSlot | null;
  fullbody_threeview?: OutputSlot | null;
  asset_sheet?: OutputSlot | null;
}

export interface JobRecord {
  id: string;
  status: JobStatus;
  mode: ActorMode;
  name: string;
  notes: string;
  description: string;
  body_description: string;
  hair_description: string;
  negative_prompt: string;
  has_actor_ref: boolean;
  has_wardrobe_ref: boolean;
  include_headwear: boolean;
  include_footwear: boolean;
  seed: number | null;
  fixed_seed: boolean;
  error: string | null;
  comfy_prompt_id: string | null;
  created_at: string;
  updated_at: string;
  outputs: JobOutputs;
  input_previews: Record<string, string>;
  actor_id: string | null;
  pipeline_id?: string;
}

export interface ActorRecord {
  id: string;
  name: string;
  notes: string;
  mode: ActorMode;
  description: string;
  body_description?: string;
  hair_description?: string;
  has_actor_ref?: boolean;
  has_wardrobe_ref?: boolean;
  include_headwear?: boolean;
  include_footwear?: boolean;
  seed: number | null;
  job_id: string;
  created_at: string;
  files: Record<string, string | null>;
  urls: Record<string, string>;
  project_id?: string | null;
}

export interface MetaDefaults {
  default_negative: string;
  default_description: string;
  default_body_description?: string;
  default_hair_description?: string;
  max_upload_mb: number;
  routing?: Record<string, unknown>;
  fields?: { id: string; label: string; required?: boolean; hint?: string }[];
  output_slots: { key: string; label: string; conditional?: boolean }[];
}

export async function fetchDefaults(): Promise<MetaDefaults> {
  const res = await fetch("/api/meta/actor-defaults");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function generateActor(form: FormData): Promise<JobRecord> {
  const res = await fetch("/api/actors/generate", { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getJob(jobId: string): Promise<JobRecord> {
  const res = await fetch(`/api/actors/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function cancelJob(jobId: string): Promise<JobRecord> {
  const res = await fetch(`/api/actors/jobs/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function saveJob(
  jobId: string,
  body?: { name?: string; notes?: string; project_id?: string | null },
): Promise<ActorRecord> {
  const res = await fetch(`/api/actors/jobs/${jobId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listActors(projectId?: string | null): Promise<ActorRecord[]> {
  const qs = projectId
    ? `?project_id=${encodeURIComponent(projectId)}`
    : "";
  const res = await fetch(`/api/actors${qs}`);
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return data.items || [];
}

export async function listActorJobs(
  projectId?: string | null,
  limit = 20,
): Promise<JobRecord[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (projectId) params.set("project_id", projectId);
  const res = await fetch(`/api/actors/jobs?${params}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
