import { useId, useState } from "react";
import { updateProject } from "../../features/director/api";
import { Select } from "../ui/select";
import { useProject } from "./ProjectContext";

export function AuthoringModeSelector() {
  const { project, refreshProjects } = useProject();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectId = useId();

  if (!project || project.mode !== "director") return null;

  return (
    <div className="authoring-mode-control flex flex-col gap-1">
      <label
        htmlFor={selectId}
        className="flex items-center gap-1.5 text-xs font-medium text-ink-secondary"
      >
        <span>Workflow</span>
        <Select
          id={selectId}
          size="sm"
          aria-label="Authoring mode"
          value={project.authoring_mode || "director_agent"}
          disabled={busy}
          title="Save your edits before switching. Your shots and assets are kept."
          onChange={(event) => {
            const mode = event.target.value as "director_agent" | "shot_workspace";
            setBusy(true);
            setError("");
            void updateProject(project.id, {
              authoring_mode: mode,
              expected_authoring_mode: project.authoring_mode || "director_agent",
            })
              .then(() => refreshProjects())
              .catch((cause) => setError(String(cause)))
              .finally(() => setBusy(false));
          }}
        >
          <option value="shot_workspace">AI Assistant</option>
          <option value="director_agent">Director</option>
        </Select>
      </label>
      {busy && (
        <small role="status" className="text-[11px] text-ink-muted">
          Switching…
        </small>
      )}
      {error && (
        <small role="alert" className="text-[11px] text-status-danger">
          {error}
        </small>
      )}
    </div>
  );
}
