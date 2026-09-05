import { parseError } from "../../shared/api/client";
import type {
  JsonProductionDocument,
  JsonShotJobRecord,
  ShotFileMaps,
} from "./types";

export type {
  JsonPictureRole,
  JsonProductionAudio,
  JsonProductionDocument,
  JsonProductionPicture,
  JsonProductionShot,
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
  files: ShotFileMaps,
): Promise<JsonShotJobRecord> {
  const fd = new FormData();
  fd.append("revision", String(revision));

  const pictureIndexes = [...files.pictures.keys()].sort((a, b) => a - b);
  for (const index of pictureIndexes) {
    const file = files.pictures.get(index);
    if (file) fd.append("pictures", file);
  }

  const audioIndexes = [...files.audio.keys()].sort((a, b) => a - b);
  for (const index of audioIndexes) {
    const file = files.audio.get(index);
    if (file) fd.append("audios", file);
  }

  const res = await fetch(
    `/api/projects/${projectId}/production-storyboard/shots/${shotId}/submit`,
    { method: "POST", body: fd },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
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
