import { Film } from "lucide-react";
import type { Shot } from "../../shared/api/types";

export function VideoDependencyIndicator({
  shot,
  shotNumber,
  sourceTitle,
  shots,
}: {
  shot: Shot;
  shotNumber?: number;
  sourceTitle?: string;
  shots?: Shot[];
}) {
  const context = shot.video_context;
  if (!context || context.mode === "off") return null;
  const targetIndex = shots?.findIndex((item) => item.id === shot.id) ?? -1;
  const sourceIndex = context.source_shot_id
    ? (shots?.findIndex((item) => item.id === context.source_shot_id) ?? -1)
    : targetIndex - 1;
  const title = sourceIndex >= 0 ? shots?.[sourceIndex]?.title : sourceTitle;
  const label =
    context.mode === "external_upload"
      ? "Continues uploaded video"
      : sourceIndex >= 0
        ? `Continues Shot ${sourceIndex + 1}`
        : context.source_shot_id
          ? `Continues source ${context.source_shot_id}`
          : shotNumber && shotNumber > 1
            ? `Continues Shot ${shotNumber - 1}`
            : "Continues previous shot";

  return (
    <p
      className="video-dependency-indicator flex flex-wrap items-center justify-between gap-2 rounded-md border border-border-subtle bg-elevated/80 px-2.5 py-1.5 text-xs text-ink-secondary"
      role="note"
      aria-label="Video dependency"
    >
      <span className="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-ink-muted">
        <Film className="h-3.5 w-3.5 text-accent" aria-hidden="true" />
        Video dependency
      </span>
      <strong className="font-medium text-ink">
        {label}
        {context.mode === "previous_shot" && title ? ` · ${title}` : ""}
      </strong>
    </p>
  );
}
