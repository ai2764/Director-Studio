import type { H3ActiveProfile, H3Profiles, PipelineInfo } from "./types";
import type { H3Analysis, H3Import, H3Mapping, H3Proposal, H3TestRun, H3Validation } from "../../features/settings/types";

export async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.message === "string") {
      const issues = data.details?.issues;
      return [data.message, ...(Array.isArray(issues) ? issues.map((issue: { message?: string }) => issue.message).filter(Boolean) : [])].join("; ");
    }
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

const H3_PROFILES = "/api/workflow-profiles/h3";
async function profileRequest<T>(path = "", method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`${H3_PROFILES}${path}`, {
    method,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
const importPath = (id: string, action: string) => `/imports/${encodeURIComponent(id)}/${action}`;
export const fetchH3Profiles = () => profileRequest<H3Profiles>();
export async function importH3Workflow(file: File): Promise<H3Import> {
  const body = new FormData();
  body.append("workflow", file);
  const res = await fetch(`${H3_PROFILES}/imports`, { method: "POST", body });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
export const fetchH3ImportAnalysis = (id: string) => profileRequest<H3Analysis>(importPath(id, "analysis"));
export const proposeH3Mapping = (id: string) => profileRequest<H3Proposal>(importPath(id, "propose-mapping"), "POST");
export const saveH3Mapping = (id: string, mapping: H3Mapping) => profileRequest<{ import_id: string; mapping: H3Mapping }>(importPath(id, "mapping"), "PUT", mapping);
export const validateH3Import = (id: string) => profileRequest<H3Validation>(importPath(id, "validate"), "POST");
export const testH3Import = (id: string, pictureAssetId: string, audioAssetId: string | null) => profileRequest<H3TestRun>(importPath(id, "test"), "POST", { picture_asset_id: pictureAssetId, audio_asset_id: audioAssetId });
export const activateH3Import = (id: string) => profileRequest<{ import_id: string; profile_id: string; active: H3ActiveProfile }>(importPath(id, "activate"), "POST");
export const selectH3Profile = (profileId: string) => profileRequest<{ active: H3ActiveProfile }>("/select", "POST", { profile_id: profileId });
