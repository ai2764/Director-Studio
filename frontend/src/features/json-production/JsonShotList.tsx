import { Panel, PanelBody, PanelHeader, PanelTitle, StatusDot, cn, type StatusDotProps } from "../../shared/ui";
import type { JsonProductionShot } from "./types";

type Props = {
  shots: JsonProductionShot[];
  selectedId: string | null;
  statusByShotId: Map<string, string>;
  onSelect: (id: string) => void;
};

function shotStatusTone(status: string): NonNullable<StatusDotProps["tone"]> {
  switch (status) {
    case "running":
    case "uploading":
      return "info";
    case "queued":
      return "accent";
    case "succeeded":
    case "ready":
      return "ok";
    case "unsaved prompt":
    case "missing files":
      return "warn";
    case "failed":
    case "cancelled":
      return "danger";
    default:
      return "neutral";
  }
}

export function JsonShotList({ shots, selectedId, statusByShotId, onSelect }: Props) {
  return (
    <aside className="json-shot-panel min-w-0" aria-label="Shot timeline">
      <Panel className="h-full overflow-hidden">
        <PanelHeader className="px-3.5 py-2.5">
          <PanelTitle>Shots</PanelTitle>
          <span className="font-mono text-[11px] text-ink-muted tabular-nums">{shots.length}</span>
        </PanelHeader>
        <PanelBody className="json-shot-list flex flex-col gap-1 p-2">
          {shots.map((shot, index) => {
            const selected = shot.id === selectedId;
            const status = statusByShotId.get(shot.id) || "idle";
            const tone = shotStatusTone(status);
            const active = status === "running" || status === "uploading";
            return (
              <button
                key={shot.id}
                type="button"
                className={cn(
                  "json-shot-item json-shot-bookmark flex w-full items-start gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors cursor-pointer",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                  selected
                    ? "selected border-accent/40 bg-accent-soft/60 text-ink"
                    : "border-transparent bg-transparent text-ink-secondary hover:bg-hover hover:text-ink",
                )}
                aria-selected={selected}
                aria-label={`${shot.id}: ${shot.title || shot.id}, ${shot.duration_s}s, ${status}`}
                onClick={() => onSelect(shot.id)}
              >
                <span
                  className="json-shot-bookmark-index mt-0.5 font-mono text-[11px] text-ink-muted tabular-nums"
                  aria-hidden="true"
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="json-shot-bookmark-copy flex min-w-0 flex-1 flex-col gap-1">
                  <span className="shot-table-title truncate text-xs font-medium text-ink">
                    {shot.title || shot.id}
                  </span>
                  <span className="json-shot-bookmark-meta flex items-center justify-between gap-2 text-[10px] text-ink-muted">
                    <span className="font-mono tabular-nums">{shot.duration_s}s</span>
                    <span
                      className="json-shot-bookmark-status inline-flex items-center gap-1 font-medium text-ink-secondary"
                      data-status={status}
                    >
                      <StatusDot tone={tone} pulse={active} />
                      {status}
                    </span>
                  </span>
                </span>
              </button>
            );
          })}
        </PanelBody>
      </Panel>
    </aside>
  );
}
