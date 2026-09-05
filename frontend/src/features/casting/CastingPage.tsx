import { useEffect, useMemo, useState } from "react";
import { ImageUploadSlot, type LocalImage } from "../../shared/components/ImageUploadSlot";
import { Lightbox } from "../../shared/components/Lightbox";
import { OutputGrid } from "../../shared/components/OutputGrid";
import type { OutputSlot } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import {
  cancelJob,
  fetchDefaults,
  generateActor,
  getJob,
  listActorJobs,
  saveJob,
  type ActorRecord,
  type JobRecord,
  type JobStatus,
  type MetaDefaults,
} from "./api";

const ACTIVE: JobStatus[] = ["queued", "uploading", "running"];

interface Props {
  onOpenLibrary: () => void;
}

export function CastingPage({ onOpenLibrary }: Props) {
  const { projectId } = useProject();

  const [defaults, setDefaults] = useState<MetaDefaults | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [description, setDescription] = useState("");
  const [negative, setNegative] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [fixedSeed, setFixedSeed] = useState(false);
  const [seed, setSeed] = useState("");
  const [actorImg, setActorImg] = useState<LocalImage | null>(null);
  const [wardrobeImg, setWardrobeImg] = useState<LocalImage | null>(null);
  const [includeHeadwear, setIncludeHeadwear] = useState(false);
  const [includeFootwear, setIncludeFootwear] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedActor, setSavedActor] = useState<ActorRecord | null>(null);
  const [lightbox, setLightbox] = useState<{ slots: OutputSlot[]; index: number } | null>(null);

  useEffect(() => {
    fetchDefaults()
      .then((d) => {
        setDefaults(d);
        setDescription((prev) => prev || d.default_description);
        setNegative((prev) => prev || d.default_negative);
      })
      .catch((e) => setFormError(String(e)));
  }, []);

  // Project-scoped drafts must never leak into the next project. On a reload,
  // reconnect only to work that is still in progress.
  useEffect(() => {
    setName("");
    setNotes("");
    setDescription(defaults?.default_description || "");
    setNegative(defaults?.default_negative || "");
    setAdvancedOpen(false);
    setFixedSeed(false);
    setSeed("");
    setActorImg(null);
    setWardrobeImg(null);
    setIncludeHeadwear(false);
    setIncludeFootwear(false);
    setFieldErrors({});
    setFormError(null);
    setJob(null);
    setBusy(false);
    setSavedActor(null);
    setLightbox(null);
    if (!projectId) return;
    let cancelled = false;
    listActorJobs(projectId, 15)
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
      getJob(job.id)
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
    if (!actorImg && !description.trim()) {
      err.description = "Description is required when no actor reference is uploaded";
    }
    if (fixedSeed && seed && Number.isNaN(Number(seed))) err.seed = "Seed must be an integer";
    setFieldErrors(err);
    return Object.keys(err).length === 0;
  };

  const onGenerate = async () => {
    setFormError(null);
    setSavedActor(null);
    if (!projectId) {
      setFormError("Select a project in the header first — assets belong to a project.");
      return;
    }
    if (!validate()) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.set("name", name.trim());
      fd.set("notes", notes);
      fd.set("description", description);
      fd.set("body_description", "");
      fd.set("hair_description", "");
      fd.set("negative_prompt", negative);
      fd.set("fixed_seed", fixedSeed ? "true" : "false");
      fd.set("project_id", projectId);
      if (fixedSeed && seed.trim()) fd.set("seed", seed.trim());
      if (actorImg) fd.set("actor_image", actorImg.file, actorImg.file.name);
      if (wardrobeImg) fd.set("wardrobe_image", wardrobeImg.file, wardrobeImg.file.name);
      fd.set("include_headwear", wardrobeImg && includeHeadwear ? "true" : "false");
      fd.set("include_footwear", wardrobeImg && includeFootwear ? "true" : "false");
      setJob(await generateActor(fd));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelJob(job.id));
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    }
  };

  const onSave = async () => {
    if (!job || job.status !== "succeeded") return;
    if (!projectId) {
      setFormError("Select a project in the header — assets must belong to a project.");
      return;
    }
    setBusy(true);
    try {
      const actor = await saveJob(job.id, {
        name: name.trim(),
        notes,
        project_id: projectId,
      });
      setSavedActor(actor);
      setJob({ ...job, actor_id: actor.id });
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onReset = () => {
    setName("");
    setNotes("");
    setActorImg(null);
    setWardrobeImg(null);
    setIncludeHeadwear(false);
    setIncludeFootwear(false);
    setFixedSeed(false);
    setSeed("");
    setFieldErrors({});
    setFormError(null);
    setJob(null);
    setSavedActor(null);
    if (defaults) {
      setDescription(defaults.default_description);
      setNegative(defaults.default_negative);
    }
  };

  const statusLabel = useMemo(() => {
    const map: Record<string, string> = {
      idle: "Idle",
      queued: "Queued",
      uploading: "Uploading images…",
      running: "Running workbench…",
      succeeded: "Succeeded",
      failed: "Failed",
      cancelled: "Cancelled",
    };
    return map[status] || status;
  }, [status]);

  const outputMap = job?.outputs
    ? {
        master: job.outputs.master,
        fullbody_threeview: job.outputs.fullbody_threeview,
        bust_threeview: job.outputs.bust_threeview,
        asset_sheet: job.outputs.asset_sheet,
        wardrobe_ref: job.outputs.wardrobe_ref,
      }
    : null;

  return (
    <>
      <main className="workspace">
        <section className="panel input-panel">
          <h2>Input</h2>
          {!projectId ? (
            <div className="banner error">Select a project in the header before casting.</div>
          ) : null}

          <div className="block">
            <div className="block-title">Identity</div>
            <label className="field">
              <span>
                Name <span className="req">*</span>
              </span>
              <input
                value={name}
                disabled={isRunning}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Lead_Female_A"
              />
              {fieldErrors.name ? <span className="field-error">{fieldErrors.name}</span> : null}
            </label>
            <label className="field">
              <span>Notes</span>
              <input
                value={notes}
                disabled={isRunning}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Optional notes for the library"
              />
            </label>
          </div>

          <div className="block">
            <div className="block-title">References</div>
            <div className="upload-row two">
              <ImageUploadSlot
                label="Actor"
                value={actorImg}
                onChange={setActorImg}
                disabled={isRunning}
                error={fieldErrors.actor}
              />
              <ImageUploadSlot
                label="Wardrobe"
                value={wardrobeImg}
                onChange={(value) => {
                  setWardrobeImg(value);
                  if (!value) {
                    setIncludeHeadwear(false);
                    setIncludeFootwear(false);
                  }
                }}
                disabled={isRunning}
                error={fieldErrors.wardrobe}
              />
            </div>
            {wardrobeImg ? (
              <div className="wardrobe-options">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={includeHeadwear}
                    disabled={isRunning}
                    onChange={(e) => setIncludeHeadwear(e.target.checked)}
                  />
                  <span>Include hat / headwear</span>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={includeFootwear}
                    disabled={isRunning}
                    onChange={(e) => setIncludeFootwear(e.target.checked)}
                  />
                  <span>Include shoes / footwear</span>
                </label>
              </div>
            ) : null}
          </div>

          <div className="block">
            <div className="block-title">Description</div>
            <label className="field">
              <span>
                Actor description {!actorImg ? <span className="req">*</span> : null}
              </span>
              <textarea
                rows={8}
                value={description}
                disabled={isRunning}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Age, look, hair, clothing, pose notes… (optional when reference photo is uploaded)"
              />
              {fieldErrors.description ? (
                <span className="field-error">{fieldErrors.description}</span>
              ) : null}
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
                      placeholder="integer"
                      inputMode="numeric"
                    />
                    {fieldErrors.seed ? <span className="field-error">{fieldErrors.seed}</span> : null}
                  </label>
                ) : null}
                <label className="field">
                  <span>Negative prompt</span>
                  <textarea rows={3} value={negative} disabled={isRunning} onChange={(e) => setNegative(e.target.value)} />
                </label>
              </div>
            ) : null}
          </div>

          {formError ? <div className="banner error">{formError}</div> : null}

          <div className="actions">
            {!isRunning ? (
              <button type="button" className="btn primary" disabled={busy} onClick={onGenerate}>
                {busy ? "Submitting…" : "Generate Actor"}
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
            </div>
          </div>

          {job?.error && (job.status === "failed" || job.status === "cancelled") ? (
            <div className="banner error">{job.error}</div>
          ) : null}

          {job?.seed != null ? (
            <p className="meta-line">
              Seed <code>{job.seed}</code>
            </p>
          ) : null}

          <OutputGrid
            status={status}
            outputs={outputMap}
            mainKeys={["master", "fullbody_threeview", "bust_threeview", "asset_sheet"]}
            featuredKey="asset_sheet"
            showSecondary={!!wardrobeImg || !!job?.has_wardrobe_ref || !!job?.outputs?.wardrobe_ref?.url}
            onOpen={(slot, all) => {
              const idx = all.findIndex((s) => s.key === slot.key);
              setLightbox({ slots: all, index: Math.max(0, idx) });
            }}
          />

          <div className="actions output-actions">
            <button
              type="button"
              className="btn primary"
              disabled={!job || job.status !== "succeeded" || busy || !!job.actor_id}
              onClick={onSave}
            >
              {job?.actor_id ? "Saved" : "Save to Library"}
            </button>
            <button type="button" className="btn secondary" disabled={!job || isRunning || busy} onClick={onGenerate}>
              Regenerate
            </button>
          </div>

          {savedActor || job?.actor_id ? (
            <div className="banner ok">
              Saved as Actor · <code>{savedActor?.id || job?.actor_id}</code>
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
