import { parseError } from "../../shared/api/client";
import type {
  JobStatus,
  OutputSlot,
  Project,
  ProjectDetail,
  PromptSections,
  Shot,
  ShotRef,
  ShotVoiceRef,
} from "../../shared/api/types";

export type { Project, ProjectDetail, PromptSections, Shot, ShotRef, ShotVoiceRef, JobStatus, OutputSlot };

export async function listProjects(): Promise<Project[]> {
  const res = await fetch("/api/projects");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getProject(projectId: string): Promise<ProjectDetail> {
  const res = await fetch(`/api/projects/${projectId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getShot(shotId: string): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function patchShot(
  shotId: string,
  body: {
    refs?: ShotRef[];
    voice_refs?: ShotVoiceRef[];
    prompt_sections?: PromptSections;
    duration_s?: number;
    dialogue?: string[];
    title?: string;
    script_beat?: string;
    feedback?: string;
  },
): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function approveShot(shotId: string): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/approve`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export type H3Provider = "local" | "minimax";

export interface H3ProviderStatus {
  default_provider: H3Provider;
  minimax_configured: boolean;
  minimax_resolution: string;
}

export async function getH3ProviderStatus(): Promise<H3ProviderStatus> {
  const res = await fetch("/api/h3-ref2va/provider");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function submitShot(
  shotId: string,
  h3Provider: H3Provider,
  resolution?: { width: number; height: number },
): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ h3_provider: h3Provider, ...resolution }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteLayout(
  shotId: string,
  layoutRefId: string,
): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/layouts/${layoutRefId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** Insert a pre-made layout still as a composition ref (optional Gate 1). */
export async function insertLayoutRefFrame(
  shotId: string,
  file: File,
  opts?: { name?: string; notes?: string; approve?: boolean },
): Promise<Shot> {
  const fd = new FormData();
  fd.append("file", file);
  if (opts?.name) fd.append("name", opts.name);
  if (opts?.notes) fd.append("notes", opts.notes);
  fd.append("approve", opts?.approve === false ? "false" : "true");
  const res = await fetch(`/api/shots/${shotId}/layout/insert`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/** Skip reference-frame gate; layout is not required for H3. */
export async function skipLayout(shotId: string): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/layout/skip`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export interface H3JobRecord {
  id: string;
  status: JobStatus;
  name: string;
  notes: string;
  prompt: string;
  dialogue: string[];
  frames: number | null;
  error: string | null;
  comfy_prompt_id: string | null;
  external_task_id: string | null;
  created_at: string;
  updated_at: string;
  outputs: Record<string, OutputSlot>;
  input_previews: Record<string, string>;
  pipeline_id: string;
  h3_provider?: H3Provider;
}

export async function getH3Job(jobId: string): Promise<H3JobRecord> {
  const res = await fetch(`/api/h3-ref2va/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function cancelH3Job(jobId: string): Promise<H3JobRecord> {
  const res = await fetch(`/api/h3-ref2va/jobs/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
