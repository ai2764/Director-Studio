import { useEffect, useMemo, useState } from "react";
import {
  PROMPT_SECTION_KEYS,
  layoutPreviewUrl,
  refPreviewCandidates,
  type Shot,
  type ShotRef,
} from "../../shared/api/types";
import { shotWorkflowStatus } from "../../shared/shotWorkflowStatus";
import { LayoutReferenceList } from "./LayoutReferenceList";
import { ShotMaterialEditor } from "./ShotMaterialEditor";
import { materialReviewMessage } from "./materialReview";

const DOCUMENT_SECTIONS = [
  { id: "shot-brief", label: "Brief" },
  { id: "shot-references", label: "References" },
  { id: "shot-layouts", label: "Layouts" },
  { id: "shot-prompt", label: "Prompt" },
  { id: "shot-production", label: "Production" },
];

function referenceLabel(role: string) {
  return role.replaceAll("_", " ");
}

function normalizedDialogueText(value: string) {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[“”‘’'\"]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function dialogueSpeaker(shot: Shot, line: string) {
  const lineKey = normalizedDialogueText(line);
  const directMatches = shot.voice_refs.filter((voice) => (
    voice.speaker.trim()
    && normalizedDialogueText(voice.notes || "").includes(lineKey)
  ));
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

  const speakers = [...new Set(shot.voice_refs.map((voice) => voice.speaker.trim()).filter(Boolean))];
  return speakers.length === 1 ? speakers[0] : "";
}

function ReferenceThumb({ refItem, onOpen }: { refItem: ShotRef; onOpen: (url: string) => void }) {
  const candidates = useMemo(() => refPreviewCandidates(refItem), [refItem]);
  const [index, setIndex] = useState(0);
  const url = candidates[index] || null;
  return (
    <div className={`shot-ref-thumb ${url ? "" : "empty"}`}>
      {url ? (
        <button type="button" onClick={() => onOpen(url)}>
          <img
            src={url}
            alt={referenceLabel(refItem.role)}
            onError={() => setIndex((current) => current + 1)}
          />
        </button>
      ) : (
        <span>no preview</span>
      )}
      <span className="shot-ref-role">P{refItem.picture_index} · {referenceLabel(refItem.role)}</span>
    </div>
  );
}

export function ShotWorkspace({
  shots,
  busy,
  onRegenerate,
  onSend,
  onSelectShot,
  onShotUpdated,
  onOpenImage,
}: {
  shots: Shot[];
  busy: boolean;
  onRegenerate: (shot: Shot) => void;
  onSend: (message: string) => void;
  onSelectShot?: (shot: Shot) => void;
  onShotUpdated?: (shot: Shot) => void;
  onOpenImage: (url: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(shots[0]?.id ?? null);
  const [materialEditorOpen, setMaterialEditorOpen] = useState(false);
  const selectedIndex = Math.max(0, shots.findIndex((shot) => shot.id === selectedId));
  const selected = shots[selectedIndex] ?? null;

  useEffect(() => {
    if ((!selectedId || !shots.some((shot) => shot.id === selectedId)) && shots[0]) {
      setSelectedId(shots[0].id);
    }
  }, [selectedId, shots]);

  const selectShot = (shot: Shot) => {
    setSelectedId(shot.id);
    onSelectShot?.(shot);
  };

  return (
    <section className="panel director-shots-panel" aria-label="Shot workspace">
      <header className="shot-workspace-header workspace-panel-header">
        <div className="shot-workspace-identity">
          <img
            className="storyboard-shot-board"
            src="/storyboard-shot-board.png"
            alt="Storyboard shot board"
          />
          <div>
            <h2>Shots</h2>
          </div>
        </div>
      </header>

      {shots.length === 0 ? (
        <p className="empty-copy">No shots yet. Ask the Director to plan them in chat.</p>
      ) : selected ? (
        <div className="shot-workspace-body">
          <nav className="shot-bookmarks" aria-label="Shots">
            {shots.map((shot, index) => {
              const stage = shotWorkflowStatus(shot);
              return (
                <button
                  key={shot.id}
                  type="button"
                  aria-label={`Shot ${index + 1} · ${shot.title}`}
                  aria-current={shot.id === selected.id ? "page" : undefined}
                  className={shot.id === selected.id ? "active" : ""}
                  onClick={() => selectShot(shot)}
                >
                  <span className="shot-bookmark-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="shot-bookmark-copy">
                    <strong>{shot.title}</strong>
                    <small title={stage.label}>Shot {index + 1}</small>
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="shot-detail">
            <div className="shot-detail-panel shot-detail-panel-clapperboard">
              <article className="shot-document" aria-label={`${selected.title} shot design document`}>
                <header className="shot-detail-header">
                  <div>
                    <div className="workspace-kicker">Shot {String(selectedIndex + 1).padStart(2, "0")}</div>
                    <h3>{selectedIndex + 1}. {selected.title}</h3>
                  </div>
                  <div className="shot-detail-header-actions">
                    <span className={`status-chip status-${shotWorkflowStatus(selected).key}`}>
                      {shotWorkflowStatus(selected).label}
                    </span>
                  </div>
                </header>

                <nav className="shot-document-nav" aria-label="Shot document sections">
                  {DOCUMENT_SECTIONS.map((section) => (
                    <a key={section.id} href={`#${section.id}`}>
                      {section.label}
                    </a>
                  ))}
                </nav>

                <dl className="shot-document-meta">
                  <div><dt>Duration</dt><dd>{selected.duration_s}s</dd></div>
                  <div><dt>Scene</dt><dd>{selected.scene_id}</dd></div>
                  <div><dt>Status</dt><dd>{shotWorkflowStatus(selected).label}</dd></div>
                </dl>

                <section id="shot-brief" className="shot-document-section shot-brief-panel">
                  <header className="shot-document-section-header">
                    <span>01 / Direction</span>
                    <h4>Creative brief</h4>
                  </header>
                  <p className="shot-beat">{selected.script_beat || "No creative brief written yet."}</p>
                  {selected.dialogue.length ? (
                    <div className="shot-dialogue" aria-label="Dialogue">
                      {selected.dialogue.map((line, index) => {
                        const speaker = dialogueSpeaker(selected, line);
                        return (
                          <p key={`${index}-${line}`}>
                            {speaker ? <strong>{speaker}</strong> : null}
                            <span>{line}</span>
                          </p>
                        );
                      })}
                    </div>
                  ) : null}
                  {shotWorkflowStatus(selected).key === "blocked" && selected.blocked_reasons.length ? (
                    <div className="banner error">{selected.blocked_reasons.join("; ")}</div>
                  ) : null}
                </section>

                <section id="shot-references" className="shot-document-section shot-references-panel">
                  <header className="shot-document-section-header">
                    <span>02 / Continuity</span>
                    <h4>Cast &amp; continuity</h4>
                    <div className="shot-reference-header-actions">
                      <small>{selected.refs.length} assigned</small>
                      <button
                        type="button"
                        className="mode-chip shot-material-edit-trigger"
                        disabled={busy}
                        onClick={() => setMaterialEditorOpen(true)}
                      >
                        Edit materials
                      </button>
                    </div>
                  </header>
                  {selected.refs.length ? (
                    <div className="shot-refs-strip">
                      {[...selected.refs].sort((a, b) => a.picture_index - b.picture_index).map((ref) => (
                        <ReferenceThumb
                          key={`${ref.role}-${ref.asset_id}-${ref.picture_index}`}
                          refItem={ref}
                          onOpen={onOpenImage}
                        />
                      ))}
                    </div>
                  ) : <p className="empty-copy">No assets cast for this Shot.</p>}
                </section>

                <section id="shot-layouts" className="shot-document-section">
                  <header className="shot-document-section-header">
                    <span>03 / Visual studies</span>
                    <h4>Layout studies</h4>
                  </header>
                  {selected.layout_refs.length ? (
                    <LayoutReferenceList
                      shot={selected}
                      busy={busy}
                      onDiscussAddReference={(description) => {
                        const ids = selected.layout_refs.map((layout) => layout.id).join(", ") || "none";
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
                    <div className="shot-document-empty">
                      <p>No visual Layout has been generated for this Shot.</p>
                      <button
                        type="button"
                        className="mode-chip"
                        disabled={busy || (selected.status === "ref_frame_pending" && Boolean(selected.ref_frame_job_id))}
                        onClick={() => onRegenerate(selected)}
                      >
                        Generate reference frame
                      </button>
                    </div>
                  )}
                </section>

                <section id="shot-prompt" className="shot-document-section">
                  <header className="shot-document-section-header">
                    <span>04 / Model direction</span>
                    <h4>Generation prompt</h4>
                  </header>
                  <div className="shot-prompt-panel">
                  {PROMPT_SECTION_KEYS.map(({ key, label }) => selected.prompt_sections[key] ? (
                    <section key={key}>
                      <h5>{label}</h5>
                      <p>{selected.prompt_sections[key]}</p>
                    </section>
                  ) : null)}
                  {!Object.values(selected.prompt_sections).some(Boolean) ? <p className="empty-copy">No H3 prompt written yet.</p> : null}
                  </div>
                </section>

                <section id="shot-production" className="shot-document-section">
                  <header className="shot-document-section-header">
                    <span>05 / Record</span>
                    <h4>Production record</h4>
                  </header>
                  <div className="shot-run-panel">
                    <div><span>Reference job</span><strong>{selected.ref_frame_job_id || "Not queued"}</strong></div>
                    <div><span>Video job</span><strong>{selected.h3_job_id || "Not queued"}</strong></div>
                    <div><span>Status</span><strong>{shotWorkflowStatus(selected).label}</strong></div>
                  </div>
                </section>
              </article>
            </div>
          </div>
          {materialEditorOpen ? (
            <ShotMaterialEditor
              shot={selected}
              shotNumber={selectedIndex + 1}
              onClose={() => setMaterialEditorOpen(false)}
              onOpenImage={onOpenImage}
              onSaved={(updated) => {
                onShotUpdated?.(updated);
                onSend(materialReviewMessage(updated, selectedIndex + 1));
              }}
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
