export interface SongSegment {
  id: string;
  start_s: number;
  end_s: number;
  text: string;
}

export interface SegmentDraft {
  id: string;
  start_s: number | null;
  end_s: number | null;
  text: string;
}

export interface SegmentPreview {
  rows: SegmentDraft[];
  unresolved: string[];
}

export interface SongSegmentsDocument {
  revision: number;
  master_sha256: string;
  /** Legacy servers may return this; canonical storage keeps only timed rows. */
  raw_input?: string;
  segments: SongSegment[];
}

export interface SongSegmentsState {
  document: SongSegmentsDocument | null;
  master_stale: boolean;
}

export interface SegmentSelection {
  revision: number;
  ids: string[];
}

const endpoint = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/song-segments`;

async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* retain status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function getSongSegments(projectId: string): Promise<SongSegmentsState> {
  return fetch(endpoint(projectId)).then(readResponse<SongSegmentsState>);
}

export function previewSongSegments(projectId: string, rawInput: string): Promise<SegmentPreview> {
  return fetch(`${endpoint(projectId)}/preview`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_input: rawInput }),
  }).then(readResponse<SegmentPreview>);
}

export function saveSongSegments(
  projectId: string, expectedRevision: number, rawInput: string, segments: SongSegment[],
): Promise<SongSegmentsDocument> {
  return fetch(endpoint(projectId), {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: expectedRevision, raw_input: rawInput, segments }),
  }).then(readResponse<SongSegmentsDocument>);
}

export function songAudioUrl(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/music-master/audio`;
}
