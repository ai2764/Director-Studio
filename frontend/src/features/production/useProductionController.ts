import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  EMPTY_PROMPT_SECTIONS,
  layoutPreviewUrl,
  type H3ActiveProfile,
  type JobStatus,
  type PromptSections,
  type Shot,
  type ShotVoiceRef,
} from "../../shared/api/types";
import { fetchH3Profiles } from "../../shared/api/client";
import { useProject } from "../../shared/project/ProjectContext";
import { promptReady } from "../../shared/shotWorkflowStatus";
import { getVideoContextStatus } from "../director/api";
import { listLibraryAssets, type LibraryAsset } from "../library/api";
import {
  cancelH3Job,
  deleteLayout,
  getH3Job,
  getH3ProviderStatus,
  getLocalH3Resolutions,
  getProject,
  patchShot,
  submitShot,
  type H3JobRecord,
  type H3Provider,
  type H3ProviderStatus,
  type LocalH3Resolution,
} from "./api";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

type ProjectLoadState = {
  projectId: string | null;
  status: "idle" | "loading" | "loaded" | "error";
};

export type ProductionDrawerTab = "layout" | "refs" | "prompt" | "run";

/** State, polling effects, and mutation handlers for ProductionPage. */
export function useProductionController({
  active,
  mobile,
}: {
  active: boolean;
  mobile: boolean;
}) {
  const { projectId } = useProject();
  const [shots, setShots] = useState<Shot[]>([]);
  const [projectLoad, setProjectLoad] = useState<ProjectLoadState>(() => ({
    projectId,
    status: projectId ? "loading" : "idle",
  }));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState<PromptSections>(EMPTY_PROMPT_SECTIONS);
  const [promptDirty, setPromptDirty] = useState(false);
  const [draftDialogue, setDraftDialogue] = useState("");
  const [draftDuration, setDraftDuration] = useState("8");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [h3Job, setH3Job] = useState<H3JobRecord | null>(null);
  const [workflowProfile, setWorkflowProfile] = useState<H3ActiveProfile | null>(null);
  const [workflowProfileError, setWorkflowProfileError] = useState<string | null>(null);
  const [tab, setTab] = useState<ProductionDrawerTab>("layout");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [voiceAssets, setVoiceAssets] = useState<LibraryAsset[]>([]);
  const [draftVoiceRefs, setDraftVoiceRefs] = useState<ShotVoiceRef[]>([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState("");
  const [voiceRefsDirty, setVoiceRefsDirty] = useState(false);
  const [materialEditorOpen, setMaterialEditorOpen] = useState(false);
  const [h3ProviderStatus, setH3ProviderStatus] = useState<H3ProviderStatus | null>(null);
  const [h3Provider, setH3Provider] = useState<H3Provider>("local");
  const [localResolutions, setLocalResolutions] = useState<LocalH3Resolution[]>([]);
  const [resolutionPreset, setResolutionPreset] = useState<string>("auto");
  const [managedActive, setManagedActive] = useState(false);
  const [contextSize, setContextSize] = useState<{
    shotId: string;
    width: number;
    height: number;
  } | null>(null);

  const profileRequest = useRef(0);

  const refreshWorkflowProfile = useCallback(async () => {
    const request = ++profileRequest.current;
    try {
      const current = await fetchH3Profiles();
      if (request === profileRequest.current) {
        setWorkflowProfile(current.active);
        setWorkflowProfileError(null);
      }
    } catch (err) {
      if (request === profileRequest.current) {
        setWorkflowProfile(null);
        setWorkflowProfileError(err instanceof Error ? err.message : String(err));
      }
    }
  }, []);

  useEffect(() => {
    if (active) void refreshWorkflowProfile();
    return () => {
      profileRequest.current += 1;
    };
  }, [active, refreshWorkflowProfile]);

  useEffect(() => {
    if (h3Job && h3Job.h3_provider !== "minimax" && !ACTIVE.includes(h3Job.status)) {
      void refreshWorkflowProfile();
    }
  }, [h3Job?.id, h3Job?.status, h3Job?.h3_provider, refreshWorkflowProfile]);

  const selected = useMemo(
    () => shots.find((s) => s.id === selectedId) || null,
    [shots, selectedId],
  );
  const continuesPrevious = selected?.video_context?.mode === "previous_shot";

  useEffect(() => {
    setContextSize(null);
    if (!selected || !continuesPrevious) return;
    let cancelled = false;
    getVideoContextStatus(selected.id)
      .then((status) => {
        if (!cancelled && status.resolution) {
          setContextSize({ shotId: selected.id, ...status.resolution });
        }
      })
      .catch(() => {
        /* Submission rechecks the source and reports a missing video. */
      });
    return () => {
      cancelled = true;
    };
  }, [selected, continuesPrevious]);

  const currentLayout = useMemo(() => {
    if (!selected) return null;
    return (
      selected.layout_refs.find((layout) => layout.asset_id === selected.layout_asset_id) ||
      [...selected.layout_refs].reverse().find((layout) => layout.asset_id) ||
      null
    );
  }, [selected]);

  const loadProject = useCallback(async (id: string) => {
    const detail = await getProject(id);
    setShots(detail.shots);
    setProjectLoad({ projectId: id, status: "loaded" });
    return detail;
  }, []);

  useEffect(() => {
    setSelectedId(null);
    setH3Job(null);
    setManagedActive(false);
    setShots([]);
    setProjectLoad({
      projectId,
      status: projectId ? "loading" : "idle",
    });
  }, [projectId]);

  // Pages stay mounted across top-level tab switches. Refresh when Production
  // becomes visible so Director-side prompt/layout changes are not stale.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    setError(null);
    setProjectLoad((current) =>
      current.projectId === projectId && current.status === "loaded"
        ? current
        : { projectId, status: "loading" },
    );
    getProject(projectId)
      .then((detail) => {
        if (cancelled) return;
        setShots(detail.shots);
        setProjectLoad({ projectId, status: "loaded" });
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) {
          setProjectLoad((current) =>
            current.projectId === projectId && current.status === "loaded"
              ? current
              : { projectId, status: "error" },
          );
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [active, projectId]);

  const projectLoading = Boolean(
    projectId && (projectLoad.projectId !== projectId || projectLoad.status === "loading"),
  );
  const projectFailed = Boolean(
    projectId && projectLoad.projectId === projectId && projectLoad.status === "error",
  );

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    getLocalH3Resolutions()
      .then(({ presets }) => {
        if (!cancelled) setLocalResolutions(presets);
      })
      .catch(() => {
        if (!cancelled) setLocalResolutions([]);
      });
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
    if (!active) return;
    let cancelled = false;
    const tick = () => {
      getH3Job(jobId)
        .then((j) => {
          if (!cancelled) {
            setH3Job(j);
            if (!ACTIVE.includes(j.status)) window.clearInterval(t);
          }
        })
        .catch(() => undefined);
    };
    tick();
    const t = window.setInterval(tick, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [active, selected?.h3_job_id]);

  useEffect(() => {
    if (!active || !projectId) return;
    const running = shots.some((s) => ACTIVE.includes(s.status as JobStatus));
    if (!running) return;
    const t = window.setInterval(() => {
      getProject(projectId)
        .then((d) => setShots(d.shots))
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(t);
  }, [active, projectId, shots]);

  const replaceShot = useCallback((updated: Shot) => {
    setShots((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }, []);

  const jobActive = h3Job ? ACTIVE.includes(h3Job.status) : false;

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
        h3Provider !== "local" || continuesPrevious || resolutionPreset === "auto"
          ? undefined
          : localResolutions.find((preset) => preset.id === resolutionPreset);
      if (h3Provider === "local") await refreshWorkflowProfile();
      replaceShot(
        await submitShot(
          selected.id,
          h3Provider,
          resolution && {
            width: resolution.width,
            height: resolution.height,
          },
        ),
      );
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

  return {
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
  };
}
