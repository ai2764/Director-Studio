import { useCallback, useEffect, useMemo, useState } from "react";
import {
  EMPTY_PROMPT_SECTIONS,
  PROMPT_SECTION_KEYS,
  layoutPreviewUrl,
  refPreviewCandidates,
  type JobStatus,
  type PromptSections,
  type Shot,
  type ShotRef,
  type ShotVoiceRef,
} from "../../shared/api/types";
import { PageShell } from "../../shared/components/PageShell";
import { useProject } from "../../shared/project/ProjectContext";
import { promptReady, shotWorkflowStatus } from "../../shared/shotWorkflowStatus";
import {
  cancelH3Job,
  deleteLayout,
  getH3Job,
  getH3ProviderStatus,
  getProject,
  patchShot,
  submitShot,
  type H3JobRecord,
  type H3Provider,
  type H3ProviderStatus,
} from "./api";
import { listLibraryAssets, type LibraryAsset } from "../library/api";
import { ShotMaterialEditor } from "../director/ShotMaterialEditor";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

type DrawerTab = "layout" | "refs" | "prompt" | "run";
type ResolutionPreset =
  | "auto"
  | "landscape-480"
  | "landscape-720"
  | "portrait-480"
  | "portrait-720";

const RESOLUTION_PRESETS: Record<
  Exclude<ResolutionPreset, "auto">,
  { width: number; height: number }
> = {
  "landscape-480": { width: 864, height: 480 },
  "landscape-720": { width: 1280, height: 704 },
  "portrait-480": { width: 480, height: 864 },
  "portrait-720": { width: 704, height: 1280 },
};

function pictureLabel(ref: { role: string; picture_index: number }): string {
  if (ref.role === "layout_ref_frame") {
    return `P${ref.picture_index} · Layout`;
  }
  return `P${ref.picture_index} · ${ref.role.replace(/_/g, " ")}`;
}

function ProductionStatusChip({ shot }: { shot: Shot }) {
  const stage = shotWorkflowStatus(shot);
  return (
    <span className={`status-chip status-${stage.key}`}>
      {stage.label}
    </span>
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
      className="ref-thumb ref-thumb-btn"
      title={`${pictureLabel(refItem)} · ${refItem.asset_id}${refItem.file_key ? ` · ${refItem.file_key}` : ""}`}
      onClick={() => {
        if (url) onOpen(url);
      }}
    >
      <div className="ref-thumb-img">
        {url ? (
          <img
            src={url}
            alt={pictureLabel(refItem)}
            draggable={false}
            onError={() => {
              if (idx + 1 < candidates.length) {
                setIdx((i) => i + 1);
              } else {
                setFailed(true);
              }
            }}
          />
        ) : (
          <div className="output-empty">no preview</div>
        )}
      </div>
      <div className="ref-thumb-label">{pictureLabel(refItem)}</div>
      <div className="muted tiny mono">{refItem.asset_id}</div>
      {refItem.file_key ? (
        <div className="muted tiny ellipsis">{refItem.file_key}</div>
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
  onReviewMaterials?: (shot: Shot, shotNumber: number) => void;
} = {}) {
  const { projectId } = useProject();
  const [shots, setShots] = useState<Shot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState<PromptSections>(EMPTY_PROMPT_SECTIONS);
  const [promptDirty, setPromptDirty] = useState(false);
  const [draftDialogue, setDraftDialogue] = useState("");
  const [draftDuration, setDraftDuration] = useState("8");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [h3Job, setH3Job] = useState<H3JobRecord | null>(null);
  const [tab, setTab] = useState<DrawerTab>("layout");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [voiceAssets, setVoiceAssets] = useState<LibraryAsset[]>([]);
  const [draftVoiceRefs, setDraftVoiceRefs] = useState<ShotVoiceRef[]>([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [voiceRefsDirty, setVoiceRefsDirty] = useState(false);
  const [materialEditorOpen, setMaterialEditorOpen] = useState(false);
  const [h3ProviderStatus, setH3ProviderStatus] = useState<H3ProviderStatus | null>(null);
  const [h3Provider, setH3Provider] = useState<H3Provider>("local");
  const [resolutionPreset, setResolutionPreset] =
    useState<ResolutionPreset>("auto");

  const selected = useMemo(
    () => shots.find((s) => s.id === selectedId) || null,
    [shots, selectedId],
  );
  const currentLayout = useMemo(() => {
    if (!selected) return null;
    return selected.layout_refs.find(
      (layout) => layout.asset_id === selected.layout_asset_id,
    ) || [...selected.layout_refs].reverse().find((layout) => layout.asset_id) || null;
  }, [selected]);

  const loadProject = useCallback(async (id: string) => {
    const detail = await getProject(id);
    setShots(detail.shots);
    return detail;
  }, []);

  useEffect(() => {
    setSelectedId(null);
    setH3Job(null);
    if (!projectId) {
      setShots([]);
    }
  }, [projectId]);

  // Pages stay mounted across top-level tab switches. Refresh when Production
  // becomes visible so Director-side prompt/layout changes are not stale.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setError(null);
    getProject(projectId)
      .then((detail) => {
        if (cancelled) return;
        setShots(detail.shots);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [active, projectId]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    getH3ProviderStatus()
      .then((status) => {
        if (cancelled) return;
        setH3ProviderStatus(status);
        setH3Provider(
          status.default_provider === "minimax" && !status.minimax_configured
            ? "local"
            : status.default_provider,
        );
      })
      .catch(() => {
        if (!cancelled) {
          setH3ProviderStatus(null);
          setH3Provider("local");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [active]);

  useEffect(() => {
    if (!mobile || shots.length === 0) return;
    setSelectedId((current) =>
      current && shots.some((shot) => shot.id === current) ? current : shots[0].id,
    );
  }, [mobile, shots]);

  useEffect(() => {
    if (!active || !projectId) {
      setVoiceAssets([]);
      return;
    }
    let cancelled = false;
    listLibraryAssets("voices", projectId)
      .then((assets) => {
        if (!cancelled) setVoiceAssets(assets);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [active, projectId]);

  useEffect(() => {
    if (!selected) {
      setDraftPrompt(EMPTY_PROMPT_SECTIONS);
      setPromptDirty(false);
      setDraftDialogue("");
      setDraftDuration("8");
      setH3Job(null);
      setTab("layout");
      setDraftVoiceRefs([]);
      setSelectedVoiceId("");
      setVoiceRefsDirty(false);
      return;
    }
    setDraftPrompt({ ...EMPTY_PROMPT_SECTIONS, ...selected.prompt_sections });
    setPromptDirty(false);
    setDraftDialogue((selected.dialogue || []).join("\n"));
    setDraftDuration(String(selected.duration_s ?? 8));
    setDraftVoiceRefs(
      [...(selected.voice_refs || [])]
        .sort((a, b) => a.audio_index - b.audio_index)
        .map((ref, index) => ({ ...ref, audio_index: index + 1 })),
    );
    setSelectedVoiceId("");
    setVoiceRefsDirty(false);
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Accept Director-side prompt generation for the selected shot, but never
  // overwrite text the user has changed locally and not saved yet.
  useEffect(() => {
    if (!selected || promptDirty) return;
    setDraftPrompt({ ...EMPTY_PROMPT_SECTIONS, ...selected.prompt_sections });
  }, [selected?.prompt_sections, promptDirty]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const jobId = selected?.h3_job_id;
    if (!jobId) {
      setH3Job(null);
      return;
    }
    let cancelled = false;
    const tick = () => {
      getH3Job(jobId)
        .then((j) => {
          if (!cancelled) setH3Job(j);
        })
        .catch(() => undefined);
    };
    tick();
    const t = window.setInterval(tick, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [selected?.h3_job_id]);

  useEffect(() => {
    if (!projectId) return;
    const running = shots.some((s) => ACTIVE.includes(s.status as JobStatus) || s.h3_job_id);
    if (!running) return;
    const t = window.setInterval(() => {
      getProject(projectId)
        .then((d) => setShots(d.shots))
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(t);
  }, [projectId, shots]);

  const replaceShot = (updated: Shot) => {
    setShots((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  };

  const onSavePrompt = async () => {
    if (!selected) return;
    setError(null);
    setBusy(true);
    try {
      const dialogue = draftDialogue
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      const duration_s = Number(draftDuration);
      const updated = await patchShot(selected.id, {
        prompt_sections: draftPrompt,
        dialogue,
        duration_s: Number.isFinite(duration_s) ? duration_s : selected.duration_s,
      });
      replaceShot(updated);
      setPromptDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteLayout = async () => {
    if (!selected || !currentLayout) return;
    if (!window.confirm("Delete this Layout permanently?")) return;
    setError(null);
    setBusy(true);
    try {
      replaceShot(await deleteLayout(selected.id, currentLayout.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = async () => {
    if (!selected || jobActive) return;
    setError(null);
    setBusy(true);
    try {
      const resolution =
        resolutionPreset === "auto"
          ? undefined
          : RESOLUTION_PRESETS[resolutionPreset];
      replaceShot(await submitShot(selected.id, h3Provider, resolution));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCancelJob = async () => {
    if (!selected?.h3_job_id) return;
    setError(null);
    setBusy(true);
    try {
      setH3Job(await cancelH3Job(selected.h3_job_id));
      if (projectId) await loadProject(projectId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const orderedRefs = useMemo(() => {
    if (!selected) return [];
    return [...selected.refs].sort((a, b) => a.picture_index - b.picture_index);
  }, [selected]);

  const voiceAssetById = useMemo(
    () => new Map(voiceAssets.map((asset) => [asset.id, asset])),
    [voiceAssets],
  );

  const normalizeVoiceRefs = (refs: ShotVoiceRef[]) =>
    refs.map((ref, index) => ({ ...ref, audio_index: index + 1 }));

  const updateVoiceRefs = (refs: ShotVoiceRef[]) => {
    setDraftVoiceRefs(normalizeVoiceRefs(refs));
    setVoiceRefsDirty(true);
  };

  const addVoiceRef = () => {
    if (!selectedVoiceId || draftVoiceRefs.length >= 3) return;
    if (draftVoiceRefs.some((ref) => ref.asset_id === selectedVoiceId)) return;
    const asset = voiceAssetById.get(selectedVoiceId);
    if (!asset) return;
    updateVoiceRefs([
      ...draftVoiceRefs,
      {
        asset_id: asset.id,
        audio_index: draftVoiceRefs.length + 1,
        file_key: "reference",
        speaker: asset.name,
        notes: "",
      },
    ]);
    setSelectedVoiceId("");
  };

  const moveVoiceRef = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= draftVoiceRefs.length) return;
    const next = [...draftVoiceRefs];
    [next[index], next[target]] = [next[target], next[index]];
    updateVoiceRefs(next);
  };

  const saveVoiceRefs = async () => {
    if (!selected || selected.source_audio_path) return;
    setError(null);
    setBusy(true);
    try {
      const voice_refs = normalizeVoiceRefs(draftVoiceRefs);
      replaceShot(await patchShot(selected.id, { voice_refs }));
      setDraftVoiceRefs(voice_refs);
      setVoiceRefsDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const jobActive = h3Job ? ACTIVE.includes(h3Job.status) : false;
  // No Gate-2 approve: ready when refs + six-section prompt exist and not mid-run
  const canSubmit =
    !!selected &&
    !jobActive &&
    !["queued", "running"].includes(selected.status) &&
    (selected.refs?.length ?? 0) > 0 &&
    promptReady(selected.prompt_sections);
  const layoutUrl = layoutPreviewUrl(selected?.layout_asset_id);
  const voicePromptStale =
    voiceRefsDirty ||
    (draftVoiceRefs.length > 0 && !selected?.meta?.prompt_voice_signature);

  const tabs: { id: DrawerTab; label: string }[] = [
    { id: "layout", label: "Layout" },
    { id: "refs", label: "Refs" },
    { id: "prompt", label: "Prompt" },
    { id: "run", label: "Run H3" },
  ];

  const providerPicker = (
    <label className="h3-provider-picker">
      <span>Provider</span>
      <select
        aria-label="H3 provider"
        value={h3Provider}
        disabled={busy || jobActive}
        onChange={(event) => setH3Provider(event.target.value as H3Provider)}
      >
        <option value="local">Local · ComfyUI</option>
        <option value="minimax" disabled={!h3ProviderStatus?.minimax_configured}>
          MiniMax · Official API
          {h3ProviderStatus && !h3ProviderStatus.minimax_configured
            ? " · not configured"
            : ""}
        </option>
      </select>
      <small>
        {h3Provider === "minimax"
          ? `Official API · ${h3ProviderStatus?.minimax_resolution || "768P"}`
          : "Local ComfyUI workflow"}
      </small>
    </label>
  );

  if (mobile) {
    const selectedNumber = selected
      ? Math.max(0, shots.findIndex((shot) => shot.id === selected.id)) + 1
      : 0;
    const videoUrl = h3Job?.outputs?.video?.url || h3Job?.outputs?.master?.url || null;
    const promptSections = selected
      ? PROMPT_SECTION_KEYS.filter(({ key }) => selected.prompt_sections?.[key]?.trim())
      : [];

    return (
      <main className="mobile-production-review" aria-label="Mobile production review">
        <header className="mobile-section-header mobile-production-header">
          <div>
            <span className="mobile-eyebrow">Production</span>
            <h1>Shot review</h1>
          </div>
          <span className="mobile-shot-count">{shots.length} shots</span>
        </header>

        {error ? <div className="banner error mobile-production-error">{error}</div> : null}

        {!projectId ? (
          <p className="mobile-production-empty">Select a project to review its shots.</p>
        ) : shots.length === 0 ? (
          <p className="mobile-production-empty">No shots yet. Plan them with Director first.</p>
        ) : (
          <nav className="mobile-production-shot-strip" aria-label="Production shots">
            {shots.map((shot, index) => (
              <button
                key={shot.id}
                type="button"
                className={shot.id === selectedId ? "active" : ""}
                aria-label={`Shot ${String(index + 1).padStart(2, "0")} · ${shot.title || shot.id}`}
                aria-current={shot.id === selectedId ? "page" : undefined}
                onClick={() => {
                  setSelectedId(shot.id);
                  setMaterialEditorOpen(false);
                }}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{shot.title || shot.id}</strong>
              </button>
            ))}
          </nav>
        )}

        {selected ? (
          <article className="mobile-production-sheet">
            <header className="mobile-production-shot-header">
              <div>
                <span className="mobile-eyebrow">
                  Shot {String(selectedNumber).padStart(2, "0")}
                </span>
                <h2>{selected.title || selected.id}</h2>
              </div>
              <ProductionStatusChip shot={selected} />
            </header>

            <section className="mobile-production-section mobile-production-result">
              <div className="mobile-production-section-title">
                <h3>Final result</h3>
                {h3Job ? <span>{h3Job.status}</span> : null}
              </div>
              {videoUrl ? (
                <video controls playsInline preload="metadata" src={videoUrl} />
              ) : jobActive ? (
                <p className="mobile-production-empty-inline">Video generation is in progress.</p>
              ) : (
                <p className="mobile-production-empty-inline">No final video for this shot yet.</p>
              )}
              {h3Job?.error ? (
                <div className="mobile-production-job-error" role="status">
                  Generation failed. Open desktop Production for diagnostic details.
                </div>
              ) : null}
              {providerPicker}
              <button
                type="button"
                className="btn primary mobile-production-run"
                disabled={busy || !canSubmit || jobActive}
                onClick={() => void onSubmit()}
              >
                {jobActive ? "H3 running…" : "Run H3"}
              </button>
            </section>

            <section className="mobile-production-section">
              <h3>Shot design</h3>
              <p className="mobile-production-beat">{selected.script_beat}</p>
              <dl className="mobile-production-metadata">
                <div><dt>Duration</dt><dd>{selected.duration_s}s</dd></div>
                <div><dt>Framing</dt><dd>{selected.shot_type || "—"}</dd></div>
                <div><dt>Angle</dt><dd>{selected.camera_angle || "—"}</dd></div>
                <div><dt>Motion</dt><dd>{selected.camera_motion || "—"}</dd></div>
              </dl>
              {selected.composition ? (
                <div className="mobile-production-note">
                  <span>Composition</span>
                  <p>{selected.composition}</p>
                </div>
              ) : null}
              {selected.dialogue?.length ? (
                <div className="mobile-production-note">
                  <span>Dialogue</span>
                  {selected.dialogue.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}
                </div>
              ) : null}
            </section>

            <section className="mobile-production-section">
              <div className="mobile-production-section-title">
                <h3>References</h3>
                <button
                  type="button"
                  className="mode-chip shot-material-edit-trigger"
                  disabled={busy}
                  onClick={() => setMaterialEditorOpen(true)}
                >
                  Edit materials
                </button>
              </div>
              {orderedRefs.length ? (
                <div className="mobile-production-reference-strip">
                  {orderedRefs.map((ref) => (
                    <ProductionRefThumb
                      key={`${ref.role}-${ref.picture_index}-${ref.asset_id}-${ref.file_key || ""}`}
                      refItem={ref}
                      onOpen={setPreviewUrl}
                    />
                  ))}
                </div>
              ) : layoutUrl ? (
                <img className="mobile-production-layout" src={layoutUrl} alt="Layout reference" />
              ) : (
                <p className="mobile-production-empty-inline">No references attached.</p>
              )}
            </section>

            <section className="mobile-production-section mobile-production-prompt">
              <h3>H3 prompt</h3>
              {promptSections.length ? promptSections.map(({ key, label }) => (
                <div key={key} className="mobile-production-prompt-section">
                  <h4>{label}</h4>
                  <p>{selected.prompt_sections[key]}</p>
                </div>
              )) : (
                <p className="mobile-production-empty-inline">No H3 prompt written yet.</p>
              )}
            </section>
          </article>
        ) : null}

        {selected && materialEditorOpen ? (
          <ShotMaterialEditor
            shot={selected}
            shotNumber={selectedNumber}
            onClose={() => setMaterialEditorOpen(false)}
            onOpenImage={setPreviewUrl}
            onSaved={(updated) => {
              replaceShot(updated);
              onReviewMaterials?.(updated, selectedNumber);
            }}
          />
        ) : null}

        {previewUrl ? (
          <div
            className="lightbox-overlay"
            role="dialog"
            aria-modal="true"
            onClick={() => setPreviewUrl(null)}
            onKeyDown={(event) => event.key === "Escape" && setPreviewUrl(null)}
          >
            <button type="button" className="lightbox-close btn secondary sm" onClick={() => setPreviewUrl(null)}>
              Close
            </button>
            <img className="lightbox-img" src={previewUrl} alt="ref preview" onClick={(event) => event.stopPropagation()} />
          </div>
        ) : null}
      </main>
    );
  }

  return (
    <PageShell
      title="Production"
      subtitle={
        projectId ? (
          <>
            Shot list → layout / refs / prompt → Submit H3. No separate approve step.
          </>
        ) : (
          "Select a project in the header."
        )
      }
      className="production-page"
    >
      {error ? <div className="banner error">{error}</div> : null}

      <div className="split-layout production-split">
        <aside className="split-side">
          <div className="section-card compact-card">
            <div className="section-card-head">
              <h2 className="section-card-title">Shots</h2>
              <span className="muted tiny">{shots.length}</span>
            </div>
            {!projectId ? (
              <p className="empty-copy">No project selected.</p>
            ) : shots.length === 0 ? (
              <p className="empty-copy">No shots yet. Plan them in Director first.</p>
            ) : (
              <div className="shot-table-wrap">
                <table className="shot-table">
                  <thead>
                    <tr>
                      <th>Shot</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shots.map((s) => (
                      <tr
                        key={s.id}
                        className={s.id === selectedId ? "selected" : ""}
                        onClick={() => setSelectedId(s.id)}
                      >
                        <td>
                          <div className="shot-table-title">{s.title || s.id}</div>
                          <div className="muted tiny">{s.duration_s}s</div>
                        </td>
                        <td>
                          <ProductionStatusChip shot={s} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </aside>

        <section className="split-main">
          {!selected ? (
            <div className="section-card empty-state-card">
              <p className="empty-copy">Select a shot from the list to edit layout, refs, and prompt.</p>
            </div>
          ) : (
            <div className="section-card shot-editor">
              <div className="shot-editor-header">
                <div>
                  <h2 className="shot-editor-title">{selected.title || selected.id}</h2>
                  <p className="shot-beat muted">{selected.script_beat}</p>
                </div>
                <ProductionStatusChip shot={selected} />
              </div>

              <div className="segment-tabs" role="tablist">
                {tabs.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === t.id}
                    className={`segment-tab ${tab === t.id ? "active" : ""}`}
                    onClick={() => setTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              <div className="shot-editor-body">
                {tab === "layout" ? (
                  <div className="tab-panel">
                    <p className="field-hint">
                      Optional still for spatial lock. It binds as a normal Picture slot
                      and does not steal Picture 1. Skip if you only have actor/scene refs.
                    </p>
                    <div className="layout-preview-box">
                      {layoutUrl ? (
                        <img src={layoutUrl} alt="layout" />
                      ) : (
                        <div className="output-empty">No layout</div>
                      )}
                    </div>
                    <div className="muted tiny mono" style={{ marginTop: "0.5rem" }}>
                      {selected.layout_asset_id || "no layout asset"}
                    </div>
                    {currentLayout ? (
                      <div className="actions" style={{ marginTop: "1rem" }}>
                      <button
                        type="button"
                        className="btn danger"
                        disabled={busy || jobActive}
                        onClick={onDeleteLayout}
                      >
                        Delete Layout
                      </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {tab === "refs" ? (
                  <div className="tab-panel">
                    <div className="refs-section-head">
                      <div>
                        <strong>Picture References</strong>
                        <div className="muted tiny">Ordered Picture slots for H3</div>
                      </div>
                    </div>
                    {orderedRefs.length === 0 ? (
                      <p className="empty-copy">
                        No Picture refs. Import in Library or cast via Director chat.
                      </p>
                    ) : (
                      <div className="ref-thumb-row">
                        {orderedRefs.map((ref) => (
                          <ProductionRefThumb
                            key={`${ref.role}-${ref.picture_index}-${ref.asset_id}-${ref.file_key || ""}`}
                            refItem={ref}
                            onOpen={setPreviewUrl}
                          />
                        ))}
                      </div>
                    )}

                    <section className="voice-ref-editor" aria-label="Voice References">
                      <div className="refs-section-head">
                        <div>
                          <strong>Voice References</strong>
                          <div className="muted tiny">
                            Audio order maps directly to &lt;Audio 1&gt;–&lt;Audio 3&gt; in the H3 prompt.
                          </div>
                        </div>
                        <span className="voice-slot-count">{draftVoiceRefs.length}/3</span>
                      </div>

                      {selected.source_audio_path ? (
                        <div className="banner voice-source-audio-banner">
                          <strong>Exact source audio controls this run</strong>
                          <span>Voice Reference overrides are disabled.</span>
                        </div>
                      ) : null}

                      <div className="voice-picker-row">
                        <label className="field voice-picker-field">
                          <span>Add Voice reference</span>
                          <select
                            aria-label="Add Voice reference"
                            value={selectedVoiceId}
                            disabled={busy || jobActive || Boolean(selected.source_audio_path) || draftVoiceRefs.length >= 3}
                            onChange={(e) => setSelectedVoiceId(e.target.value)}
                          >
                            <option value="">Choose from Voice Library…</option>
                            {voiceAssets
                              .filter((asset) => !draftVoiceRefs.some((ref) => ref.asset_id === asset.id))
                              .map((asset) => (
                                <option key={asset.id} value={asset.id}>{asset.name}</option>
                              ))}
                          </select>
                        </label>
                        <button
                          type="button"
                          className="btn secondary"
                          disabled={!selectedVoiceId || busy || jobActive || Boolean(selected.source_audio_path)}
                          onClick={addVoiceRef}
                        >
                          Add Voice
                        </button>
                      </div>

                      {draftVoiceRefs.length === 0 ? (
                        <p className="empty-copy">No Voice references selected.</p>
                      ) : (
                        <div className="voice-ref-list">
                          {draftVoiceRefs.map((ref, index) => {
                            const asset = voiceAssetById.get(ref.asset_id);
                            return (
                              <article className="voice-ref-item" key={ref.asset_id}>
                                <div className="voice-ref-index">Audio {index + 1}</div>
                                <div className="voice-ref-main">
                                  <div className="voice-ref-title-row">
                                    <strong>{asset?.name || ref.asset_id}</strong>
                                    {asset?.meta?.duration_s != null ? (
                                      <span className="muted tiny">{String(asset.meta.duration_s)}s</span>
                                    ) : null}
                                  </div>
                                  {asset?.notes ? <div className="muted tiny">{asset.notes}</div> : null}
                                  {asset?.urls?.[ref.file_key || "reference"] ? (
                                    <audio controls preload="metadata" src={asset.urls[ref.file_key || "reference"]} />
                                  ) : (
                                    <div className="muted tiny">Reference audio is unavailable.</div>
                                  )}
                                  <label className="field voice-speaker-field">
                                    <span>Speaker label</span>
                                    <input
                                      value={ref.speaker}
                                      disabled={busy || jobActive || Boolean(selected.source_audio_path)}
                                      onChange={(e) => updateVoiceRefs(draftVoiceRefs.map((item, itemIndex) =>
                                        itemIndex === index ? { ...item, speaker: e.target.value } : item,
                                      ))}
                                    />
                                  </label>
                                </div>
                                <div className="voice-ref-actions">
                                  <button type="button" className="btn secondary compact" aria-label={`Move Audio ${index + 1} up`} disabled={index === 0 || busy || Boolean(selected.source_audio_path)} onClick={() => moveVoiceRef(index, -1)}>↑</button>
                                  <button type="button" className="btn secondary compact" aria-label={`Move Audio ${index + 1} down`} disabled={index === draftVoiceRefs.length - 1 || busy || Boolean(selected.source_audio_path)} onClick={() => moveVoiceRef(index, 1)}>↓</button>
                                  <button type="button" className="btn secondary compact" aria-label={`Remove Audio ${index + 1}`} disabled={busy || Boolean(selected.source_audio_path)} onClick={() => updateVoiceRefs(draftVoiceRefs.filter((_, itemIndex) => itemIndex !== index))}>Remove</button>
                                </div>
                              </article>
                            );
                          })}
                        </div>
                      )}

                      {voicePromptStale && !selected.source_audio_path ? (
                        <p className="field-hint voice-prompt-stale">Prompt will refresh before H3 submission.</p>
                      ) : null}
                      <div className="actions">
                        <button type="button" className="btn secondary" disabled={busy || jobActive || Boolean(selected.source_audio_path) || !voiceRefsDirty} onClick={saveVoiceRefs}>
                          Save Voice refs
                        </button>
                      </div>
                    </section>
                  </div>
                ) : null}

                {tab === "prompt" ? (
                  <div className="tab-panel">
                    {PROMPT_SECTION_KEYS.map(({ key, label }) => (
                      <label key={key} className="field">
                        <span>{label}</span>
                        <textarea
                          rows={key === "detailed_description" ? 6 : 2}
                          value={draftPrompt[key]}
                          disabled={busy || jobActive}
                          onChange={(e) => {
                            setPromptDirty(true);
                            setDraftPrompt((p) => ({ ...p, [key]: e.target.value }));
                          }}
                        />
                      </label>
                    ))}
                    <div className="form-row-2">
                      <label className="field">
                        <span>Dialogue (one line per cue)</span>
                        <textarea
                          rows={3}
                          value={draftDialogue}
                          disabled={busy || jobActive}
                          onChange={(e) => setDraftDialogue(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        <span>Duration (seconds)</span>
                        <input
                          value={draftDuration}
                          disabled={busy || jobActive}
                          onChange={(e) => setDraftDuration(e.target.value)}
                          inputMode="decimal"
                        />
                      </label>
                    </div>
                    <div className="actions">
                      <button
                        type="button"
                        className="btn secondary"
                        disabled={busy || jobActive}
                        onClick={onSavePrompt}
                      >
                        Save prompt
                      </button>
                    </div>
                  </div>
                ) : null}

                {tab === "run" ? (
                  <div className="tab-panel">
                    <label className="field-label">
                      Resolution
                      <select
                        className="field-input"
                        value={resolutionPreset}
                        disabled={busy || jobActive}
                        onChange={(event) =>
                          setResolutionPreset(event.target.value as ResolutionPreset)
                        }
                      >
                        <option value="auto">Auto from project</option>
                        <option value="landscape-480">Landscape · 864×480</option>
                        <option value="landscape-720">Landscape 720p tier · 1280×704</option>
                        <option value="portrait-480">Portrait · 480×864</option>
                        <option value="portrait-720">Portrait 720p tier · 704×1280</option>
                      </select>
                    </label>
                    <ol className="run-steps">
                      <li className={(selected.refs?.length ?? 0) > 0 ? "done" : ""}>
                        Refs cast
                      </li>
                      <li className={promptReady(selected.prompt_sections) ? "done" : ""}>
                        Six-section prompt saved
                      </li>
                      <li
                        className={
                          selected.status === "queued" ||
                          selected.status === "running" ||
                          selected.status === "succeeded"
                            ? "done"
                            : ""
                        }
                      >
                        Submit H3 Ref2AV
                      </li>
                    </ol>
                    {!canSubmit && !jobActive ? (
                      <p className="field-hint">
                        Need at least one ref and a complete prompt (all six sections
                        non-empty). Save prompt on the Prompt tab first.
                      </p>
                    ) : null}

                    {h3Job ? (
                      <div className="run-job-box">
                        <div className={`status-pill status-${h3Job.status}`}>
                          <span className="dot" />
                          {h3Job.status}
                          <span className="job-id">{h3Job.id}</span>
                        </div>
                        <div className="field-hint">
                          Provider: {h3Job.h3_provider === "minimax" ? "MiniMax · Official API" : "Local · ComfyUI"}
                        </div>
                        {h3Job.error ? (
                          <div className="banner error">{h3Job.error}</div>
                        ) : null}
                        {h3Job.outputs?.video?.url || h3Job.outputs?.master?.url ? (
                          <video
                            className="h3-preview"
                            controls
                            src={
                              (h3Job.outputs.video?.url ||
                                h3Job.outputs.master?.url) as string
                            }
                          />
                        ) : null}
                      </div>
                    ) : (
                      <p className="field-hint">No H3 job yet for this shot.</p>
                    )}

                    {providerPicker}

                    <div className="production-submit-actions">
                      <button
                        type="button"
                        className="btn primary"
                        disabled={busy || !canSubmit || jobActive}
                        onClick={onSubmit}
                      >
                        {jobActive ? "H3 running…" : "Submit H3"}
                      </button>
                      {jobActive ? (
                        <button
                          type="button"
                          className="btn danger"
                          disabled={busy}
                          onClick={onCancelJob}
                        >
                          Cancel
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </section>
      </div>

      {previewUrl ? (
        <div
          className="lightbox-overlay"
          role="dialog"
          aria-modal="true"
          onClick={() => setPreviewUrl(null)}
          onKeyDown={(e) => e.key === "Escape" && setPreviewUrl(null)}
        >
          <button
            type="button"
            className="lightbox-close btn secondary sm"
            onClick={() => setPreviewUrl(null)}
          >
            Close
          </button>
          <img
            className="lightbox-img"
            src={previewUrl}
            alt="ref preview"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      ) : null}
    </PageShell>
  );
}
