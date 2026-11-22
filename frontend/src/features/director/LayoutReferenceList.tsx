import { useState } from "react";
import { Plus } from "lucide-react";
import {
  layoutPreviewUrl,
  type LayoutReference,
  type LayoutReviewStatus,
  type Shot,
} from "../../shared/api/types";
import { isDisplayableLayout, isRetiredLayout } from "../../shared/layoutReferenceStatus";
import { Badge, Button, Textarea, cn } from "../../shared/ui";

interface LayoutReferenceListProps {
  shot: Shot;
  busy: boolean;
  onDiscussAddReference: (description: string) => void;
  onOpenImage: (assetId: string) => void;
}

function isTerminalJobFailure(layout: LayoutReference): boolean {
  return layout.job_status === "failed" || layout.job_status === "cancelled";
}

function reviewLabel(status: LayoutReviewStatus | null, layout: LayoutReference): string {
  if (layout.job_status === "failed") return "generation failed";
  if (layout.job_status === "cancelled") return "generation cancelled";
  if (layout.superseded_by) return "previous";
  if (status === "reject") return "not used";
  if (layout.asset_id) return "available";
  if (!status) return "awaiting generation";
  return status.replace(/_/g, " ");
}

function reviewTone(status: LayoutReviewStatus | null, layout: LayoutReference) {
  if (isTerminalJobFailure(layout)) return "danger" as const;
  if (layout.superseded_by || status === "reject") return "neutral" as const;
  if (layout.asset_id) return "ok" as const;
  return "warn" as const;
}

function shouldDefaultExpand(layout: LayoutReference): boolean {
  if (isTerminalJobFailure(layout)) return true;
  if (layout.job_status === "queued" || layout.job_status === "running") return true;
  return (
    !layout.review_status ||
    layout.review_status === "pending_review" ||
    layout.review_status === "usable_with_repair"
  );
}

function LayoutReferenceCard({
  shot,
  layout,
  onOpenImage,
}: {
  shot: Shot;
  layout: LayoutReference;
  onOpenImage: (assetId: string) => void;
}) {
  const picture = layout.asset_id
    ? shot.refs.find(
        (ref) => ref.role === "layout_ref_frame" && ref.asset_id === layout.asset_id,
      )
    : undefined;
  const status = layout.review_status;
  const terminalJobFailure = isTerminalJobFailure(layout);
  const imageUrl = layoutPreviewUrl(layout.asset_id);
  const tone = reviewTone(status, layout);

  return (
    <article
      className="layout-reference-card overflow-hidden rounded-lg border border-border-subtle bg-panel text-ink shadow-[var(--shadow-card)]"
      aria-labelledby={`layout-${layout.id}-title`}
    >
      <details className="layout-reference-details group" open={shouldDefaultExpand(layout)}>
        <summary className="layout-reference-summary flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-xs transition-colors hover:bg-hover/50">
          <span className="layout-reference-kicker shrink-0 font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
            Layout
          </span>
          <h3
            id={`layout-${layout.id}-title`}
            className="layout-reference-title min-w-0 flex-1 truncate text-xs font-semibold text-ink"
          >
            {layout.purpose || layout.id}
          </h3>
          <Badge tone={tone} className={cn("gate-badge", `layout-review-${status || "queued"}`)}>
            {reviewLabel(status, layout)}
          </Badge>
          {picture ? (
            <Badge tone="accent" className="layout-picture-badge font-mono">
              Picture {picture.picture_index}
            </Badge>
          ) : null}
        </summary>
        <div className="layout-reference-body space-y-3 border-t border-border-subtle bg-elevated/30 p-3 text-xs">
          <div className="layout-reference-rail flex flex-wrap items-center gap-x-3 gap-y-1 border-b-0 text-[11px] text-ink-muted">
            <span>{layout.time_hint || "time not specified"}</span>
            <span>
              {layout.source_refs.length} source{layout.source_refs.length === 1 ? "" : "s"}
            </span>
            {layout.origin?.kind === "clip_tail_frame" ? (
              <span className="layout-origin-label font-mono text-ink-secondary">
                {`Tail frame · ${layout.origin.source_shot_id} · v${layout.origin.source_generation} · ${layout.origin.output_kind}`}
              </span>
            ) : null}
          </div>
          <div className="layout-reference-evidence grid gap-3 sm:grid-cols-[140px_minmax(0,1fr)]">
            {imageUrl ? (
              <button
                type="button"
                className="layout-reference-image group/img overflow-hidden rounded-md border border-border-subtle bg-canvas transition-colors hover:border-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent cursor-pointer"
                onClick={() => layout.asset_id && onOpenImage(layout.asset_id)}
                aria-label={`Open ${layout.purpose || "layout reference"}`}
              >
                <img
                  src={imageUrl}
                  alt={layout.purpose || "layout reference"}
                  className="aspect-video w-full object-cover transition-transform duration-200 group-hover/img:scale-[1.02]"
                />
              </button>
            ) : (
              <div className="layout-reference-empty flex aspect-video items-center justify-center rounded-md border border-dashed border-border-subtle bg-elevated px-2 text-center text-[11px] text-ink-muted">
                {terminalJobFailure
                  ? layout.job_status === "cancelled"
                    ? "Reference generation cancelled"
                    : "Reference generation failed"
                  : layout.job_id
                    ? "Generating reference frame"
                    : "No reference frame yet"}
              </div>
            )}
            <p className="layout-reference-state leading-relaxed text-ink-secondary">
              {layout.state_description || "No state description provided."}
            </p>
          </div>
          <div className="layout-reference-controls flex flex-wrap items-center gap-2 border-t border-border-subtle pt-2.5 text-[11px]">
            <Badge tone={tone} className={cn("gate-badge", `layout-review-${status || "queued"}`)}>
              {reviewLabel(status, layout)}
            </Badge>
            <span className="layout-job font-mono text-ink-muted">
              {layout.job_id ? `Job ${layout.job_id}` : "No generation job"}
            </span>
            {terminalJobFailure ? (
              <p
                className="layout-action-error w-full rounded-md border border-status-danger/30 bg-status-danger-soft px-2.5 py-1.5 text-xs text-status-danger"
                role="alert"
              >
                {layout.job_error ||
                  (layout.job_status === "cancelled"
                    ? "Layout generation was cancelled. Add a new reference frame to try again."
                    : "Layout generation failed. Add a new reference frame to try again.")}
              </p>
            ) : null}
            {layout.review_feedback ? (
              <p className="layout-feedback w-full text-xs leading-relaxed text-ink-secondary">
                {layout.review_feedback}
              </p>
            ) : null}
            {layout.feedback_source === "director_chat" ? (
              <span
                className="layout-feedback-source rounded border border-border-subtle bg-panel px-1.5 py-0.5 text-[10px] font-medium text-ink-muted"
                title={layout.feedback_quote || undefined}
              >
                From Director chat
              </span>
            ) : null}
            {status === "reject" ? (
              <span className="layout-selection-hint text-ink-muted">
                This Layout is not used.
              </span>
            ) : picture ? (
              <span className="layout-selection-hint text-status-ok">
                Used automatically in prompt and H3
              </span>
            ) : layout.asset_id ? (
              <span className="layout-selection-hint text-ink-muted">
                Previous Layout — not submitted
              </span>
            ) : null}
          </div>
        </div>
      </details>
    </article>
  );
}

export function LayoutReferenceList({
  shot,
  busy,
  onDiscussAddReference,
  onOpenImage,
}: LayoutReferenceListProps) {
  const layouts = shot.layout_refs;
  const displayableLayouts = layouts.filter(isDisplayableLayout);
  const currentLayouts = displayableLayouts.filter((layout) => !isRetiredLayout(layout));
  const retiredLayouts = displayableLayouts.filter(isRetiredLayout);
  const [adding, setAdding] = useState(false);
  const [description, setDescription] = useState("");

  const discuss = () => {
    const trimmed = description.trim();
    if (!trimmed) return;
    onDiscussAddReference(trimmed);
    setDescription("");
    setAdding(false);
  };

  return (
    <section className="layout-reference-list space-y-2.5" aria-label="Layout references">
      {currentLayouts.map((layout) => (
        <LayoutReferenceCard
          key={layout.id}
          shot={shot}
          layout={layout}
          onOpenImage={onOpenImage}
        />
      ))}
      {retiredLayouts.length ? (
        <details className="layout-history rounded-lg border border-border-subtle bg-elevated/40">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-ink-secondary hover:text-ink">
            Previous layouts ({retiredLayouts.length})
          </summary>
          <div className="layout-history-list space-y-2 border-t border-border-subtle p-2.5">
            {retiredLayouts.map((layout) => (
              <LayoutReferenceCard
                key={layout.id}
                shot={shot}
                layout={layout}
                onOpenImage={onOpenImage}
              />
            ))}
          </div>
        </details>
      ) : null}
      {layouts.length > 0 ? (
        <div className="layout-add-reference space-y-2.5 pt-1">
          <Button
            type="button"
            variant="secondary"
            size="xs"
            className="mode-chip"
            disabled={busy}
            aria-expanded={adding}
            onClick={() => setAdding((open) => !open)}
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            Add reference frame
          </Button>
          {adding ? (
            <div className="layout-brief-form space-y-2.5 rounded-lg border border-border-subtle bg-elevated/50 p-3">
              <label className="flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                Description
                <Textarea
                  rows={3}
                  value={description}
                  disabled={busy}
                  placeholder="What should an additional reference frame help establish?"
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <div className="flex justify-end">
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  disabled={busy || !description.trim()}
                  onClick={discuss}
                >
                  Discuss with Director
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
