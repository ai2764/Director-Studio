import type { DirectorExecutionProgress, ExecutionStage } from "./api";
import "./executionProgress.css";

const stages: [ExecutionStage, string][] = [
  ["plan", "Plan shots"], ["materials", "Prepare materials"],
  ["prompt", "Write and validate"], ["generation", "Generate and check"],
];
const labels: Record<string, string> = {
  running: "In progress", completed: "Completed", reused: "Reused",
  not_requested: "Not requested", failed: "Failed", cancelled: "Cancelled",
  interrupted: "Interrupted", unconfirmed: "Needs checking", submitted: "Submitted",
  ready: "Ready for review",
};

export function ExecutionProgress({ progress, now }: { progress: DirectorExecutionProgress; now: number }) {
  return <section className="director-execution" aria-label="Director execution progress">
    <div className="director-execution-caption">
      <strong>Production progress</strong>
      {progress.shot_index != null && <span>Shot {progress.shot_index} of {progress.shot_total}</span>}
      {["failed", "cancelled", "interrupted"].includes(progress.status) && <span>{labels[progress.status]}</span>}
    </div>
    <ol>
      {stages.map(([key, title], index) => {
        const item = progress.stages[key];
        const end = item.finished_at ? Date.parse(item.finished_at)
          : item.status === "running" ? now : Date.parse(progress.updated_at);
        const elapsed = item.started_at ? Math.max(0, Math.floor((end - Date.parse(item.started_at)) / 1000)) : null;
        return <li key={key} data-state={item.status} aria-current={progress.current_stage === key ? "step" : undefined}>
          <span className="director-execution-index" aria-hidden="true">{index + 1}</span>
          <span><strong>{title}</strong><small>{labels[item.status] || item.status}</small>
            {item.quality_status === "not_reviewed" && <small>Quality review not performed</small>}</span>
          {elapsed != null && Number.isFinite(elapsed) && <time>{elapsed}s</time>}
        </li>;
      })}
    </ol>
    <p aria-live="polite">{progress.activity}</p>
  </section>;
}
