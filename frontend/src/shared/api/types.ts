/** Shared job / media types used across features. */

export interface H3ActiveProfile {
  profile_id: string;
  display_name: string;
  source: "builtin" | "custom";
  workflow_sha256: string;
  contract_version: number;
  validated_at?: string | null;
  warning: { code: string; message: string; details?: Record<string, unknown> } | null;
}

export interface H3Profiles {
  active: H3ActiveProfile;
  profiles: { profile_id: string; display_name: string; source: "builtin" | "custom"; workflow_sha256: string; status: "active" | "available" | "tested" }[];
}

export type JobStatus =
  | "queued"
  | "uploading"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface OutputSlot {
  key: string;
  label: string;
  path?: string | null;
  filename?: string | null;
  url?: string | null;
}

export interface PipelineInfo {
  id: string;
  asset_kind: string;
  display_name: string;
  description: string;
  enabled: boolean;
}

/** Director / Production project-shot models (match backend projects API). */

export type ShotStatus =
  | "draft"
  | "planning"
  | "ref_frame_pending"
  | "needs_review"
  | "approved"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "blocked";

export type RefRole =
  | "layout_ref_frame"
  | "actor"
  | "costume"
  | "scene"
  | "prop"
  | "other";

export type LayoutReviewStatus =
  | "pending_review"
  | "usable"
  | "usable_with_repair"
  | "reject";

export interface LayoutSourceRef {
  role: RefRole;
  asset_id: string;
  file_key?: string | null;
  notes?: string;
}

export interface ClipTailFrameOrigin {
  kind: "clip_tail_frame";
  source_shot_id: string;
  source_job_id: string;
  source_generation: number;
  output_kind: "enhanced" | "raw";
  output_key: "video" | "video_raw";
  source_filename: string;
  source_duration_s?: number | null;
  extracted_timestamp_s?: number | null;
}

export interface LayoutReference {
  id: string;
  provider?: "comfy" | "gpt";
  asset_id: string | null;
  job_id: string | null;
  job_status?: JobStatus | null;
  job_error?: string;
  purpose: string;
  state_description: string;
  time_hint: string;
  source_refs: LayoutSourceRef[];
  review_status: LayoutReviewStatus | null;
  review_feedback: string;
  feedback_source?: string;
  feedback_quote?: string;
  revision_of?: string | null;
  superseded_by?: string | null;
  selected_for_h3: boolean;
  activation_mode?: "replace" | "append";
  created_at: string;
  origin?: ClipTailFrameOrigin | null;
}

export interface ShotRef {
  role: RefRole;
  asset_id: string;
  picture_index: number;
  notes?: string;
  file_key?: string | null;
}

export interface ShotVoiceRef {
  asset_id: string;
  audio_index: number;
  file_key: string;
  speaker: string;
  notes?: string;
}

export interface PromptSections {
  subject_definitions: string;
  summary: string;
  retention_analysis: string;
  detailed_description: string;
  overall_soundscape: string;
  non_diegetic_music: string;
}

export interface Shot {
  id: string;
  project_id: string;
  scene_id: string;
  title: string;
  script_beat: string;
  shot_type?: string;
  camera_angle?: string;
  camera_motion?: string;
  composition?: string;
  duration_s: number;
  status: ShotStatus;
  refs: ShotRef[];
  voice_refs: ShotVoiceRef[];
  prompt_sections: PromptSections;
  dialogue: string[];
  layout_asset_id: string | null;
  layout_review_status: LayoutReviewStatus | string | null;
  ref_frame_job_id: string | null;
  layout_refs: LayoutReference[];
  h3_job_id: string | null;
  source_audio_path: string | null;
  feedback: string;
  blocked_reasons: string[];
  meta?: Record<string, unknown>;
}

export type ProjectMode = "director" | "json_production";

export interface AssetCoverageRecommendation {
  kind: "actor" | "scene" | "prop" | "costume" | "layout" | "other";
  asset_id?: string | null;
  needed_variant: string;
  reason: string;
  shot_ids: string[];
  priority: "low" | "medium" | "high";
  resolution: "pending" | "accepted" | "dismissed" | "generated";
}

export interface AssetCoverageReview {
  script_hash: string;
  status: "reviewed" | "skipped";
  recommendations: AssetCoverageRecommendation[];
  notes: string;
}

export interface Project {
  id: string;
  name: string;
  script_text: string;
  mode: ProjectMode;
  created_at: string;
  updated_at: string;
  shot_ids: string[];
  asset_coverage_review?: AssetCoverageReview | null;
}

export interface ProjectDetail {
  project: Project;
  shots: Shot[];
}

/** Prefer layout.png / master.png under library for thumbnails. */
export function libraryFileUrl(kind: string, assetId: string, filename: string): string {
  return `/api/files/library/${kind}/${assetId}/${filename}`;
}

export function roleToLibraryKind(role: RefRole): string | null {
  switch (role) {
    case "layout_ref_frame":
      return "layouts";
    case "actor":
      return "actors";
    case "costume":
      return "costumes";
    case "scene":
      return "scenes";
    case "prop":
      return "props";
    default:
      return null;
  }
}

/**
 * Best-effort preview URL for a shot ref.
 * Prefers agent-selected `file_key` then role defaults.
 * Note: external props are often `master.jpg` — prefer candidates + onError for reliability.
 */
export function refPreviewUrl(ref: ShotRef): string | null {
  const candidates = refPreviewCandidates(ref);
  return candidates[0] || null;
}

/** Ordered candidate preview URLs (for <img onError> fallback). */
export function refPreviewCandidates(ref: ShotRef): string[] {
  const kind = roleToLibraryKind(ref.role);
  if (!kind) return [];
  const out: string[] = [];
  const push = (filename: string) => {
    const u = libraryFileUrl(kind, ref.asset_id, filename);
    if (!out.includes(u)) out.push(u);
  };
  const key = (ref.file_key || "").trim();
  if (key) {
    if (/\.(png|jpe?g|webp|gif)$/i.test(key)) push(key);
    else if (ref.role === "prop") {
      // External imports / product stills are usually JPEG
      push(`${key}.jpg`);
      push(`${key}.jpeg`);
      push(`${key}.png`);
      push(`${key}.webp`);
    } else {
      push(`${key}.png`);
      push(`${key}.jpg`);
      push(`${key}.jpeg`);
      push(`${key}.webp`);
    }
  }
  if (ref.role === "layout_ref_frame") {
    push("layout.png");
    push("master.png");
  } else if (ref.role === "actor") {
    push("fullbody_threeview.png");
    push("bust_threeview.png");
    push("master.png");
    push("asset_sheet.png");
  } else if (ref.role === "scene") {
    push("master.png");
    push("plate.png");
    push("image.png");
    // Multi-angle plates may be the only files
  } else if (ref.role === "prop") {
    push("master.jpg");
    push("master.jpeg");
    push("master.png");
    push("master.webp");
    push("image.jpg");
    push("image.png");
  } else {
    push("master.png");
    push("master.jpg");
    push("image.png");
  }
  return out;
}

export function layoutPreviewUrl(layoutAssetId: string | null | undefined): string | null {
  if (!layoutAssetId) return null;
  return libraryFileUrl("layouts", layoutAssetId, "layout.png");
}

export const EMPTY_PROMPT_SECTIONS: PromptSections = {
  subject_definitions: "",
  summary: "",
  retention_analysis: "",
  detailed_description: "",
  overall_soundscape: "",
  non_diegetic_music: "",
};

export const PROMPT_SECTION_KEYS: { key: keyof PromptSections; label: string }[] = [
  { key: "subject_definitions", label: "Subject definitions" },
  { key: "summary", label: "Summary" },
  { key: "retention_analysis", label: "Retention analysis" },
  { key: "detailed_description", label: "Detailed description" },
  { key: "overall_soundscape", label: "Overall soundscape" },
  { key: "non_diegetic_music", label: "Non-diegetic music" },
];
