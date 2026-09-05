import { useEffect, useMemo, useState } from "react";
import {
  layoutPreviewUrl,
  refPreviewCandidates,
  type LayoutReference,
  type Shot,
  type ShotRef,
} from "../../shared/api/types";
import { isDisplayableLayout, isRetiredLayout } from "../../shared/layoutReferenceStatus";
import { shotWorkflowStatus } from "../../shared/shotWorkflowStatus";

function referenceLabel(role: string) {
  return role.replaceAll("_", " ");
}

function MobileReferenceThumb({ refItem, onOpen }: { refItem: ShotRef; onOpen: (url: string) => void }) {
  const candidates = useMemo(() => refPreviewCandidates(refItem), [refItem]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const url = candidates[candidateIndex] || null;
  return (
    <div className="mobile-reference-thumb">
      {url ? (
        <button type="button" onClick={() => onOpen(url)}>
          <img
            src={url}
            alt={referenceLabel(refItem.role)}
            onError={() => setCandidateIndex((current) => current + 1)}
          />
        </button>
      ) : <span className="mobile-reference-empty">No preview</span>}
      <small>{referenceLabel(refItem.role)}</small>
    </div>
  );
}

function MobileLayoutCard({ layout, onOpen }: { layout: LayoutReference; onOpen: (url: string) => void }) {
  const url = layout.asset_id ? layoutPreviewUrl(layout.asset_id) : null;
  return (
    <article>
      {url ? <button type="button" onClick={() => onOpen(url)}><img src={url} alt={layout.purpose} /></button> : null}
      <strong>{layout.purpose}</strong>
      <small>{layout.superseded_by ? "previous" : layout.review_status?.replaceAll("_", " ") || "Awaiting review"}</small>
    </article>
  );
}

export function MobileShotDrawer({
  shots,
  onOpenImage,
}: {
  shots: Shot[];
  onOpenImage: (url: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(shots[0]?.id ?? null);
  const selectedIndex = Math.max(0, shots.findIndex((shot) => shot.id === selectedId));
  const selected = shots[selectedIndex] ?? null;
  const displayableLayouts = selected?.layout_refs.filter(isDisplayableLayout) ?? [];

  useEffect(() => {
    if ((!selectedId || !shots.some((shot) => shot.id === selectedId)) && shots[0]) {
      setSelectedId(shots[0].id);
    }
  }, [selectedId, shots]);

  return (
    <section className={`mobile-shot-drawer${expanded ? " expanded" : ""}`}>
      <button
        type="button"
        className="mobile-shot-toggle"
        aria-label={`Shots, ${shots.length} planned`}
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        <span><strong>Shots</strong><small>{shots.length} planned</small></span>
        <span aria-hidden="true">{expanded ? "Close" : "Open"} ↓</span>
      </button>

      {expanded ? (
        <div className="mobile-shot-drawer-body">
          {shots.length ? (
            <nav className="mobile-shot-filmstrip" aria-label="Shots">
              {shots.map((shot, index) => (
                <button
                  key={shot.id}
                  type="button"
                  aria-label={`Shot ${index + 1} · ${shot.title}`}
                  aria-current={shot.id === selected?.id ? "page" : undefined}
                  className={shot.id === selected?.id ? "active" : ""}
                  onClick={() => {
                    setSelectedId(shot.id);
                  }}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{shot.title}</strong>
                </button>
              ))}
            </nav>
          ) : <p className="empty-copy">No Shots yet. Ask the Director to plan them.</p>}

          {selected ? (
            <div className="mobile-shot-detail">
              <header>
                <div>
                  <span className="mobile-eyebrow">Shot {String(selectedIndex + 1).padStart(2, "0")}</span>
                  <h3>{selected.title}</h3>
                </div>
              </header>

              <nav className="mobile-shot-document-nav" aria-label="Shot document sections">
                <a href="#mobile-shot-brief">Brief</a>
                <a href="#mobile-shot-references">References</a>
                <a href="#mobile-shot-layouts">Layouts</a>
              </nav>

              <article className="mobile-shot-document" aria-label={`${selected.title} shot design document`}>
                  <dl className="mobile-shot-meta">
                    <div><dt>Duration</dt><dd>{selected.duration_s}s</dd></div>
                    <div><dt>Status</dt><dd>{shotWorkflowStatus(selected).label}</dd></div>
                  </dl>

                  <section id="mobile-shot-brief" className="mobile-shot-brief">
                    <span className="mobile-document-section-label">01 / Direction</span>
                    <h4>Creative brief</h4>
                    <p>{selected.script_beat || "No brief written yet."}</p>
                  </section>

                  <section id="mobile-shot-references" className="mobile-shot-document-section">
                    <span className="mobile-document-section-label">02 / Continuity</span>
                    <h4>Cast &amp; continuity</h4>
                  {
                  selected.refs.length ? (
                    <div className="mobile-reference-strip">
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
                  ) : <p className="empty-copy">No reusable assets assigned to this Shot.</p>
                  }
                  </section>

                  <section id="mobile-shot-layouts" className="mobile-shot-document-section">
                    <span className="mobile-document-section-label">03 / Visual studies</span>
                    <h4>Layout studies</h4>
                  <div className="mobile-layout-strip">
                    {displayableLayouts
                      .filter((layout) => layout.asset_id && !isRetiredLayout(layout))
                      .map((layout) => <MobileLayoutCard key={layout.id} layout={layout} onOpen={onOpenImage} />)}
                    {!displayableLayouts.some((layout) => layout.asset_id) && selected.layout_asset_id ? (
                      <article>
                        <button type="button" onClick={() => onOpenImage(layoutPreviewUrl(selected.layout_asset_id!)!)}>
                          <img src={layoutPreviewUrl(selected.layout_asset_id)!} alt="Reference frame" />
                        </button>
                        <strong>Reference frame</strong>
                      </article>
                    ) : null}
                    {!displayableLayouts.some((layout) => layout.asset_id) && !selected.layout_asset_id ? (
                      <p className="empty-copy">No Layouts generated for this Shot.</p>
                    ) : null}
                  </div>
                  {displayableLayouts.some((layout) => layout.asset_id && isRetiredLayout(layout)) ? (
                    <details className="mobile-layout-history">
                      <summary>
                        Previous layouts ({displayableLayouts.filter((layout) => layout.asset_id && isRetiredLayout(layout)).length})
                      </summary>
                      <div className="mobile-layout-strip">
                        {displayableLayouts
                          .filter((layout) => layout.asset_id && isRetiredLayout(layout))
                          .map((layout) => <MobileLayoutCard key={layout.id} layout={layout} onOpen={onOpenImage} />)}
                      </div>
                    </details>
                  ) : null}
                  </section>
              </article>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
