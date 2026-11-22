import { useState } from "react";
import { Check, Plus } from "lucide-react";
import { parseError } from "../api/client";
import type { ProjectMode } from "../api/types";
import { StudioIcon } from "../components/StudioIcon";
import { Banner } from "../ui/banner";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { useProject } from "./ProjectContext";

const PROJECT_MODES = [
  {
    id: "assistant",
    title: "AI Assistant",
    description: "You direct each shot. AI helps with descriptions and prompts.",
    image: "/mode-ai-assistant.png",
    mode: "director",
    authoring: "shot_workspace",
  },
  {
    id: "director",
    title: "Director",
    description: "Work with the agent to plan shots and develop your film.",
    image: "/director-agent-shot-board.png",
    mode: "director",
    authoring: "director_agent",
  },
  {
    id: "music",
    title: "Music Video",
    description: "Build a video around your music and its rhythm.",
    image: "/mode-music-video.png",
    mode: "mv",
    authoring: "director_agent",
    beta: true,
  },
  {
    id: "json",
    title: "JSON Production",
    description: "Bring a structured shot plan and manage production.",
    image: "/mode-json-production.png",
    mode: "json_production",
    authoring: "director_agent",
  },
] as const;

export function ProjectPicker({ allowDelete = false }: { allowDelete?: boolean }) {
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
  const [deleting, setDeleting] = useState<{ id: string; name: string } | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<ProjectMode>("director");
  const [newAuthoringMode, setNewAuthoringMode] = useState<"director_agent" | "shot_workspace">(
    "shot_workspace",
  );
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  function closeCreateForm() {
    setCreating(false);
    setNewName("");
    setNewMode("director");
    setNewAuthoringMode("shot_workspace");
    setLocalError(null);
  }

  async function onCreate() {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setLocalError(null);
    try {
      await createAndSelect(
        name,
        "",
        newMode,
        newMode === "director" ? newAuthoringMode : "director_agent",
      );
      closeCreateForm();
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!deleting || confirmation !== deleting.name || busy) return;
    setBusy(true);
    setLocalError(null);
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(deleting.id)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation }),
      });
      if (!response.ok) throw new Error(await parseError(response));
      setDeleting(null);
      setConfirmation("");
      await refreshProjects();
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="project-picker flex flex-wrap items-center gap-1.5">
        <label
          className="project-picker-label hidden text-xs font-medium text-ink-muted md:inline"
          htmlFor="global-project"
        >
          Project
        </label>
        <Select
          id="global-project"
          size="sm"
          className="project-select min-w-[140px] max-w-[200px]"
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
        </Select>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={() => {
            setLocalError(null);
            setCreating(true);
          }}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          New
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy || loading}
          onClick={() => void refreshProjects()}
          title="Refresh project list"
          aria-label="Refresh project list"
        >
          <StudioIcon name="refresh" size={14} />
          <span className="hidden lg:inline">Refresh</span>
        </Button>
        {allowDelete && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="project-delete-button text-ink-secondary hover:bg-status-danger-soft hover:text-status-danger"
            aria-label="Delete project"
            title="Permanently delete project"
            disabled={busy || loading || !project}
            onClick={() => {
              if (project) {
                setDeleting({ id: project.id, name: project.name });
                setConfirmation("");
                setLocalError(null);
              }
            }}
          >
            <StudioIcon name="delete" size={14} />
            <span className="hidden lg:inline">Delete</span>
          </Button>
        )}
        {error ? (
          <span className="project-picker-error text-xs text-status-danger">{error}</span>
        ) : null}
      </div>

      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleting(null);
        }}
      >
        <DialogContent
          role="alertdialog"
          aria-labelledby="project-delete-title"
          aria-describedby="project-delete-desc"
          hideClose
          className="project-create-modal-panel max-w-[460px]"
        >
          {deleting ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void onDelete();
              }}
            >
              <DialogHeader>
                <DialogTitle id="project-delete-title">Permanently delete project?</DialogTitle>
                <p className="mt-1 text-xs font-semibold text-ink">{deleting.name}</p>
                <DialogDescription id="project-delete-desc" className="mt-1">
                  This permanently deletes this project's shots, project assets, conversations, and
                  generated files. This cannot be undone. Shared Library assets are kept.
                </DialogDescription>
              </DialogHeader>
              <label className="mt-4 flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                <span>Type the project name to confirm</span>
                <Input
                  autoFocus
                  aria-label="Confirm project name"
                  value={confirmation}
                  disabled={busy}
                  onChange={(event) => setConfirmation(event.target.value)}
                />
              </label>
              {localError ? (
                <Banner tone="danger" role="alert" className="mt-3">
                  {localError}
                </Banner>
              ) : null}
              <DialogFooter>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => setDeleting(null)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="danger-solid"
                  className="project-delete-confirm"
                  disabled={busy || confirmation !== deleting.name}
                >
                  {busy ? "Deleting…" : "Delete permanently"}
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog
        open={creating}
        onOpenChange={(open) => {
          if (!open && !busy) closeCreateForm();
        }}
      >
        <DialogContent
          aria-labelledby="project-create-title"
          aria-describedby="project-create-desc"
          hideClose
          className="project-create-modal-panel w-[min(680px,calc(100vw-28px))]"
        >
          <form
            className="flex min-h-0 flex-col"
            onSubmit={(event) => {
              event.preventDefault();
              void onCreate();
            }}
          >
            <DialogHeader>
              <span className="text-[11px] font-medium uppercase tracking-wider text-ink-muted">
                New workspace
              </span>
              <DialogTitle id="project-create-title">Create project</DialogTitle>
              <DialogDescription id="project-create-desc" className="sr-only">
                Enter a project name and choose a creation workflow.
              </DialogDescription>
            </DialogHeader>

            {localError ? (
              <Banner tone="danger" role="alert" className="mt-3">
                {localError}
              </Banner>
            ) : null}

            <div className="project-create-fields mt-4 flex min-h-0 flex-col gap-4 overflow-y-auto pr-0.5">
              <label className="flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                <span>Project name</span>
                <Input
                  type="text"
                  placeholder="Project name"
                  value={newName}
                  disabled={busy}
                  autoFocus
                  onChange={(event) => setNewName(event.target.value)}
                />
              </label>

              <fieldset className="project-mode-picker min-w-0 border-0 p-0" disabled={busy}>
                <legend className="mb-2.5 text-xs font-semibold text-ink">
                  Choose how you want to create
                </legend>
                <div className="project-mode-grid grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {PROJECT_MODES.map((choice) => {
                    const selected =
                      newMode === choice.mode &&
                      (newMode !== "director" || newAuthoringMode === choice.authoring);
                    return (
                      <label
                        key={choice.id}
                        className={cn(
                          "project-mode-card relative flex min-w-0 cursor-pointer flex-col items-start gap-1.5 rounded-lg border p-3.5 transition-colors",
                          "focus-within:ring-2 focus-within:ring-accent",
                          selected
                            ? "is-selected border-accent bg-accent-soft"
                            : "border-border-subtle bg-elevated/40 hover:border-border-strong hover:bg-hover/50",
                        )}
                      >
                        <input
                          type="radio"
                          name="project-mode"
                          aria-label={choice.title}
                          checked={selected}
                          className="sr-only"
                          onChange={() => {
                            setNewMode(choice.mode);
                            setNewAuthoringMode(choice.authoring);
                          }}
                        />
                        <span
                          aria-hidden="true"
                          className={cn(
                            "project-mode-selection absolute right-3 top-3 grid h-5 w-5 place-items-center rounded-full border text-[11px]",
                            selected
                              ? "border-transparent bg-accent text-accent-fg"
                              : "border-border-strong text-transparent",
                          )}
                        >
                          {selected ? <Check className="h-3 w-3" /> : null}
                        </span>
                        <img
                          src={choice.image}
                          alt=""
                          width={84}
                          height={84}
                          className="pointer-events-none mb-0.5 h-[84px] w-[84px] self-center object-contain"
                        />
                        <span className="project-mode-title flex flex-wrap items-center gap-2 text-[13px] font-semibold text-ink">
                          {choice.title}
                          {"beta" in choice && choice.beta ? (
                            <Badge tone="warn" className="project-mode-beta px-1.5 py-0 text-[10px]">
                              Beta
                            </Badge>
                          ) : null}
                        </span>
                        <span className="project-mode-description text-xs leading-relaxed text-ink-secondary">
                          {choice.description}
                        </span>
                      </label>
                    );
                  })}
                </div>
                <p className="project-mode-help mt-2.5 text-[11px] leading-relaxed text-ink-muted">
                  AI Assistant and Director can be switched later. Save your edits before
                  switching.
                </p>
              </fieldset>
            </div>

            <DialogFooter>
              <Button type="button" variant="secondary" disabled={busy} onClick={closeCreateForm}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" disabled={busy || !newName.trim()}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
