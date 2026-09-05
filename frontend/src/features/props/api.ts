import { parseError } from "../../shared/api/client";
import type { JobStatus, OutputSlot } from "../../shared/api/types";

export type { JobStatus, OutputSlot };

export interface PropJobRecord {
  id: string;
  status: JobStatus;
  name: string;
  notes: string;
  seed: number | null;
  fixed_seed: boolean;
  error: string | null;
  comfy_prompt_id: string | null;
  created_at: string;
  updated_at: string;
  outputs: Record<string, OutputSlot>;
  output_order: string[];
  input_previews: Record<string, string>;
  prop_id: string | null;
  pipeline_id?: string;
}

export interface PropRecord {
  id: string;
  name: string;
  notes: string;
  seed: number | null;
  job_id: string;
  created_at: string;
  files: Record<string, string | null>;
  urls: Record<string, string>;
  meta?: Record<string, unknown>;
  project_id?: string | null;
}

export async function generateProp(form: FormData): Promise<PropJobRecord> {
  const res = await fetch("/api/props/generate", { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getPropJob(jobId: string): Promise<PropJobRecord> {
  const res = await fetch(`/api/props/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function cancelPropJob(jobId: string): Promise<PropJobRecord> {
  const res = await fetch(`/api/props/jobs/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function savePropJob(
  jobId: string,
  body?: { name?: string; notes?: string; project_id?: string | null },
): Promise<PropRecord> {
  const res = await fetch(`/api/props/jobs/${jobId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listPropJobs(
  projectId?: string | null,
  limit = 20,
): Promise<PropJobRecord[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (projectId) params.set("project_id", projectId);
  const res = await fetch(`/api/props/jobs?${params}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
