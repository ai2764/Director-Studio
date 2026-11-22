import { useEffect, useRef, useState } from "react";
import { parseError } from "../../shared/api/client";
import type { Shot, ShotRef } from "../../shared/api/types";
import { formatDialogue, replaceShotMaterials, suggestShotMaterials } from "./api";
import type { PromptCheck } from "./promptCheck";
import type { AssistantReply, AssistantTurn } from "./DescriptionAssistant";
import { useOperationTimings } from "./OperationTimings";

export async function json<T>(url: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method,
    signal,
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export interface Suggestion {
  title: string;
  duration_s: number;
  description: string;
  shot_type: string;
  camera_angle: string;
  camera_motion: string;
  composition: string;
  dialogue: string[];
  questions: string[];
  original_description: string;
}

export interface ShotEditorControllerParams {
  shot: Shot;
  busy: boolean;
  onSaved: (shot: Shot) => void;
  onWrite: (shot: Shot, revisionRequest?: string) => Promise<Shot | void>;
  onDelete: (shot: Shot) => Promise<void>;
  writing: boolean;
  onCancel: () => Promise<void>;
  onAssistingChange?: (busy: boolean) => void;
}

/** Encapsulates ShotEditor's form state, prompt-check lifecycle, AI assist, and auto-run pipeline. */
export function useShotEditorController({
  shot,
  busy,
  onSaved,
  onWrite,
  onDelete,
  writing,
  onCancel,
  onAssistingChange,
}: ShotEditorControllerParams) {
  const generationRef = useRef<{ generate: (source: Shot) => Promise<void>; prepare?: () => void; clear?: () => void }>(
    null,
  );
  const chainPending = useRef(false);
  const [description, setDescription] = useState(shot.script_beat);
  const [title, setTitle] = useState(shot.title);
  const [duration, setDuration] = useState(shot.duration_s);
  const [dialogue, setDialogue] = useState(formatDialogue(shot));
  const [camera, setCamera] = useState({
    shot_type: shot.shot_type || "",
    camera_angle: shot.camera_angle || "",
    camera_motion: shot.camera_motion || "",
    composition: shot.composition || "",
  });
  const [beforeAmendment, setBeforeAmendment] = useState<{
    title: string;
    description: string;
    duration: number;
    dialogue: string;
    camera: typeof camera;
  } | null>(null);
  const [original, setOriginal] = useState(shot.script_beat);
  const [version, setVersion] = useState("");
  const [working, setWorking] = useState("");
  const [autoStage, setAutoStage] = useState<"brief" | "references" | "prompt" | null>(null);
  const timings = useOperationTimings(shot.id, !!working || writing);
  const latestTiming = (stage: string) => [...timings].reverse().find((entry) => entry.stage === stage);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    setElapsed(0);
    if (!working) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [working]);

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [materials, setMaterials] = useState(false);
  const [removedRef, setRemovedRef] = useState<{ before: ShotRef[]; after: ShotRef[] } | null>(null);
  const [autoRefUndo, setAutoRefUndo] = useState<{ before: ShotRef[]; after: ShotRef[] } | null>(null);
  const [promptCheck, setPromptCheck] = useState<PromptCheck | null>(null);
  const [checkingPrompt, setCheckingPrompt] = useState(true);
  const [checkedShot, setCheckedShot] = useState<Shot | null>(null);
  const [checkError, setCheckError] = useState("");

  useEffect(() => {
    let current = true;
    setCheckingPrompt(true);
    setCheckError("");
    void json<PromptCheck>(`/api/shot-workspace/shots/${shot.id}/prompt-check`)
      .then((result) => {
        if (current) {
          setPromptCheck(result);
          setCheckedShot(shot);
        }
      })
      .catch((cause) => {
        if (current) {
          setPromptCheck(null);
          setCheckError(String(cause));
        }
      })
      .finally(() => {
        if (current) setCheckingPrompt(false);
      });
    return () => {
      current = false;
    };
  }, [shot]);

  const disabled = busy || !!working;
  const referenceKey = JSON.stringify([shot.refs, shot.voice_refs]);
  const continuityKey = JSON.stringify(shot.video_context);
  const titleNeeded = !title.trim() || title === shot.meta?.manual_default_title;

  useEffect(() => {
    let current = true;
    void json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`)
      .then((data) => {
        if (current) setVersion(data.version);
      })
      .catch((cause) => {
        if (current) setError(String(cause));
      });
    return () => {
      current = false;
    };
  }, [shot.id, referenceKey, continuityKey]);

  async function changeReferences(refs: ShotRef[], expected: ShotRef[], undo = false) {
    setWorking(undo ? "Restore reference" : "Remove reference");
    setError("");
    try {
      const saved = await replaceShotMaterials(
        shot.id,
        refs.map((ref) => ({
          role: ref.role,
          asset_id: ref.asset_id,
          file_key: ref.file_key,
          notes: ref.notes || "",
        })),
        { expectedRefs: expected },
      );
      onSaved(saved);
      setRemovedRef(undo ? null : { before: expected, after: saved.refs });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setWorking("");
    }
  }

  async function autoSelectReferences() {
    if (chainPending.current) return;
    chainPending.current = true;
    setWorking("Selecting references");
    setError("");
    setMessage("");
    onAssistingChange?.(true);
    try {
      const result = await suggestShotMaterials(shot.id, shot.refs, description);
      if (!result.materials.length) {
        setMessage(["No matching references found. Existing selection kept.", ...result.warnings].join("\n"));
        return;
      }
      const saved = await replaceShotMaterials(shot.id, result.materials, { expectedRefs: shot.refs });
      setAutoRefUndo({ before: shot.refs, after: saved.refs });
      setRemovedRef(null);
      onSaved(saved);
      const input = await json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`);
      setVersion(input.version);
      setMessage([`Selected ${saved.refs.length} references · ${result.operation_duration_s.toFixed(1)}s.`, ...result.warnings].join("\n"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      chainPending.current = false;
      setWorking("");
      onAssistingChange?.(false);
    }
  }

  async function save(expectedVersion = version, draft?: Suggestion) {
    const saved = await json<Shot>(`/api/shot-workspace/shots/${shot.id}/description`, "PUT", {
      description: draft?.description ?? description,
      title: draft?.title || title,
      duration_s: draft?.duration_s ?? duration,
      dialogue: draft?.dialogue ?? dialogue.split("\n").map((s) => s.trim()).filter(Boolean),
      ...camera,
      ...(draft
        ? {
            shot_type: draft.shot_type,
            camera_angle: draft.camera_angle,
            camera_motion: draft.camera_motion,
            composition: draft.composition,
          }
        : {}),
      original_description: original || description,
      expected_version: expectedVersion,
    });
    const input = await json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`);
    setVersion(input.version);
    // Adopt the server's saved brief, including assistant-generated amendments.
    // Otherwise old local fields incorrectly make a freshly written prompt stale.
    setTitle(saved.title);
    setDescription(saved.script_beat);
    setDuration(saved.duration_s);
    setDialogue(formatDialogue(saved));
    setCamera({
      shot_type: saved.shot_type || "",
      camera_angle: saved.camera_angle || "",
      camera_motion: saved.camera_motion || "",
      composition: saved.composition || "",
    });
    onSaved(saved);
    return { saved, version: input.version };
  }

  async function runFullShot() {
    if (chainPending.current) return;
    chainPending.current = true;
    onAssistingChange?.(true);
    setError("");
    setMessage("");
    setRemovedRef(null);
    setAutoRefUndo(null);
    let stage = "brief";
    try {
      setAutoStage("brief");
      setWorking("Refining brief");
      const initial = await save();
      // Resolve the user's original intent before refinement can paraphrase it.
      stage = "continuity";
      setWorking("Checking continuity");
      const connected = await json<Shot>(`/api/shot-workspace/shots/${shot.id}/continuity/explicit`, "POST", {
        description: initial.saved.script_beat,
      });
      onSaved(connected);
      const continuityVersion = await json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`);
      stage = "brief";
      setWorking("Refining brief");
      const draft = await json<Suggestion>(`/api/shot-workspace/shots/${shot.id}/description/refine`, "POST", {
        description: initial.saved.script_beat,
        title: initial.saved.title,
        duration_s: initial.saved.duration_s,
        suggest_title: titleNeeded,
        expected_version: continuityVersion.version,
        description_only: true,
      });
      if (draft.questions.length) throw new Error(draft.questions.join("\n"));
      const refined = await save(continuityVersion.version, draft);
      stage = "references";
      setAutoStage("references");
      setWorking("Selecting references");
      const selection = await suggestShotMaterials(shot.id, refined.saved.refs, refined.saved.script_beat);
      if (!selection.materials.length) {
        throw new Error(["No matching references found.", ...selection.warnings].join("\n"));
      }
      const bound = await replaceShotMaterials(shot.id, selection.materials, { expectedRefs: refined.saved.refs });
      onSaved(bound);
      const fresh = await json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`);
      setVersion(fresh.version);
      stage = "prompt";
      setAutoStage("prompt");
      setWorking("Writing prompt");
      generationRef.current?.prepare?.();
      const written = await onWrite(bound);
      if (!written) throw new Error("Prompt writing did not complete. Video was not started.");
      stage = "video";
      setAutoStage(null);
      setWorking("Starting video");
      if (!generationRef.current) throw new Error("Video output is unavailable.");
      await generationRef.current.generate(written);
      setMessage("Video queued. Progress is shown in Video output.");
    } catch (cause) {
      generationRef.current?.clear?.();
      setError(`Auto run stopped at ${stage}: ${cause instanceof Error ? cause.message : String(cause)}`);
    } finally {
      chainPending.current = false;
      setAutoStage(null);
      setWorking("");
      onAssistingChange?.(false);
    }
  }

  async function act(action: "save" | "write" | "write-generate", expectedVersion?: string) {
    if (chainPending.current) return;
    chainPending.current = true;
    setWorking(action !== "save" ? "Write prompt" : "Saving brief");
    setError("");
    setMessage("");
    try {
      if (action !== "save" && !description.trim()) throw new Error("Enter a shot brief first.");
      const result = await save(expectedVersion);
      if (action !== "save") {
        if (action === "write-generate") generationRef.current?.prepare?.();
        const written = await onWrite(result.saved);
        if (action === "write-generate") {
          if (!written) throw new Error("Prompt writing was interrupted. Video generation was not started.");
          setWorking("Starting video");
          if (!generationRef.current) throw new Error("Video output is unavailable.");
          await generationRef.current.generate(written);
        }
      } else {
        setMessage("Shot brief saved.");
      }
    } catch (cause) {
      generationRef.current?.clear?.();
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      chainPending.current = false;
      setWorking("");
    }
  }

  async function assistDescription(
    request: string,
    recentTurns: AssistantTurn[],
    signal?: AbortSignal,
  ): Promise<AssistantReply> {
    let promptStarted = false;
    const abortPrompt = () => {
      if (promptStarted) void onCancel().catch((cause) => setError(String(cause)));
    };
    signal?.addEventListener("abort", abortPrompt);
    onAssistingChange?.(true);
    setWorking("Thinking about shot");
    setError("");
    setMessage("");
    try {
      const result = await json<
        AssistantReply & {
          kind: "reply" | "draft" | "updated" | "prompt";
          saved_shot?: Shot;
          version?: string;
          draft?: Suggestion;
          prompt_request?: string;
          update_brief?: boolean;
        }
      >(
        `/api/shot-workspace/shots/${shot.id}/description/assist`,
        "POST",
        {
          description,
          amendment: request,
          title,
          duration_s: duration,
          suggest_title: titleNeeded,
          expected_version: version,
          recent_turns: recentTurns,
        },
        signal,
      );
      signal?.throwIfAborted();
      if (result.kind === "prompt") {
        setWorking("Write prompt");
        let saved = await save(version);
        if (result.update_brief) {
          const draft = await json<Suggestion>(`/api/shot-workspace/shots/${shot.id}/description/refine`, "POST", {
            description: saved.saved.script_beat || request,
            amendment: request,
            title: saved.saved.title,
            duration_s: saved.saved.duration_s,
            suggest_title: titleNeeded,
            expected_version: saved.version,
          });
          if (draft.questions.length) throw new Error(draft.questions.join("\n"));
          saved = await save(saved.version, draft);
        }
        signal?.throwIfAborted();
        promptStarted = true;
        await onWrite(saved.saved, result.prompt_request || request);
        return {
          reply: "Prompt saved. Review the six sections before generating.",
          context_scope: result.context_scope,
        };
      }
      if (result.kind === "updated") {
        if (!result.saved_shot || !result.version) {
          throw new Error("The saved description could not be loaded. Refresh this shot.");
        }
        const saved = result.saved_shot;
        setBeforeAmendment({ title, description, duration, dialogue, camera: { ...camera } });
        setOriginal(description);
        setVersion(result.version);
        setTitle(saved.title);
        setDescription(saved.script_beat);
        setDuration(saved.duration_s);
        setDialogue(formatDialogue(saved));
        setCamera({
          shot_type: saved.shot_type || "",
          camera_angle: saved.camera_angle || "",
          camera_motion: saved.camera_motion || "",
          composition: saved.composition || "",
        });
        onSaved(saved);
        setMessage("Description updated and saved.");
        return {
          reply: `${result.reply}\n\nDuration: ${saved.duration_s}s\n\n${saved.script_beat}`,
          context_scope: result.context_scope,
        };
      }
      if (result.kind !== "draft" || !result.draft) return result;
      const suggestion = result.draft;
      setBeforeAmendment({ title, description, duration, dialogue, camera: { ...camera } });
      if (titleNeeded && suggestion.title?.trim()) setTitle(suggestion.title);
      setOriginal(description);
      setDescription(suggestion.description);
      setDuration(suggestion.duration_s);
      setDialogue(suggestion.dialogue.join("\n"));
      setCamera({
        shot_type: suggestion.shot_type,
        camera_angle: suggestion.camera_angle,
        camera_motion: suggestion.camera_motion,
        composition: suggestion.composition,
      });
      setMessage("Description draft updated. Review it, then save or write the prompt.");
      return {
        reply: `${result.reply}\n\nSuggested duration: ${suggestion.duration_s}s\n\n${suggestion.description}`,
        context_scope: result.context_scope,
      };
    } finally {
      signal?.removeEventListener("abort", abortPrompt);
      setWorking("");
      onAssistingChange?.(false);
    }
  }

  function undoDescriptionChange() {
    if (!beforeAmendment) return;
    setTitle(beforeAmendment.title);
    setDescription(beforeAmendment.description);
    setDuration(beforeAmendment.duration);
    setDialogue(beforeAmendment.dialogue);
    setCamera(beforeAmendment.camera);
    setBeforeAmendment(null);
    setMessage("Description change undone in draft.");
  }

  function clearBrief() {
    setBeforeAmendment({ title, description, duration, dialogue, camera: { ...camera } });
    setError("");
    setDescription("");
    setOriginal("");
    setDialogue("");
    setCamera({ shot_type: "", camera_angle: "", camera_motion: "", composition: "" });
    setMessage("Brief cleared in draft. Save to keep it empty, or enter a new description.");
  }

  function deleteCurrentShot() {
    setWorking("Delete shot");
    setError("");
    void onDelete(shot)
      .catch((cause) => setError(String(cause)))
      .finally(() => setWorking(""));
  }

  function refreshVersionAfterMaterials(updated: Shot, write: boolean) {
    onSaved(updated);
    void json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`)
      .then((input) => {
        setVersion(input.version);
        if (write) void act("write", input.version);
      })
      .catch((cause) => setError(String(cause)));
  }

  function refreshVersionAfterVoice(saved: Shot) {
    onSaved(saved);
    void json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`)
      .then((input) => setVersion(input.version))
      .catch((cause) => setError(String(cause)));
  }

  const dirty =
    title !== shot.title ||
    description !== shot.script_beat ||
    duration !== shot.duration_s ||
    dialogue !== formatDialogue(shot) ||
    Object.entries(camera).some(([key, value]) => value !== (shot[key as keyof Shot] || ""));

  const hasPrompt = Object.values(shot.prompt_sections).some(Boolean);
  const checkPending = checkingPrompt || checkedShot !== shot;
  const effectiveCheck =
    dirty && hasPrompt
      ? { stale: true, valid: false, errors: [], warnings: [] }
      : checkPending
        ? null
        : promptCheck;

  const promptWriting = writing || working === "Write prompt";
  const promptStatus = promptWriting
    ? writing
      ? "Writing prompt…"
      : "Preparing prompt…"
    : !hasPrompt
      ? "No prompt yet"
      : effectiveCheck?.stale
        ? "Prompt out of date"
        : checkError
          ? "Prompt check unavailable"
          : checkPending
            ? "Checking prompt…"
            : effectiveCheck?.valid
              ? "Prompt up to date"
              : "Prompt needs attention";

  return {
    generationRef,
    description,
    setDescription,
    title,
    setTitle,
    duration,
    setDuration,
    dialogue,
    setDialogue,
    camera,
    setCamera,
    beforeAmendment,
    setOriginal,
    version,
    working,
    autoStage,
    latestTiming,
    elapsed,
    message,
    setMessage,
    error,
    setError,
    materials,
    setMaterials,
    removedRef,
    autoRefUndo,
    promptCheck,
    checkPending,
    checkError,
    effectiveCheck,
    disabled,
    dirty,
    hasPrompt,
    promptWriting,
    promptStatus,
    changeReferences,
    autoSelectReferences,
    runFullShot,
    act,
    assistDescription,
    undoDescriptionChange,
    clearBrief,
    deleteCurrentShot,
    refreshVersionAfterMaterials,
    refreshVersionAfterVoice,
  };
}
