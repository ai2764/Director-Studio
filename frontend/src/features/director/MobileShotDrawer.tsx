import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  layoutPreviewUrl,
  refPreviewCandidates,
  type LayoutReference,
  type Shot,
  type ShotRef,
} from "../../shared/api/types";
import { isDisplayableLayout, isRetiredLayout } from "../../shared/layoutReferenceStatus";
import { shotWorkflowStatus } from "../../shared/shotWorkflowStatus";
import { cn } from "../../shared/ui";
import { VideoContextPanel } from "./VideoContextPanel";

function referenceLabel(role: string) {
  return role.replaceAll("_", " ");
}

function MobileReferenceThumb({
  refItem,
  onOpen,
}: {
  refItem: ShotRef;
  onOpen: (url: string) => void;
}) {
  const candidates = useMemo(() => refPreviewCandidates(refItem), [refItem]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const url = candidates[candidateIndex] || null;
  return (
    <div className="mobile-reference-thumb flex w-24 shrink-0 flex-col gap-1">
      {url ? (
        <button
          type="button"
          onClick={() => onOpen(url)}
          className="aspect-square w-full cursor-pointer overflow-hidden rounded-md border border-border-subtle bg-canvas"
        >
          <img
            src={url}
            alt={referenceLabel(refItem.role)}
            onError={() => setCandidateIndex((current) => current + 1)}
            className="h-full w-full object-cover"
          />
        </button>
      ) : (
        <span className="mobile-reference-empty flex aspect-square items-center justify-center rounded-md border border-dashed border-border-subtle bg-elevated text-[11px] text-ink-muted">
          No preview
        </span>
      )}
      <small className="truncate font-mono text-[10px] text-ink-muted">
        {referenceLabel(refItem.role)}
      </small>
    </div>
  );
}

function MobileLayoutCard({
  layout,
  onOpen,
}: {
  layout: LayoutReference;
  onOpen: (url: string) => void;
}) {
  const url = layout.asset_id ? layoutPreviewUrl(layout.asset_id) : null;
  return (
    <article className="flex w-36 shrink-0 flex-col gap-1.5 rounded-md border border-border-subtle bg-elevated/45 p-2 text-xs">
      {url ? (
        <button
          type="button"
          onClick={() => onOpen(url)}
          className="aspect-video w-full cursor-pointer overflow-hidden rounded border border-border-subtle bg-canvas"
        >
          <img src={url} alt={layout.purpose} className="h-full w-full object-cover" />
        </button>
      ) : null}
      <strong className="truncate text-xs font-medium text-ink">{layout.purpose}</strong>
      <small className="truncate text-[11px] text-ink-muted">
        {layout.superseded_by
          ? "previous"
          : layout.review_status?.replaceAll("_", " ") || "Awaiting review"}
      </small>
    </article>
  );
}

export function MobileShotDrawer({
  shots,
  onOpenImage,
  focusShotId = null,
  onShotUpdated,
}: {
  shots: Shot[];
  onOpenImage: (url: string) => void;
  focusShotId?: string | null;
  onShotUpdated?: (shot: Shot) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(shots[0]?.id ?? null);
  const appliedFocus = useRef<string | null>(null);
  const selectedIndex = Math.max(0, shots.findIndex((shot) => shot.id === selectedId));
  const selected = shots[selectedIndex] ?? null;
  const displayableLayouts = selected?.layout_refs.filter(isDisplayableLayout) ?? [];

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
    setExpanded(true);
  }, [focusShotId, shots]);

  return (
    <section
      className={cn(
        "mobile-shot-drawer shrink-0 border-b border-border-subtle bg-elevated/40",
        expanded && "expanded",
      )}
    >
      <button
        type="button"
        className="mobile-shot-toggle flex w-full cursor-pointer items-center justify-between gap-2 px-4 py-2 text-xs text-ink transition-colors hover:bg-hover/60"
        aria-label={`Shots, ${shots.length} planned`}
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="flex items-center gap-2">
          <strong className="font-semibold text-ink">Shots</strong>
          <small className="font-mono text-[11px] text-ink-muted">{shots.length} planned</small>
        </span>
        <span
          aria-hidden="true"
          className="inline-flex items-center gap-1 text-[11px] text-ink-secondary"
        >
          {expanded ? "Close" : "Open"}
          <ChevronDown
            className={cn("h-3.5 w-3.5 transition-transform", expanded && "rotate-180")}
          />
        </span>
      </button>

      {expanded ? (
        <div className="mobile-shot-drawer-body max-h-[55dvh] space-y-3 overflow-y-auto border-t border-border-subtle bg-canvas/60 p-3">
          {shots.length ? (
            <nav className="mobile-shot-filmstrip flex gap-1.5 overflow-x-auto pb-1" aria-label="Shots">
              {shots.map((shot, index) => {
                const isActive = shot.id === selected?.id;
                return (
                  <button
                    key={shot.id}
                    type="button"
                    aria-label={`Shot ${index + 1} · ${shot.title}`}
                    aria-current={isActive ? "page" : undefined}
                    className={cn(
                      "flex shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors",
                      isActive
                        ? "active border-accent bg-accent-soft text-ink"
                        : "border-border-subtle bg-panel text-ink-secondary hover:text-ink",
                    )}
                    onClick={() => {
                      setSelectedId(shot.id);
                    }}
                  >
                    <span className="font-mono text-[10px] text-ink-muted">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <strong className="font-medium">{shot.title}</strong>
                  </button>
                );
              })}
            </nav>
          ) : (
            <p className="empty-copy text-xs text-ink-muted">
              No Shots yet. Ask the Director to plan them.
            </p>
          )}

          {selected ? (
            <div className="mobile-shot-detail space-y-3 rounded-lg border border-border-subtle bg-panel p-3">
              <header>
                <div>
                  <span className="mobile-eyebrow font-mono text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                    Shot {String(selectedIndex + 1).padStart(2, "0")}
                  </span>
                  <h3 className="text-sm font-semibold text-ink">{selected.title}</h3>
                </div>
              </header>

              <nav
                className="mobile-shot-document-nav flex flex-wrap gap-1 border-b border-border-subtle pb-2"
                aria-label="Shot document sections"
              >
                <a
                  href="#mobile-shot-brief"
                  className="rounded px-2 py-0.5 text-[11px] font-medium text-ink-secondary hover:bg-hover hover:text-ink"
                >
                  Brief
                </a>
                <a
                  href="#mobile-shot-references"
                  className="rounded px-2 py-0.5 text-[11px] font-medium text-ink-secondary hover:bg-hover hover:text-ink"
                >
                  References
                </a>
                <a
                  href="#mobile-shot-layouts"
                  className="rounded px-2 py-0.5 text-[11px] font-medium text-ink-secondary hover:bg-hover hover:text-ink"
                >
                  Layouts
                </a>
                <a
                  href="#mobile-shot-production"
                  className="rounded px-2 py-0.5 text-[11px] font-medium text-ink-secondary hover:bg-hover hover:text-ink"
                >
                  Production
                </a>
              </nav>

              <article
                className="mobile-shot-document space-y-3"
                aria-label={`${selected.title} shot design document`}
              >
                <VideoContextPanel
                  key={selected.id}
                  shot={selected}
                  shots={shots}
                  expanded={focusShotId === selected.id}
                  onShotUpdated={onShotUpdated}
                />
                <dl className="mobile-shot-meta grid grid-cols-2 gap-2 rounded-md border border-border-subtle bg-elevated/45 px-2.5 py-2 text-xs">
                  <div>
                    <dt className="text-[10px] uppercase tracking-wider text-ink-muted">Duration</dt>
                    <dd className="font-mono text-ink">{selected.duration_s}s</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] uppercase tracking-wider text-ink-muted">Status</dt>
                    <dd className="text-ink">{shotWorkflowStatus(selected).label}</dd>
                  </div>
                </dl>

                <section id="mobile-shot-brief" className="mobile-shot-brief space-y-1">
                  <span className="mobile-document-section-label font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    01 / Direction
                  </span>
                  <h4 className="text-xs font-semibold text-ink">Creative brief</h4>
                  <p className="text-xs leading-relaxed text-ink-secondary">
                    {selected.script_beat || "No brief written yet."}
                  </p>
                </section>

                <section id="mobile-shot-references" className="mobile-shot-document-section space-y-1.5">
                  <span className="mobile-document-section-label font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    02 / Continuity
                  </span>
                  <h4 className="text-xs font-semibold text-ink">Cast &amp; continuity</h4>
                  {selected.refs.length ? (
                    <div className="mobile-reference-strip flex gap-2 overflow-x-auto pb-1">
                      {[...selected.refs]
                        .sort((a, b) => a.picture_index - b.picture_index)
                        .map((ref) => (
                          <MobileReferenceThumb
                            key={`${ref.role}-${ref.asset_id}-${ref.picture_index}`}
                            refItem={ref}
                            onOpen={onOpenImage}
                          />
                        ))}
                    </div>
                  ) : (
                    <p className="empty-copy text-xs text-ink-muted">
                      No reusable assets assigned to this Shot.
                    </p>
                  )}
                </section>

                <section id="mobile-shot-layouts" className="mobile-shot-document-section space-y-2">
                  <span className="mobile-document-section-label font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    03 / Visual studies
                  </span>
                  <h4 className="text-xs font-semibold text-ink">Layout studies</h4>
                  <div className="mobile-layout-strip flex gap-2 overflow-x-auto pb-1">
                    {displayableLayouts
                      .filter((layout) => layout.asset_id && !isRetiredLayout(layout))
                      .map((layout) => (
                        <MobileLayoutCard key={layout.id} layout={layout} onOpen={onOpenImage} />
                      ))}
                    {!displayableLayouts.some((layout) => layout.asset_id) &&
                    selected.layout_asset_id ? (
                      <article className="flex w-36 shrink-0 flex-col gap-1.5 rounded-md border border-border-subtle bg-elevated/45 p-2 text-xs">
                        <button
                          type="button"
                          onClick={() => onOpenImage(layoutPreviewUrl(selected.layout_asset_id!)!)}
                          className="aspect-video w-full cursor-pointer overflow-hidden rounded border border-border-subtle bg-canvas"
                        >
                          <img
                            src={layoutPreviewUrl(selected.layout_asset_id)!}
                            alt="Reference frame"
                            className="h-full w-full object-cover"
                          />
                        </button>
                        <strong className="text-xs font-medium text-ink">Reference frame</strong>
                      </article>
                    ) : null}
                    {!displayableLayouts.some((layout) => layout.asset_id) &&
                    !selected.layout_asset_id ? (
                      <p className="empty-copy text-xs text-ink-muted">
                        No Layouts generated for this Shot.
                      </p>
                    ) : null}
                  </div>
                  {displayableLayouts.some((layout) => layout.asset_id && isRetiredLayout(layout)) ? (
                    <details className="mobile-layout-history rounded-md border border-border-subtle bg-elevated/40">
                      <summary className="cursor-pointer px-2.5 py-1.5 text-xs font-medium text-ink-secondary">
                        Previous layouts (
                        {
                          displayableLayouts.filter(
                            (layout) => layout.asset_id && isRetiredLayout(layout),
                          ).length
                        }
                        )
                      </summary>
                      <div className="mobile-layout-strip flex gap-2 overflow-x-auto border-t border-border-subtle p-2">
                        {displayableLayouts
                          .filter((layout) => layout.asset_id && isRetiredLayout(layout))
                          .map((layout) => (
                            <MobileLayoutCard
                              key={layout.id}
                              layout={layout}
                              onOpen={onOpenImage}
                            />
                          ))}
                      </div>
                    </details>
                  ) : null}
                </section>

                <section id="mobile-shot-production" className="mobile-shot-document-section space-y-1">
                  <span className="mobile-document-section-label font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                    04 / Record
                  </span>
                  <h4 className="text-xs font-semibold text-ink">Production record</h4>
                </section>
              </article>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
