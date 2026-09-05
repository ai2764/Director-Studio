import { parseError } from "../../shared/api/client";
import type {
  LayoutReviewStatus,
  LayoutSourceRef,
  Project,
  ProjectDetail,
  ProjectMode,
  Shot,
  ShotRef,
} from "../../shared/api/types";
import type { DirectorVramStatus } from "./generationStatus";

export type { Project, ProjectDetail, Shot };

export class DirectorChatError extends Error {
  readonly code: string;
  readonly generationCount: number;

  constructor(message: string, code: string, generationCount = 0) {
    super(message);
    this.name = "DirectorChatError";
    this.code = code;
    this.generationCount = generationCount;
  }
}

async function directorResponseError(res: Response): Promise<Error> {
  try {
    const data = await res.json();
    const detail = data?.detail;
    if (detail && typeof detail === "object" && detail.code) {
      return new DirectorChatError(
        detail.message || res.statusText || "Director chat unavailable",
        String(detail.code),
        Number(detail.generation_count || 0),
      );
    }
    if (typeof detail === "string") return new Error(detail);
    return new Error(JSON.stringify(data));
  } catch {
    return new Error(res.statusText || `HTTP ${res.status}`);
  }
}

export async function getDirectorVramStatus(): Promise<DirectorVramStatus> {
  const res = await fetch("/api/director/vram");
  if (!res.ok) throw await directorResponseError(res);
  return res.json();
}

export interface LayoutBrief {
  purpose: string;
  state_description: string;
  time_hint: string;
  source_refs: LayoutSourceRef[];
}

export interface LayoutReviewRequest {
  status: LayoutReviewStatus;
  feedback: string;
  human_override?: boolean;
}

export async function listProjects(): Promise<Project[]> {
  const res = await fetch("/api/projects");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createProject(body: {
  name: string;
  script_text: string;
  mode?: ProjectMode;
}): Promise<Project> {
  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getProject(projectId: string): Promise<ProjectDetail> {
  const res = await fetch(`/api/projects/${projectId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function updateProject(
  projectId: string,
  body: { name?: string; script_text?: string },
): Promise<Project> {
  const res = await fetch(`/api/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function planProject(projectId: string): Promise<ProjectDetail> {
  const res = await fetch(`/api/projects/${projectId}/plan`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export interface ChatImage {
  url: string;
  caption?: string;
  shot_id?: string | null;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  images?: ChatImage[];
  created_at?: string;
  /** Model chain-of-thought (if any) */
  thinking?: string;
  /** Pipeline steps: GPU queue, tools, etc. */
  steps?: string[];
}

export async function getDirectorChatHistory(projectId: string): Promise<ChatMessage[]> {
  const res = await fetch(`/api/projects/${projectId}/chat/history`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export interface DirectorChatSessionStatus {
  active: boolean;
  session_id: string | null;
  started_at: string | null;
}

export async function getDirectorChatSession(
  projectId: string,
): Promise<DirectorChatSessionStatus> {
  const res = await fetch(`/api/projects/${projectId}/chat/session`);
  if (!res.ok) throw await directorResponseError(res);
  return res.json();
}

export async function cancelDirectorChatSession(
  projectId: string,
): Promise<DirectorChatSessionStatus> {
  const res = await fetch(`/api/projects/${projectId}/chat/session/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw await directorResponseError(res);
  return res.json();
}

export interface ChatResponse {
  reply: string;
  actions: string[];
  project: Project;
  shots: Shot[];
  images?: ChatImage[];
  thinking?: string;
  steps?: string[];
}

export interface ChatStreamHandlers {
  onStatus?: (text: string) => void;
  onRuntime?: (text: string) => void;
  onThink?: (text: string) => void;
  onToken?: (text: string) => void;
  onTool?: (text: string) => void;
}

export async function chatWithDirector(
  projectId: string,
  message: string,
  history: { role: string; content: string }[] = [],
): Promise<ChatResponse> {
  const res = await fetch(`/api/projects/${projectId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history: history.map((h) => ({ role: h.role, content: h.content })),
    }),
  });
  if (!res.ok) throw await directorResponseError(res);
  return res.json();
}

/** Stream Director chat (SSE). Live status / thinking / tokens; final result on resolve. */
export async function chatWithDirectorStream(
  projectId: string,
  message: string,
  history: { role: string; content: string }[] = [],
  handlers: ChatStreamHandlers = {},
  images: File[] = [],
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const serializedHistory = history.map((h) => ({ role: h.role, content: h.content }));
  let path = `/api/projects/${projectId}/chat/stream`;
  let body: BodyInit;
  let headers: HeadersInit;
  if (images.length) {
    path += "/images";
    const form = new FormData();
    form.append("message", message);
    form.append("history", JSON.stringify(serializedHistory));
    images.forEach((image) => form.append("images", image, image.name));
    body = form;
    headers = { Accept: "text/event-stream" };
  } else {
    body = JSON.stringify({ message, history: serializedHistory });
    headers = { "Content-Type": "application/json", Accept: "text/event-stream" };
  }
  const res = await fetch(path, { method: "POST", headers, body, signal });
  if (!res.ok) throw await directorResponseError(res);
  if (!res.body) throw new Error("No stream body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: ChatResponse | null = null;
  let streamError: Error | null = null;

  const handleEvent = (payload: string) => {
    const line = payload.trim();
    if (!line || !line.startsWith("data:")) return;
    const raw = line.slice(5).trim();
    if (!raw) return;
    let ev: {
      type?: string;
      text?: string;
      message?: string;
      code?: string;
      generation_count?: number;
      data?: ChatResponse;
    };
    try {
      ev = JSON.parse(raw);
    } catch {
      return;
    }
    const t = ev.type || "";
    if (t === "status" && ev.text) handlers.onStatus?.(ev.text);
    else if (t === "runtime" && ev.text) handlers.onRuntime?.(ev.text);
    else if (t === "think" && ev.text) handlers.onThink?.(ev.text);
    else if (t === "token" && ev.text) handlers.onToken?.(ev.text);
    else if (t === "tool" && ev.text) handlers.onTool?.(ev.text);
    else if (t === "result" && ev.data) finalResult = ev.data;
    else if (t === "error") {
      streamError = ev.code
        ? new DirectorChatError(
            ev.message || "stream error",
            ev.code,
            Number(ev.generation_count || 0),
          )
        : new Error(ev.message || "stream error");
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE events separated by blank line
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      for (const line of part.split("\n")) {
        if (line.startsWith("data:")) handleEvent(line);
      }
    }
  }
  if (buffer.trim()) {
    for (const line of buffer.split("\n")) {
      if (line.startsWith("data:")) handleEvent(line);
    }
  }

  if (streamError) throw streamError;
  if (!finalResult) {
    throw new Error("Director chat stream ended without a result");
  }
  return finalResult;
}

export async function queueRefFrame(shotId: string): Promise<Shot[]> {
  const res = await fetch(`/api/shots/${shotId}/ref-frame`, { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function queueLayout(shotId: string, brief: LayoutBrief): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/layouts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(brief),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function reviewLayout(
  shotId: string,
  layoutRefId: string,
  review: LayoutReviewRequest,
): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/layouts/${layoutRefId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(review),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function selectLayout(
  shotId: string,
  layoutRefId: string,
  selectedForH3: boolean,
): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/layouts/${layoutRefId}/selection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_for_h3: selectedForH3 }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function approveLayout(
  shotId: string,
  opts?: { rewrite_prompt?: boolean; layout_asset_id?: string },
): Promise<Shot> {
  const qs =
    opts?.rewrite_prompt === true ? "?rewrite_prompt=true" : "";
  const res = await fetch(`/api/shots/${shotId}/ref-frame/approve${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rewrite_prompt: opts?.rewrite_prompt ?? false,
      layout_asset_id: opts?.layout_asset_id,
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function rejectLayout(shotId: string, feedback: string): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}/ref-frame/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ feedback }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getShot(shotId: string): Promise<Shot> {
  const res = await fetch(`/api/shots/${shotId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export type ShotMaterialSelection = Pick<ShotRef, "role" | "asset_id" | "file_key">;

export async function replaceShotMaterials(
  shotId: string,
  materials: ShotMaterialSelection[],
  opts?: { rewritePrompt?: boolean },
): Promise<Shot> {
  const query = opts?.rewritePrompt ? "?rewrite_prompt=true" : "";
  const res = await fetch(`/api/shots/${shotId}/materials${query}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ materials }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export interface DirectorModelStatus {
  model: string;
  override?: string | null;
  persisted?: string | null;
  env_default?: string;
  source?: string;
  provider?: string;
  reachable?: boolean;
  available?: string[];
}

export async function getDirectorModel(): Promise<DirectorModelStatus> {
  const res = await fetch("/api/director/model");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function setDirectorModel(
  model: string,
  persist = true,
): Promise<DirectorModelStatus> {
  const res = await fetch("/api/director/model", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, persist }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
