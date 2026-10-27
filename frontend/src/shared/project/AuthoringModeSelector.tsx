import { useState } from "react";
import { updateProject } from "../../features/director/api";
import { useProject } from "./ProjectContext";

export function AuthoringModeSelector() {
  const { project, refreshProjects } = useProject();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!project || project.mode !== "director") return null;
  return <div className="authoring-mode-control">
    <label>Workflow<select aria-label="Authoring mode" value={project.authoring_mode || "director_agent"} disabled={busy}
      title="Save your edits before switching. Your shots and assets are kept."
      onChange={(event) => {
        const mode = event.target.value as "director_agent" | "shot_workspace";
        setBusy(true); setError("");
        void updateProject(project.id, { authoring_mode: mode, expected_authoring_mode: project.authoring_mode || "director_agent" })
          .then(() => refreshProjects()).catch((cause) => setError(String(cause))).finally(() => setBusy(false));
      }}><option value="shot_workspace">Shot Workspace</option><option value="director_agent">Director Agent</option></select></label>
    {busy && <small role="status">Switching…</small>}
    {error && <small role="alert">{error}</small>}
  </div>;
}
