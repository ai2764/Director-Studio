import { useState } from "react";
import { parseError } from "../api/client";
import { StudioIcon } from "../components/StudioIcon";
import { createPortal } from "react-dom";
import "./projectModes.css";
import type { ProjectMode } from "../api/types";
import { useProject } from "./ProjectContext";

const PROJECT_MODES = [
  {id:"assistant",title:"AI Assistant",description:"You direct each shot. AI helps with descriptions and prompts.",image:"/mode-ai-assistant.png",mode:"director",authoring:"shot_workspace"},
  {id:"director",title:"Director",description:"Work with the agent to plan shots and develop your film.",image:"/director-agent-shot-board.png",mode:"director",authoring:"director_agent"},
  {id:"music",title:"Music Video",description:"Build a video around your music and its rhythm.",image:"/mode-music-video.png",mode:"mv",authoring:"director_agent",beta:true},
  {id:"json",title:"JSON Production",description:"Bring a structured shot plan and manage production.",image:"/mode-json-production.png",mode:"json_production",authoring:"director_agent"},
] as const;

export function ProjectPicker({allowDelete = false}: {allowDelete?: boolean}) {
  const {
    projects,
    projectId,
    project,
    loading,
    error,
    setProjectId,
    createAndSelect,
    refreshProjects,
  } = useProject();
  const [deleting, setDeleting] = useState<{id:string;name:string} | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<ProjectMode>("director");
  const [newAuthoringMode, setNewAuthoringMode] = useState<"director_agent" | "shot_workspace">("shot_workspace");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  function closeCreateForm() {
    setCreating(false);
    setNewName("");
    setNewMode("director"); setNewAuthoringMode("shot_workspace");
    setLocalError(null);
  }

  async function onCreate() {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setLocalError(null);
    try {
      await createAndSelect(name, "", newMode, newMode === "director" ? newAuthoringMode : "director_agent");
      closeCreateForm();
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!deleting || confirmation !== deleting.name || busy) return;
    setBusy(true); setLocalError(null);
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(deleting.id)}`, {
        method:"DELETE", headers:{"Content-Type":"application/json"}, body:JSON.stringify({confirmation}),
      });
      if (!response.ok) throw new Error(await parseError(response));
      setDeleting(null); setConfirmation("");
      await refreshProjects();
    } catch (cause) {setLocalError(cause instanceof Error ? cause.message : String(cause));}
    finally {setBusy(false);}
  }
  return (
    <>
    <div className="project-picker">
      <label className="project-picker-label" htmlFor="global-project">
        Project
      </label>
      <select
        id="global-project"
        className="project-select"
        disabled={loading || busy}
        value={projectId || ""}
        onChange={(e) => setProjectId(e.target.value || null)}
        title={project ? `${project.name} (${project.id})` : "Select project"}
      >
        {projects.length === 0 ? (
          <option value="">No projects</option>
        ) : (
          projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))
        )}
      </select>
      <button
        type="button"
        className="btn secondary btn-sm"
        disabled={busy}
        onClick={() => {
          setLocalError(null);
          setCreating(true);
        }}
      >
        New
      </button>
      <button
        type="button"
        className="btn secondary btn-sm"
        disabled={busy || loading}
        onClick={() => void refreshProjects()}
        title="Refresh project list"
        aria-label="Refresh project list"
      >
        <StudioIcon name="refresh" size={16} /> Refresh
      </button>
      {allowDelete && <button type="button" className="btn secondary btn-sm project-delete-button" aria-label="Delete project"
        title="Permanently delete project" disabled={busy || loading || !project}
        onClick={() => {if(project) {setDeleting({id:project.id,name:project.name});setConfirmation("");setLocalError(null);}}}>
        <StudioIcon name="delete" size={16} /> Delete
      </button>}
      {error ? <span className="project-picker-error">{error}</span> : null}
    </div>
    {deleting && createPortal(<div className="folder-modal project-create-modal" role="alertdialog" aria-modal="true" aria-labelledby="project-delete-title"
      onClick={() => {if(!busy)setDeleting(null);}} onKeyDown={event => {if(event.key === "Escape" && !busy)setDeleting(null);}}>
      <form className="folder-modal-panel project-create-modal-panel" onClick={event=>event.stopPropagation()} onSubmit={event=>{event.preventDefault();void onDelete();}}>
        <h2 id="project-delete-title">Permanently delete project?</h2>
        <p><strong>{deleting.name}</strong></p>
        <p>This permanently deletes this project's shots, project assets, conversations, and generated files. This cannot be undone. Shared Library assets are kept.</p>
        <label className="field">Type the project name to confirm<input autoFocus aria-label="Confirm project name" value={confirmation} disabled={busy} onChange={event=>setConfirmation(event.target.value)}/></label>
        {localError && <p className="banner error" role="alert">{localError}</p>}
        <div className="project-create-actions"><button type="button" className="btn secondary" disabled={busy} onClick={()=>setDeleting(null)}>Cancel</button>
          <button type="submit" className="btn project-delete-confirm" disabled={busy || confirmation !== deleting.name}>{busy ? "Deleting…" : "Delete permanently"}</button></div>
      </form></div>, document.body)}
    {creating ? createPortal(
      <div
        className="folder-modal project-create-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-create-title"
        onClick={() => !busy && closeCreateForm()}
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) closeCreateForm();
        }}
      >
        <form
          className="folder-modal-panel project-create-modal-panel"
          onClick={(event) => event.stopPropagation()}
          onSubmit={(event) => {
            event.preventDefault();
            void onCreate();
          }}
        >
          <div className="folder-modal-head">
            <div>
              <span className="mobile-eyebrow">New workspace</span>
              <h2 className="folder-modal-title" id="project-create-title">Create project</h2>
            </div>
          </div>
          {localError ? <div className="banner error">{localError}</div> : null}
          <div className="project-create-fields">
            <label className="field">
              <span>Project name</span>
              <input
                type="text"
                placeholder="Project name"
                value={newName}
                disabled={busy}
                autoFocus
                onChange={(event) => setNewName(event.target.value)}
              />
            </label>
            <fieldset className="project-mode-picker" disabled={busy}>
              <legend>Choose how you want to create</legend>
              <div className="project-mode-grid">
                {PROJECT_MODES.map((choice) => {
                  const selected = newMode === choice.mode && (newMode !== "director" || newAuthoringMode === choice.authoring);
                  return <label key={choice.id} className={`project-mode-card${selected ? " is-selected" : ""}`}>
                    <input type="radio" name="project-mode" aria-label={choice.title} checked={selected}
                      onChange={() => { setNewMode(choice.mode); setNewAuthoringMode(choice.authoring); }} />
                    <span className="project-mode-selection" aria-hidden="true">{selected ? "✓" : ""}</span>
                    <img src={choice.image} alt="" width={112} height={112} />
                    <span className="project-mode-title">{choice.title}{"beta" in choice && choice.beta && <span className="project-mode-beta">Beta</span>}</span>
                    <span className="project-mode-description">{choice.description}</span>
                  </label>;
                })}
              </div>
              <p className="project-mode-help">AI Assistant and Director can be switched later. Save your edits before switching.</p>
            </fieldset>
          </div>
          <div className="project-create-actions">
            <button type="button" className="btn secondary" disabled={busy} onClick={closeCreateForm}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={busy || !newName.trim()}>
              Create
            </button>
          </div>
        </form>
      </div>,
      document.body,
    ) : null}
    </>
  );
}
