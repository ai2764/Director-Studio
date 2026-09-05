import type { PipelineInfo } from "./types";

export async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ");
    }
    return JSON.stringify(data);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function fetchHealth(): Promise<{
  ok: boolean;
  comfy_reachable: boolean;
  comfy_error: string | null;
}> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchPipelines(): Promise<PipelineInfo[]> {
  const res = await fetch("/api/pipelines");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
