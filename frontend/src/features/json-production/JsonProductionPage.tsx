import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
} from "react";
import { EMPTY_PROMPT_SECTIONS, type JobStatus, type PromptSections } from "../../shared/api/types";
import { PageShell } from "../../shared/components/PageShell";
import { useProject } from "../../shared/project/ProjectContext";
import { cancelH3Job } from "../production/api";
import { getStoryboard, listJsonShotJobs, putStoryboard, submitJsonShot } from "./api";
import { JsonAssetSlots } from "./JsonAssetSlots";
import { JsonPromptPanel } from "./JsonPromptPanel";
import { JsonShotList } from "./JsonShotList";
import type { JsonProductionDocument, JsonShotJobRecord, ShotFileMaps } from "./types";
import { parseShotJson, parseStoryboardJson, validateShotReadiness } from "./validation";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

type NestedFileMap = Map<string, Map<number, File>>;
type NestedUrlMap = Map<string, Map<number, string>>;

function emptyFiles(): ShotFileMaps {
  return { pictures: new Map(), audio: new Map() };
}

function filesForShot(
  pictures: NestedFileMap,
  audio: NestedFileMap,
  shotId: string,
): ShotFileMaps {
  return {
    pictures: pictures.get(shotId) || new Map(),
    audio: audio.get(shotId) || new Map(),
  };
}

function setNestedFile(map: NestedFileMap, shotId: string, index: number, file: File | null): NestedFileMap {
  const next = new Map(map);
  const slot = new Map(next.get(shotId) || []);
  if (file) slot.set(index, file);
  else slot.delete(index);
  next.set(shotId, slot);
  return next;
}

function revokeUrls(urls: NestedUrlMap) {
  for (const slots of urls.values()) {
    for (const url of slots.values()) URL.revokeObjectURL(url);
  }
}

function compareGenerations(a: JsonShotJobRecord, b: JsonShotJobRecord): number {
  return a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id);
}

function latestGeneration(
  jobs: JsonShotJobRecord[] | undefined,
  storyboardRevision: number | null,
): { job: JsonShotJobRecord; version: number } | null {
  if (!jobs?.length || storyboardRevision == null) return null;
  const ordered = jobs
    .filter((job) => job.json_storyboard_revision === storyboardRevision)
    .sort(compareGenerations);
  if (!ordered.length) return null;
  return { job: ordered[ordered.length - 1], version: ordered.length };
}

function upsertJob(list: JsonShotJobRecord[] | undefined, job: JsonShotJobRecord): JsonShotJobRecord[] {
  const next = [...(list || [])];
  const index = next.findIndex((item) => item.id === job.id);
  if (index >= 0) next[index] = job;
  else next.unshift(job);
  return next;
}

type MobileSection = "prompt" | "references" | "output";
type DesktopInspector = "references" | "output";

export function JsonProductionPage({ active = true, mobile = false }: { active?: boolean; mobile?: boolean } = {}) {
  const { projectId } = useProject();
  const [storyboard, setStoryboard] = useState<JsonProductionDocument | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState<PromptSections>(EMPTY_PROMPT_SECTIONS);
  const [promptDirty, setPromptDirty] = useState(false);
  const [shotJsonEditing, setShotJsonEditing] = useState(false);
  const [shotJsonText, setShotJsonText] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pictureFiles, setPictureFiles] = useState<NestedFileMap>(() => new Map());
  const [audioFiles, setAudioFiles] = useState<NestedFileMap>(() => new Map());
  const [picturePreviews, setPicturePreviews] = useState<NestedUrlMap>(() => new Map());
  const [jobsByShotId, setJobsByShotId] = useState<Map<string, JsonShotJobRecord[]>>(() => new Map());
  const [mobileSection, setMobileSection] = useState<MobileSection>("prompt");
  const [desktopInspector, setDesktopInspector] = useState<DesktopInspector>("references");

  const previewRef = useRef(picturePreviews);
  previewRef.current = picturePreviews;
  const jobsRef = useRef(jobsByShotId);
  jobsRef.current = jobsByShotId;
  const loadGenRef = useRef(0);

  const resetWorkspace = useCallback(() => {
    revokeUrls(previewRef.current);
    setPicturePreviews(new Map());
    setPictureFiles(new Map());
    setAudioFiles(new Map());
    setJobsByShotId(new Map());
    setPasteText("");
    setPromptDirty(false);
    setShotJsonEditing(false);
    setShotJsonText("");
    setDraftPrompt(EMPTY_PROMPT_SECTIONS);
    setSelectedId(null);
    setStoryboard(null);
    setError(null);
  }, []);

  const selected = useMemo(
    () => storyboard?.shots.find((shot) => shot.id === selectedId) || null,
    [storyboard, selectedId],
  );

  const applyStoryboard = useCallback((doc: JsonProductionDocument) => {
    setStoryboard(doc);
    setSelectedId((current) =>
      current && doc.shots.some((shot) => shot.id === current) ? current : doc.shots[0]?.id ?? null,
    );
  }, []);

  const loadStoryboard = useCallback(
    async (id: string) => {
      const gen = ++loadGenRef.current;
      try {
        const doc = await getStoryboard(id);
        if (gen !== loadGenRef.current) return;
        applyStoryboard(doc);
      } catch (e) {
        if (gen !== loadGenRef.current) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [applyStoryboard],
  );

  useEffect(() => {
    resetWorkspace();
    if (!projectId) {
      loadGenRef.current += 1;
      return;
    }
    void loadStoryboard(projectId);
  }, [projectId, loadStoryboard, resetWorkspace]);

  useEffect(() => {
    if (!active || !projectId) return;
    void loadStoryboard(projectId);
  }, [active, projectId, loadStoryboard]);

  useEffect(() => {
    if (!selected) {
      setDraftPrompt(EMPTY_PROMPT_SECTIONS);
      setPromptDirty(false);
      return;
    }
    setDraftPrompt({ ...EMPTY_PROMPT_SECTIONS, ...selected.prompt });
    setPromptDirty(false);
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setShotJsonEditing(false);
    setShotJsonText("");
  }, [selected?.id]);

  useEffect(() => {
    if (!selected || promptDirty) return;
    setDraftPrompt({ ...EMPTY_PROMPT_SECTIONS, ...selected.prompt });
  }, [selected?.prompt, promptDirty]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => revokeUrls(previewRef.current);
  }, []);

  useEffect(() => {
    if (!active || !projectId || !storyboard?.shots.length) return;
    let cancelled = false;

    const refresh = async (shotIds: string[]) => {
      const entries = await Promise.all(
        shotIds.map(async (shotId) => {
          const jobs = await listJsonShotJobs(projectId, shotId, storyboard.revision);
          return [shotId, jobs] as const;
        }),
      );
      if (cancelled) return;
      setJobsByShotId((prev) => {
        const next = new Map(prev);
        for (const [shotId, jobs] of entries) next.set(shotId, jobs);
        return next;
      });
    };

    void refresh(storyboard.shots.map((shot) => shot.id));
    const timer = window.setInterval(() => {
      const activeIds = [...jobsRef.current.entries()]
        .filter(([, jobs]) => jobs.some((job) => ACTIVE.includes(job.status)))
        .map(([shotId]) => shotId);
      if (activeIds.length) void refresh(activeIds);
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, projectId, storyboard?.revision, storyboard?.shots.map((s) => s.id).join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const importJsonText = async (text: string) => {
    if (!projectId) return;
    setError(null);
    let parsed: JsonProductionDocument;
    try {
      parsed = parseStoryboardJson(text);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    if ((storyboard?.shots.length ?? 0) > 0) {
      const ok = window.confirm(
        "Replace the current storyboard? This replaces every shot in the saved JSON document.",
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      const saved = await putStoryboard(projectId, parsed);
      loadGenRef.current += 1;
      revokeUrls(previewRef.current);
      setPicturePreviews(new Map());
      setPictureFiles(new Map());
      setAudioFiles(new Map());
      setJobsByShotId(new Map());
      setPasteText("");
      setPromptDirty(false);
      applyStoryboard(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onJsonFile = (file: File | null) => {
    if (!file) return;
    void file.text().then((text) => importJsonText(text));
  };

  const onPasteJson = (event: ReactClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = event.clipboardData.getData("text/plain");
    if (!pasted) return;
    event.preventDefault();
    const textarea = event.currentTarget;
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    setPasteText(
      `${textarea.value.slice(0, start)}${pasted}${textarea.value.slice(end)}`,
    );
  };

  const onSavePrompt = async () => {
    if (!projectId || !storyboard || !selected) return;
    setError(null);
    setBusy(true);
    try {
      const next: JsonProductionDocument = {
        ...storyboard,
        shots: storyboard.shots.map((shot) =>
          shot.id === selected.id ? { ...shot, prompt: draftPrompt } : shot,
        ),
      };
      const saved = await putStoryboard(projectId, next);
      loadGenRef.current += 1;
      applyStoryboard(saved);
      setPromptDirty(false);
      const updated = saved.shots.find((shot) => shot.id === selected.id);
      if (updated) setDraftPrompt({ ...EMPTY_PROMPT_SECTIONS, ...updated.prompt });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onEditShotJson = () => {
    if (!selected) return;
    setError(null);
    setShotJsonText(
      JSON.stringify(
        promptDirty ? { ...selected, prompt: draftPrompt } : selected,
        null,
        2,
      ),
    );
    setShotJsonEditing(true);
  };

  const onSaveShotJson = async () => {
    if (!projectId || !storyboard || !selected) return;
    setError(null);
    let parsed;
    try {
      parsed = parseShotJson(shotJsonText, selected.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }

    setBusy(true);
    try {
      const next: JsonProductionDocument = {
        ...storyboard,
        shots: storyboard.shots.map((shot) => (shot.id === selected.id ? parsed : shot)),
      };
      const saved = await putStoryboard(projectId, next);
      loadGenRef.current += 1;
      applyStoryboard(saved);

      const pictureIndexes = new Set(parsed.pictures.map((slot) => slot.index));
      const audioIndexes = new Set(parsed.audio.map((slot) => slot.index));
      setPictureFiles((prev) => {
        const nextFiles = new Map(prev);
        nextFiles.set(
          selected.id,
          new Map([...(prev.get(selected.id) || new Map())].filter(([index]) => pictureIndexes.has(index))),
        );
        return nextFiles;
      });
      setAudioFiles((prev) => {
        const nextFiles = new Map(prev);
        nextFiles.set(
          selected.id,
          new Map([...(prev.get(selected.id) || new Map())].filter(([index]) => audioIndexes.has(index))),
        );
        return nextFiles;
      });
      setPicturePreviews((prev) => {
        const nextPreviews = new Map(prev);
        const retained = new Map<number, string>();
        for (const [index, url] of prev.get(selected.id) || new Map()) {
          if (pictureIndexes.has(index)) retained.set(index, url);
          else URL.revokeObjectURL(url);
        }
        nextPreviews.set(selected.id, retained);
        return nextPreviews;
      });

      setDraftPrompt({ ...EMPTY_PROMPT_SECTIONS, ...parsed.prompt });
      setPromptDirty(false);
      setShotJsonEditing(false);
      setShotJsonText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const replacePicturePreview = (shotId: string, index: number, file: File | null) => {
    setPicturePreviews((prev) => {
      const next = new Map(prev);
      const slot = new Map(next.get(shotId) || []);
      const previous = slot.get(index);
      if (previous) URL.revokeObjectURL(previous);
      if (file && file.type.startsWith("image/")) slot.set(index, URL.createObjectURL(file));
      else slot.delete(index);
      next.set(shotId, slot);
      return next;
    });
  };

  const onPictureFile = (index: number, file: File | null) => {
    if (!selected) return;
    setPictureFiles((prev) => setNestedFile(prev, selected.id, index, file));
    replacePicturePreview(selected.id, index, file);
  };

  const onAudioFile = (index: number, file: File | null) => {
    if (!selected) return;
    setAudioFiles((prev) => setNestedFile(prev, selected.id, index, file));
  };

  const onGenerate = async () => {
    if (!projectId || !storyboard || !selected) return;
    const files = filesForShot(pictureFiles, audioFiles, selected.id);
    if (promptDirty || validateShotReadiness(selected, files).length) return;
    setError(null);
    setBusy(true);
    try {
      const job = await submitJsonShot(projectId, selected.id, storyboard.revision, files);
      setJobsByShotId((prev) => {
        const next = new Map(prev);
        next.set(selected.id, upsertJob(next.get(selected.id), job));
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    const job = latestGeneration(
      jobsByShotId.get(selected?.id || ""),
      storyboard?.revision ?? null,
    )?.job;
    if (!job) return;
    setError(null);
    setBusy(true);
    try {
      const updated = await cancelH3Job(job.id);
      if (!selected) return;
      setJobsByShotId((prev) => {
        const next = new Map(prev);
        next.set(
          selected.id,
          upsertJob(next.get(selected.id), { ...job, ...updated, json_shot_id: selected.id }),
        );
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const selectedFiles = selected ? filesForShot(pictureFiles, audioFiles, selected.id) : emptyFiles();
  const readinessErrors = selected ? validateShotReadiness(selected, selectedFiles) : [];
  const selectedGeneration = latestGeneration(
    jobsByShotId.get(selected?.id || ""),
    storyboard?.revision ?? null,
  );
  const selectedJob = selectedGeneration?.job ?? null;
  const selectedJobActive = selectedJob ? ACTIVE.includes(selectedJob.status) : false;
  const canGenerate = Boolean(
    selected && !busy && !promptDirty && !selectedJobActive && readinessErrors.length === 0,
  );

  const statusByShotId = useMemo(() => {
    const map = new Map<string, string>();
    for (const shot of storyboard?.shots || []) {
      const job = latestGeneration(jobsByShotId.get(shot.id), storyboard?.revision ?? null)?.job;
      if (job) {
        map.set(shot.id, job.status);
        continue;
      }
      const files = filesForShot(pictureFiles, audioFiles, shot.id);
      const dirty = shot.id === selectedId && promptDirty;
      if (dirty) map.set(shot.id, "unsaved prompt");
      else if (validateShotReadiness(shot, files).length) map.set(shot.id, "missing files");
      else map.set(shot.id, "ready");
    }
    return map;
  }, [storyboard, jobsByShotId, pictureFiles, audioFiles, selectedId, promptDirty]);

  const hasShots = (storyboard?.shots.length ?? 0) > 0;
  const mobileReadinessText = selectedJobActive
    ? `${selectedJob!.status} on H3`
    : promptDirty
      ? "Save prompt first"
      : readinessErrors.length
        ? `${readinessErrors.length} files missing`
        : selectedJob?.status === "succeeded"
          ? "Output ready"
          : "Ready to generate";

  const promptPanel = selected ? (
    <JsonPromptPanel
      shot={selected}
      draftPrompt={draftPrompt}
      promptDirty={promptDirty}
      busy={busy}
      jsonEditing={shotJsonEditing}
      shotJson={shotJsonText}
      onChangePrompt={(next) => {
        setDraftPrompt(next);
        setPromptDirty(true);
      }}
      onSave={() => void onSavePrompt()}
      onEditJson={onEditShotJson}
      onChangeShotJson={setShotJsonText}
      onSaveShotJson={() => void onSaveShotJson()}
      onCancelShotJson={() => {
        setShotJsonEditing(false);
        setShotJsonText("");
        setError(null);
      }}
    />
  ) : null;

  const assetPanel = selected ? (
    <JsonAssetSlots
      shot={selected}
      files={selectedFiles}
      picturePreviews={picturePreviews.get(selected.id) || new Map()}
      readinessErrors={readinessErrors}
      promptDirty={promptDirty}
      job={selectedJob}
      outputVersion={selectedGeneration?.version ?? null}
      busy={busy}
      onPictureFile={onPictureFile}
      onAudioFile={onAudioFile}
      onGenerate={() => {
        if (!mobile) setDesktopInspector("output");
        void onGenerate();
      }}
      onCancel={() => void onCancel()}
      view={mobile ? (mobileSection === "output" ? "output" : "references") : desktopInspector}
      showActions={!mobile}
    />
  ) : null;

  return (
    <PageShell
      title={mobile ? "JSON Production" : "Production"}
      className="json-production-page"
      subtitle={
        projectId ? (
          <>Import a storyboard JSON, attach Picture and Audio files, then Generate each shot on H3.</>
        ) : (
          "Select a project in the header."
        )
      }
      actions={
        <label className="field json-file-action">
          <span>{mobile ? "Replace JSON" : "JSON file"}</span>
          <input
            type="file"
            accept=".json,application/json"
            disabled={!projectId || busy}
            onChange={(e) => {
              const file = e.target.files?.[0] || null;
              e.target.value = "";
              onJsonFile(file);
            }}
          />
        </label>
      }
    >
      {error ? <div className="banner error">{error}</div> : null}

      {!projectId ? (
        <div className="section-card empty-state-card">
          <p className="empty-copy">Select a project in the header.</p>
        </div>
      ) : !hasShots ? (
        <div className="section-card empty-state-card json-empty-import">
          <p className="empty-copy">
            Empty storyboard. Choose a .json file or paste JSON to import shots.
          </p>
          <label className="field">
            <span>Paste JSON</span>
            <textarea
              rows={12}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              onPaste={onPasteJson}
              spellCheck={false}
            />
          </label>
          <div className="field-hint json-paste-count" aria-live="polite">
            {pasteText.length} characters
          </div>
          <button
            type="button"
            className="btn primary"
            disabled={busy || !pasteText.trim()}
            onClick={() => void importJsonText(pasteText)}
          >
            Import JSON
          </button>
        </div>
      ) : (
        mobile ? <div className="json-mobile-workspace">
          <div className="json-mobile-control-deck">
          <div className="json-mobile-shot-bar">
            <label className="json-mobile-shot-picker">
              <span>Shot</span>
              <select
                aria-label="Current shot"
                value={selectedId || ""}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                {storyboard!.shots.map((shot, index) => (
                  <option key={shot.id} value={shot.id}>
                    {`${index + 1}/${storyboard!.shots.length} · ${shot.title || shot.id} · ${shot.duration_s}s`}
                  </option>
                ))}
              </select>
            </label>
            <span className={`json-mobile-status json-mobile-status-${promptDirty ? "dirty" : selectedJob?.status || (readinessErrors.length ? "missing" : "ready")}`}>
              {promptDirty
                ? "Save prompt"
                : selectedJobActive
                  ? selectedJob!.status
                  : readinessErrors.length
                    ? `${readinessErrors.length} missing`
                    : "Ready"}
            </span>
          </div>

          <nav className="json-mobile-workflow-nav" aria-label="Shot workflow">
            {(["prompt", "references", "output"] as const).map((section) => (
              <button
                key={section}
                type="button"
                className={mobileSection === section ? "active" : ""}
                aria-selected={mobileSection === section}
                onClick={() => setMobileSection(section)}
              >
                {section.charAt(0).toUpperCase() + section.slice(1)}
              </button>
            ))}
          </nav>
          </div>

          <div className="json-mobile-stage">
            {mobileSection === "prompt" ? promptPanel : assetPanel}
          </div>

          <div className="json-mobile-generate-bar">
            <div>
              <strong>{mobileReadinessText}</strong>
              <span>{selected?.title || selected?.id}</span>
            </div>
            <button
              type="button"
              className="btn primary"
              disabled={!canGenerate}
              onClick={() => {
                setMobileSection("output");
                void onGenerate();
              }}
            >
              {selectedJobActive ? "Running…" : "Generate"}
            </button>
            {selectedJobActive ? (
              <button type="button" className="btn danger" disabled={busy} onClick={() => void onCancel()}>
                Cancel
              </button>
            ) : null}
          </div>
        </div> : <div className="json-desktop-workspace">
          <JsonShotList
            shots={storyboard!.shots}
            selectedId={selectedId}
            statusByShotId={statusByShotId}
            onSelect={(id) => {
              setSelectedId(id);
              setDesktopInspector("references");
            }}
          />
          {selected ? (
            <div className="json-desktop-workbench">
              {promptPanel}
              <section className="json-desktop-inspector">
                <nav className="json-desktop-inspector-nav" aria-label="Shot inspector">
                  {(["references", "output"] as const).map((section) => (
                    <button
                      key={section}
                      type="button"
                      className={desktopInspector === section ? "active" : ""}
                      aria-selected={desktopInspector === section}
                      onClick={() => setDesktopInspector(section)}
                    >
                      {section.charAt(0).toUpperCase() + section.slice(1)}
                    </button>
                  ))}
                </nav>
                {assetPanel}
              </section>
            </div>
          ) : (
            <div className="section-card empty-state-card json-prompt-panel">
              <p className="empty-copy">Select a shot from the list.</p>
            </div>
          )}
        </div>
      )}
    </PageShell>
  );
}
