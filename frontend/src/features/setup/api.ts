import type {
  CapabilityResult, ComfyRuntimeStatus, ConfigResponse, Draft, DoctorReport, OllamaModels, PullJob, PutResult, SetupState, TestResult,
} from "./types";

export class ApiError extends Error {
  status: number;
  fields: Record<string, string>;
  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

/** Config endpoints require the X-DS-Admin header (forces a CORS preflight; see api/security.py). */
async function adminFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "X-DS-Admin": "1", ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers || {}) },
  });
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`;
    let fields: Record<string, string> = {};
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (data.detail) message = JSON.stringify(data.detail);
      if (data.fields) fields = data.fields;
    } catch {
      /* not json */
    }
    throw new ApiError(message, res.status, fields);
  }
  return res.json() as Promise<T>;
}

export const getConfig = () => adminFetch<ConfigResponse>("/api/config");
export const putConfig = (revision: string, changes: Draft) =>
  adminFetch<PutResult>("/api/config", { method: "PUT", body: JSON.stringify({ revision, changes }) });
export const getSetupState = () => adminFetch<SetupState>("/api/setup/state");
export const completeSetup = (skipped = false) =>
  adminFetch<SetupState>("/api/setup/complete", { method: "POST", body: JSON.stringify({ skipped }) });
export const getDoctor = () => adminFetch<DoctorReport>("/api/doctor");
export const getComfyRuntime = (log = false) => adminFetch<ComfyRuntimeStatus>(`/api/comfy/runtime${log ? "?log=true" : ""}`);
export const restartComfy = () => adminFetch<ComfyRuntimeStatus & { result: string }>("/api/comfy/restart", { method: "POST" });
export const getLaunchPreview = () =>
  adminFetch<{ ok: boolean; args?: string[]; error?: string; warnings?: string[]; sage_available?: boolean; models_dir?: string }>(
    "/api/comfy/launch-preview",
  );
export const getSystemInfo = () => adminFetch<Record<string, unknown>>("/api/system/info");
export const testLlm = (body: Record<string, unknown>) =>
  adminFetch<TestResult>("/api/config/test/llm", { method: "POST", body: JSON.stringify(body) });
export const testComfy = (body: Record<string, unknown>) =>
  adminFetch<TestResult>("/api/config/test/comfy", { method: "POST", body: JSON.stringify(body) });
export const testMinimax = (body: Record<string, unknown>) =>
  adminFetch<TestResult>("/api/config/test/minimax", { method: "POST", body: JSON.stringify(body) });
export async function selectDirectorModel(model: string): Promise<void> {
  const res = await fetch("/api/director/model", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, persist: true }),
  });
  if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
}
export const testLlmCapabilities = (body: Record<string, unknown>) =>
  adminFetch<CapabilityResult>("/api/config/test/llm-capabilities", { method: "POST", body: JSON.stringify(body) });

// ---- app restart (DS launcher) -------------------------------------------------------
export const restartApp = () =>
  adminFetch<{ restarting: boolean; boot_id: string }>("/api/system/restart", { method: "POST" });

/** Resolve once the backend answers with a different boot id (or reject after ``timeoutMs``). */
export async function waitForRestart(previousBootId: string, timeoutMs = 180000, intervalMs = 1000): Promise<SetupState> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    try {
      const state = await getSetupState();
      if (state.boot_id && state.boot_id !== previousBootId) return state;
    } catch {
      /* backend is restarting */
    }
  }
  throw new ApiError("restart timed out", 504);
}

// ---- Ollama model management -----------------------------------------------------------
export const listOllamaModels = () => adminFetch<OllamaModels>("/api/llm/ollama/models");
export const pullOllamaModel = (name: string) =>
  adminFetch<PullJob>("/api/llm/ollama/pull", { method: "POST", body: JSON.stringify({ name }) });
export const listOllamaPulls = () => adminFetch<{ jobs: PullJob[] }>("/api/llm/ollama/pulls");
export const cancelOllamaPull = (id: string) =>
  adminFetch<PullJob>(`/api/llm/ollama/pulls/${encodeURIComponent(id)}`, { method: "DELETE" });
export const deleteOllamaModel = (name: string) =>
  adminFetch<{ deleted: string }>(`/api/llm/ollama/models/${name.split("/").map(encodeURIComponent).join("/")}`, { method: "DELETE" });
