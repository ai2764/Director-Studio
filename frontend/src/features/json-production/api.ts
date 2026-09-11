import { parseError } from "../../shared/api/client";
import type { H3Provider } from "../production/api";
import type {
  JsonProductionAssetKind,
  JsonProductionDocument,
  JsonProductionStoredAsset,
  JsonShotJobRecord,
} from "./types";

export type {
  JsonPictureRole,
  JsonProductionAudio,
  JsonProductionDocument,
  JsonProductionPicture,
  JsonProductionShot,
  JsonProductionStoredAsset,
  JsonShotJobRecord,
  ShotFileMaps,
} from "./types";

export async function getStoryboard(
  projectId: string,
): Promise<JsonProductionDocument> {
  const res = await fetch(`/api/projects/${projectId}/production-storyboard`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function putStoryboard(
  projectId: string,
  document: JsonProductionDocument,
): Promise<JsonProductionDocument> {
  const res = await fetch(`/api/projects/${projectId}/production-storyboard`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(document),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function submitJsonShot(
  projectId: string,
  shotId: string,
  revision: number,
  h3Provider: H3Provider,
): Promise<JsonShotJobRecord> {
  const fd = new FormData();
  fd.append("revision", String(revision));
  fd.append("h3_provider", h3Provider);

  const res = await fetch(
    `/api/projects/${projectId}/production-storyboard/shots/${shotId}/submit`,
    { method: "POST", body: fd },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listJsonShotAssets(
  projectId: string,
): Promise<JsonProductionStoredAsset[]> {
  const res = await fetch(`/api/projects/${projectId}/production-storyboard/assets`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function putJsonShotAsset(
  projectId: string,
  shotId: string,
  kind: JsonProductionAssetKind,
  index: number,
  file: File,
): Promise<JsonProductionStoredAsset> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(
    `/api/projects/${projectId}/production-storyboard/shots/${encodeURIComponent(shotId)}/assets/${kind}/${index}`,
    { method: "PUT", body: fd },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function clearJsonShotAsset(
  projectId: string,
  shotId: string,
  kind: JsonProductionAssetKind,
  index: number,
): Promise<void> {
  const res = await fetch(
    `/api/projects/${projectId}/production-storyboard/shots/${encodeURIComponent(shotId)}/assets/${kind}/${index}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await parseError(res));
}

export async function listJsonShotJobs(
  projectId: string,
  jsonShotId: string,
  jsonStoryboardRevision: number,
): Promise<JsonShotJobRecord[]> {
  const params = new URLSearchParams({
    project_id: projectId,
    json_shot_id: jsonShotId,
    json_storyboard_revision: String(jsonStoryboardRevision),
  });
  const res = await fetch(`/api/h3-ref2va/jobs?${params}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
