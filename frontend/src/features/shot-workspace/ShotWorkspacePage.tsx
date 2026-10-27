import { useEffect, useRef, useState } from "react";
import { useProject } from "../../shared/project/ProjectContext";
import { parseError } from "../../shared/api/client";
import { PROMPT_SECTION_KEYS, refPreviewCandidates, type Shot, type ShotRef } from "../../shared/api/types";
import { cancelDirectorChatSession, chatWithDirectorStream, getDirectorChatSession, getProject, replaceShotMaterials, type AgentProgress } from "./api";
import { ShotMaterialEditor } from "./WorkspaceReferences";
import { ManualContinuity } from "./ManualContinuity";
import { ManualShotOutput } from "./ManualShotOutput";
import { formatDialogue } from "./api";
import type { PromptCheck } from "./promptCheck";
import { DescriptionAssistant, type AssistantTurn, type AssistantReply } from "./DescriptionAssistant";
import "./manualShots.css";
import "./workspaceDesign.css";

async function json<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(url, { method, ...(body === undefined ? {} : {
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }) });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

interface Suggestion {
  title: string;
  duration_s: number;
  description: string; shot_type: string; camera_angle: string; camera_motion: string;
  composition: string; dialogue: string[]; questions: string[]; original_description: string;
}

function ReferencePreview({ item }: { item: ShotRef }) {
  const candidates = refPreviewCandidates(item);
  const [index, setIndex] = useState(0);
  const url = candidates[index];
  return url ? <a className="manual-ref-preview" href={url} target="_blank" rel="noopener noreferrer"
    aria-label={`View reference ${item.picture_index}`}>
    <img src={url} alt={`Picture ${item.picture_index}`} onError={() => setIndex((current) => current + 1)} />
  </a> : <span className="manual-ref-preview">Preview unavailable</span>;
}
export function ShotEditor({ shot, index, shots, busy, onSaved, onWrite, onDelete, desktopOutput, writing, writeElapsed, onCancel }: {
  shot: Shot; index: number; shots: Shot[]; busy: boolean;
  onSaved: (shot: Shot) => void; onWrite: (shot: Shot) => Promise<void>;
  onDelete: (shot: Shot) => Promise<void>;
  desktopOutput: boolean;
  writing: boolean; writeElapsed: number; onCancel: () => Promise<void>;
}) {
  const [description, setDescription] = useState(shot.script_beat);
  const [title, setTitle] = useState(shot.title);
  const [duration, setDuration] = useState(shot.duration_s);
  const [dialogue, setDialogue] = useState(formatDialogue(shot));
  const [camera, setCamera] = useState({ shot_type: shot.shot_type || "", camera_angle: shot.camera_angle || "",
    camera_motion: shot.camera_motion || "", composition: shot.composition || "" });
  const [beforeAmendment, setBeforeAmendment] = useState<{ title: string; description: string; duration: number; dialogue: string; camera: typeof camera } | null>(null);
  const [original, setOriginal] = useState(shot.script_beat);
  const [version, setVersion] = useState("");
  const [working, setWorking] = useState("");
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
  const [promptCheck, setPromptCheck] = useState<PromptCheck | null>(null);
  const [checkingPrompt, setCheckingPrompt] = useState(true);
  const [checkedShot, setCheckedShot] = useState<Shot | null>(null);
  const [checkError, setCheckError] = useState("");
  useEffect(() => {
    let current = true;
    setCheckingPrompt(true); setCheckError("");
    void json<PromptCheck>(`/api/shot-workspace/shots/${shot.id}/prompt-check`).then((result) => {
      if (current) { setPromptCheck(result); setCheckedShot(shot); }
    }).catch((cause) => { if (current) { setPromptCheck(null); setCheckError(String(cause)); } })
      .finally(() => { if (current) setCheckingPrompt(false); });
    return () => { current = false; };
  }, [shot]);
  const disabled = busy || !!working;
  const referenceKey = JSON.stringify([shot.refs, shot.voice_refs]);
  const continuityKey = JSON.stringify(shot.video_context);
  const titleNeeded = !title.trim() || title === shot.meta?.manual_default_title;
  useEffect(() => {
    let current = true;
    void json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`).then((data) => {
      if (current) setVersion(data.version);
    }).catch((cause) => { if (current) setError(String(cause)); });
    return () => { current = false; };
  }, [shot.id, referenceKey, continuityKey]);

  async function changeReferences(refs: ShotRef[], expected: ShotRef[], undo = false) {
    setWorking(undo ? "Restore reference" : "Remove reference"); setError("");
    try {
      const saved = await replaceShotMaterials(shot.id, refs.map((ref) => ({
        role: ref.role, asset_id: ref.asset_id, file_key: ref.file_key, notes: ref.notes || "",
      })), { expectedRefs: expected });
      onSaved(saved);
      setRemovedRef(undo ? null : { before: expected, after: saved.refs });
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(""); }
  }

  async function save(expectedVersion = version) {
    const saved = await json<Shot>(`/api/shot-workspace/shots/${shot.id}/description`, "PUT", {
      description, title, duration_s: duration, dialogue: dialogue.split("\n").map((s) => s.trim()).filter(Boolean),
      ...camera, original_description: original || description, expected_version: expectedVersion,
    });
    const input = await json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`);
    setVersion(input.version);
    onSaved(saved);
    return { saved, version: input.version };
  }
  async function act(action: "save" | "write", expectedVersion?: string) {
    setWorking(action === "write" ? "Write prompt" : "Saving brief");
    setError(""); setMessage("");
    try {
      if (action === "write" && !description.trim()) throw new Error("Enter a shot brief first.");
      const result = await save(expectedVersion);
      if (action === "write") await onWrite(result.saved);
      else setMessage("Shot brief saved.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setWorking(""); }
  }

  async function assistDescription(request: string, recentTurns: AssistantTurn[]): Promise<AssistantReply> {
    setWorking("Thinking about shot");
    setError(""); setMessage("");
    try {
      const result = await json<AssistantReply & {kind: "reply" | "draft"; draft?: Suggestion}>(`/api/shot-workspace/shots/${shot.id}/description/assist`, "POST", {
        description, amendment: request, title, suggest_title: titleNeeded, expected_version: version, recent_turns: recentTurns,
      });
      if (result.kind !== "draft" || !result.draft) return result;
      const suggestion = result.draft;
      setBeforeAmendment({ title, description, duration, dialogue, camera: { ...camera } });
      if (titleNeeded && suggestion.title?.trim()) setTitle(suggestion.title);
      setOriginal(description); setDescription(suggestion.description);
      setDialogue(suggestion.dialogue.join("\n"));
      setCamera({shot_type:suggestion.shot_type,camera_angle:suggestion.camera_angle,camera_motion:suggestion.camera_motion,composition:suggestion.composition});
      setMessage("Description draft updated. Review it, then save or write the prompt.");
      return {reply: `${result.reply}\n\n${suggestion.description}`, context_scope: result.context_scope};
    } finally { setWorking(""); }
  }
  function undoDescriptionChange() {
    if (!beforeAmendment) return;
    setTitle(beforeAmendment.title); setDescription(beforeAmendment.description); setDuration(beforeAmendment.duration);
    setDialogue(beforeAmendment.dialogue); setCamera(beforeAmendment.camera); setBeforeAmendment(null);
    setMessage("Description change undone in draft.");
  }

  const dirty = title !== shot.title || description !== shot.script_beat || duration !== shot.duration_s
    || dialogue !== formatDialogue(shot) || Object.entries(camera).some(([key, value]) => value !== (shot[key as keyof Shot] || ""));
  const hasPrompt = Object.values(shot.prompt_sections).some(Boolean);
  const checkPending = checkingPrompt || checkedShot !== shot;
  const effectiveCheck = dirty && hasPrompt
    ? { stale: true, valid: false, errors: [], warnings: [] }
    : checkPending ? null : promptCheck;
  const promptStatus = !hasPrompt ? "No prompt yet" : effectiveCheck?.stale ? "Prompt out of date"
    : checkError ? "Prompt check unavailable" : checkPending ? "Checking prompt…"
    : effectiveCheck?.valid ? "Prompt up to date" : "Prompt needs attention";
  return <div className={desktopOutput ? "manual-authoring-columns" : "manual-authoring-single"}><article className="manual-shot-editor">
    <div className="manual-shot-heading"><input aria-label="Shot title" value={title}
      disabled={disabled} onChange={(event) => setTitle(event.target.value)} />

      <button type="button" className="mode-chip manual-delete-shot" aria-label="Delete shot" title="Delete shot (undo available)"
        disabled={disabled} onClick={() => {
          setWorking("Delete shot"); setError("");
          void onDelete(shot).catch((cause) => setError(String(cause))).finally(() => setWorking(""));
        }}>🗑</button></div>
    <div className="manual-description-label"><label htmlFor={`description-${shot.id}`}>Shot brief</label>
      <div className="manual-description-tools">
      <button type="button" className="mode-chip" disabled={disabled || !version || !description.trim()}
        onClick={() => { void assistDescription("Refine the current shot description. Preserve its intended action and explicit dialogue, clarify the framing and camera movement, and fill the camera fields. Return an updated description draft, not a discussion or prompt.", [])
          .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))); }}>
        {working === "Thinking about shot" ? `Refining · ${elapsed}s` : "✦ Refine"}</button>
      <button type="button" className="mode-chip" aria-label="Clear shot brief" disabled={disabled || !description}
        onClick={() => { setBeforeAmendment({ title, description, duration, dialogue, camera: { ...camera } });
          setError(""); setDescription(""); setOriginal(""); setDialogue(""); setCamera({ shot_type: "", camera_angle: "", camera_motion: "", composition: "" });
          setMessage("Brief cleared in draft. Save to keep it empty, or enter a new description."); }}>Clear</button>
      </div></div>
    <div className="manual-editor-feedback" aria-live="polite">
      {working && <p role="status">{working} · {elapsed}s</p>}
      {message && <p role="status">{message}</p>}
      {error && <p className="banner error workspace-task-error" role="alert">{error}</p>}
    </div>
    <small className="manual-brief-hint">Use Description assistant to create or revise this brief, or edit it directly. Save the brief before writing its prompt.</small>
    <textarea id={`description-${shot.id}`} rows={6} value={description} disabled={disabled}
      placeholder="Describe the action, framing and camera movement. You can link references first."
      onChange={(event) => { setDescription(event.target.value); setOriginal(event.target.value); setMessage("");
        setCamera({ shot_type: "", camera_angle: "", camera_motion: "", composition: "" }); }} />
    <details className="manual-shot-settings"><summary>Duration and dialogue</summary>
      <small>Use one cue per line: Guide [English]: Welcome. Include each speaker and language; Description assistant can extract cues from the brief.</small>
      <label>Duration (seconds)<input type="number" min={1} max={120} value={duration} disabled={disabled}
        onChange={(event) => setDuration(Number(event.target.value))} /></label>
      <label>Dialogue (optional, one cue per line)<textarea rows={2} value={dialogue} disabled={disabled} placeholder="Guide [English]: Welcome.\nVisitor [English]: Thank you."
        onChange={(event) => setDialogue(event.target.value)} /></label>
    </details>
    {shot.dialogue_lines?.length && dialogue === formatDialogue(shot) && description === shot.script_beat
      ? <div className="manual-dialogue-speakers" aria-label="Saved dialogue speakers">{shot.dialogue_lines.map((line) =>
        <p key={line.line_id}><strong>{line.speaker_name}：</strong>{line.text}</p>)}</div> : null}
    {desktopOutput && <div className="manual-brief-fields">{([
      ["shot_type", "Framing"], ["camera_angle", "Angle"], ["camera_motion", "Camera movement"], ["composition", "Composition"],
    ] as const).map(([key, label]) => <label key={key}>{label}<textarea rows={2} aria-label={label} placeholder="Enter a value or use Description assistant" value={camera[key]} disabled={disabled}
      onChange={(event) => setCamera((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div>}
    <section className="manual-reference-section" aria-label="Reference">
      <div className="manual-description-label"><strong>Reference</strong>
        <button className="btn secondary compact" disabled={disabled} onClick={() => setMaterials(true)}>Link ref</button></div>
    <div className="manual-reference-strip" aria-label="Selected references">
      {shot.refs.map((ref) => <figure key={`${ref.asset_id}:${ref.file_key}`}>
        <ReferencePreview item={ref} />
        <button type="button" className="manual-ref-remove" aria-label={`Remove reference ${ref.picture_index}`}
          disabled={disabled} onClick={() => void changeReferences(shot.refs.filter((item) => item !== ref), shot.refs)}>×</button>
        <figcaption>P{ref.picture_index}</figcaption></figure>)}
      {!shot.refs.length && <small>Link references before or after writing the brief.</small>}
    </div>
    {removedRef && <div className="manual-undo" role="status">Reference removed
      <button className="mode-chip" disabled={disabled} onClick={() => void changeReferences(removedRef.before, removedRef.after, true)}>Undo removal</button></div>}
    <ManualContinuity shot={shot} shots={shots} disabled={disabled} onSaved={onSaved} />
    </section>
    <strong className="manual-prompt-label">Prompt</strong>
    <small role="status" aria-label="Prompt status" className={effectiveCheck?.stale ? "manual-prompt-warning" : undefined}>{promptStatus}</small>
    {dirty && <small role="status">Unsaved shot changes. Save and rewrite the prompt before generating.</small>}
    {(promptCheck?.stale || Object.values(shot.prompt_sections).some(Boolean)) && promptCheck?.errors?.map((item) => <p className="manual-prompt-warning" role="alert" key={item.code + item.message}>{item.message}</p>)}
    {promptCheck?.warnings?.map((item) => <small key={item.code}>{item.message}</small>)}
    {checkError && <p role="alert">Prompt check unavailable: {checkError}</p>}
    <div className="manual-shot-actions">
      <button className="btn secondary" disabled={disabled || !version} onClick={() => void act("save")}>Save</button>
      <button className="btn primary" disabled={writing ? false : disabled || !version || !description.trim() || !shot.refs.length}
        aria-label={writing ? "Cancel prompt writing" : "Write prompt"}
        onClick={() => { if (writing) void onCancel().catch((cause) => setError(String(cause))); else void act("write"); }}>
        {writing ? `Cancel · ${writeElapsed}s` : working === "Write prompt" ? `Preparing prompt · ${elapsed}s` : "Write prompt"}</button>
      {writing && <small role="status">Writing prompt</small>}
    </div>
    {Object.values(shot.prompt_sections).some(Boolean) ? <details className="manual-generated-prompt" aria-label="Prompt contents" open={desktopOutput}><summary>Saved prompt</summary><small className="manual-saved-prompt-label">{effectiveCheck?.stale ? "Previously saved prompt · Out of date. Failed writes do not replace this text." : "Saved prompt · Failed writes do not replace this text."}</small>{PROMPT_SECTION_KEYS.map(({ key, label }) => shot.prompt_sections[key]
        ? <label key={key}>{label}<textarea aria-label={label} readOnly rows={4} value={shot.prompt_sections[key]} /></label> : null)}
    </details> : <small>Write from the current brief and selected references. Refinement is optional.</small>}
    {materials && <ShotMaterialEditor shot={shot} shots={shots} shotNumber={index + 1} manualReferences
      onClose={() => setMaterials(false)} onOpenImage={(url) => window.open(url, "_blank", "noopener")}
      onSaved={(updated, _note, write) => {
        onSaved(updated);
        void json<{ version: string }>(`/api/shot-workspace/shots/${shot.id}/description`).then((input) => {
          setVersion(input.version);
          if (write) void act("write", input.version);
        }).catch((cause) => setError(String(cause)));
      }} />}
    <DescriptionAssistant shotId={shot.id} description={description} disabled={disabled} ready={!!version}
      onRequest={assistDescription} onUndo={undoDescriptionChange} canUndo={!!beforeAmendment} />
  </article><ManualShotOutput shot={shot} disabled={disabled || dirty || checkPending || !!checkError}
    check={effectiveCheck} onSaved={onSaved} /></div>;
}

export function ShotWorkspacePage({ desktopOutput = false }: { desktopOutput?: boolean } = {}) {
  const { projectId, createAndSelect } = useProject();
  const [shots, setShots] = useState<Shot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [writing, setWriting] = useState(false);
  const [writeStarted, setWriteStarted] = useState<number | null>(null);
  const [progress, setProgress] = useState<AgentProgress | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [removedShot, setRemovedShot] = useState<{ project_id: string; archive_id: string } | null>(null);
  const [now, setNow] = useState(Date.now());
  const epoch = useRef(0);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  useEffect(() => {
    const id = ++epoch.current;
    setShots([]); setSelected(null); setProgress(null); setError(""); setRemovedShot(null); setWriteStarted(null);
    if (!projectId) return;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [detail, session] = await Promise.all([getProject(projectId!), getDirectorChatSession(projectId!)]);
        if (epoch.current !== id) return;
        setShots(detail.shots); setWriting(session.active);
        if (session.error) setError(session.error);
        if (session.progress && (session.active || session.progress.action_id)) setProgress(session.progress);
        setSelected((current) => current && detail.shots.some((s) => s.id === current) ? current : detail.shots[0]?.id ?? null);
        if (session.active) timer = setTimeout(refresh, 1200);
      } catch (cause) { if (epoch.current === id) setError(String(cause)); }
    }
    void refresh();
    return () => { epoch.current++; clearTimeout(timer); };
  }, [projectId]);
  useEffect(() => {
    if (!writing) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [writing]);
  const update = (shot: Shot) => { setShots((current) => current.map((s) => s.id === shot.id ? shot : s)); setNotice(""); };
  async function remove(shot: Shot) {
    const index = shots.findIndex((item) => item.id === shot.id);
    const result = await json<{ shots: Shot[]; project_id: string; archive_id: string }>(`/api/shot-workspace/shots/${shot.id}`, "DELETE", { expected_shot: shot });
    if (activeProject.current !== shot.project_id) return;
    setShots(result.shots); setSelected(result.shots[Math.min(index, result.shots.length - 1)]?.id ?? null);
    setRemovedShot(result); setProgress(null); setNotice("");
  }
  async function undoRemove() {
    if (!removedShot || removedShot.project_id !== projectId) return;
    setCreating(true); setError("");
    try {
      const result = await json<{ shots: Shot[]; shot: Shot }>(`/api/shot-workspace/projects/${projectId}/removed-shots/${removedShot.archive_id}/restore`, "POST");
      if (activeProject.current !== result.shot.project_id) return;
      setShots(result.shots); setSelected(result.shot.id); setRemovedShot(null);
    } catch (cause) { setError(String(cause)); }
    finally { setCreating(false); }
  }
  async function add() {
    setCreating(true); setError("");
    try {
      const pid = projectId || (await createAndSelect("Untitled shots", "", "director", "shot_workspace")).id;
      const saved = await json<Shot>(`/api/shot-workspace/projects/${pid}/shots`, "POST", {
        request_id: crypto.randomUUID?.() ?? `manual-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      });
      if (activeProject.current === pid) {
        setShots((current) => current.some((s) => s.id === saved.id) ? current : [...current, saved]);
        setSelected(saved.id);
      }
    } catch (cause) { setError(String(cause)); }
    finally { setCreating(false); }
  }
  async function write(shot: Shot) {
    if (!projectId) return;
    const id = epoch.current;
    setWriting(true); setWriteStarted(Date.now()); setNow(Date.now()); setProgress(null); setError(""); setNotice("");
    try {
      const result = await chatWithDirectorStream(projectId, "Write a prompt using the saved shot and selected references", [], {
        onAgentProgress: (value) => { if (epoch.current === id) setProgress(value); },
      }, [], undefined, undefined, undefined, { shot_id: shot.id, choice: "skip",
        action_id: crypto.randomUUID?.() ?? `write-${Date.now()}-${Math.random().toString(36).slice(2)}` });
      if (epoch.current === id) { setShots(result.shots); setNotice(result.reply); }
    } catch (cause) { if (epoch.current === id) setError(String(cause)); }
    finally { if (epoch.current === id) setWriting(false); }
  }
  const current = shots.find((shot) => shot.id === selected) ?? shots[0];
  const started = progress?.started_at ? Date.parse(progress.started_at) : writeStarted;
  const elapsed = started ? Math.max(0, Math.floor(((writing ? now : progress ? Date.parse(progress.updated_at) : Date.now()) - started) / 1000)) : 0;
  async function cancelWriting() {
    if (!projectId) return;
    const session = await cancelDirectorChatSession(projectId);
    if (activeProject.current !== projectId) return;
    setWriting(session.active); if (session.progress) setProgress(session.progress);
  }
  return <main className={`workspace manual-shots${desktopOutput ? " manual-shots-desktop" : ""}`}>
    <header className="manual-shots-header"><div className="workspace-heading"><strong>Shot workspace</strong><small>Shape the shot. Bring it to life.</small></div>
      <button type="button" className="manual-add-shot" aria-label="Add shot" disabled={creating || writing} onClick={() => void add()}>＋</button></header>
    <nav className="manual-shot-nav" aria-label="Shots">{shots.map((shot, index) => <button key={shot.id} className="mode-chip"
      title={shot.title || `Shot ${index + 1}`} aria-label={`Shot ${index + 1}`}
      aria-current={current?.id === shot.id ? "page" : undefined} onClick={() => setSelected(shot.id)}>{index + 1}</button>)}</nav>
    {error && <p className="banner error workspace-task-error" role="alert">{error}</p>}
    {removedShot && <div className="manual-undo" role="status">Shot deleted
      <button className="mode-chip" disabled={creating || writing} onClick={() => void undoRemove()}>Undo delete</button></div>}
    {notice && <p className="manual-task-status">{notice}</p>}
    {current ? <ShotEditor key={current.id} shot={current} shots={shots} index={shots.indexOf(current)}
      busy={writing || creating} onSaved={update} onWrite={write} onDelete={remove} desktopOutput={desktopOutput}
      writing={writing} writeElapsed={elapsed} onCancel={cancelWriting} /> : <p className="empty-copy">Click + to add a shot, write its brief and link references.</p>}
  </main>;
}
