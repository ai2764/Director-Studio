import { useEffect, useMemo, useState } from "react";
import { ImageUploadSlot, type LocalImage } from "../../shared/components/ImageUploadSlot";
import { Lightbox } from "../../shared/components/Lightbox";
import { OutputGrid } from "../../shared/components/OutputGrid";
import type { OutputSlot } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import {
  cancelSceneJob,
  fetchSceneDefaults,
  generateScene,
  getSceneJob,
  listSceneJobs,
  saveSceneJob,
  type JobStatus,
  type SceneJobRecord,
  type SceneMetaDefaults,
  type SceneRecord,
} from "./api";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

interface Props {
  onOpenLibrary: () => void;
}

function angleLabel(prompt: string): string {
  const parts = prompt.split(",").map((part) => part.trim());
  const primary = parts[0] || "Angle";
  const descriptive = /^front view$/i.test(primary) && parts[1] && !/^eye level$/i.test(parts[1])
    ? parts[1]
    : primary;
  const shortLabel = descriptive.replace(/\s+view$/i, "").trim();
  return shortLabel.charAt(0).toUpperCase() + shortLabel.slice(1);
}

export function SetDesignPage({ onOpenLibrary }: Props) {
  const { projectId, project } = useProject();

  const [defaults, setDefaults] = useState<SceneMetaDefaults | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [angles, setAngles] = useState("");
  const [disabledAngleIndexes, setDisabledAngleIndexes] = useState<Set<number>>(() => new Set());
  const [prepend, setPrepend] = useState("");
  const [append, setAppend] = useState("");
  const [sceneImg, setSceneImg] = useState<LocalImage | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [fixedSeed, setFixedSeed] = useState(false);
  const [seed, setSeed] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [job, setJob] = useState<SceneJobRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<SceneRecord | null>(null);
  const [lightbox, setLightbox] = useState<{ slots: OutputSlot[]; index: number } | null>(null);

  useEffect(() => {
    fetchSceneDefaults()
      .then((d) => {
        setDefaults(d);
        setAngles((prev) => prev || d.default_angles);
        setPrepend((prev) => prev || d.default_prepend || "");
        setAppend((prev) => prev || d.default_append || "");
      })
      .catch((e) => setFormError(String(e)));
  }, []);

  // Project-scoped drafts must never leak into the next project. On a reload,
  // reconnect only to work that is still in progress.
  useEffect(() => {
    setName("");
    setNotes("");
    setAngles(defaults?.default_angles || "");
    setDisabledAngleIndexes(new Set());
    setPrepend(defaults?.default_prepend || "");
    setAppend(defaults?.default_append || "");
    setSceneImg(null);
    setAdvancedOpen(false);
    setFixedSeed(false);
    setSeed("");
    setFieldErrors({});
    setFormError(null);
    setJob(null);
    setBusy(false);
    setSaved(null);
    setLightbox(null);
    if (!projectId) return;
    let cancelled = false;
    listSceneJobs(projectId, 15)
      .then((jobs) => {
        if (cancelled) return;
        const active = jobs.find((j) => ACTIVE.includes(j.status));
        if (!active) return;
        setJob(active);
        if (active.name) setName(active.name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (!job || !ACTIVE.includes(job.status)) return;
    const t = window.setInterval(() => {
      getSceneJob(job.id)
        .then(setJob)
        .catch((e) => setFormError(String(e)));
    }, 1500);
    return () => window.clearInterval(t);
  }, [job?.id, job?.status]);

  const status: JobStatus | "idle" = job?.status || "idle";
  const isRunning = job ? ACTIVE.includes(job.status) : false;

  const angleLines = useMemo(
    () => angles.split("\n").map((line) => line.trim()).filter(Boolean),
    [angles],
  );
  const selectedAngleLines = useMemo(
    () => angleLines.filter((_, index) => !disabledAngleIndexes.has(index)),
    [angleLines, disabledAngleIndexes],
  );
  const angleCount = selectedAngleLines.length;

  const toggleAngle = (index: number) => {
    setDisabledAngleIndexes((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const validate = (): boolean => {
    const err: Record<string, string> = {};
    if (!name.trim()) err.name = "Name is required";
    if (!sceneImg) err.scene = "Scene reference image is required";
    if (angleCount < 1) err.angles = "Add at least one angle line";
    if (fixedSeed && seed && Number.isNaN(Number(seed))) err.seed = "Seed must be an integer";
    setFieldErrors(err);
    return Object.keys(err).length === 0;
  };

  const onGenerate = async () => {
    setFormError(null);
    setSaved(null);
    if (!projectId) {
      setFormError("Select a project in the header first — scenes belong to a project.");
      return;
    }
    if (!validate()) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.set("name", name.trim());
      fd.set("notes", notes);
      fd.set("angle_prompts", selectedAngleLines.join("\n"));
      fd.set("prepend_text", prepend);
      fd.set("append_text", append);
      fd.set("fixed_seed", fixedSeed ? "true" : "false");
      fd.set("project_id", projectId);
      if (fixedSeed && seed.trim()) fd.set("seed", seed.trim());
      if (sceneImg) fd.set("scene_image", sceneImg.file, sceneImg.file.name);
      setJob(await generateScene(fd));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelSceneJob(job.id));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    }
  };

  const onSave = async () => {
    if (!job || job.status !== "succeeded") return;
    if (!projectId) {
      setFormError("Select a project in the header — scenes must belong to a project.");
      return;
    }
    setBusy(true);
    try {
      const scene = await saveSceneJob(job.id, {
        name: name.trim(),
        notes,
        project_id: projectId,
      });
      setSaved(scene);
      setJob({ ...job, scene_id: scene.id });
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onReset = () => {
    setName("");
    setNotes("");
    setSceneImg(null);
    setDisabledAngleIndexes(new Set());
    setFixedSeed(false);
    setSeed("");
    setFieldErrors({});
    setFormError(null);
    setJob(null);
    setSaved(null);
    if (defaults) {
      setAngles(defaults.default_angles);
      setPrepend(defaults.default_prepend || "");
      setAppend(defaults.default_append || "");
    }
  };

  const statusLabel = useMemo(() => {
    const map: Record<string, string> = {
      idle: "Idle",
      queued: "Queued",
      uploading: "Uploading…",
      running: `Generating ${angleCount || "?"} angles…`,
      succeeded: "Succeeded",
      failed: "Failed",
      cancelled: "Cancelled",
    };
    return map[status] || status;
  }, [status, angleCount]);

  const mainKeys = job?.output_order?.length
    ? job.output_order
    : job?.output_stems?.length
      ? job.output_stems
      : Object.keys(job?.outputs || {});
  const placeholderKeys = Array.from(
    { length: angleCount },
    (_, index) => `angle_${String(index).padStart(2, "0")}`,
  );

  const outputMap = job?.outputs || null;

  return (
    <>
      <main className="workspace">
        <section className="panel input-panel">
          <h2>Set Design · Multi-Angle</h2>
          {projectId ? (
            <p className="project-scope-hint">
              Saving to project <strong>{project?.name || projectId}</strong>
            </p>
          ) : (
            <div className="banner error">Select a project in the header before generating scenes.</div>
          )}
          <p className="field-hint" style={{ marginBottom: "1rem" }}>
            Upload one scene plate. 1728×960 quality mode re-shoots seven selectable views with
            Qwen Edit 2511 multi-angle LoRA. The original plate is saved as master for H3. Filenames
            use your scene name + view, e.g.{" "}
            <code>{name.trim() || "SceneName"}_01_left_side_view_h270_v0.png</code>.
          </p>

          <div className="block">
            <div className="block-title">1. Identity</div>
            <label className="field">
              <span>
                Name <span className="req">*</span>
              </span>
              <input
                value={name}
                disabled={isRunning}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Audition_Room"
              />
              {fieldErrors.name ? <span className="field-error">{fieldErrors.name}</span> : null}
            </label>
            <label className="field">
              <span>Notes</span>
              <input value={notes} disabled={isRunning} onChange={(e) => setNotes(e.target.value)} />
            </label>
          </div>

          <div className="block">
            <div className="block-title">2. Scene reference</div>
            <ImageUploadSlot
              label="Scene image"
              required
              hint="Single set / environment still. Multi-angle LoRA keeps layout while changing camera."
              value={sceneImg}
              onChange={setSceneImg}
              disabled={isRunning}
              error={fieldErrors.scene}
            />
          </div>

          <div className="block">
            <div className="block-title">
              3. Angles ({angleCount}/{angleLines.length} selected)
            </div>
            <div className="angle-toggle-row" role="group" aria-label="Scene angles">
              {angleLines.map((angle, index) => {
                const selected = !disabledAngleIndexes.has(index);
                const label = angleLabel(angle);
                return (
                  <button
                    key={`${index}-${angle}`}
                    type="button"
                    className={`angle-toggle ${selected ? "selected" : ""}`}
                    aria-label={label}
                    aria-pressed={selected}
                    title={angle}
                    disabled={isRunning}
                    onClick={() => toggleAngle(index)}
                  >
                    <span className="angle-toggle-index">{String(index + 1).padStart(2, "0")}</span>
                    <span>{label}</span>
                  </button>
                );
              })}
            </div>
            <label className="field">
              <span>
                Angle prompts <span className="req">*</span>
              </span>
              <textarea
                rows={6}
                value={angles}
                disabled={isRunning}
                onChange={(e) => setAngles(e.target.value)}
                placeholder="One angle per line…"
                className="mono-area"
              />
              {fieldErrors.angles ? <span className="field-error">{fieldErrors.angles}</span> : null}
              <span className="field-hint">
                Multi-angle LoRA format, e.g.{" "}
                <code>front view, eye level, medium shot (horizontal: 0, vertical: 0, zoom: 5.0)</code>
              </span>
            </label>
            <label className="field">
              <span>Prepend</span>
              <input
                value={prepend}
                disabled={isRunning}
                onChange={(e) => setPrepend(e.target.value)}
                placeholder="Keep the same set; only change the camera"
              />
            </label>
            <label className="field">
              <span>Append (optional)</span>
              <input value={append} disabled={isRunning} onChange={(e) => setAppend(e.target.value)} />
            </label>
          </div>

          <div className="block advanced">
            <button type="button" className="advanced-toggle" onClick={() => setAdvancedOpen((v) => !v)}>
              {advancedOpen ? "▾" : "▸"} Advanced
            </button>
            {advancedOpen ? (
              <div className="advanced-body">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={fixedSeed}
                    disabled={isRunning}
                    onChange={(e) => setFixedSeed(e.target.checked)}
                  />
                  <span>Fixed seed</span>
                </label>
                {fixedSeed ? (
                  <label className="field">
                    <span>Seed</span>
                    <input
                      value={seed}
                      disabled={isRunning}
                      onChange={(e) => setSeed(e.target.value)}
                      inputMode="numeric"
                    />
                    {fieldErrors.seed ? <span className="field-error">{fieldErrors.seed}</span> : null}
                  </label>
                ) : null}
              </div>
            ) : null}
          </div>

          {formError ? <div className="banner error">{formError}</div> : null}

          <div className="actions">
            {!isRunning ? (
              <button type="button" className="btn primary" disabled={busy} onClick={onGenerate}>
                {busy ? "Submitting…" : `Generate ${angleCount || ""} Angles`}
              </button>
            ) : (
              <button type="button" className="btn danger" onClick={onCancel}>
                Cancel
              </button>
            )}
            <button type="button" className="btn ghost" disabled={isRunning} onClick={onReset}>
              Reset form
            </button>
          </div>
        </section>

        <section className="panel output-panel">
          <div className="output-header">
            <h2>Output</h2>
            <div className={`status-pill status-${status}`}>
              <span className="dot" />
              {statusLabel}
              {job ? <span className="job-id">{job.id}</span> : null}
            </div>
          </div>

          {!job ? (
            <p className="empty-copy">
              Each non-empty angle line produces one image in a single Comfy batch. Results appear as a multi-view
              set for the library.
            </p>
          ) : null}

          {job?.error && (job.status === "failed" || job.status === "cancelled") ? (
            <div className="banner error">{job.error}</div>
          ) : null}

          {job?.seed != null ? (
            <p className="meta-line">
              Seed <code>{job.seed}</code>
              {job.comfy_prompt_id ? (
                <>
                  {" "}
                  · Comfy <code>{job.comfy_prompt_id.slice(0, 8)}…</code>
                </>
              ) : null}
              {job.used_angles?.length ? <> · {job.used_angles.length} angles</> : null}
            </p>
          ) : null}

          <OutputGrid
            status={status}
            outputs={outputMap}
            mainKeys={mainKeys.length ? mainKeys : placeholderKeys}
            showSecondary={false}
            onOpen={(slot, all) => {
              const idx = all.findIndex((s) => s.key === slot.key);
              setLightbox({ slots: all, index: Math.max(0, idx) });
            }}
          />

          <div className="actions output-actions">
            <button
              type="button"
              className="btn primary"
              disabled={!job || job.status !== "succeeded" || busy || !!job.scene_id}
              onClick={onSave}
            >
              {job?.scene_id ? "Saved" : "Save to Library"}
            </button>
            <button type="button" className="btn secondary" disabled={!job || isRunning || busy} onClick={onGenerate}>
              Regenerate
            </button>
          </div>

          {saved || job?.scene_id ? (
            <div className="banner ok">
              Saved as Scene · <code>{saved?.id || job?.scene_id}</code>
              <button type="button" className="btn ghost sm" onClick={onOpenLibrary}>
                View in Library
              </button>
            </div>
          ) : null}
        </section>
      </main>

      {lightbox ? (
        <Lightbox
          slots={lightbox.slots}
          index={lightbox.index}
          onClose={() => setLightbox(null)}
          onIndex={(i) => setLightbox((lb) => (lb ? { ...lb, index: i } : null))}
        />
      ) : null}
    </>
  );
}
