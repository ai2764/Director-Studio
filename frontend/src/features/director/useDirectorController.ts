import { useCallback, useEffect, useRef, useState } from "react";
import { layoutPreviewUrl, type Shot } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import type { SegmentSelection } from "../music/api";
import { continuationFocus } from "./VideoContextPanel";
import {
  cancelDirectorChatSession,
  DirectorChatError,
  chatWithDirectorStream,
  getDirectorChatHistory,
  getDirectorChatSession,
  getDirectorModel,
  getDirectorVramStatus,
  getProject,
  setDirectorModel,
  type ChatMessage,
  type ContextUsage,
  type DirectorChatSessionStatus,
  type PromptRetryRequest,
} from "./api";
import type { DirectorVramStatus } from "./generationStatus";
import type { DirectorChatRequest } from "./DirectorPage";

export const MAX_CHAT_IMAGES = 4;
const MAX_CHAT_IMAGE_BYTES = 20 * 1024 * 1024;
const CHAT_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const IDLE_CHAT_SESSION: DirectorChatSessionStatus = {
  active: false,
  session_id: null,
  started_at: null,
};

export interface PendingChatImage {
  file: File;
  previewUrl: string;
}

export function layoutChatEntries(shot: Shot) {
  const layouts = shot.layout_refs;
  if (layouts.length) {
    const submittedLayoutAssets = new Set(
      shot.refs
        .filter((ref) => ref.role === "layout_ref_frame")
        .map((ref) => ref.asset_id),
    );
    if (submittedLayoutAssets.size === 0 && shot.layout_asset_id) {
      submittedLayoutAssets.add(shot.layout_asset_id);
    }
    return layouts.flatMap((layout) => {
      if (!layout.asset_id) return [];
      if (!submittedLayoutAssets.has(layout.asset_id)) return [];
      if (layout.review_status === "reject") return [];
      if (layout.superseded_by) return [];
      const url = layoutPreviewUrl(layout.asset_id);
      return url
        ? [
            {
              key: `${layout.id}:${layout.asset_id}`,
              assetId: layout.asset_id,
              url,
              purpose: layout.purpose,
            },
          ]
        : [];
    });
  }
  if (!shot.layout_asset_id) return [];
  const url = layoutPreviewUrl(shot.layout_asset_id);
  return url
    ? [
        {
          key: `legacy:${shot.layout_asset_id}`,
          assetId: shot.layout_asset_id,
          url,
          purpose: "reference frame",
        },
      ]
    : [];
}

export function visibleChatImages(images: ChatMessage["images"], shots: Shot[]) {
  return (images ?? []).filter((image) => {
    if (!image.shot_id || !image.url.includes("/api/files/library/layouts/")) {
      return true;
    }
    const shot = shots.find((candidate) => candidate.id === image.shot_id);
    if (!shot) return false;
    return layoutChatEntries(shot).some((entry) => entry.url === image.url);
  });
}

function shouldPollShot(shot: Shot): boolean {
  if (["failed", "blocked"].includes(shot.status)) return false;
  const unresolvedLayout = shot.layout_refs.some(
    (layout) =>
      Boolean(layout.job_id) &&
      !layout.asset_id &&
      layout.job_status !== "failed" &&
      layout.job_status !== "cancelled",
  );
  if (shot.layout_refs.length) {
    // A Layout collection owns reference-frame lifecycle. Shot status still drives
    // unrelated planning/H3 work, but cannot keep a terminal Layout polling.
    return unresolvedLayout || ["planning", "queued", "running"].includes(shot.status);
  }
  return ["planning", "ref_frame_pending", "queued", "running"].includes(shot.status);
}

export function useDirectorController({
  requestedMessage,
  segmentSelection: providedSelection,
}: {
  requestedMessage: DirectorChatRequest | null;
  segmentSelection?: SegmentSelection;
}) {
  const { projectId, project, refreshProjects, createAndSelect } = useProject();
  const [musicSelection, setMusicSelection] = useState<SegmentSelection | undefined>();
  const segmentSelection =
    providedSelection ?? (project?.mode === "mv" ? musicSelection : undefined);
  const [shots, setShots] = useState<Shot[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [pendingImages, setPendingImages] = useState<PendingChatImage[]>([]);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const [videoContextFocusId, setVideoContextFocusId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [vramPollError, setVramPollError] = useState<string | null>(null);
  const [vramStatus, setVramStatus] = useState<DirectorVramStatus | null>(null);
  const [clockNow, setClockNow] = useState(() => new Date());
  const [busy, setBusy] = useState(false);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [liveStatus, setLiveStatus] = useState<string[]>([]);
  const [liveRuntime, setLiveRuntime] = useState("");
  const [liveThink, setLiveThink] = useState("");
  const [liveTokens, setLiveTokens] = useState("");
  const [contextUsage, setContextUsage] = useState<ContextUsage[]>([]);
  const [compactingContext, setCompactingContext] = useState(false);
  const [llmModel, setLlmModel] = useState<string>("");
  const [llmOptions, setLlmOptions] = useState<string[]>([]);
  const [llmProvider, setLlmProvider] = useState("LLM provider");
  const [llmReachable, setLlmReachable] = useState(false);
  const [llmBusy, setLlmBusy] = useState(false);
  const [chatSession, setChatSession] =
    useState<DirectorChatSessionStatus>(IDLE_CHAT_SESSION);
  const [loadedProjectId, setLoadedProjectId] = useState<string | null>(null);

  const chatAbortRef = useRef<AbortController | null>(null);
  const usageProjectRef = useRef(projectId);
  usageProjectRef.current = projectId;
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const seenLayouts = useRef<Set<string>>(new Set());
  const shotRevision = useRef(0);
  const handledRequestId = useRef<string | null>(null);

  const generationLocked = vramStatus?.chat_locked === true;
  const chatActive = chatSession.active;
  const chatDisabled =
    busy || compactingContext || generationLocked || chatActive || !llmModel;

  const addChatImages = (files: FileList | null) => {
    if (!files?.length) return;
    const available = MAX_CHAT_IMAGES - pendingImages.length;
    const selected = Array.from(files).slice(0, available);
    const invalid = selected.find(
      (file) => !CHAT_IMAGE_TYPES.has(file.type) || file.size > MAX_CHAT_IMAGE_BYTES,
    );
    if (invalid) {
      setError(
        CHAT_IMAGE_TYPES.has(invalid.type)
          ? `${invalid.name} exceeds the 20 MB image limit.`
          : `${invalid.name} is not a supported JPG, PNG, or WebP image.`,
      );
      return;
    }
    if (files.length > available) {
      setError(`Director chat accepts at most ${MAX_CHAT_IMAGES} images.`);
    } else {
      setError(null);
    }
    setPendingImages((current) => [
      ...current,
      ...selected.map((file) => ({ file, previewUrl: URL.createObjectURL(file) })),
    ]);
  };

  const removeChatImage = (index: number) => {
    setPendingImages((current) => {
      const removed = current[index];
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((_, candidateIndex) => candidateIndex !== index);
    });
  };

  const replaceShots = useCallback((nextShots: Shot[]) => {
    shotRevision.current += 1;
    setShots(nextShots);
  }, []);

  useEffect(() => {
    setSelectedShotId((current) =>
      current && shots.some((shot) => shot.id === current) ? current : (shots[0]?.id ?? null),
    );
  }, [shots]);

  const loadLlmModels = useCallback(async () => {
    try {
      const st = await getDirectorModel();
      setLlmModel(st.model || "");
      setLlmProvider(st.provider || "LLM provider");
      setLlmReachable(st.reachable !== false);
      setLlmOptions([...(st.available || [])]);
    } catch {
      setLlmReachable(false);
      setLlmOptions([]);
    }
  }, []);

  useEffect(() => {
    void loadLlmModels();
  }, [loadLlmModels]);

  const refreshVramStatus = useCallback(async () => {
    try {
      const status = await getDirectorVramStatus();
      setVramStatus(status);
      setClockNow(new Date());
      setVramPollError(null);
    } catch (cause) {
      setVramPollError(
        `Could not refresh generation status: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
    }
  }, []);

  useEffect(() => {
    if (!projectId) {
      setVramStatus(null);
      setVramPollError(null);
      return;
    }
    void refreshVramStatus();
    const timer = window.setInterval(() => void refreshVramStatus(), 2500);
    return () => window.clearInterval(timer);
  }, [projectId, refreshVramStatus]);

  useEffect(() => {
    if (!generationLocked) return;
    const timer = window.setInterval(() => setClockNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, [generationLocked]);

  const onChangeLlm = async (model: string) => {
    if (!model || model === llmModel) return;
    setLlmBusy(true);
    setError(null);
    try {
      const st = await setDirectorModel(model, true);
      setLlmModel(st.model);
      setContextUsage([]);
      if (st.available?.length) setLlmOptions(st.available);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLlmBusy(false);
    }
  };

  const loadProject = useCallback(
    async (id: string) => {
      const detail = await getProject(id);
      replaceShots(detail.shots);
      return detail;
    },
    [replaceShots],
  );

  useEffect(() => {
    seenLayouts.current = new Set();
    setLoadedProjectId(null);
    setContextUsage([]);
    setChatSession(IDLE_CHAT_SESSION);
    if (!projectId) {
      replaceShots([]);
      setMessages([
        {
          role: "assistant",
          content:
            "Select or create a project in the header. Then talk to me about the story, shots, or composition, and I will help move the project forward.",
        },
      ]);
      return;
    }
    setBusy(true);
    setError(null);
    setPollError(null);
    Promise.all([
      loadProject(projectId),
      getDirectorChatHistory(projectId).catch(() => []),
      getDirectorChatSession(projectId),
    ])
      .then(([d, savedMessages, session]) => {
        const entries = d.shots.flatMap((shot) =>
          layoutChatEntries(shot).map((entry) => ({ shot, entry })),
        );
        for (const { entry } of entries) {
          seenLayouts.current.add(entry.key);
        }
        setMessages(
          savedMessages.length
            ? savedMessages
            : [
                {
                  role: "assistant",
                  content:
                    `Working on **${d.project.name}**.\n` +
                    (d.shots.length
                      ? `${d.shots.length} shot${d.shots.length === 1 ? "" : "s"} planned. Ask about progress, revise an idea, generate reference frames, or write an H3 prompt.`
                      : `The script is about ${(d.project.script_text || "").length} characters long. Send me the story or ask me to plan the shots.`),
                  images: entries.map(({ shot, entry }) => ({
                    url: entry.url,
                    caption: `${shot.title} · ${entry.purpose}`,
                    shot_id: shot.id,
                  })),
                },
              ],
        );
        setChatSession(session);
        if (session.active) {
          setLiveRuntime("LLM busy — response is still running");
        }
        setLoadedProjectId(projectId);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [projectId, loadProject, replaceShots]);

  useEffect(() => {
    if (!projectId || !chatActive) return;
    let disposed = false;
    const poll = async () => {
      try {
        const session = await getDirectorChatSession(projectId);
        if (disposed) return;
        if (session.active) {
          setChatSession(session);
          setLiveRuntime("LLM busy — response is still running");
          return;
        }
        setChatSession(IDLE_CHAT_SESSION);
        setLiveStatus([]);
        setLiveRuntime("");
        setLiveThink("");
        setLiveTokens("");
        const [savedMessages] = await Promise.all([
          getDirectorChatHistory(projectId),
          loadProject(projectId),
          refreshProjects(),
        ]);
        if (!disposed) setMessages(savedMessages);
      } catch (cause) {
        if (!disposed) {
          setPollError(
            `Could not refresh Director response status: ${cause instanceof Error ? cause.message : String(cause)}`,
          );
        }
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [chatActive, loadProject, projectId, refreshProjects]);

  useEffect(() => {
    if (!projectId) return;
    const active = shots.some(shouldPollShot);
    if (!active) return;
    const t = window.setInterval(() => {
      const revisionAtRequest = shotRevision.current;
      getProject(projectId)
        .then((d) => {
          if (revisionAtRequest !== shotRevision.current) return;
          replaceShots(d.shots);
          setPollError(null);
          const fresh = d.shots.flatMap((shot) =>
            layoutChatEntries(shot)
              .filter((entry) => !seenLayouts.current.has(entry.key))
              .map((entry) => ({ shot, entry })),
          );
          if (fresh.length) {
            for (const { entry } of fresh) {
              seenLayouts.current.add(entry.key);
            }
            setMessages((m) => [
              ...m,
              {
                role: "assistant",
                content: `Reference frame${fresh.length === 1 ? " is" : "s are"} ready for review: ${fresh.map(({ shot, entry }) => `${shot.title} (${entry.purpose})`).join(", ")}`,
                images: fresh.map(({ shot, entry }) => ({
                  url: entry.url,
                  caption: `${shot.title} · ${entry.purpose}`,
                  shot_id: shot.id,
                })),
              },
            ]);
          }
        })
        .catch((cause) => {
          setPollError(
            `Could not refresh Layout status: ${cause instanceof Error ? cause.message : String(cause)}`,
          );
        });
    }, 2500);
    return () => window.clearInterval(t);
  }, [projectId, replaceShots, shots]);

  useEffect(() => {
    const log = chatLogRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, busy, liveStatus, liveRuntime, liveThink, liveTokens]);

  const send = async (
    text?: string,
    options?: { preserveComposer?: boolean; promptRetry?: PromptRetryRequest },
  ) => {
    const preserveComposer = options?.preserveComposer === true;
    const typedMessage = (text ?? draft).trim();
    const message =
      typedMessage || (pendingImages.length ? "Please analyze the attached image(s)." : "");
    if (!message || chatDisabled) return;
    if (!projectId) {
      setBusy(true);
      setError(null);
      try {
        const name =
          message.length < 40 && !message.includes("\n")
            ? message
            : message.split("\n")[0].slice(0, 40) || "Chat Project";
        const script = message.length >= 40 || message.includes("\n") ? message : "";
        const p = await createAndSelect(name, script);
        setMessages((m) => [
          ...m,
          { role: "user", content: message },
          {
            role: "assistant",
            content: script
              ? `Created **${p.name}** and saved the script. Ask me to plan the shots when you are ready.`
              : `Created **${p.name}**. Send me the story or script to get started.`,
          },
        ]);
        setDraft("");
        await refreshProjects();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
      return;
    }

    const outgoingDraft = draft;
    const outgoingImages = preserveComposer ? [] : [...pendingImages];
    const optimisticMessageId = `director-local-${Date.now()}-${Math.random()}`;
    if (!preserveComposer) setDraft("");
    const requestMessage = message;
    setContextUsage([]);
    if (!preserveComposer) setPendingImages([]);
    setMessages((m) => [
      ...m,
      {
        id: optimisticMessageId,
        role: "user",
        content: message,
        images: outgoingImages.map((image) => ({
          url: image.previewUrl,
          caption: image.file.name,
        })),
      },
    ]);
    setBusy(true);
    setError(null);
    setLiveStatus([]);
    setLiveRuntime("");
    setLiveThink("");
    setLiveTokens("");
    const controller = new AbortController();
    chatAbortRef.current = controller;
    setChatSession({
      active: true,
      session_id: null,
      started_at: new Date().toISOString(),
    });
    try {
      const handlers = {
        onContextUsage: (usage: ContextUsage) => {
          if (
            chatAbortRef.current !== controller ||
            controller.signal.aborted ||
            usageProjectRef.current !== projectId
          )
            return;
          setContextUsage((calls) => {
            const existing = calls.findIndex((call) => call.call_id === usage.call_id);
            return (
              existing < 0
                ? [...calls, usage]
                : calls.map((call, index) => (index === existing ? usage : call))
            ).slice(-64);
          });
        },
        onStatus: (t: string) => setLiveStatus((s) => [...s.slice(-40), t]),
        onRuntime: (t: string) => setLiveRuntime(t),
        onThink: (t: string) => setLiveThink((prev) => prev + t),
        onToken: (t: string) => setLiveTokens((prev) => prev + t),
        onTool: (t: string) => setLiveStatus((s) => [...s.slice(-40), t]),
      };
      const res = options?.promptRetry
        ? await chatWithDirectorStream(
            projectId,
            requestMessage,
            [],
            handlers,
            [],
            controller.signal,
            options.promptRetry,
          )
        : outgoingImages.length
          ? segmentSelection
            ? await chatWithDirectorStream(
                projectId,
                requestMessage,
                [],
                handlers,
                outgoingImages.map((image) => image.file),
                controller.signal,
                undefined,
                segmentSelection,
              )
            : await chatWithDirectorStream(
                projectId,
                requestMessage,
                [],
                handlers,
                outgoingImages.map((image) => image.file),
                controller.signal,
              )
          : segmentSelection
            ? await chatWithDirectorStream(
                projectId,
                requestMessage,
                [],
                handlers,
                [],
                controller.signal,
                undefined,
                segmentSelection,
              )
            : await chatWithDirectorStream(
                projectId,
                requestMessage,
                [],
                handlers,
                [],
                controller.signal,
              );
      const images = (res.images || []).filter((img) => img.url);
      for (const img of images) {
        const sid = img.shot_id;
        if (sid) {
          const sh = res.shots.find((s) => s.id === sid);
          if (sh) {
            for (const entry of layoutChatEntries(sh)) {
              if (entry.url === img.url) seenLayouts.current.add(entry.key);
            }
          }
        }
      }
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: res.reply,
          images: images.length ? images : undefined,
          thinking: (res.thinking || "").trim() || undefined,
          steps: res.steps?.length ? res.steps : undefined,
          prompt_retry: res.prompt_retry,
        },
      ]);
      replaceShots(res.shots);
      const focus = continuationFocus(res.actions || [], res.shots);
      if (focus) {
        setSelectedShotId(focus);
        setVideoContextFocusId(focus);
      }
      await refreshProjects();
    } catch (e) {
      if (
        controller.signal.aborted ||
        (e instanceof DOMException && e.name === "AbortError")
      ) {
        return;
      }
      if (e instanceof DirectorChatError && e.code === "GPU_GENERATION_ACTIVE") {
        if (!preserveComposer) {
          setDraft(outgoingDraft);
          setPendingImages(outgoingImages);
        }
        setMessages((current) =>
          current.filter((message) => message.id !== optimisticMessageId),
        );
        void refreshVramStatus();
        return;
      }
      const err = e instanceof Error ? e.message : String(e);
      setError(err);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `Something went wrong: ${err}`,
        },
      ]);
    } finally {
      if (chatAbortRef.current === controller) {
        chatAbortRef.current = null;
      }
      if (usageProjectRef.current === projectId) {
        setContextUsage((calls) =>
          calls.map((call) =>
            call.status === "running"
              ? { ...call, status: controller.signal.aborted ? "cancelled" : "failed" }
              : call,
          ),
        );
      }
      setChatSession(IDLE_CHAT_SESSION);
      setBusy(false);
      setLiveStatus([]);
      setLiveRuntime("");
      setLiveThink("");
      setLiveTokens("");
    }
  };

  const cancelChat = async () => {
    if (!projectId || !chatActive || llmBusy) return;
    setLlmBusy(true);
    setError(null);
    try {
      chatAbortRef.current?.abort();
      await cancelDirectorChatSession(projectId);
      setChatSession(IDLE_CHAT_SESSION);
      setMessages(await getDirectorChatHistory(projectId));
      setLiveStatus([]);
      setLiveRuntime("");
      setLiveThink("");
      setLiveTokens("");
      setBusy(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLlmBusy(false);
    }
  };

  useEffect(() => {
    if (
      !requestedMessage ||
      requestedMessage.projectId !== projectId ||
      loadedProjectId !== projectId ||
      chatDisabled ||
      handledRequestId.current === requestedMessage.id
    )
      return;
    handledRequestId.current = requestedMessage.id;
    void send(requestedMessage.message);
  }, [requestedMessage, projectId, loadedProjectId, chatDisabled]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateShot = (updated: Shot) => {
    shotRevision.current += 1;
    setShots((current) => current.map((shot) => (shot.id === updated.id ? updated : shot)));
  };

  const selectedShotIndex = Math.max(0, shots.findIndex((shot) => shot.id === selectedShotId));
  const selectedShot = shots[selectedShotIndex] ?? null;

  return {
    projectId,
    project,
    shots,
    messages,
    draft,
    setDraft,
    pendingImages,
    selectedShotId,
    setSelectedShotId,
    selectedShotIndex,
    selectedShot,
    videoContextFocusId,
    error,
    pollError,
    vramPollError,
    vramStatus,
    clockNow,
    busy,
    lightbox,
    setLightbox,
    liveStatus,
    liveRuntime,
    liveThink,
    liveTokens,
    contextUsage,
    setCompactingContext,
    llmModel,
    llmOptions,
    llmProvider,
    llmReachable,
    llmBusy,
    chatActive,
    chatDisabled,
    generationLocked,
    chatLogRef,
    setMusicSelection,
    addChatImages,
    removeChatImage,
    onChangeLlm,
    send,
    cancelChat,
    updateShot,
  };
}
