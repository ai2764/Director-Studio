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
        {shots.map((shot) => {
          const selected = shot.id === selectedId;
          return (
            <button
              key={shot.id}
              type="button"
              className={selected ? "json-shot-item selected" : "json-shot-item"}
              aria-selected={selected}
              onClick={() => onSelect(shot.id)}
            >
              <div className="shot-table-title">{shot.title || shot.id}</div>
              <div className="muted tiny">
                {shot.id} · {shot.duration_s}s
              </div>
              <span className="status-chip">{statusByShotId.get(shot.id) || "idle"}</span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
