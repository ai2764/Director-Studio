import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
} from "react";
import { EMPTY_PROMPT_SECTIONS, type JobStatus, type PromptSections } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import {
  cancelH3Job,
  getH3ProviderStatus,
  type H3Provider,
  type H3ProviderStatus,
} from "../production/api";
import {
  clearJsonShotAsset,
  getStoryboard,
  listJsonShotAssets,
  listJsonShotJobs,
  putJsonShotAsset,
  putStoryboard,
  submitJsonShot,
} from "./api";
import type {
  JsonProductionAssetValue,
  JsonProductionDocument,
  JsonProductionStoredAsset,
  JsonShotJobRecord,
  ShotFileMaps,
} from "./types";
import { parseShotJson, parseStoryboardJson, validateShotReadiness } from "./validation";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

type NestedFileMap = Map<string, ShotFileMaps["pictures"]>;
type NestedUrlMap = Map<string, Map<number, string>>;

export type JsonMobileSection = "prompt" | "references" | "output";
export type JsonDesktopInspector = "references" | "output";

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

function setNestedFile(
  map: NestedFileMap,
  shotId: string,
  index: number,
  file: JsonProductionAssetValue | null,
): NestedFileMap {
  const next = new Map(map);
  const slot = new Map(next.get(shotId) || []);
  if (file) slot.set(index, file);
  else slot.delete(index);
  next.set(shotId, slot);
  return next;
}

function mapsFromStoredAssets(assets: JsonProductionStoredAsset[]): {
  pictures: NestedFileMap;
  audio: NestedFileMap;
} {
  const pictures: NestedFileMap = new Map();
  const audio: NestedFileMap = new Map();
  for (const asset of assets) {
    const target = asset.kind === "picture" ? pictures : audio;
    const shotAssets = new Map(target.get(asset.shot_id) || []);
    shotAssets.set(asset.index, asset);
    target.set(asset.shot_id, shotAssets);
  }
  return { pictures, audio };
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

function upsertJob(
  list: JsonShotJobRecord[] | undefined,
  job: JsonShotJobRecord,
): JsonShotJobRecord[] {
  const next = [...(list || [])];
  const index = next.findIndex((item) => item.id === job.id);
  if (index >= 0) next[index] = job;
  else next.unshift(job);
  return next;
}

/** State, polling effects, and action handlers for JsonProductionPage. */
export function useJsonProductionController({ active }: { active: boolean }) {
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
  const [jobsByShotId, setJobsByShotId] = useState<Map<string, JsonShotJobRecord[]>>(
    () => new Map(),
  );
  const [mobileSection, setMobileSection] = useState<JsonMobileSection>("prompt");
  const [desktopInspector, setDesktopInspector] = useState<JsonDesktopInspector>("references");
  const [h3ProviderStatus, setH3ProviderStatus] = useState<H3ProviderStatus | null>(null);
  const [h3Provider, setH3Provider] = useState<H3Provider>("local");

  const previewRef = useRef(picturePreviews);
  previewRef.current = picturePreviews;
  const jobsRef = useRef(jobsByShotId);
  jobsRef.current = jobsByShotId;
  const loadGenRef = useRef(0);

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
        const [doc, assets] = await Promise.all([getStoryboard(id), listJsonShotAssets(id)]);
        if (gen !== loadGenRef.current) return;
        applyStoryboard(doc);
        const restored = mapsFromStoredAssets(assets);
        setPictureFiles(restored.pictures);
        setAudioFiles(restored.audio);
      } catch (e) {
        if (gen !== loadGenRef.current) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [applyStoryboard],
  );

  useEffect(() => {
    resetWorkspace();
    loadGenRef.current += 1;
  }, [projectId, resetWorkspace]);

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
    if (!active || !projectId || !selectedId || !storyboard?.shots.some((shot) => shot.id === selectedId)) {
      return;
    }
    let cancelled = false;

    const refresh = async () => {
      const jobs = await listJsonShotJobs(projectId, selectedId, storyboard.revision);
      if (!cancelled) {
        setJobsByShotId((prev) => new Map(prev).set(selectedId, jobs));
      }
    };

    void refresh().catch(() => undefined);
    const timer = window.setInterval(() => {
      const jobs = jobsRef.current.get(selectedId);
      if (jobs?.some((job) => ACTIVE.includes(job.status))) {
        void refresh().catch(() => undefined);
      }
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, projectId, selectedId, storyboard?.revision]); // eslint-disable-line react-hooks/exhaustive-deps

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
    setPasteText(`${textarea.value.slice(0, start)}${pasted}${textarea.value.slice(end)}`);
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
      JSON.stringify(promptDirty ? { ...selected, prompt: draftPrompt } : selected, null, 2),
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
          new Map(
            [...(prev.get(selected.id) || new Map())].filter(([index]) =>
              pictureIndexes.has(index),
            ),
          ),
        );
        return nextFiles;
      });
      setAudioFiles((prev) => {
        const nextFiles = new Map(prev);
        nextFiles.set(
          selected.id,
          new Map(
            [...(prev.get(selected.id) || new Map())].filter(([index]) => audioIndexes.has(index)),
          ),
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

  const onPictureFile = async (index: number, file: File | null) => {
    if (!projectId || !selected) return;
    const previous = pictureFiles.get(selected.id)?.get(index) || null;
    setError(null);
    setBusy(true);
    try {
      if (file) {
        setPictureFiles((prev) => setNestedFile(prev, selected.id, index, file));
        replacePicturePreview(selected.id, index, file);
        const saved = await putJsonShotAsset(projectId, selected.id, "picture", index, file);
        setPictureFiles((prev) => setNestedFile(prev, selected.id, index, saved));
      } else {
        await clearJsonShotAsset(projectId, selected.id, "picture", index);
        setPictureFiles((prev) => setNestedFile(prev, selected.id, index, null));
        replacePicturePreview(selected.id, index, null);
      }
    } catch (e) {
      setPictureFiles((prev) => setNestedFile(prev, selected.id, index, previous));
      replacePicturePreview(selected.id, index, null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onAudioFile = async (index: number, file: File | null) => {
    if (!projectId || !selected) return;
    const previous = audioFiles.get(selected.id)?.get(index) || null;
    setError(null);
    setBusy(true);
    try {
      if (file) {
        setAudioFiles((prev) => setNestedFile(prev, selected.id, index, file));
        const saved = await putJsonShotAsset(projectId, selected.id, "audio", index, file);
        setAudioFiles((prev) => setNestedFile(prev, selected.id, index, saved));
      } else {
        await clearJsonShotAsset(projectId, selected.id, "audio", index);
        setAudioFiles((prev) => setNestedFile(prev, selected.id, index, null));
      }
    } catch (e) {
      setAudioFiles((prev) => setNestedFile(prev, selected.id, index, previous));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onGenerate = async () => {
    if (!projectId || !storyboard || !selected) return;
    const files = filesForShot(pictureFiles, audioFiles, selected.id);
    if (promptDirty || validateShotReadiness(selected, files).length) return;
    setError(null);
    setBusy(true);
    try {
      const job = await submitJsonShot(projectId, selected.id, storyboard.revision, h3Provider);
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

  const selectedFiles = selected
    ? filesForShot(pictureFiles, audioFiles, selected.id)
    : emptyFiles();
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

  return {
    projectId,
    storyboard,
    selectedId,
    setSelectedId,
    selected,
    draftPrompt,
    setDraftPrompt,
    promptDirty,
    setPromptDirty,
    shotJsonEditing,
    setShotJsonEditing,
    shotJsonText,
    setShotJsonText,
    pasteText,
    setPasteText,
    error,
    setError,
    busy,
    picturePreviews,
    mobileSection,
    setMobileSection,
    desktopInspector,
    setDesktopInspector,
    h3ProviderStatus,
    h3Provider,
    setH3Provider,
    selectedFiles,
    readinessErrors,
    selectedGeneration,
    selectedJob,
    selectedJobActive,
    canGenerate,
    statusByShotId,
    hasShots,
    mobileReadinessText,
    importJsonText,
    onJsonFile,
    onPasteJson,
    onSavePrompt,
    onEditShotJson,
    onSaveShotJson,
    onPictureFile,
    onAudioFile,
    onGenerate,
    onCancel,
  };
}
