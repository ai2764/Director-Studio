import type { JobStatus, OutputSlot, PromptSections } from "../../shared/api/types";

export type { PromptSections };

export type JsonPictureRole =
  | "actor"
  | "costume"
  | "scene"
  | "prop"
  | "layout"
  | "other";

export interface JsonProductionPicture {
  index: number;
  role: JsonPictureRole;
  label: string;
}

export interface JsonProductionAudio {
  index: number;
  label: string;
}

export interface JsonProductionShot {
  id: string;
  title: string;
  script_beat: string;
  duration_s: number;
  dialogue: string[];
  pictures: JsonProductionPicture[];
  audio: JsonProductionAudio[];
  prompt: PromptSections;
}

export interface JsonProductionDocument {
  version: 1;
  revision: number;
  aspect_ratio: "16:9" | "9:16";
  shots: JsonProductionShot[];
}

export type JsonProductionAssetKind = "picture" | "audio";

export interface JsonProductionStoredAsset {
  shot_id: string;
  kind: JsonProductionAssetKind;
  index: number;
  filename: string;
  content_type: string;
  size_bytes: number;
  url: string;
  slot_signature: string;
}

export type JsonProductionAssetValue = File | JsonProductionStoredAsset;

export interface ShotFileMaps {
  pictures: Map<number, JsonProductionAssetValue>;
  audio: Map<number, JsonProductionAssetValue>;
}

/** H3 job row as returned for JSON Production polling. */
export interface JsonShotJobRecord {
  id: string;
  status: JobStatus;
  name: string;
  notes: string;
  prompt: string;
  dialogue: string[];
  frames: number | null;
  width?: number | null;
  height?: number | null;
  image_keys?: string[];
  seed?: number | null;
  fixed_seed?: boolean;
  error: string | null;
  comfy_prompt_id: string | null;
  external_task_id: string | null;
  created_at: string;
  updated_at: string;
  outputs: Record<string, OutputSlot>;
  input_previews: Record<string, string>;
  pipeline_id: string;
  project_id: string | null;
  json_shot_id: string | null;
  json_storyboard_revision: number | null;
}
