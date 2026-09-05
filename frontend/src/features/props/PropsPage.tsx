import { useEffect, useMemo, useState } from "react";
import { ImageUploadSlot, type LocalImage } from "../../shared/components/ImageUploadSlot";
import { Lightbox } from "../../shared/components/Lightbox";
import { OutputGrid } from "../../shared/components/OutputGrid";
import type { OutputSlot } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import {
  cancelPropJob,
  generateProp,
  getPropJob,
  listPropJobs,
  savePropJob,
  type JobStatus,
  type PropJobRecord,
  type PropRecord,
} from "./api";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

const MASTER_LABELS = { master: "Prop Reference Sheet" };

interface Props {
  onOpenLibrary: () => void;
}

export function PropsPage({ onOpenLibrary }: Props) {
  const { projectId, project } = useProject();

  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [propImg, setPropImg] = useState<LocalImage | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [fixedSeed, setFixedSeed] = useState(false);
  const [seed, setSeed] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [job, setJob] = useState<PropJobRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<PropRecord | null>(null);
  const [lightbox, setLightbox] = useState<{ slots: OutputSlot[]; index: number } | null>(null);

  useEffect(() => {
    setName("");
    setNotes("");
    setPropImg(null);
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
    listPropJobs(projectId, 15)
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
      getPropJob(job.id)
        .then(setJob)
        .catch((e) => setFormError(String(e)));
    }, 1500);
    return () => window.clearInterval(t);
  }, [job?.id, job?.status]);

  const status: JobStatus | "idle" = job?.status || "idle";
  const isRunning = job ? ACTIVE.includes(job.status) : false;

  const validate = (): boolean => {
    const err: Record<string, string> = {};
    if (!name.trim()) err.name = "Name is required";
    if (!propImg) err.prop = "Prop photo is required";
    if (fixedSeed && seed && Number.isNaN(Number(seed))) err.seed = "Seed must be an integer";
    setFieldErrors(err);
    return Object.keys(err).length === 0;
  };

  const onGenerate = async () => {
    setFormError(null);
    setSaved(null);
    if (!projectId) {
      setFormError("Select a project in the header first — props belong to a project.");
      return;
    }
    if (!validate()) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.set("name", name.trim());
      fd.set("notes", notes);
      fd.set("fixed_seed", fixedSeed ? "true" : "false");
      fd.set("project_id", projectId);
      if (fixedSeed && seed.trim()) fd.set("seed", seed.trim());
      if (propImg) fd.set("prop_image", propImg.file, propImg.file.name);
      setJob(await generateProp(fd));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelPropJob(job.id));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    }
  };

  const onSave = async () => {
    if (!job || job.status !== "succeeded") return;
    if (!projectId) {
      setFormError("Select a project in the header — props must belong to a project.");
      return;
    }
    setBusy(true);
    try {
      const record = await savePropJob(job.id, {
        name: name.trim(),
        notes,
        project_id: projectId,
      });
      setSaved(record);
      setJob({ ...job, prop_id: record.id });
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onReset = () => {
    setName("");
    setNotes("");
    setPropImg(null);
    setFixedSeed(false);
    setSeed("");
    setFieldErrors({});
    setFormError(null);
    setJob(null);
    setSaved(null);
  };

  const statusLabel = useMemo(() => {
    const map: Record<string, string> = {
      idle: "Idle",
      queued: "Queued",
      uploading: "Uploading…",
      running: "Preparing reference sheet…",
      succeeded: "Succeeded",
      failed: "Failed",
      cancelled: "Cancelled",
    };
    return map[status] || status;
  }, [status]);

  const outputMap = job?.outputs || null;
  const mainKeys = job?.output_order?.length ? job.output_order : ["master"];

  return (
    <>
      <main className="workspace">
        <section className="panel input-panel">
          <h2>Props · Reference Sheet</h2>
          {projectId ? (
            <p className="project-scope-hint">
              Saving to project <strong>{project?.name || projectId}</strong>
            </p>
          ) : (
            <div className="banner error">Select a project in the header before preparing props.</div>
          )}
          <p className="field-hint" style={{ marginBottom: "1rem" }}>
            Upload one photo. Director Studio creates one multi-view reference sheet: the same
            prop in a large hero view and supporting angles, on a neutral studio background.
            H3 receives the finished sheet as one picture reference.
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
                placeholder="e.g. Folding knife"
              />
              {fieldErrors.name ? <span className="field-error">{fieldErrors.name}</span> : null}
            </label>
            <label className="field">
              <span>Notes</span>
              <input
                value={notes}
                disabled={isRunning}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Optional: logo, material, color"
              />
            </label>
          </div>

          <div className="block">
            <div className="block-title">2. Photo</div>
            <ImageUploadSlot
              label="Prop photo"
              required
              hint="Phone snap, catalog still, or generated object. One object, as complete as possible."
              value={propImg}
              onChange={setPropImg}
              disabled={isRunning}
              error={fieldErrors.prop}
            />
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
                {busy ? "Submitting…" : "Prepare Reference Sheet"}
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
              The prepared reference sheet appears here. Save it to the library so Director can
              cast its complete multi-view object information as a single H3 picture.
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
            </p>
          ) : null}

          <OutputGrid
            status={status}
            outputs={outputMap}
            mainKeys={mainKeys}
            labels={MASTER_LABELS}
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
              disabled={!job || job.status !== "succeeded" || busy || !!job.prop_id}
              onClick={onSave}
            >
              {job?.prop_id ? "Saved" : "Save to Library"}
            </button>
            <button type="button" className="btn secondary" disabled={!job || isRunning || busy} onClick={onGenerate}>
              Regenerate
            </button>
          </div>

          {saved || job?.prop_id ? (
            <div className="banner ok">
              Saved as Prop · <code>{saved?.id || job?.prop_id}</code>
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
