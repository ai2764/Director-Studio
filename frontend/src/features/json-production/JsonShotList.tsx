import type { JsonProductionShot } from "./types";

type Props = {
  shots: JsonProductionShot[];
  selectedId: string | null;
  statusByShotId: Map<string, string>;
  onSelect: (id: string) => void;
};

export function JsonShotList({ shots, selectedId, statusByShotId, onSelect }: Props) {
  return (
    <aside className="section-card compact-card json-shot-panel" aria-label="Shot timeline">
      <div className="section-card-head">
        <h2 className="section-card-title">Shots</h2>
        <span className="muted tiny">{shots.length}</span>
      </div>
      <div className="json-shot-list">
        {shots.map((shot, index) => {
          const selected = shot.id === selectedId;
          const status = statusByShotId.get(shot.id) || "idle";
          return (
            <button
              key={shot.id}
              type="button"
              className={
                selected
                  ? "json-shot-item json-shot-bookmark selected"
                  : "json-shot-item json-shot-bookmark"
              }
              aria-selected={selected}
              aria-label={`${shot.id}: ${shot.title || shot.id}, ${shot.duration_s}s, ${status}`}
              onClick={() => onSelect(shot.id)}
            >
              <span className="json-shot-bookmark-index" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="json-shot-bookmark-copy">
                <span className="shot-table-title">{shot.title || shot.id}</span>
                <span className="json-shot-bookmark-meta">
                  <span>{shot.duration_s}s</span>
                  <span className="json-shot-bookmark-status" data-status={status}>
                    {status}
                  </span>
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
