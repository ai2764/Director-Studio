import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { ImageUploadSlot, type LocalImage } from "../../shared/components/ImageUploadSlot";
import { Lightbox } from "../../shared/components/Lightbox";
import { OutputGrid } from "../../shared/components/OutputGrid";
import type { OutputSlot } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import { Badge, Banner, Button, Input, StatusDot } from "../../shared/ui";
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

const STATUS_TONES: Record<JobStatus | "idle", "neutral" | "info" | "ok" | "danger" | "warn"> = {
  idle: "neutral",
  queued: "info",
  uploading: "info",
  running: "info",
  succeeded: "ok",
  failed: "danger",
  cancelled: "warn",
};

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
      <main className="workspace mx-auto grid w-full max-w-[1440px] grid-cols-1 gap-4 p-5 lg:grid-cols-[420px_minmax(0,1fr)] lg:items-start">
        <section className="panel input-panel flex flex-col gap-4 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]">
          <div className="border-b border-border-subtle pb-3">
            <h2 className="text-sm font-semibold tracking-[-0.01em] text-ink">
              Props · Reference Sheet
            </h2>
            {projectId ? (
              <p className="project-scope-hint mt-1 text-xs text-ink-secondary">
                Saving to project{" "}
                <strong className="font-medium text-ink">{project?.name || projectId}</strong>
              </p>
            ) : null}
          </div>

          {!projectId ? (
            <Banner tone="danger" role="alert">
              Select a project in the header before preparing props.
            </Banner>
          ) : null}

          <p className="field-hint text-xs leading-relaxed text-ink-secondary">
            Upload one photo. Director Studio creates one multi-view reference sheet: the same prop
            in a large hero view and supporting angles, on a neutral studio background. H3 receives
            the finished sheet as one picture reference.
          </p>

          <div className="block flex flex-col gap-3 rounded-md border border-border-subtle bg-elevated/40 p-3">
            <div className="block-title text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              1. Identity
            </div>
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">
                Name <span className="req text-status-danger">*</span>
              </span>
              <Input
                value={name}
                disabled={isRunning}
                hasError={Boolean(fieldErrors.name)}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Folding knife"
              />
              {fieldErrors.name ? (
                <span className="field-error text-[11px] text-status-danger">
                  {fieldErrors.name}
                </span>
              ) : null}
            </label>
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Notes</span>
              <Input
                value={notes}
                disabled={isRunning}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Optional: logo, material, color"
              />
            </label>
          </div>

          <div className="block flex flex-col gap-3 rounded-md border border-border-subtle bg-elevated/40 p-3">
            <div className="block-title text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              2. Photo
            </div>
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

          <div className="block advanced rounded-md border border-border-subtle bg-elevated/30 p-3">
            <button
              type="button"
              className="advanced-toggle flex w-full cursor-pointer items-center gap-1.5 text-left text-xs font-medium text-ink-secondary hover:text-ink"
              onClick={() => setAdvancedOpen((v) => !v)}
            >
              {advancedOpen ? (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              <span>Advanced</span>
            </button>
            {advancedOpen ? (
              <div className="advanced-body mt-3 flex flex-col gap-3 border-t border-border-subtle pt-3">
                <label className="check flex cursor-pointer items-center gap-2 text-xs text-ink">
                  <input
                    type="checkbox"
                    checked={fixedSeed}
                    disabled={isRunning}
                    className="h-3.5 w-3.5 rounded border-border-strong accent-accent"
                    onChange={(e) => setFixedSeed(e.target.checked)}
                  />
                  <span>Fixed seed</span>
                </label>
                {fixedSeed ? (
                  <label className="field flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-ink-secondary">Seed</span>
                    <Input
                      value={seed}
                      disabled={isRunning}
                      hasError={Boolean(fieldErrors.seed)}
                      onChange={(e) => setSeed(e.target.value)}
                      inputMode="numeric"
                    />
                    {fieldErrors.seed ? (
                      <span className="field-error text-[11px] text-status-danger">
                        {fieldErrors.seed}
                      </span>
                    ) : null}
                  </label>
                ) : null}
              </div>
            ) : null}
          </div>

          {formError ? (
            <Banner tone="danger" role="alert">
              {formError}
            </Banner>
          ) : null}

          <div className="actions flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
            {!isRunning ? (
              <Button type="button" variant="primary" disabled={busy} onClick={onGenerate}>
                {busy ? "Submitting…" : "Prepare Reference Sheet"}
              </Button>
            ) : (
              <Button type="button" variant="danger" onClick={onCancel}>
                Cancel
              </Button>
            )}
            <Button type="button" variant="ghost" disabled={isRunning} onClick={onReset}>
              Reset form
            </Button>
          </div>
        </section>

        <section className="panel output-panel flex min-w-0 flex-col gap-4 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]">
          <div className="output-header flex items-center justify-between gap-3 border-b border-border-subtle pb-3">
            <h2 className="text-sm font-semibold tracking-[-0.01em] text-ink">Output</h2>
            <Badge tone={STATUS_TONES[status]} className={`status-pill status-${status}`}>
              <StatusDot tone={STATUS_TONES[status]} pulse={isRunning} />
              <span>{statusLabel}</span>
              {job ? <span className="job-id font-mono text-[10px] opacity-80">{job.id}</span> : null}
            </Badge>
          </div>

          {!job ? (
            <p className="empty-copy text-xs leading-relaxed text-ink-muted">
              The prepared reference sheet appears here. Save it to the library so Director can cast
              its complete multi-view object information as a single H3 picture.
            </p>
          ) : null}

          {job?.error && (job.status === "failed" || job.status === "cancelled") ? (
            <Banner tone="danger" role="alert">
              {job.error}
            </Banner>
          ) : null}

          {job?.seed != null ? (
            <p className="meta-line text-xs text-ink-secondary">
              Seed{" "}
              <code className="rounded bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-ink">
                {job.seed}
              </code>
              {job.comfy_prompt_id ? (
                <>
                  {" "}
                  · Comfy{" "}
                  <code className="rounded bg-elevated px-1.5 py-0.5 font-mono text-[11px] text-ink">
                    {job.comfy_prompt_id.slice(0, 8)}…
                  </code>
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

          <div className="actions output-actions flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
            <Button
              type="button"
              variant="primary"
              disabled={!job || job.status !== "succeeded" || busy || !!job.prop_id}
              onClick={onSave}
            >
              {job?.prop_id ? "Saved" : "Save to Library"}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={!job || isRunning || busy}
              onClick={onGenerate}
            >
              Regenerate
            </Button>
          </div>

          {saved || job?.prop_id ? (
            <Banner tone="ok" role="status">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  Saved as Prop ·{" "}
                  <code className="font-mono text-[11px]">{saved?.id || job?.prop_id}</code>
                </span>
                <Button type="button" variant="ghost" size="xs" onClick={onOpenLibrary}>
                  View in Library
                </Button>
              </div>
            </Banner>
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
