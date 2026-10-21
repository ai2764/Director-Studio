import { ApiError } from "../setup/api";

export type DepState = "installed" | "missing" | "size_mismatch" | "restart_needed" | "unknown";
export type JobState = "queued" | "downloading" | "verifying" | "installing" | "done" | "failed" | "cancelled";

export interface DepJob {
  kind: "model" | "pack";
  id: string;
  state: JobState;
  done: number;
  total: number | null;
  error: string | null;
  source: string | null;
}

export interface DepItem {
  kind: "model" | "pack";
  id: string;
  name: string;
  state: DepState;
  installable: boolean;
  license: string | null;
  license_url?: string | null;
  unlicensed?: boolean;
  gated?: boolean;
  community?: boolean;
  size: number | null;
  partial?: number;
  folder?: string;
  path?: string | null;
  page?: string;
  url?: string | null;
  repo?: string | null;
  ref?: string | null;
  bundled?: boolean;
  preinstalled?: boolean;
  note?: { zh?: string | null; en?: string | null };
  job?: DepJob;
}

export interface DepWorkflow {
  file: string;
  zh: string;
  en: string;
  ready: boolean;
  missing_bytes: number;
  kind?: "llm";
  items: DepItem[];
}

export interface DepsStatus {
  mode: "managed" | "external" | "remote";
  can_install: boolean;
  comfy_reachable: boolean;
  hf_endpoint: string;
  hf_token_set: boolean;
  models_dir: string;
  custom_nodes_dir: string;
  restart_needed: boolean;
  workflows: DepWorkflow[];
  jobs: DepJob[];
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-DS-Admin": "1", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* not json */
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<T>;
}

export const getDeps = () => call<DepsStatus>("/api/deps");
export const installDeps = (items: { kind: string; id: string }[]) => call<{ jobs: DepJob[] }>("/api/deps/install", { items });
export const installWorkflow = (workflow: string) => call<{ jobs: DepJob[] }>("/api/deps/install", { workflow });
export const cancelDep = (kind: string, id: string) => call<{ cancelled: boolean }>("/api/deps/cancel", { kind, id });
export const restartComfyForDeps = () => call<{ result: string }>("/api/deps/restart-comfy", {});

export function formatBytes(n: number | null | undefined): string {
  if (!n) return "—";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

export const ACTIVE_JOB: JobState[] = ["queued", "downloading", "verifying", "installing"];
