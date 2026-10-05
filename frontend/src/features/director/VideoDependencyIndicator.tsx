import type { Shot } from "../../shared/api/types";

export function VideoDependencyIndicator({ shot, shotNumber, sourceTitle }: {
  shot: Shot; shotNumber?: number; sourceTitle?: string;
}) {
  const context = shot.video_context;
  if (!context || context.mode === "off") return null;
  const label = context.mode === "external_upload"
    ? "Continues uploaded video"
    : shotNumber && shotNumber > 1 ? `Continues Shot ${shotNumber - 1}` : "Continues previous shot";
  return <p className="video-dependency-indicator" role="note" aria-label="Video dependency">
    <span>Video dependency</span>
    <strong>{label}{sourceTitle ? ` · ${sourceTitle}` : ""}</strong>
  </p>;
}
