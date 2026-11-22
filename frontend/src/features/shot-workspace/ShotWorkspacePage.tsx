import { useEffect, useRef, useState } from "react";
import { ChevronRight, Clapperboard, Plus } from "lucide-react";
import { useProject } from "../../shared/project/ProjectContext";
import { PROMPT_SECTION_KEYS, refPreviewCandidates, type Shot, type ShotRef } from "../../shared/api/types";
import { cancelDirectorChatSession, chatWithDirectorStream, getDirectorChatSession, getProject, type AgentProgress } from "./api";
import { ShotMaterialEditor } from "./WorkspaceReferences";
import { ManualContinuity } from "./ManualContinuity";
import { ManualShotOutput } from "./ManualShotOutput";
import { formatDialogue } from "./api";
import { DescriptionAssistant } from "./DescriptionAssistant";
import { VoiceReferences } from "./VoiceReferences";
import { WorkspaceMascot } from "./WorkspaceMascot";
import { StudioIcon } from "../../shared/components/StudioIcon";
import { WorkspaceModelPicker } from "./WorkspaceModelPicker";
import { TimingNote } from "./OperationTimings";
import { json, useShotEditorController } from "./useShotEditorController";
import { Badge, Banner, Button, EmptyState, Input, Textarea, cn } from "../../shared/ui";

function ReferencePreview({ item }: { item: ShotRef }) {
  const candidates = refPreviewCandidates(item);
  const [index, setIndex] = useState(0);
  const url = candidates[index];
  return url ? (
    <a
      className="flex h-20 w-20 items-center justify-center overflow-hidden rounded-lg border border-border-subtle bg-canvas text-[11px] text-ink-muted transition-colors hover:border-border-strong"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`View reference ${item.picture_index}`}
    >
      <img
        src={url}
        alt={`Picture ${item.picture_index}`}
        className="block h-full w-full object-contain"
        onError={() => setIndex((current) => current + 1)}
      />
    </a>
  ) : (
    <span className="flex h-20 w-20 items-center justify-center rounded-lg border border-border-subtle bg-canvas p-1 text-center text-[11px] text-ink-muted">
      Preview unavailable
    </span>
  );
}

export function ShotEditor({
  shot,
  index,
  shots,
  busy,
  onSaved,
  onWrite,
  onDelete,
  desktopOutput,
  writing,
  writeElapsed,
  onCancel,
  onAssistingChange,
}: {
  shot: Shot;
  index: number;
  shots: Shot[];
  busy: boolean;
  onSaved: (shot: Shot) => void;
  onWrite: (shot: Shot, revisionRequest?: string) => Promise<Shot | void>;
  onDelete: (shot: Shot) => Promise<void>;
  desktopOutput: boolean;
  writing: boolean;
  writeElapsed: number;
  onCancel: () => Promise<void>;
  onAssistingChange?: (busy: boolean) => void;
}) {
  const ctrl = useShotEditorController({
    shot,
    busy,
    onSaved,
    onWrite,
    onDelete,
    writing,
    onCancel,
    onAssistingChange,
  });

  const promptBadgeTone = ctrl.promptWriting
    ? "border-accent/30 bg-accent-soft text-accent"
    : ctrl.effectiveCheck?.stale || ctrl.checkError
      ? "border-status-warn/30 bg-status-warn-soft text-status-warn"
      : ctrl.effectiveCheck?.valid
        ? "border-status-ok/30 bg-status-ok-soft text-status-ok"
        : ctrl.hasPrompt && !ctrl.checkPending
          ? "border-status-warn/30 bg-status-warn-soft text-status-warn"
          : "border-border-subtle bg-elevated text-ink-secondary";

  return (
    <div
      className={cn(
        "min-w-0",
        desktopOutput
          ? "grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]"
          : "flex flex-col gap-4",
      )}
    >
      <article className="flex min-w-0 flex-col gap-4 rounded-xl border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)] sm:p-5">
        <div className="flex items-center justify-between gap-2 border-b border-border-subtle pb-3">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <Badge tone="neutral" className="shrink-0 font-mono">
              #{index + 1}
            </Badge>
            <Input
              aria-label="Shot title"
              value={ctrl.title}
              disabled={ctrl.disabled}
              onChange={(event) => ctrl.setTitle(event.target.value)}
              className="h-9 flex-1 border-transparent bg-transparent px-2 text-base font-semibold tracking-[-0.015em] hover:border-border-subtle focus-visible:bg-panel"
            />
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="shrink-0 text-ink-muted hover:text-status-danger"
            aria-label="Delete shot"
            title="Delete shot (undo available)"
            disabled={ctrl.disabled}
            onClick={ctrl.deleteCurrentShot}
          >
            <StudioIcon name="delete" size={16} />
          </Button>
        </div>

        <div className="flex flex-col gap-2 empty:hidden" aria-live="polite">
          {ctrl.working && (
            <p
              role="status"
              className="m-0 flex items-center gap-2 rounded-md border border-accent/25 bg-accent-soft px-3 py-1.5 font-mono text-xs text-accent"
            >
              {ctrl.working} · {ctrl.elapsed}s
            </p>
          )}
          {ctrl.message && (
            <p
              role="status"
              className="m-0 whitespace-pre-wrap rounded-md border border-border-subtle bg-elevated px-3 py-2 text-xs text-ink-secondary"
            >
              {ctrl.message}
            </p>
          )}
          {ctrl.error && (
            <Banner tone="danger" className="whitespace-pre-wrap break-words">
              {ctrl.error}
            </Banner>
          )}
        </div>

        <div
          className={cn(
            "flex min-w-0 flex-col gap-2.5 rounded-xl border bg-elevated/35 p-3.5 transition-[border-color,box-shadow]",
            ctrl.autoStage === "brief"
              ? "border-accent ring-2 ring-accent/25"
              : "border-border-subtle focus-within:border-border-strong",
          )}
          data-workflow-stage="brief"
          data-active={ctrl.autoStage === "brief" || undefined}
        >
          <div className="flex items-center justify-between gap-2">
            <label
              htmlFor={`description-${shot.id}`}
              className="text-[13px] font-semibold tracking-[-0.01em] text-ink"
            >
              Shot brief
            </label>
            <Button
              type="button"
              variant={ctrl.working === "Thinking about shot" ? "danger" : "ghost"}
              size="icon"
              aria-label="Refine"
              title="Refine shot brief"
              disabled={ctrl.disabled || !ctrl.version || !ctrl.description.trim()}
              onClick={() => {
                void ctrl
                  .assistDescription(
                    "Refine the current shot description. Preserve its intended action and explicit dialogue, clarify the framing and camera movement, and fill the camera fields. Return an updated description draft, not a discussion or prompt.",
                    [],
                  )
                  .catch((cause) => ctrl.setError(cause instanceof Error ? cause.message : String(cause)));
              }}
            >
              <StudioIcon name="wand" size={16} />
            </Button>
          </div>

          <Textarea
            id={`description-${shot.id}`}
            rows={6}
            value={ctrl.description}
            disabled={ctrl.disabled}
            placeholder="Start with a rough idea…"
            className="min-h-[140px] bg-panel text-[13px] leading-relaxed"
            onChange={(event) => {
              ctrl.setDescription(event.target.value);
              ctrl.setOriginal(event.target.value);
              ctrl.setMessage("");
              ctrl.setCamera({ shot_type: "", camera_angle: "", camera_motion: "", composition: "" });
            }}
          />

          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={ctrl.disabled || !ctrl.version}
              onClick={() => void ctrl.act("save")}
            >
              Save
            </Button>
            <div className="flex min-w-0 flex-wrap items-center justify-center gap-1.5">
              <TimingNote entry={ctrl.latestTiming("refinement")} />
              <TimingNote entry={ctrl.latestTiming("assistant")} />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="Clear shot brief"
              disabled={ctrl.disabled || !ctrl.description}
              onClick={ctrl.clearBrief}
            >
              Clear
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-2.5 border-t border-border-subtle pt-2.5">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              aria-label="Auto run"
              aria-describedby={`auto-run-hint-${shot.id}`}
              aria-busy={ctrl.autoStage !== null}
              title={
                !ctrl.description.trim()
                  ? "Add a rough idea to get started."
                  : "Refine the brief, choose references, write the prompt, then generate video"
              }
              disabled={
                ctrl.disabled ||
                !ctrl.version ||
                !ctrl.description.trim() ||
                ["queued", "running"].includes(shot.status)
              }
              onClick={() => void ctrl.runFullShot()}
            >
              <StudioIcon name="play" size={13} />
              <span>{ctrl.autoStage !== null ? `${ctrl.working}…` : "Auto run"}</span>
            </Button>
            <small id={`auto-run-hint-${shot.id}`} className="min-w-0 text-[11px] text-ink-muted">
              {ctrl.description.trim() ? "Refine → References → Prompt → Video" : "Add a rough idea to get started."}
            </small>
          </div>
        </div>

        <small className="hidden text-[11px] leading-relaxed text-ink-muted md:block">
          Use Description assistant to create or revise this brief, or edit it directly. Save the brief before writing
          its prompt.
        </small>

        <details className="rounded-lg border border-border-subtle bg-elevated/40 px-3 py-2.5 text-xs">
          <summary className="flex cursor-pointer list-none items-center gap-2 font-medium text-ink select-none [&::-webkit-details-marker]:hidden">
            <ChevronRight className="h-3.5 w-3.5 text-ink-muted" aria-hidden="true" />
            <span>Duration and dialogue</span>
          </summary>
          <div className="mt-3 grid gap-2.5 border-t border-border-subtle pt-3">
            <small className="text-[11px] leading-relaxed text-ink-muted">
              The assistant can suggest a duration to fit the action and dialogue. You can edit it here.
            </small>
            <small className="text-[11px] leading-relaxed text-ink-muted">
              Use one cue per line: Guide [English]: Welcome. Include each speaker and language; Description assistant
              can extract cues from the brief.
            </small>
            <label className="grid gap-1 text-xs font-medium text-ink-secondary">
              <span>Duration (seconds)</span>
              <Input
                type="number"
                min={1}
                max={120}
                value={ctrl.duration}
                disabled={ctrl.disabled}
                className="w-28 font-mono"
                onChange={(event) => ctrl.setDuration(Number(event.target.value))}
              />
            </label>
            <label className="grid gap-1 text-xs font-medium text-ink-secondary">
              <span>Dialogue (optional, one cue per line)</span>
              <Textarea
                rows={2}
                value={ctrl.dialogue}
                disabled={ctrl.disabled}
                placeholder={"Guide [English]: Welcome.\nVisitor [English]: Thank you."}
                className="min-h-[60px] text-xs"
                onChange={(event) => ctrl.setDialogue(event.target.value)}
              />
            </label>
          </div>
        </details>

        {shot.dialogue_lines?.length &&
        ctrl.dialogue === formatDialogue(shot) &&
        ctrl.description === shot.script_beat ? (
          <div
            className="grid gap-1 rounded-lg border border-border-subtle bg-elevated/40 p-3 text-xs"
            aria-label="Saved dialogue speakers"
          >
            {shot.dialogue_lines.map((line) => (
              <p key={line.line_id} className="m-0 text-ink-secondary">
                <strong className="font-semibold text-ink">{line.speaker_name}：</strong>
                {line.text}
              </p>
            ))}
          </div>
        ) : null}

        {desktopOutput && (
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {(
              [
                ["shot_type", "Framing"],
                ["camera_angle", "Angle"],
                ["camera_motion", "Camera movement"],
                ["composition", "Composition"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="grid gap-1 text-xs font-medium text-ink-secondary">
                <span>{label}</span>
                <Textarea
                  rows={2}
                  aria-label={label}
                  placeholder="Enter a value or use Description assistant"
                  value={ctrl.camera[key]}
                  disabled={ctrl.disabled}
                  className="min-h-[62px] text-xs"
                  onChange={(event) => ctrl.setCamera((current) => ({ ...current, [key]: event.target.value }))}
                />
              </label>
            ))}
          </div>
        )}

        <section
          className={cn(
            "flex flex-col gap-3 rounded-xl border bg-elevated/25 p-3.5 transition-[border-color,box-shadow]",
            ctrl.autoStage === "references" || ctrl.working === "Selecting references"
              ? "border-accent ring-2 ring-accent/25"
              : "border-border-subtle",
          )}
          aria-label="Reference"
          data-workflow-stage="references"
          data-active={ctrl.autoStage === "references" || ctrl.working === "Selecting references" || undefined}
        >
          <div className="flex items-center justify-between gap-2">
            <strong className="text-[13px] font-semibold tracking-[-0.01em] text-ink">Reference</strong>
            <div className="flex items-center gap-1.5">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={ctrl.disabled}
                onClick={() => ctrl.setMaterials(true)}
              >
                Link ref
              </Button>
              <Button
                type="button"
                variant={ctrl.working === "Selecting references" ? "danger" : "secondary"}
                size="icon"
                className={cn("h-7 w-7", ctrl.working === "Selecting references" && "animate-pulse")}
                aria-label="Choose references with AI"
                title="Choose references that match this shot brief"
                aria-busy={ctrl.working === "Selecting references"}
                disabled={ctrl.disabled || !ctrl.description.trim()}
                onClick={() => void ctrl.autoSelectReferences()}
              >
                <StudioIcon name="assist-references" size={16} />
              </Button>
            </div>
          </div>

          <div className="flex items-start gap-2.5 overflow-x-auto pb-1" aria-label="Selected references">
            {shot.refs.map((ref) => (
              <figure key={`${ref.asset_id}:${ref.file_key}`} className="relative m-0 shrink-0 pt-2 pr-2">
                <ReferencePreview item={ref} />
                <button
                  type="button"
                  className="absolute top-0 right-0 flex h-5 w-5 cursor-pointer items-center justify-center rounded-full border border-border-strong bg-panel text-xs leading-none text-ink-secondary shadow-xs transition-colors hover:border-status-danger hover:text-status-danger disabled:pointer-events-none disabled:opacity-45"
                  aria-label={`Remove reference ${ref.picture_index}`}
                  disabled={ctrl.disabled}
                  onClick={() =>
                    void ctrl.changeReferences(
                      shot.refs.filter((item) => item !== ref),
                      shot.refs,
                    )
                  }
                >
                  ×
                </button>
                <figcaption className="mt-1 text-center font-mono text-[11px] font-medium text-ink-muted">
                  P{ref.picture_index}
                </figcaption>
              </figure>
            ))}
            {!shot.refs.length && (
              <small className="text-xs text-ink-muted">Link references before or after writing the brief.</small>
            )}
          </div>

          {ctrl.removedRef && (
            <div
              className="flex items-center justify-between gap-2 rounded-md border border-border-subtle bg-panel px-2.5 py-1.5 text-xs text-ink-secondary"
              role="status"
            >
              <span>Reference removed</span>
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={ctrl.disabled}
                onClick={() => void ctrl.changeReferences(ctrl.removedRef!.before, ctrl.removedRef!.after, true)}
              >
                Undo removal
              </Button>
            </div>
          )}

          {ctrl.autoRefUndo && JSON.stringify(shot.refs) === JSON.stringify(ctrl.autoRefUndo.after) && (
            <div
              className="flex items-center justify-between gap-2 rounded-md border border-border-subtle bg-panel px-2.5 py-1.5 text-xs text-ink-secondary"
              role="status"
            >
              <span>References auto-selected</span>
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={ctrl.disabled}
                onClick={() => void ctrl.changeReferences(ctrl.autoRefUndo!.before, ctrl.autoRefUndo!.after, true)}
              >
                Undo selection
              </Button>
            </div>
          )}

          {ctrl.dirty && (
            <small className="text-[11px] text-status-warn">Save the brief before changing voice references.</small>
          )}

          <VoiceReferences shot={shot} disabled={ctrl.disabled || ctrl.dirty} onSaved={ctrl.refreshVersionAfterVoice} />
          <ManualContinuity shot={shot} shots={shots} disabled={ctrl.disabled} onSaved={onSaved} />
        </section>

        <section
          className={cn(
            "flex min-w-0 flex-col gap-2.5 rounded-xl border bg-elevated/25 p-3.5 transition-[border-color,box-shadow]",
            ctrl.autoStage === "prompt" || writing
              ? "border-accent ring-2 ring-accent/25"
              : "border-border-subtle",
          )}
          aria-label="Prompt workflow"
          data-workflow-stage="prompt"
          data-active={ctrl.autoStage === "prompt" || writing || undefined}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <strong className="text-[13px] font-semibold tracking-[-0.01em] text-ink">Prompt</strong>
              <small
                role="status"
                aria-label="Prompt status"
                className={cn(
                  "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
                  promptBadgeTone,
                )}
              >
                {ctrl.promptStatus}
              </small>
            </div>
            <TimingNote entry={ctrl.latestTiming("prompt")} />
          </div>

          {ctrl.dirty && !ctrl.promptWriting && (
            <small role="status" className="text-xs text-status-warn">
              Unsaved shot changes. Save and rewrite the prompt before generating.
            </small>
          )}

          {!ctrl.promptWriting &&
            (ctrl.promptCheck?.stale || ctrl.hasPrompt) &&
            ctrl.promptCheck?.errors
              ?.filter((item) => item.code !== "prompt_stale")
              .map((item) => (
                <Banner tone="warn" key={item.code + item.message}>
                  {item.message}
                </Banner>
              ))}

          {!ctrl.promptWriting &&
            ctrl.promptCheck?.warnings?.map((item) => (
              <small key={item.code} className="text-xs text-ink-muted">
                {item.message}
              </small>
            ))}

          {ctrl.checkError && <Banner tone="danger">Prompt check unavailable: {ctrl.checkError}</Banner>}

          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              variant={ctrl.promptWriting ? "danger-solid" : "primary"}
              size="md"
              disabled={writing ? false : ctrl.disabled || !ctrl.version || !ctrl.description.trim() || !shot.refs.length}
              aria-label={writing ? "Cancel prompt writing" : "Write prompt"}
              onClick={() => {
                if (writing) void onCancel().catch((cause) => ctrl.setError(String(cause)));
                else void ctrl.act("write");
              }}
            >
              {!writing && <WorkspaceMascot className="h-5 w-5 rounded border-white/25" />}
              <span>
                {writing
                  ? `Cancel · ${writeElapsed}s`
                  : ctrl.working === "Write prompt"
                    ? `Preparing prompt · ${ctrl.elapsed}s`
                    : "Write prompt"}
              </span>
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="md"
              disabled={ctrl.disabled || writing || !ctrl.version || !ctrl.description.trim() || !shot.refs.length}
              onClick={() => void ctrl.act("write-generate")}
            >
              Write prompt &amp; generate
            </Button>
          </div>

          {Object.values(shot.prompt_sections).some(Boolean) ? (
            <details
              className="rounded-lg border border-border-subtle bg-elevated/40 p-3"
              aria-label="Prompt contents"
              open={desktopOutput}
            >
              <summary className="cursor-pointer text-xs font-semibold text-ink select-none">Saved prompt</summary>
              <div className="mt-3 flex flex-col gap-3">
                <small className="text-[11px] leading-relaxed text-ink-muted">
                  {ctrl.effectiveCheck?.stale
                    ? "Previously saved prompt · Out of date. Failed writes do not replace this text."
                    : "Saved prompt · Failed writes do not replace this text."}
                </small>
                {PROMPT_SECTION_KEYS.map(({ key, label }) =>
                  shot.prompt_sections[key] ? (
                    <label key={key} className="grid gap-1 text-xs font-medium text-ink-secondary">
                      <span>{label}</span>
                      <Textarea
                        aria-label={label}
                        readOnly
                        rows={4}
                        value={shot.prompt_sections[key]}
                        className="select-text bg-panel text-xs leading-relaxed"
                      />
                    </label>
                  ) : null,
                )}
              </div>
            </details>
          ) : (
            <small className="text-[11px] text-ink-muted">
              Write from the current brief and selected references. Refinement is optional.
            </small>
          )}
        </section>

        {ctrl.materials && (
          <ShotMaterialEditor
            shot={shot}
            shots={shots}
            shotNumber={index + 1}
            manualReferences
            onClose={() => ctrl.setMaterials(false)}
            onOpenImage={(url) => window.open(url, "_blank", "noopener")}
            onSaved={(updated, _note, write) => ctrl.refreshVersionAfterMaterials(updated, write)}
          />
        )}

        <DescriptionAssistant
          shotId={shot.id}
          description={ctrl.description}
          savedDescription={shot.script_beat}
          disabled={ctrl.disabled}
          ready={!!ctrl.version}
          onRequest={ctrl.assistDescription}
          onUndo={ctrl.undoDescriptionChange}
          canUndo={!!ctrl.beforeAmendment}
        />
      </article>

      <ManualShotOutput
        generationRef={ctrl.generationRef}
        shot={shot}
        disabled={ctrl.disabled || ctrl.dirty || ctrl.checkPending || !!ctrl.checkError}
        check={ctrl.effectiveCheck}
        promptWriting={ctrl.promptWriting}
        onSaved={onSaved}
      />
    </div>
  );
}

export function ShotWorkspacePage({ desktopOutput = false }: { desktopOutput?: boolean } = {}) {
  const { projectId, createAndSelect } = useProject();
  const [shots, setShots] = useState<Shot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [writing, setWriting] = useState(false);
  const [assisting, setAssisting] = useState(false);
  const [modelSwitching, setModelSwitching] = useState(false);
  const [writeStarted, setWriteStarted] = useState<number | null>(null);
  const [progress, setProgress] = useState<AgentProgress | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [removedShot, setRemovedShot] = useState<{ project_id: string; archive_id: string } | null>(null);
  const [now, setNow] = useState(Date.now());
  const epoch = useRef(0);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;

  useEffect(() => {
    const id = ++epoch.current;
    setShots([]);
    setSelected(null);
    setProgress(null);
    setError("");
    setRemovedShot(null);
    setWriteStarted(null);
    if (!projectId) return;
    let remembered: string | null = null;
    try {
      remembered = localStorage.getItem(`ds.shotWorkspace.selectedShot.${projectId}`);
    } catch {
      /* Storage may be unavailable. */
    }
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [detail, session] = await Promise.all([getProject(projectId!), getDirectorChatSession(projectId!)]);
        if (epoch.current !== id) return;
        setShots(detail.shots);
        setWriting(session.active);
        if (session.error) setError(session.error);
        if (session.progress && (session.active || session.progress.action_id)) setProgress(session.progress);
        setSelected(
          (current) =>
            [current, remembered].find((candidate) => candidate && detail.shots.some((s) => s.id === candidate)) ??
            detail.shots[0]?.id ??
            null,
        );
        if (session.active) timer = setTimeout(refresh, 1200);
      } catch (cause) {
        if (epoch.current === id) setError(String(cause));
      }
    }
    void refresh();
    return () => {
      epoch.current++;
      clearTimeout(timer);
    };
  }, [projectId]);

  useEffect(() => {
    // Never store the outgoing project's selection while the next project loads.
    if (!projectId || !selected || !shots.some((shot) => shot.id === selected && shot.project_id === projectId)) return;
    try {
      localStorage.setItem(`ds.shotWorkspace.selectedShot.${projectId}`, selected);
    } catch {
      /* Keep editing without storage. */
    }
  }, [projectId, selected, shots]);

  useEffect(() => {
    if (!writing) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [writing]);

  const update = (shot: Shot) => {
    setShots((current) => current.map((s) => (s.id === shot.id ? shot : s)));
    setNotice("");
  };

  async function remove(shot: Shot) {
    const index = shots.findIndex((item) => item.id === shot.id);
    const result = await json<{ shots: Shot[]; project_id: string; archive_id: string }>(
      `/api/shot-workspace/shots/${shot.id}`,
      "DELETE",
      { expected_shot: shot },
    );
    if (activeProject.current !== shot.project_id) return;
    setShots(result.shots);
    setSelected(result.shots[Math.min(index, result.shots.length - 1)]?.id ?? null);
    setRemovedShot(result);
    setProgress(null);
    setNotice("");
  }

  async function undoRemove() {
    if (!removedShot || removedShot.project_id !== projectId) return;
    setCreating(true);
    setError("");
    try {
      const result = await json<{ shots: Shot[]; shot: Shot }>(
        `/api/shot-workspace/projects/${projectId}/removed-shots/${removedShot.archive_id}/restore`,
        "POST",
      );
      if (activeProject.current !== result.shot.project_id) return;
      setShots(result.shots);
      setSelected(result.shot.id);
      setRemovedShot(null);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setCreating(false);
    }
  }

  async function add() {
    setCreating(true);
    setError("");
    try {
      const pid = projectId || (await createAndSelect("Untitled shots", "", "director", "shot_workspace")).id;
      const saved = await json<Shot>(`/api/shot-workspace/projects/${pid}/shots`, "POST", {
        request_id: crypto.randomUUID?.() ?? `manual-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      });
      if (activeProject.current === pid) {
        setShots((current) => (current.some((s) => s.id === saved.id) ? current : [...current, saved]));
        setSelected(saved.id);
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setCreating(false);
    }
  }

  async function write(shot: Shot, revisionRequest = "") {
    if (!projectId) return;
    const id = epoch.current;
    setWriting(true);
    setWriteStarted(Date.now());
    setNow(Date.now());
    setProgress(null);
    setError("");
    setNotice("");
    try {
      const result = await chatWithDirectorStream(
        projectId,
        "Write a prompt using the saved shot and selected references",
        [],
        {
          onAgentProgress: (value) => {
            if (epoch.current === id) setProgress(value);
          },
        },
        [],
        undefined,
        undefined,
        undefined,
        {
          shot_id: shot.id,
          choice: "skip",
          revision_request: revisionRequest,
          action_id: crypto.randomUUID?.() ?? `write-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        },
      );
      if (epoch.current === id) {
        setShots(result.shots);
        setNotice(result.reply);
        const written = result.shots.find((item) => item.id === shot.id);
        if (!written) throw new Error("The written shot could not be found.");
        return written;
      }
      throw new Error("The project changed before prompt writing finished.");
    } catch (cause) {
      if (epoch.current === id) setError(String(cause));
      throw cause;
    } finally {
      if (epoch.current === id) setWriting(false);
    }
  }

  const current = shots.find((shot) => shot.id === selected) ?? shots[0];
  const started = progress?.started_at ? Date.parse(progress.started_at) : writeStarted;
  const elapsed = started
    ? Math.max(
        0,
        Math.floor(((writing ? now : progress ? Date.parse(progress.updated_at) : Date.now()) - started) / 1000),
      )
    : 0;

  async function cancelWriting() {
    if (!projectId) return;
    const session = await cancelDirectorChatSession(projectId);
    if (activeProject.current !== projectId) return;
    setWriting(session.active);
    if (session.progress) setProgress(session.progress);
  }

  return (
    <main
      className={cn(
        "mx-auto flex w-full min-w-0 flex-col gap-4 px-3 py-3 pb-24 sm:px-6 sm:py-5",
        desktopOutput ? "max-w-[1440px] overflow-y-auto overscroll-contain" : "max-w-[960px]",
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle pb-3">
        <div className="min-w-0">
          <strong className="block text-base font-semibold tracking-[-0.02em] text-ink sm:text-lg">
            Shot workspace
          </strong>
          <small className="block text-xs text-ink-secondary">Shape the shot. Bring it to life.</small>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <WorkspaceModelPicker disabled={writing || creating || assisting} onSwitching={setModelSwitching} />
          <Button
            type="button"
            variant="secondary"
            size="icon"
            className="h-8 w-8"
            aria-label="Add shot"
            title="Add shot"
            disabled={creating || writing || assisting || modelSwitching}
            onClick={() => void add()}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </header>

      <nav className="flex min-h-[34px] items-center gap-1.5 overflow-x-auto pb-1" aria-label="Shots">
        {shots.map((shot, index) => {
          const isSelected = current?.id === shot.id;
          return (
            <button
              key={shot.id}
              type="button"
              title={shot.title || `Shot ${index + 1}`}
              aria-label={`Shot ${index + 1}`}
              aria-current={isSelected ? "page" : undefined}
              disabled={assisting || modelSwitching}
              onClick={() => setSelected(shot.id)}
              className={cn(
                "inline-flex h-8 min-w-[36px] shrink-0 cursor-pointer items-center justify-center rounded-md border px-2.5 font-mono text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:pointer-events-none disabled:opacity-45",
                isSelected
                  ? "border-accent bg-accent text-accent-fg shadow-xs"
                  : "border-border-subtle bg-panel text-ink-secondary hover:border-border-strong hover:bg-hover hover:text-ink",
              )}
            >
              {index + 1}
            </button>
          );
        })}
      </nav>

      {error && (
        <Banner tone="danger" className="whitespace-pre-wrap break-words">
          {error}
        </Banner>
      )}

      {removedShot && (
        <div
          className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-elevated px-3 py-2 text-xs text-ink-secondary"
          role="status"
        >
          <span>Shot deleted</span>
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={creating || writing}
            onClick={() => void undoRemove()}
          >
            Undo delete
          </Button>
        </div>
      )}

      {notice && (
        <p className="m-0 whitespace-pre-wrap rounded-lg border border-border-subtle bg-elevated/60 px-3 py-2 text-xs text-ink-secondary">
          {notice}
        </p>
      )}

      {current ? (
        <ShotEditor
          key={current.id}
          shot={current}
          shots={shots}
          index={shots.indexOf(current)}
          busy={writing || creating || modelSwitching}
          onSaved={update}
          onWrite={write}
          onDelete={remove}
          desktopOutput={desktopOutput}
          onAssistingChange={setAssisting}
          writing={writing}
          writeElapsed={elapsed}
          onCancel={cancelWriting}
        />
      ) : (
        <EmptyState
          icon={<Clapperboard className="h-4 w-4" aria-hidden="true" />}
          title="No shots yet"
          description="Click + to add a shot, write its brief and link references."
        />
      )}
    </main>
  );
}
