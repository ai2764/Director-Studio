import { parseError } from "../../shared/api/client";
import type { Shot, ShotRef } from "../../shared/api/types";
export { getProject, getShot, saveVideoContext, uploadVideoContext } from "../director/api";
import { getProject } from "../director/api";

export interface AgentProgress {
  run_id: string; shot_id: string | null; started_at: string; updated_at: string;
  status: "running" | "completed" | "failed" | "cancelled" | "interrupted"; current_stage: string; action_id: string;
}
interface Task extends Omit<AgentProgress, "current_stage" | "action_id"> { request_id: string; error: string }

async function json<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(url, { method, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}
const taskUrl = (projectId: string) => `/api/shot-workspace/projects/${projectId}/task`;
const progress = (task: Task): AgentProgress => ({ ...task, current_stage: "prompt", action_id: task.request_id });

export async function getDirectorChatSession(projectId: string) {
  const task = await json<Task | null>(taskUrl(projectId));
  return { active: task?.status === "running", session_id: task?.run_id || null,
    started_at: task?.started_at || null, error: task?.status === "failed" || task?.status === "interrupted" ? task.error : "", progress: task ? progress(task) : null };
}

export async function cancelDirectorChatSession(projectId: string) {
  await json(taskUrl(projectId) + "/cancel", "POST");
  return getDirectorChatSession(projectId);
}

export async function chatWithDirectorStream(projectId: string, _message: string, _history: unknown[],
  handlers: { onAgentProgress?: (value: AgentProgress) => void }, _images: unknown[], _signal?: AbortSignal,
  _retry?: unknown, _segments?: unknown, action?: { shot_id: string; action_id: string; choice: string }) {
  if (!action) throw new Error("Select a shot before writing its prompt.");
  let task = await json<Task>(`/api/shot-workspace/shots/${action.shot_id}/prompt`, "POST", { request_id: action.action_id });
  while (task.status === "running") {
    handlers.onAgentProgress?.(progress(task));
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const latest = await json<Task | null>(taskUrl(projectId));
    if (!latest || latest.request_id !== action.action_id) throw new Error("The prompt task changed. Refresh its status.");
    task = latest;
  }
  handlers.onAgentProgress?.(progress(task));
  if (task.status !== "completed") throw new Error(task.error || "Prompt writing did not complete.");
  return { shots: (await getProject(projectId)).shots, reply: "Prompt saved." };
}

export type ShotMaterialSelection = Pick<ShotRef, "role" | "asset_id" | "file_key" | "notes">;
export async function replaceShotMaterials(shotId: string, materials: ShotMaterialSelection[], opts: { expectedRefs: ShotRef[]; revisionRequest?: string }) {
  return json<Shot>(`/api/shot-workspace/shots/${shotId}/materials`, "PUT", { materials,
    expected_refs: opts.expectedRefs, revision_request: opts.revisionRequest || "" });
}

export function formatDialogue(shot: Shot) {
  const certified = (shot.meta?.prompt_dialogue_contract as { lines?: Array<{ line_id: string; speaker_id: string; text: string; language: string }> } | undefined)?.lines || [];
  return shot.dialogue_lines?.length ? shot.dialogue_lines.map((line) => {
    const previous = certified.find((item) => item.line_id === line.line_id && item.speaker_id === line.speaker_id && item.text === line.text);
    return `${line.speaker_name} [${line.language || previous?.language || ""}]: ${line.text}`;
  }).join("\n") : shot.dialogue.join("\n");
}
