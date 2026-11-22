import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Circle,
  Film,
  Image as ImageIcon,
  Layers,
  Play,
  Plus,
  Square,
  Trash2,
  X,
} from "lucide-react";
import {
  PROMPT_SECTION_KEYS,
  refPreviewCandidates,
  type Shot,
  type ShotRef,
} from "../../shared/api/types";
import { PageShell } from "../../shared/components/PageShell";
import { promptReady, shotWorkflowStatus } from "../../shared/shotWorkflowStatus";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  Input,
  Panel,
  PanelBody,
  PanelHeader,
  PanelTitle,
  Select,
  StatusDot,
  Textarea,
  cn,
  type StatusDotProps,
} from "../../shared/ui";
import { ShotMaterialEditor } from "../director/ShotMaterialEditor";
import { VideoContextPanel } from "../director/VideoContextPanel";
import { type H3Provider } from "./api";
import { ManagedRunControls } from "./ManagedRunControls";
import { useProductionController, type ProductionDrawerTab } from "./useProductionController";

function pictureLabel(ref: { role: string; picture_index: number }): string {
  if (ref.role === "layout_ref_frame") {
    return `P${ref.picture_index} · Layout`;
  }
  return `P${ref.picture_index} · ${ref.role.replace(/_/g, " ")}`;
}

function statusTone(key: string): NonNullable<StatusDotProps["tone"]> {
  switch (key) {
    case "h3-running":
      return "info";
    case "h3-queued":
      return "accent";
    case "h3-succeeded":
    case "ready-for-h3":
      return "ok";
    case "prompt-stale":
      return "warn";
    case "h3-failed":
    case "blocked":
      return "danger";
    default:
      return "neutral";
  }
}

function jobStatusTone(status: string): NonNullable<StatusDotProps["tone"]> {
  switch (status) {
    case "running":
    case "uploading":
      return "info";
    case "queued":
      return "accent";
    case "succeeded":
      return "ok";
    case "failed":
    case "cancelled":
      return "danger";
    default:
      return "neutral";
  }
}

function ProductionStatusChip({ shot }: { shot: Shot }) {
  const stage = shotWorkflowStatus(shot);
  const tone = statusTone(stage.key);
  return (
    <Badge tone={tone} className={cn("status-chip", `status-${stage.key}`)}>
      <StatusDot tone={tone} pulse={stage.key === "h3-running"} />
      {stage.label}
    </Badge>
  );
}

/** Thumb with extension fallback; click opens preview (does not hide on error). */
function ProductionRefThumb({
  refItem,
  onOpen,
}: {
  refItem: ShotRef;
  onOpen: (url: string) => void;
}) {
  const candidates = useMemo(() => refPreviewCandidates(refItem), [refItem]);
  const [idx, setIdx] = useState(0);
  const [failed, setFailed] = useState(false);
  const url = !failed && idx < candidates.length ? candidates[idx] : null;

  // Reset when ref identity changes (not on every parent poll re-render of same ref)
  useEffect(() => {
    setIdx(0);
    setFailed(false);
  }, [refItem.asset_id, refItem.file_key, refItem.role, refItem.picture_index]);

  return (
    <button
      type="button"
      className="ref-thumb ref-thumb-btn group flex flex-col overflow-hidden rounded-lg border border-border-subtle bg-elevated/40 p-2 text-left transition-colors hover:border-border-strong hover:bg-hover/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      title={`${pictureLabel(refItem)} · ${refItem.asset_id}${refItem.file_key ? ` · ${refItem.file_key}` : ""}`}
      onClick={() => {
        if (url) onOpen(url);
      }}
    >
      <div className="ref-thumb-img relative aspect-video w-full overflow-hidden rounded border border-border-subtle bg-canvas">
        {url ? (
          <img
            src={url}
            alt={pictureLabel(refItem)}
            draggable={false}
            className="h-full w-full object-cover transition-transform duration-150 group-hover:scale-[1.02]"
            onError={() => {
              if (idx + 1 < candidates.length) {
                setIdx((i) => i + 1);
              } else {
                setFailed(true);
              }
            }}
          />
        ) : (
          <div className="output-empty flex h-full w-full items-center justify-center text-[11px] text-ink-muted">
            no preview
          </div>
        )}
      </div>
      <div className="ref-thumb-label mt-2 text-xs font-medium text-ink">
        {pictureLabel(refItem)}
      </div>
      <div className="mt-0.5 truncate font-mono text-[10px] text-ink-muted">{refItem.asset_id}</div>
      {refItem.file_key ? (
        <div className="truncate text-[10px] text-ink-muted">{refItem.file_key}</div>
      ) : null}
    </button>
  );
}

export function ProductionPage({
  active = true,
  mobile = false,
  onReviewMaterials,
}: {
  active?: boolean;
  mobile?: boolean;
  onReviewMaterials?: (shot: Shot, shotNumber: number, message: string) => void;
} = {}) {
  const ctrl = useProductionController({ active, mobile });
  const {
    projectId,
    shots,
    projectLoading,
    projectFailed,
    selectedId,
    setSelectedId,
    selected,
    continuesPrevious,
    currentLayout,
    draftPrompt,
    setDraftPrompt,
    setPromptDirty,
    draftDialogue,
    setDraftDialogue,
    draftDuration,
    setDraftDuration,
    error,
    busy,
    h3Job,
    jobActive,
    canSubmit,
    workflowProfile,
    workflowProfileError,
    tab,
    setTab,
    previewUrl,
    setPreviewUrl,
    voiceAssets,
    voiceAssetById,
    draftVoiceRefs,
    selectedVoiceId,
    setSelectedVoiceId,
    voiceRefsDirty,
    voicePromptStale,
    materialEditorOpen,
    setMaterialEditorOpen,
    h3ProviderStatus,
    h3Provider,
    setH3Provider,
    localResolutions,
    resolutionPreset,
    setResolutionPreset,
    managedActive,
    setManagedActive,
    contextSize,
    orderedRefs,
    layoutUrl,
    loadProject,
    replaceShot,
    onSavePrompt,
    onDeleteLayout,
    onSubmit,
    onCancelJob,
    updateVoiceRefs,
    addVoiceRef,
    moveVoiceRef,
    saveVoiceRefs,
  } = ctrl;

  const tabs: { id: ProductionDrawerTab; label: string }[] = [
    { id: "layout", label: "Layout" },
    { id: "refs", label: "Refs" },
    { id: "prompt", label: "Prompt" },
    { id: "run", label: "Run H3" },
  ];

  const providerPicker = (
    <label className="h3-provider-picker flex flex-col gap-1 text-xs font-medium text-ink-secondary">
      <span>Provider</span>
      <Select
        aria-label="H3 provider"
        value={h3Provider}
        disabled={busy || jobActive}
        wrapperClassName="w-full"
        onChange={(event) => setH3Provider(event.target.value as H3Provider)}
      >
        <option value="local">Local · ComfyUI</option>
        <option value="minimax" disabled={!h3ProviderStatus?.minimax_configured}>
          MiniMax · Official API
          {h3ProviderStatus && !h3ProviderStatus.minimax_configured ? " · not configured" : ""}
        </option>
      </Select>
      <small className="text-[11px] font-normal text-ink-muted">
        {h3Provider === "minimax"
          ? `Official API · ${h3ProviderStatus?.minimax_resolution || "768P"}`
          : "Local ComfyUI workflow"}
      </small>
    </label>
  );

  const resolutionPicker = (
    <label className="h3-resolution-picker flex flex-col gap-1 text-xs font-medium text-ink-secondary">
      Resolution
      <Select
        value={continuesPrevious ? "previous" : resolutionPreset}
        disabled={busy || jobActive || continuesPrevious}
        wrapperClassName="w-full"
        onChange={(event) => setResolutionPreset(event.target.value)}
      >
        {continuesPrevious ? (
          <option value="previous">
            Match previous shot
            {contextSize && contextSize.shotId === selected?.id
              ? ` · ${contextSize.width}×${contextSize.height}`
              : ""}
          </option>
        ) : (
          <option value="auto">Auto from project</option>
        )}
        {!continuesPrevious &&
          localResolutions.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.label}
            </option>
          ))}
      </Select>
    </label>
  );

  const lightbox = previewUrl ? (
    <div
      className="lightbox-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onClick={() => setPreviewUrl(null)}
      onKeyDown={(e) => e.key === "Escape" && setPreviewUrl(null)}
    >
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="lightbox-close fixed right-4 top-4 gap-1"
        onClick={() => setPreviewUrl(null)}
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
        Close
      </Button>
      <img
        className="lightbox-img max-h-[88dvh] max-w-[92vw] rounded-lg border border-border-subtle bg-panel object-contain shadow-[var(--shadow-popover)]"
        src={previewUrl}
        alt="ref preview"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  ) : null;

  if (mobile) {
    const selectedNumber = selected
      ? Math.max(0, shots.findIndex((shot) => shot.id === selected.id)) + 1
      : 0;
    const videoUrl = h3Job?.outputs?.video?.url || h3Job?.outputs?.master?.url || null;
    const promptSections = selected
      ? PROMPT_SECTION_KEYS.filter(({ key }) => selected.prompt_sections?.[key]?.trim())
      : [];

    return (
      <main
        className="mobile-production-review flex flex-col gap-3 p-3.5 text-ink"
        aria-label="Mobile production review"
      >
        <header className="mobile-section-header mobile-production-header flex items-end justify-between gap-3">
          <div>
            <span className="mobile-eyebrow block text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
              Production
            </span>
            <h1 className="text-base font-semibold tracking-[-0.015em] text-ink">Shot review</h1>
          </div>
          <Badge tone="neutral" className="mobile-shot-count font-mono tabular-nums">
            {projectLoading ? "Loading…" : `${shots.length} shots`}
          </Badge>
        </header>

        {error ? (
          <Banner tone="danger" className="mobile-production-error">
            {error}
          </Banner>
        ) : null}

        {projectId ? (
          <ManagedRunControls
            projectId={projectId}
            shots={shots}
            provider={h3Provider}
            presets={localResolutions}
            onStateChange={setManagedActive}
            onProjectChanged={() => {
              void loadProject(projectId);
            }}
          />
        ) : null}

        {!projectId ? (
          <p className="mobile-production-empty rounded-lg border border-border-subtle bg-panel px-4 py-6 text-center text-xs text-ink-muted">
            Select a project to review its shots.
          </p>
        ) : projectLoading ? (
          <p
            className="mobile-production-empty rounded-lg border border-border-subtle bg-panel px-4 py-6 text-center text-xs text-ink-muted"
            role="status"
          >
            Loading shots…
          </p>
        ) : projectFailed ? null : shots.length === 0 ? (
          <p className="mobile-production-empty rounded-lg border border-border-subtle bg-panel px-4 py-6 text-center text-xs text-ink-muted">
            No shots yet. Plan them with Director first.
          </p>
        ) : (
          <nav
            className="mobile-production-shot-strip flex gap-1.5 overflow-x-auto pb-1"
            aria-label="Production shots"
          >
            {shots.map((shot, index) => {
              const isCurrent = shot.id === selectedId;
              return (
                <button
                  key={shot.id}
                  type="button"
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors cursor-pointer",
                    isCurrent
                      ? "active border-accent bg-accent-soft text-ink font-medium"
                      : "border-border-subtle bg-panel text-ink-secondary hover:bg-hover",
                  )}
                  aria-label={`Shot ${String(index + 1).padStart(2, "0")} · ${shot.title || shot.id}`}
                  aria-current={isCurrent ? "page" : undefined}
                  onClick={() => {
                    setSelectedId(shot.id);
                    setMaterialEditorOpen(false);
                  }}
                >
                  <span className="font-mono text-[10px] text-ink-muted tabular-nums">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <strong className="font-medium">{shot.title || shot.id}</strong>
                </button>
              );
            })}
          </nav>
        )}

        {selected ? (
          <article className="mobile-production-sheet flex flex-col gap-3">
            <header className="mobile-production-shot-header flex items-start justify-between gap-3 rounded-lg border border-border-subtle bg-panel p-3.5">
              <div>
                <span className="mobile-eyebrow block font-mono text-[10px] uppercase tracking-[0.06em] text-ink-muted">
                  Shot {String(selectedNumber).padStart(2, "0")}
                </span>
                <h2 className="mt-0.5 text-sm font-semibold text-ink">
                  {selected.title || selected.id}
                </h2>
              </div>
              <ProductionStatusChip shot={selected} />
            </header>

            <section className="mobile-production-section mobile-production-result flex flex-col gap-3 rounded-lg border border-border-subtle bg-panel p-3.5">
              <div className="mobile-production-section-title flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                  Final result
                </h3>
                {h3Job ? (
                  <Badge tone={jobStatusTone(h3Job.status)}>
                    <StatusDot
                      tone={jobStatusTone(h3Job.status)}
                      pulse={jobActive}
                    />
                    {h3Job.status}
                  </Badge>
                ) : null}
              </div>
              {videoUrl ? (
                <video
                  controls
                  playsInline
                  preload="metadata"
                  src={videoUrl}
                  className="w-full rounded-md border border-border-subtle bg-black"
                />
              ) : jobActive ? (
                <p className="mobile-production-empty-inline rounded-md border border-dashed border-border-subtle bg-elevated/40 px-3 py-4 text-center text-xs text-ink-muted">
                  Video generation is in progress.
                </p>
              ) : (
                <p className="mobile-production-empty-inline rounded-md border border-dashed border-border-subtle bg-elevated/40 px-3 py-4 text-center text-xs text-ink-muted">
                  No final video for this shot yet.
                </p>
              )}
              {h3Job?.error ? (
                <div
                  className="mobile-production-job-error rounded-md border border-status-danger/30 bg-status-danger-soft px-3 py-2 text-xs text-status-danger"
                  role="status"
                >
                  Generation failed. Open desktop Production for diagnostic details.
                </div>
              ) : null}
              {providerPicker}
              {h3Provider === "local" ? (
                <VideoContextPanel
                  key={selected.id}
                  shot={selected}
                  shots={shots}
                  onShotUpdated={replaceShot}
                />
              ) : null}
              {h3Provider === "local" ? resolutionPicker : null}
              <Button
                type="button"
                variant="primary"
                className="mobile-production-run w-full"
                disabled={busy || !canSubmit || jobActive || managedActive}
                onClick={() => void onSubmit()}
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {jobActive ? "H3 running…" : "Run H3"}
              </Button>
            </section>

            <section className="mobile-production-section flex flex-col gap-2.5 rounded-lg border border-border-subtle bg-panel p-3.5">
              <h3 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                Shot design
              </h3>
              <p className="mobile-production-beat text-xs leading-relaxed text-ink">
                {selected.script_beat}
              </p>
              <dl className="mobile-production-metadata grid grid-cols-2 gap-2 rounded-md border border-border-subtle bg-elevated/40 p-2.5 text-xs">
                <div>
                  <dt className="text-[10px] uppercase tracking-[0.04em] text-ink-muted">
                    Duration
                  </dt>
                  <dd className="mt-0.5 font-mono font-medium text-ink">{selected.duration_s}s</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-[0.04em] text-ink-muted">
                    Framing
                  </dt>
                  <dd className="mt-0.5 text-ink">{selected.shot_type || "—"}</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-[0.04em] text-ink-muted">Angle</dt>
                  <dd className="mt-0.5 text-ink">{selected.camera_angle || "—"}</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-[0.04em] text-ink-muted">Motion</dt>
                  <dd className="mt-0.5 text-ink">{selected.camera_motion || "—"}</dd>
                </div>
              </dl>
              {selected.composition ? (
                <div className="mobile-production-note text-xs">
                  <span className="block text-[11px] font-medium text-ink-muted">Composition</span>
                  <p className="mt-0.5 text-ink-secondary">{selected.composition}</p>
                </div>
              ) : null}
              {selected.dialogue?.length ? (
                <div className="mobile-production-note text-xs">
                  <span className="block text-[11px] font-medium text-ink-muted">Dialogue</span>
                  {selected.dialogue.map((line, index) => (
                    <p key={`${line}-${index}`} className="mt-0.5 text-ink-secondary">
                      {line}
                    </p>
                  ))}
                </div>
              ) : null}
            </section>

            <section className="mobile-production-section flex flex-col gap-2.5 rounded-lg border border-border-subtle bg-panel p-3.5">
              <div className="mobile-production-section-title flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                  References
                </h3>
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  className="shot-material-edit-trigger"
                  disabled={busy}
                  onClick={() => setMaterialEditorOpen(true)}
                >
                  Edit materials
                </Button>
              </div>
              {orderedRefs.length ? (
                <div className="mobile-production-reference-strip grid grid-cols-2 gap-2">
                  {orderedRefs.map((ref) => (
                    <ProductionRefThumb
                      key={`${ref.role}-${ref.picture_index}-${ref.asset_id}-${ref.file_key || ""}`}
                      refItem={ref}
                      onOpen={setPreviewUrl}
                    />
                  ))}
                </div>
              ) : layoutUrl ? (
                <img
                  className="mobile-production-layout max-h-56 w-full rounded-md border border-border-subtle object-contain bg-canvas"
                  src={layoutUrl}
                  alt="Layout reference"
                />
              ) : (
                <p className="mobile-production-empty-inline text-xs text-ink-muted">
                  No references attached.
                </p>
              )}
            </section>

            <section className="mobile-production-section mobile-production-prompt flex flex-col gap-2.5 rounded-lg border border-border-subtle bg-panel p-3.5">
              <h3 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                H3 prompt
              </h3>
              {promptSections.length ? (
                promptSections.map(({ key, label }) => (
                  <div
                    key={key}
                    className="mobile-production-prompt-section rounded-md border border-border-subtle bg-elevated/40 p-2.5"
                  >
                    <h4 className="text-[11px] font-semibold text-ink-secondary">{label}</h4>
                    <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-ink">
                      {selected.prompt_sections[key]}
                    </p>
                  </div>
                ))
              ) : (
                <p className="mobile-production-empty-inline text-xs text-ink-muted">
                  No H3 prompt written yet.
                </p>
              )}
            </section>
          </article>
        ) : null}

        {selected && materialEditorOpen ? (
          <ShotMaterialEditor
            shot={selected}
            shots={shots}
            shotNumber={selectedNumber}
            onClose={() => setMaterialEditorOpen(false)}
            onOpenImage={setPreviewUrl}
            onSaved={(updated, message, notifyAgent) => {
              replaceShot(updated);
              if (notifyAgent) {
                onReviewMaterials?.(updated, selectedNumber, message);
              }
            }}
          />
        ) : null}

        {lightbox}
      </main>
    );
  }

  return (
    <PageShell
      title="Production"
      subtitle={
        projectId ? (
          <strong className="production-workflow-title font-medium text-ink-secondary">
            {workflowProfile
              ? `Local · ComfyUI — ${workflowProfile.display_name}`
              : workflowProfileError
                ? `Workflow status unavailable: ${workflowProfileError}`
                : "Loading local workflow…"}
          </strong>
        ) : (
          "Select a project in the header."
        )
      }
      actions={
        projectId ? (
          <ManagedRunControls
            projectId={projectId}
            shots={shots}
            provider={h3Provider}
            presets={localResolutions}
            onStateChange={setManagedActive}
            onProjectChanged={() => {
              void loadProject(projectId);
            }}
          />
        ) : null
      }
      className="production-page"
    >
      <div className="flex flex-col gap-3">
        {workflowProfile?.warning ? (
          <Banner
            tone="warn"
            role="status"
            title={`Using ${workflowProfile.display_name}`}
            className="production-workflow-warning"
          >
            {workflowProfile.warning.message}
          </Banner>
        ) : null}
        {error ? <Banner tone="danger">{error}</Banner> : null}

        <div className="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-[276px_minmax(0,1fr)] lg:items-start">
          <aside className="min-w-0">
            <Panel>
              <PanelHeader className="px-3.5 py-2.5">
                <PanelTitle>Shots</PanelTitle>
                <span className="font-mono text-[11px] text-ink-muted tabular-nums">
                  {projectLoading ? "Loading…" : shots.length}
                </span>
              </PanelHeader>
              <PanelBody className="p-2">
                {!projectId ? (
                  <p className="px-2 py-4 text-center text-xs text-ink-muted">
                    No project selected.
                  </p>
                ) : projectLoading ? (
                  <p className="px-2 py-4 text-center text-xs text-ink-muted" role="status">
                    Loading shots…
                  </p>
                ) : projectFailed ? null : shots.length === 0 ? (
                  <p className="px-2 py-4 text-center text-xs text-ink-muted">
                    No shots yet. Plan them in Director first.
                  </p>
                ) : (
                  <div className="flex flex-col gap-1">
                    {shots.map((s, index) => {
                      const isSelected = s.id === selectedId;
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => setSelectedId(s.id)}
                          className={cn(
                            "flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-2 text-left transition-colors cursor-pointer",
                            isSelected
                              ? "border-accent/40 bg-accent-soft/60 text-ink"
                              : "border-transparent bg-transparent text-ink-secondary hover:bg-hover hover:text-ink",
                          )}
                        >
                          <div className="flex min-w-0 items-start gap-2">
                            <span className="mt-0.5 font-mono text-[11px] text-ink-muted tabular-nums">
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            <div className="min-w-0">
                              <div className="truncate text-xs font-medium text-ink">
                                {s.title || s.id}
                              </div>
                              <div className="mt-0.5 font-mono text-[10px] text-ink-muted">
                                {s.duration_s}s
                              </div>
                            </div>
                          </div>
                          <ProductionStatusChip shot={s} />
                        </button>
                      );
                    })}
                  </div>
                )}
              </PanelBody>
            </Panel>
          </aside>

          <section className="min-w-0">
            {!selected ? (
              <EmptyState
                icon={<Film className="h-4 w-4" aria-hidden="true" />}
                title="No shot selected"
                description="Select a shot from the list to edit layout, refs, and prompt."
                className="min-h-[320px]"
              />
            ) : (
              <Panel className="shot-editor overflow-hidden">
                <PanelHeader className="px-5 py-3.5">
                  <div className="min-w-0">
                    <h2 className="shot-editor-title truncate text-sm font-semibold tracking-[-0.015em] text-ink">
                      {selected.title || selected.id}
                    </h2>
                  </div>
                  <ProductionStatusChip shot={selected} />
                </PanelHeader>

                <section
                  className="desktop-shot-design border-b border-border-subtle bg-elevated/30 px-5 py-3.5"
                  aria-label="Shot design"
                >
                  <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-muted">
                    Shot design
                  </h3>
                  <p className="shot-beat mt-1 text-xs leading-relaxed text-ink-secondary">
                    {selected.script_beat}
                  </p>
                  <dl className="shot-design-metadata mt-2.5 grid grid-cols-2 gap-3 rounded-md border border-border-subtle bg-panel px-3 py-2 sm:grid-cols-4">
                    <div>
                      <dt className="text-[10px] uppercase tracking-[0.05em] text-ink-muted">
                        Duration
                      </dt>
                      <dd className="mt-0.5 font-mono text-xs font-medium text-ink">
                        {selected.duration_s}s
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[10px] uppercase tracking-[0.05em] text-ink-muted">
                        Framing
                      </dt>
                      <dd className="mt-0.5 text-xs text-ink">{selected.shot_type || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-[10px] uppercase tracking-[0.05em] text-ink-muted">
                        Angle
                      </dt>
                      <dd className="mt-0.5 text-xs text-ink">{selected.camera_angle || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-[10px] uppercase tracking-[0.05em] text-ink-muted">
                        Motion
                      </dt>
                      <dd className="mt-0.5 text-xs text-ink">
                        {selected.camera_motion || "—"}
                      </dd>
                    </div>
                  </dl>
                  {selected.composition || selected.dialogue?.length ? (
                    <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
                      {selected.composition ? (
                        <div className="shot-design-note rounded-md border border-border-subtle bg-panel px-3 py-2 text-xs">
                          <span className="block text-[10px] font-medium uppercase tracking-[0.05em] text-ink-muted">
                            Composition
                          </span>
                          <p className="mt-0.5 text-ink-secondary">{selected.composition}</p>
                        </div>
                      ) : null}
                      {selected.dialogue?.length ? (
                        <div className="shot-design-note rounded-md border border-border-subtle bg-panel px-3 py-2 text-xs">
                          <span className="block text-[10px] font-medium uppercase tracking-[0.05em] text-ink-muted">
                            Dialogue
                          </span>
                          {selected.dialogue.map((line, index) => (
                            <p key={`${line}-${index}`} className="mt-0.5 text-ink-secondary">
                              {line}
                            </p>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </section>

                <div
                  className="flex items-center gap-5 border-b border-border-subtle bg-panel px-5"
                  role="tablist"
                >
                  {tabs.map((t) => {
                    const isActive = tab === t.id;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        role="tab"
                        aria-selected={isActive}
                        className={cn(
                          "-mb-px border-b-2 py-2.5 text-xs font-medium transition-colors cursor-pointer",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                          isActive
                            ? "border-accent text-ink"
                            : "border-transparent text-ink-secondary hover:text-ink",
                        )}
                        onClick={() => setTab(t.id)}
                      >
                        {t.label}
                      </button>
                    );
                  })}
                </div>

                <div className="shot-editor-body p-5">
                  {tab === "layout" ? (
                    <div className="tab-panel flex flex-col gap-3">
                      <p className="text-xs leading-relaxed text-ink-secondary">
                        Optional still for spatial lock. It binds as a normal Picture slot and does
                        not steal Picture 1. Skip if you only have actor/scene refs.
                      </p>
                      <div className="flex min-h-[220px] items-center justify-center overflow-hidden rounded-lg border border-border-subtle bg-canvas p-3">
                        {layoutUrl ? (
                          <img
                            src={layoutUrl}
                            alt="layout"
                            className="max-h-[360px] rounded object-contain"
                          />
                        ) : (
                          <div className="flex flex-col items-center gap-1.5 text-xs text-ink-muted">
                            <Layers className="h-5 w-5 opacity-50" aria-hidden="true" />
                            <span>No layout</span>
                          </div>
                        )}
                      </div>
                      <div className="font-mono text-[11px] text-ink-muted">
                        {selected.layout_asset_id || "no layout asset"}
                      </div>
                      {currentLayout ? (
                        <div className="pt-1">
                          <Button
                            type="button"
                            variant="danger"
                            size="sm"
                            disabled={busy || jobActive}
                            onClick={onDeleteLayout}
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                            Delete Layout
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {tab === "refs" ? (
                    <div className="tab-panel flex flex-col gap-6">
                      <div>
                        <div className="mb-3 flex items-center justify-between">
                          <div>
                            <strong className="text-xs font-semibold text-ink">
                              Picture References
                            </strong>
                            <div className="text-[11px] text-ink-muted">
                              Ordered Picture slots for H3
                            </div>
                          </div>
                        </div>
                        {orderedRefs.length === 0 ? (
                          <EmptyState
                            icon={<ImageIcon className="h-4 w-4" aria-hidden="true" />}
                            title="No Picture refs"
                            description="Import in Library or cast via Director chat."
                            className="py-6"
                          />
                        ) : (
                          <div className="ref-thumb-row grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                            {orderedRefs.map((ref) => (
                              <ProductionRefThumb
                                key={`${ref.role}-${ref.picture_index}-${ref.asset_id}-${ref.file_key || ""}`}
                                refItem={ref}
                                onOpen={setPreviewUrl}
                              />
                            ))}
                          </div>
                        )}
                      </div>

                      <section
                        className="voice-ref-editor flex flex-col gap-3 border-t border-border-subtle pt-5"
                        aria-label="Voice References"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div>
                            <strong className="text-xs font-semibold text-ink">
                              Voice References
                            </strong>
                            <div className="text-[11px] text-ink-muted">
                              Audio order maps directly to &lt;Audio 1&gt;–&lt;Audio 3&gt; in the
                              H3 prompt.
                            </div>
                          </div>
                          <Badge tone="neutral" className="voice-slot-count font-mono tabular-nums">
                            {draftVoiceRefs.length}/3
                          </Badge>
                        </div>

                        {selected.source_audio_path ? (
                          <Banner
                            tone="warn"
                            title="Exact source audio controls this run"
                            className="voice-source-audio-banner"
                          >
                            Voice Reference overrides are disabled.
                          </Banner>
                        ) : null}

                        <div className="voice-picker-row flex flex-wrap items-end gap-2">
                          <label className="voice-picker-field flex min-w-[220px] flex-1 flex-col gap-1 text-xs font-medium text-ink-secondary">
                            <span>Add Voice reference</span>
                            <Select
                              aria-label="Add Voice reference"
                              value={selectedVoiceId}
                              disabled={
                                busy ||
                                jobActive ||
                                Boolean(selected.source_audio_path) ||
                                draftVoiceRefs.length >= 3
                              }
                              wrapperClassName="w-full"
                              onChange={(e) => setSelectedVoiceId(e.target.value)}
                            >
                              <option value="">Choose from Voice Library…</option>
                              {voiceAssets
                                .filter(
                                  (asset) => !draftVoiceRefs.some((ref) => ref.asset_id === asset.id),
                                )
                                .map((asset) => (
                                  <option key={asset.id} value={asset.id}>
                                    {asset.name}
                                  </option>
                                ))}
                            </Select>
                          </label>
                          <Button
                            type="button"
                            variant="secondary"
                            size="md"
                            disabled={
                              !selectedVoiceId ||
                              busy ||
                              jobActive ||
                              Boolean(selected.source_audio_path)
                            }
                            onClick={addVoiceRef}
                          >
                            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                            Add Voice
                          </Button>
                        </div>

                        {draftVoiceRefs.length === 0 ? (
                          <p className="text-xs text-ink-muted">No Voice references selected.</p>
                        ) : (
                          <div className="voice-ref-list flex flex-col gap-2.5">
                            {draftVoiceRefs.map((ref, index) => {
                              const asset = voiceAssetById.get(ref.asset_id);
                              return (
                                <article
                                  className="voice-ref-item flex flex-col gap-3 rounded-lg border border-border-subtle bg-elevated/40 p-3 sm:flex-row sm:items-start"
                                  key={ref.asset_id}
                                >
                                  <Badge tone="accent" className="voice-ref-index shrink-0 font-mono">
                                    Audio {index + 1}
                                  </Badge>
                                  <div className="voice-ref-main flex min-w-0 flex-1 flex-col gap-2">
                                    <div className="flex items-center gap-2">
                                      <strong className="text-xs font-semibold text-ink">
                                        {asset?.name || ref.asset_id}
                                      </strong>
                                      {asset?.meta?.duration_s != null ? (
                                        <span className="font-mono text-[11px] text-ink-muted">
                                          {String(asset.meta.duration_s)}s
                                        </span>
                                      ) : null}
                                    </div>
                                    {asset?.notes ? (
                                      <div className="text-xs text-ink-secondary">{asset.notes}</div>
                                    ) : null}
                                    {asset?.urls?.[ref.file_key || "reference"] ? (
                                      <audio
                                        controls
                                        preload="metadata"
                                        src={asset.urls[ref.file_key || "reference"]}
                                        className="h-8 w-full max-w-md"
                                      />
                                    ) : (
                                      <div className="text-xs text-ink-muted">
                                        Reference audio is unavailable.
                                      </div>
                                    )}
                                    <label className="voice-speaker-field flex max-w-xs flex-col gap-1 text-[11px] font-medium text-ink-secondary">
                                      <span>Speaker label</span>
                                      <Input
                                        value={ref.speaker}
                                        disabled={
                                          busy || jobActive || Boolean(selected.source_audio_path)
                                        }
                                        onChange={(e) =>
                                          updateVoiceRefs(
                                            draftVoiceRefs.map((item, itemIndex) =>
                                              itemIndex === index
                                                ? { ...item, speaker: e.target.value }
                                                : item,
                                            ),
                                          )
                                        }
                                      />
                                    </label>
                                  </div>
                                  <div className="voice-ref-actions flex shrink-0 items-center gap-1">
                                    <Button
                                      type="button"
                                      variant="secondary"
                                      size="xs"
                                      aria-label={`Move Audio ${index + 1} up`}
                                      disabled={
                                        index === 0 || busy || Boolean(selected.source_audio_path)
                                      }
                                      onClick={() => moveVoiceRef(index, -1)}
                                    >
                                      <ArrowUp className="h-3 w-3" aria-hidden="true" />
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="secondary"
                                      size="xs"
                                      aria-label={`Move Audio ${index + 1} down`}
                                      disabled={
                                        index === draftVoiceRefs.length - 1 ||
                                        busy ||
                                        Boolean(selected.source_audio_path)
                                      }
                                      onClick={() => moveVoiceRef(index, 1)}
                                    >
                                      <ArrowDown className="h-3 w-3" aria-hidden="true" />
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="xs"
                                      aria-label={`Remove Audio ${index + 1}`}
                                      disabled={busy || Boolean(selected.source_audio_path)}
                                      onClick={() =>
                                        updateVoiceRefs(
                                          draftVoiceRefs.filter(
                                            (_, itemIndex) => itemIndex !== index,
                                          ),
                                        )
                                      }
                                    >
                                      Remove
                                    </Button>
                                  </div>
                                </article>
                              );
                            })}
                          </div>
                        )}

                        {voicePromptStale && !selected.source_audio_path ? (
                          <p className="voice-prompt-stale text-xs text-status-warn">
                            Prompt will refresh before H3 submission.
                          </p>
                        ) : null}

                        <div>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={
                              busy ||
                              jobActive ||
                              Boolean(selected.source_audio_path) ||
                              !voiceRefsDirty
                            }
                            onClick={saveVoiceRefs}
                          >
                            Save Voice refs
                          </Button>
                        </div>
                      </section>
                    </div>
                  ) : null}

                  {tab === "prompt" ? (
                    <div className="tab-panel flex flex-col gap-3.5">
                      {PROMPT_SECTION_KEYS.map(({ key, label }) => {
                        const inputId = `production-prompt-${key}`;
                        return (
                          <div key={key} className="flex flex-col gap-1.5">
                            <div className="flex items-center justify-between gap-2">
                              <label
                                htmlFor={inputId}
                                className="text-xs font-medium text-ink-secondary"
                              >
                                {label}
                              </label>
                              <span className="font-mono text-[10px] text-ink-muted">{key}</span>
                            </div>
                            <Textarea
                              id={inputId}
                              rows={key === "detailed_description" ? 6 : 2}
                              value={draftPrompt[key]}
                              disabled={busy || jobActive}
                              className="font-mono text-xs leading-relaxed"
                              onChange={(e) => {
                                setPromptDirty(true);
                                setDraftPrompt((p) => ({ ...p, [key]: e.target.value }));
                              }}
                            />
                          </div>
                        );
                      })}
                      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px]">
                        <label className="flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                          <span>Dialogue (one line per cue)</span>
                          <Textarea
                            rows={3}
                            value={draftDialogue}
                            disabled={busy || jobActive}
                            className="font-mono text-xs leading-relaxed"
                            onChange={(e) => setDraftDialogue(e.target.value)}
                          />
                        </label>
                        <label className="flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                          <span>Duration (seconds)</span>
                          <Input
                            value={draftDuration}
                            disabled={busy || jobActive}
                            className="font-mono"
                            onChange={(e) => setDraftDuration(e.target.value)}
                            inputMode="decimal"
                          />
                        </label>
                      </div>
                      <div className="pt-1">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          disabled={busy || jobActive}
                          onClick={onSavePrompt}
                        >
                          Save prompt
                        </Button>
                      </div>
                    </div>
                  ) : null}

                  {tab === "run" ? (
                    <div className="tab-panel flex flex-col gap-4">
                      {h3Provider === "local" ? (
                        <VideoContextPanel
                          key={selected.id}
                          shot={selected}
                          shots={shots}
                          onShotUpdated={replaceShot}
                        />
                      ) : null}
                      {h3Provider === "local" ? resolutionPicker : null}

                      <ol className="run-steps grid gap-2 sm:grid-cols-3">
                        {[
                          {
                            label: "Refs cast",
                            done: (selected.refs?.length ?? 0) > 0,
                          },
                          {
                            label: "Six-section prompt saved",
                            done: promptReady(selected.prompt_sections),
                          },
                          {
                            label: "Submit H3 Ref2AV",
                            done:
                              selected.status === "queued" ||
                              selected.status === "running" ||
                              selected.status === "succeeded",
                          },
                        ].map((step) => (
                          <li
                            key={step.label}
                            className={cn(
                              "flex items-center gap-2 rounded-md border px-3 py-2 text-xs font-medium",
                              step.done
                                ? "done border-status-ok/30 bg-status-ok-soft text-status-ok"
                                : "border-border-subtle bg-elevated/40 text-ink-secondary",
                            )}
                          >
                            {step.done ? (
                              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                            ) : (
                              <Circle
                                className="h-3.5 w-3.5 shrink-0 text-ink-muted"
                                aria-hidden="true"
                              />
                            )}
                            <span>{step.label}</span>
                          </li>
                        ))}
                      </ol>

                      {!canSubmit && !jobActive ? (
                        <p className="text-xs text-ink-muted">
                          Need at least one ref and a complete prompt (all six sections non-empty).
                          Save prompt on the Prompt tab first.
                        </p>
                      ) : null}

                      {h3Job ? (
                        <div className="run-job-box flex flex-col gap-3 rounded-lg border border-border-subtle bg-elevated/40 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <Badge
                              tone={jobStatusTone(h3Job.status)}
                              className={cn("status-pill", `status-${h3Job.status}`)}
                            >
                              <StatusDot
                                tone={jobStatusTone(h3Job.status)}
                                pulse={jobActive}
                              />
                              {h3Job.status}
                            </Badge>
                            <span className="job-id font-mono text-[11px] text-ink-muted">
                              {h3Job.id}
                            </span>
                          </div>
                          <div className="text-xs text-ink-secondary">
                            Provider:{" "}
                            {h3Job.h3_provider === "minimax"
                              ? "MiniMax · Official API"
                              : "Local · ComfyUI"}
                          </div>
                          {h3Job.error ? <Banner tone="danger">{h3Job.error}</Banner> : null}
                          {h3Job.outputs?.video?.url || h3Job.outputs?.master?.url ? (
                            <video
                              className="h3-preview max-h-[420px] w-full rounded-md border border-border-subtle bg-black"
                              controls
                              src={
                                (h3Job.outputs.video?.url ||
                                  h3Job.outputs.master?.url) as string
                              }
                            />
                          ) : null}
                        </div>
                      ) : (
                        <p className="rounded-md border border-dashed border-border-subtle bg-elevated/30 px-3 py-3 text-xs text-ink-muted">
                          No H3 job yet for this shot.
                        </p>
                      )}

                      {providerPicker}

                      <div className="production-submit-actions flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
                        <Button
                          type="button"
                          variant="primary"
                          disabled={busy || !canSubmit || jobActive || managedActive}
                          onClick={onSubmit}
                        >
                          <Play className="h-3.5 w-3.5" aria-hidden="true" />
                          {jobActive ? "H3 running…" : "Submit H3"}
                        </Button>
                        {jobActive ? (
                          <Button
                            type="button"
                            variant="danger"
                            disabled={busy}
                            onClick={onCancelJob}
                          >
                            <Square className="h-3 w-3" aria-hidden="true" />
                            Cancel
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              </Panel>
            )}
          </section>
        </div>
      </div>

      {lightbox}
    </PageShell>
  );
}
