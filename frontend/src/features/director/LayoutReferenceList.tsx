import { useState } from "react";
import {
  layoutPreviewUrl,
  type LayoutReference,
  type LayoutReviewStatus,
  type Shot,
} from "../../shared/api/types";
import { isDisplayableLayout, isRetiredLayout } from "../../shared/layoutReferenceStatus";

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

function shouldDefaultExpand(layout: LayoutReference): boolean {
  if (isTerminalJobFailure(layout)) return true;
  if (layout.job_status === "queued" || layout.job_status === "running") return true;
  return !layout.review_status
    || layout.review_status === "pending_review"
    || layout.review_status === "usable_with_repair";
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

  return (
    <article className="layout-reference-card" aria-labelledby={`layout-${layout.id}-title`}>
      <details className="layout-reference-details" open={shouldDefaultExpand(layout)}>
        <summary className="layout-reference-summary">
          <span className="layout-reference-kicker">Layout</span>
          <h3 id={`layout-${layout.id}-title`} className="layout-reference-title">
            {layout.purpose || layout.id}
          </h3>
          <span className={`gate-badge layout-review-${status || "queued"}`}>
            {reviewLabel(status, layout)}
          </span>
          {picture ? <span className="layout-picture-badge">Picture {picture.picture_index}</span> : null}
        </summary>
        <div className="layout-reference-body">
          <div className="layout-reference-rail">
            <span>{layout.time_hint || "time not specified"}</span>
            <span>{layout.source_refs.length} source{layout.source_refs.length === 1 ? "" : "s"}</span>
            {layout.origin?.kind === "clip_tail_frame" ? (
              <span className="layout-origin-label">
                {`Tail frame · ${layout.origin.source_shot_id} · v${layout.origin.source_generation} · ${layout.origin.output_kind}`}
              </span>
            ) : null}
          </div>
          <div className="layout-reference-evidence">
            {imageUrl ? (
              <button
                type="button"
                className="layout-reference-image"
                onClick={() => layout.asset_id && onOpenImage(layout.asset_id)}
                aria-label={`Open ${layout.purpose || "layout reference"}`}
              >
                <img src={imageUrl} alt={layout.purpose || "layout reference"} />
              </button>
            ) : (
              <div className="layout-reference-empty">
                {terminalJobFailure
                  ? layout.job_status === "cancelled"
                    ? "Reference generation cancelled"
                    : "Reference generation failed"
                  : layout.job_id ? "Generating reference frame" : "No reference frame yet"}
              </div>
            )}
            <p className="layout-reference-state">{layout.state_description || "No state description provided."}</p>
          </div>
          <div className="layout-reference-controls">
            <span className={`gate-badge layout-review-${status || "queued"}`}>
              {reviewLabel(status, layout)}
            </span>
            <span className="layout-job">{layout.job_id ? `Job ${layout.job_id}` : "No generation job"}</span>
            {terminalJobFailure ? (
              <p className="layout-action-error" role="alert">
                {layout.job_error || (layout.job_status === "cancelled"
                  ? "Layout generation was cancelled. Add a new reference frame to try again."
                  : "Layout generation failed. Add a new reference frame to try again.")}
              </p>
            ) : null}
            {layout.review_feedback ? <p className="layout-feedback">{layout.review_feedback}</p> : null}
            {layout.feedback_source === "director_chat" ? (
              <span className="layout-feedback-source" title={layout.feedback_quote || undefined}>
                From Director chat
              </span>
            ) : null}
            {status === "reject" ? (
              <span className="layout-selection-hint">This Layout is not used.</span>
            ) : picture ? (
              <span className="layout-selection-hint">Used automatically in prompt and H3</span>
            ) : layout.asset_id ? (
              <span className="layout-selection-hint">Previous Layout — not submitted</span>
            ) : null}
          </div>
        </div>
      </details>
    </article>
  );
}

export function LayoutReferenceList({ shot, busy, onDiscussAddReference, onOpenImage }: LayoutReferenceListProps) {
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
    <section className="layout-reference-list" aria-label="Layout references">
      {currentLayouts.map((layout) => (
        <LayoutReferenceCard
          key={layout.id}
          shot={shot}
          layout={layout}
          onOpenImage={onOpenImage}
        />
      ))}
      {retiredLayouts.length ? (
        <details className="layout-history">
          <summary>Previous layouts ({retiredLayouts.length})</summary>
          <div className="layout-history-list">
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
        <div className="layout-add-reference">
          <button
            type="button"
            className="mode-chip"
            disabled={busy}
            aria-expanded={adding}
            onClick={() => setAdding((open) => !open)}
          >
            Add reference frame
          </button>
          {adding ? (
            <div className="layout-brief-form">
              <label>
                Description
                <textarea
                  rows={3}
                  value={description}
                  disabled={busy}
                  placeholder="What should an additional reference frame help establish?"
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <button type="button" className="btn primary" disabled={busy || !description.trim()} onClick={discuss}>
                Discuss with Director
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
