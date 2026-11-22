import { useEffect, useMemo, useRef, useState } from "react";
import { Clapperboard, Pencil, Sparkles } from "lucide-react";
import {
  PROMPT_SECTION_KEYS,
  layoutPreviewUrl,
  refPreviewCandidates,
  type Shot,
  type ShotRef,
} from "../../shared/api/types";
import { shotWorkflowStatus } from "../../shared/shotWorkflowStatus";
import { Banner, Button, EmptyState, StatusDot, cn } from "../../shared/ui";
import { LayoutReferenceList } from "./LayoutReferenceList";
import { ShotMaterialEditor } from "./ShotMaterialEditor";
import { materialReviewMessage } from "./materialReview";
import { VideoContextPanel } from "./VideoContextPanel";

const DOCUMENT_SECTIONS = [
  { id: "shot-brief", label: "Brief" },
  { id: "shot-references", label: "References" },
  { id: "shot-layouts", label: "Layouts" },
  { id: "shot-prompt", label: "Prompt" },
  { id: "shot-production", label: "Production" },
];

function statusTone(key: string): {
  pill: string;
  dot: "neutral" | "accent" | "ok" | "warn" | "danger" | "info";
} {
  switch (key) {
    case "ready-for-h3":
    case "h3-succeeded":
      return {
        pill: "border-status-ok/30 bg-status-ok-soft text-status-ok",
        dot: "ok",
      };
    case "h3-running":
    case "h3-queued":
      return {
        pill: "border-status-info/30 bg-status-info-soft text-status-info",
        dot: "info",
      };
    case "prompt-stale":
      return {
        pill: "border-status-warn/30 bg-status-warn-soft text-status-warn",
        dot: "warn",
      };
    case "blocked":
    case "h3-failed":
      return {
        pill: "border-status-danger/30 bg-status-danger-soft text-status-danger",
        dot: "danger",
      };
    default:
      return {
        pill: "border-border-subtle bg-elevated text-ink-secondary",
        dot: "neutral",
      };
  }
}

function referenceLabel(role: string) {
  return role.replaceAll("_", " ");
}

function normalizedDialogueText(value: string) {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[“”‘’'"]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function dialogueSpeaker(shot: Shot, line: string) {
  const lineKey = normalizedDialogueText(line);
  const directMatches = shot.voice_refs.filter(
    (voice) =>
      voice.speaker.trim() && normalizedDialogueText(voice.notes || "").includes(lineKey),
  );
  if (directMatches.length === 1) return directMatches[0].speaker.trim();

  const beat = shot.script_beat.toLocaleLowerCase();
  const linePosition = beat.indexOf(line.toLocaleLowerCase());
  if (linePosition >= 0) {
    const prefix = beat.slice(0, linePosition);
    const nearest = shot.voice_refs
      .map((voice) => ({
        speaker: voice.speaker.trim(),
        position: prefix.lastIndexOf(voice.speaker.trim().toLocaleLowerCase()),
      }))
      .filter((candidate) => candidate.speaker && candidate.position >= 0)
      .sort((left, right) => right.position - left.position)[0];
    if (nearest) return nearest.speaker;
  }

  const speakers = [
    ...new Set(shot.voice_refs.map((voice) => voice.speaker.trim()).filter(Boolean)),
  ];
  return speakers.length === 1 ? speakers[0] : "";
}

function ReferenceThumb({
  refItem,
  onOpen,
}: {
  refItem: ShotRef;
  onOpen: (url: string) => void;
}) {
  const candidates = useMemo(() => refPreviewCandidates(refItem), [refItem]);
  const [index, setIndex] = useState(0);
  const url = candidates[index] || null;
  return (
    <div
      className={cn(
        "shot-ref-thumb group flex flex-col overflow-hidden rounded-md border border-border-subtle bg-elevated/50",
        !url && "empty",
      )}
    >
      {url ? (
        <button
          type="button"
          onClick={() => onOpen(url)}
          className="aspect-square w-full cursor-pointer overflow-hidden bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <img
            src={url}
            alt={referenceLabel(refItem.role)}
            onError={() => setIndex((current) => current + 1)}
            className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
          />
        </button>
      ) : (
        <span className="flex aspect-square items-center justify-center px-2 text-center text-[11px] text-ink-muted">
          no preview
        </span>
      )}
      <span className="shot-ref-role truncate border-t border-border-subtle px-2 py-1 font-mono text-[10px] text-ink-secondary">
        P{refItem.picture_index} · {referenceLabel(refItem.role)}
      </span>
    </div>
  );
}

export function ShotWorkspace({
  shots,
  busy,
  onSend,
  onSelectShot,
  onShotUpdated,
  onOpenImage,
  focusShotId = null,
}: {
  shots: Shot[];
  busy: boolean;
  onSend: (message: string) => void;
  onSelectShot?: (shot: Shot) => void;
  onShotUpdated?: (shot: Shot) => void;
  onOpenImage: (url: string) => void;
  focusShotId?: string | null;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(shots[0]?.id ?? null);
  const [materialEditorOpen, setMaterialEditorOpen] = useState(false);
  const appliedFocus = useRef<string | null>(null);
  const selectedIndex = Math.max(0, shots.findIndex((shot) => shot.id === selectedId));
  const selected = shots[selectedIndex] ?? null;

  useEffect(() => {
    if ((!selectedId || !shots.some((shot) => shot.id === selectedId)) && shots[0]) {
      setSelectedId(shots[0].id);
    }
  }, [selectedId, shots]);

  useEffect(() => {
    if (!focusShotId || appliedFocus.current === focusShotId) return;
    if (!shots.some((shot) => shot.id === focusShotId)) return;
    appliedFocus.current = focusShotId;
    setSelectedId(focusShotId);
  }, [focusShotId, shots]);

  const selectShot = (shot: Shot) => {
    setSelectedId(shot.id);
    onSelectShot?.(shot);
  };

  const selectedStage = selected ? shotWorkflowStatus(selected) : null;
  const selectedTone = selectedStage ? statusTone(selectedStage.key) : null;

  return (
    <section
      className="panel director-shots-panel flex h-full max-h-none min-h-0 flex-col overflow-hidden rounded-lg border border-border-subtle bg-panel p-0 text-ink shadow-[var(--shadow-card)]"
      aria-label="Shot workspace"
    >
      <header className="shot-workspace-header workspace-panel-header flex min-h-[54px] shrink-0 items-center justify-between border-b border-border-subtle bg-panel/90 px-4 py-2">
        <div className="shot-workspace-identity flex items-center gap-2.5">
          <img
            className="storyboard-shot-board h-7 w-7 rounded-md border border-border-subtle bg-elevated object-cover"
            src="/storyboard-shot-board.png"
            alt="Storyboard shot board"
          />
          <div>
            <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-ink">Shots</h2>
          </div>
        </div>
      </header>

      {shots.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={<Clapperboard className="h-4 w-4" aria-hidden="true" />}
            title="No shots yet"
            description="No shots yet. Ask the Director to plan them in chat."
            className="w-full max-w-sm"
          />
        </div>
      ) : selected && selectedStage && selectedTone ? (
        <div className="shot-workspace-body grid min-h-0 flex-1 grid-cols-[120px_minmax(0,1fr)] lg:grid-cols-[188px_minmax(0,1fr)]">
          <nav
            className="shot-bookmarks min-h-0 space-y-1 overflow-y-auto border-r border-border-subtle bg-elevated/35 p-2"
            aria-label="Shots"
          >
            {shots.map((shot, index) => {
              const stage = shotWorkflowStatus(shot);
              const tone = statusTone(stage.key);
              const isSelected = shot.id === selected.id;
              return (
                <button
                  key={shot.id}
                  type="button"
                  aria-label={`Shot ${index + 1} · ${shot.title}`}
                  aria-current={isSelected ? "page" : undefined}
                  className={cn(
                    "flex w-full cursor-pointer items-start gap-2 rounded-md border px-2 py-2 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                    isSelected
                      ? "active border-border-subtle bg-panel text-ink shadow-[var(--shadow-card)]"
                      : "border-transparent text-ink-secondary hover:bg-hover/65 hover:text-ink",
                  )}
                  onClick={() => selectShot(shot)}
                >
                  <span className="shot-bookmark-index mt-0.5 shrink-0 font-mono text-[10px] font-medium text-ink-muted tabular-nums">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="shot-bookmark-copy min-w-0 flex-1">
                    <strong className="block truncate text-xs font-medium text-ink">
                      {shot.title}
                    </strong>
                    <small
                      title={stage.label}
                      className="mt-0.5 flex items-center gap-1.5 text-[10px] text-ink-muted"
                    >
                      <StatusDot tone={tone.dot} />
                      Shot {index + 1}
                    </small>
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="shot-detail flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <div className="shot-detail-panel shot-detail-panel-clapperboard min-h-0 flex-1 overflow-y-auto bg-canvas [background-image:none] p-3 sm:p-4 scroll-smooth">
              <article
                className="shot-document mx-auto max-w-3xl space-y-5 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)] sm:p-5"
                aria-label={`${selected.title} shot design document`}
              >
                <header className="shot-detail-header flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle pb-3.5">
                  <div className="min-w-0">
                    <div className="workspace-kicker font-mono text-[11px] font-medium uppercase tracking-wider text-ink-muted">
                      Shot {String(selectedIndex + 1).padStart(2, "0")}
                    </div>
                    <h3 className="mt-0.5 text-[15px] font-semibold tracking-[-0.015em] text-ink">
                      {selectedIndex + 1}. {selected.title}
                    </h3>
                  </div>
                  <div className="shot-detail-header-actions flex shrink-0 items-center gap-2">
                    <span
                      className={cn(
                        "status-chip inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium leading-tight whitespace-nowrap",
                        `status-${selectedStage.key}`,
                        selectedTone.pill,
                      )}
                    >
                      <StatusDot tone={selectedTone.dot} />
                      {selectedStage.label}
                    </span>
                  </div>
                </header>

                <VideoContextPanel
                  key={selected.id}
                  shot={selected}
                  shots={shots}
                  expanded={focusShotId === selected.id}
                  onShotUpdated={onShotUpdated}
                />

                <nav
                  className="shot-document-nav flex flex-wrap items-center gap-1 border-b border-border-subtle pb-2.5"
                  aria-label="Shot document sections"
                >
                  {DOCUMENT_SECTIONS.map((section) => (
                    <a
                      key={section.id}
                      href={`#${section.id}`}
                      className="rounded-md px-2 py-1 text-[11px] font-medium text-ink-secondary transition-colors hover:bg-hover hover:text-ink"
                    >
                      {section.label}
                    </a>
                  ))}
                </nav>

                <dl className="shot-document-meta grid grid-cols-3 gap-2 rounded-md border border-border-subtle bg-elevated/45 px-3 py-2 text-xs">
                  <div>
                    <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      Duration
                    </dt>
                    <dd className="mt-0.5 font-mono font-medium text-ink tabular-nums">
                      {selected.duration_s}s
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      Scene
                    </dt>
                    <dd className="mt-0.5 truncate font-mono font-medium text-ink">
                      {selected.scene_id}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      Status
                    </dt>
                    <dd className="mt-0.5 truncate font-medium text-ink">
                      {selectedStage.label}
                    </dd>
                  </div>
                </dl>

                <section
                  id="shot-brief"
                  className="shot-document-section shot-brief-panel space-y-3 border-t border-border-subtle pt-4"
                >
                  <header className="shot-document-section-header flex items-baseline gap-2">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      01 / Direction
                    </span>
                    <h4 className="text-xs font-semibold text-ink">Creative brief</h4>
                  </header>
                  <p className="shot-beat font-sans text-[13px] leading-relaxed text-ink">
                    {selected.script_beat || "No creative brief written yet."}
                  </p>
                  <dl className="shot-design-metadata grid grid-cols-2 gap-2 rounded-md border border-border-subtle bg-elevated/35 p-2.5 text-xs sm:grid-cols-4">
                    <div>
                      <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Duration
                      </dt>
                      <dd className="mt-0.5 font-mono text-ink tabular-nums">
                        {selected.duration_s}s
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Framing
                      </dt>
                      <dd className="mt-0.5 text-ink">{selected.shot_type || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Angle
                      </dt>
                      <dd className="mt-0.5 text-ink">{selected.camera_angle || "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Motion
                      </dt>
                      <dd className="mt-0.5 text-ink">{selected.camera_motion || "—"}</dd>
                    </div>
                  </dl>
                  {selected.composition ? (
                    <div className="shot-design-note rounded-md border border-border-subtle bg-elevated/35 px-3 py-2 text-xs">
                      <span className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Composition
                      </span>
                      <p className="mt-1 leading-relaxed text-ink-secondary">
                        {selected.composition}
                      </p>
                    </div>
                  ) : null}
                  {selected.dialogue.length ? (
                    <div
                      className="shot-dialogue space-y-1.5 rounded-md border border-border-subtle bg-elevated/35 px-3 py-2.5 text-xs"
                      aria-label="Dialogue"
                    >
                      {selected.dialogue.map((line, index) => {
                        const speaker = dialogueSpeaker(selected, line);
                        return (
                          <p
                            key={`${index}-${line}`}
                            className="flex items-baseline text-ink-secondary"
                          >
                            {speaker ? (
                              <strong className="mr-2 shrink-0 font-semibold text-ink">
                                {speaker}
                              </strong>
                            ) : null}
                            <span>{line}</span>
                          </p>
                        );
                      })}
                    </div>
                  ) : null}
                  {selectedStage.key === "blocked" && selected.blocked_reasons.length ? (
                    <Banner tone="danger" className="banner error">
                      {selected.blocked_reasons.join("; ")}
                    </Banner>
                  ) : null}
                </section>

                <section
                  id="shot-references"
                  className="shot-document-section shot-references-panel space-y-3 border-t border-border-subtle pt-4"
                >
                  <header className="shot-document-section-header flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      02 / Continuity
                    </span>
                    <h4 className="text-xs font-semibold text-ink">Cast &amp; continuity</h4>
                    <div className="shot-reference-header-actions ml-auto flex items-center gap-2">
                      <small className="font-mono text-[11px] text-ink-muted tabular-nums">
                        {selected.refs.length} assigned
                      </small>
                      <Button
                        type="button"
                        variant="secondary"
                        size="xs"
                        className="mode-chip shot-material-edit-trigger"
                        disabled={busy}
                        onClick={() => setMaterialEditorOpen(true)}
                      >
                        <Pencil className="h-3 w-3" aria-hidden="true" />
                        Edit materials
                      </Button>
                    </div>
                  </header>
                  {selected.refs.length ? (
                    <div className="shot-refs-strip grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4">
                      {[...selected.refs]
                        .sort((a, b) => a.picture_index - b.picture_index)
                        .map((ref) => (
                          <ReferenceThumb
                            key={`${ref.role}-${ref.asset_id}-${ref.picture_index}`}
                            refItem={ref}
                            onOpen={onOpenImage}
                          />
                        ))}
                    </div>
                  ) : (
                    <p className="empty-copy text-xs text-ink-muted">
                      No assets cast for this Shot.
                    </p>
                  )}
                </section>

                <section
                  id="shot-layouts"
                  className="shot-document-section space-y-3 border-t border-border-subtle pt-4"
                >
                  <header className="shot-document-section-header flex items-baseline gap-2">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      03 / Visual studies
                    </span>
                    <h4 className="text-xs font-semibold text-ink">Layout studies</h4>
                  </header>
                  {selected.layout_refs.length ? (
                    <LayoutReferenceList
                      shot={selected}
                      busy={busy}
                      onDiscussAddReference={(description) => {
                        const ids =
                          selected.layout_refs.map((layout) => layout.id).join(", ") || "none";
                        onSend(
                          `I want to discuss adding another reference frame for shot "${selected.title}" (${selected.id}). ` +
                            `Inspect the existing Layout images for this shot (LayoutReference IDs: ${ids}) and read my description: ${description}\n\n` +
                            "First explain whether another Layout is useful, what distinct visual state it should establish, and which 1–3 real source assets would best support it. Ask about any ambiguity. Do not queue generation yet; start the discussion with me.",
                        );
                      }}
                      onOpenImage={(assetId) => {
                        const url = layoutPreviewUrl(assetId);
                        if (url) onOpenImage(url);
                      }}
                    />
                  ) : (
                    <div className="shot-document-empty flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed border-border-subtle bg-elevated/35 px-3.5 py-3">
                      <p className="text-xs text-ink-muted">Layout is optional for H3.</p>
                      <Button
                        type="button"
                        variant="secondary"
                        size="xs"
                        className="mode-chip"
                        disabled={busy}
                        onClick={() =>
                          onSend(
                            `I want to discuss whether a reference frame would help shot "${selected.title}" (${selected.id}). ` +
                              "No Layout exists for this shot. Ask what distinct visual state I want and which references should control it. " +
                              "Do not queue generation yet; start the discussion with me.",
                          )
                        }
                      >
                        <Sparkles className="h-3 w-3" aria-hidden="true" />
                        Discuss a Layout
                      </Button>
                    </div>
                  )}
                </section>

                <section
                  id="shot-prompt"
                  className="shot-document-section space-y-3 border-t border-border-subtle pt-4"
                >
                  <header className="shot-document-section-header flex items-baseline gap-2">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      04 / Model direction
                    </span>
                    <h4 className="text-xs font-semibold text-ink">Generation prompt</h4>
                  </header>
                  <div className="shot-prompt-panel space-y-2.5">
                    {PROMPT_SECTION_KEYS.map(({ key, label }) =>
                      selected.prompt_sections[key] ? (
                        <section
                          key={key}
                          className="rounded-md border border-border-subtle bg-elevated/35 px-3 py-2"
                        >
                          <h5 className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                            {label}
                          </h5>
                          <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-ink">
                            {selected.prompt_sections[key]}
                          </p>
                        </section>
                      ) : null,
                    )}
                    {!Object.values(selected.prompt_sections).some(Boolean) ? (
                      <p className="empty-copy text-xs text-ink-muted">
                        No H3 prompt written yet.
                      </p>
                    ) : null}
                  </div>
                </section>

                <section
                  id="shot-production"
                  className="shot-document-section space-y-3 border-t border-border-subtle pt-4"
                >
                  <header className="shot-document-section-header flex items-baseline gap-2">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      05 / Record
                    </span>
                    <h4 className="text-xs font-semibold text-ink">Production record</h4>
                  </header>
                  <div className="shot-run-panel grid grid-cols-1 gap-2 rounded-md border border-border-subtle bg-elevated/35 p-3 text-xs sm:grid-cols-3">
                    <div>
                      <span className="block text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Reference job
                      </span>
                      <strong className="mt-0.5 block truncate font-mono text-ink">
                        {selected.ref_frame_job_id || "Not queued"}
                      </strong>
                    </div>
                    <div>
                      <span className="block text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Video job
                      </span>
                      <strong className="mt-0.5 block truncate font-mono text-ink">
                        {selected.h3_job_id || "Not queued"}
                      </strong>
                    </div>
                    <div>
                      <span className="block text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                        Status
                      </span>
                      <strong className="mt-0.5 block truncate font-medium text-ink">
                        {selectedStage.label}
                      </strong>
                    </div>
                  </div>
                </section>
              </article>
            </div>
          </div>

          {materialEditorOpen ? (
            <ShotMaterialEditor
              shot={selected}
              shots={shots}
              shotNumber={selectedIndex + 1}
              onClose={() => setMaterialEditorOpen(false)}
              onOpenImage={onOpenImage}
              onSaved={(updated, message, notifyAgent) => {
                onShotUpdated?.(updated);
                if (notifyAgent) {
                  onSend(materialReviewMessage(updated, selectedIndex + 1, message));
                }
              }}
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
