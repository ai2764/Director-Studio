import type { Shot } from "../../shared/api/types";

export function VideoDependencyIndicator({ shot, shotNumber, sourceTitle, shots }: {
  shot: Shot; shotNumber?: number; sourceTitle?: string; shots?: Shot[];
}) {
  const context = shot.video_context;
  if (!context || context.mode === "off") return null;
  const targetIndex = shots?.findIndex((item) => item.id === shot.id) ?? -1;
  const sourceIndex = context.source_shot_id
    ? (shots?.findIndex((item) => item.id === context.source_shot_id) ?? -1)
    : targetIndex - 1;
  const title = sourceIndex >= 0 ? shots?.[sourceIndex]?.title : sourceTitle;
  const label = context.mode === "external_upload"
    ? "Continues uploaded video"
    : sourceIndex >= 0 ? `Continues Shot ${sourceIndex + 1}`
    : context.source_shot_id ? `Continues source ${context.source_shot_id}`
    : shotNumber && shotNumber > 1 ? `Continues Shot ${shotNumber - 1}` : "Continues previous shot";
  return <p className="video-dependency-indicator" role="note" aria-label="Video dependency">
    <span>Video dependency</span>
    <strong>{label}{context.mode === "previous_shot" && title ? ` · ${title}` : ""}</strong>
  </p>;
}
