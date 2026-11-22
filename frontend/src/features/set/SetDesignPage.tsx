import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { ImageUploadSlot, type LocalImage } from "../../shared/components/ImageUploadSlot";
import { Lightbox } from "../../shared/components/Lightbox";
import { OutputGrid } from "../../shared/components/OutputGrid";
import type { OutputSlot } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import { Badge, Banner, Button, Input, StatusDot, Textarea, cn } from "../../shared/ui";
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

const STATUS_TONES: Record<JobStatus | "idle", "neutral" | "info" | "ok" | "danger" | "warn"> = {
  idle: "neutral",
  queued: "info",
  uploading: "info",
  running: "info",
  succeeded: "ok",
  failed: "danger",
  cancelled: "warn",
};

interface Props {
  onOpenLibrary: () => void;
}

function angleLabel(prompt: string): string {
  const parts = prompt.split(",").map((part) => part.trim());
  const primary = parts[0] || "Angle";
  const descriptive =
    /^front view$/i.test(primary) && parts[1] && !/^eye level$/i.test(parts[1])
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
    () =>
      angles
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
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
      <main className="workspace mx-auto grid w-full max-w-[1440px] grid-cols-1 gap-4 p-5 lg:grid-cols-[420px_minmax(0,1fr)] lg:items-start">
        <section className="panel input-panel flex flex-col gap-4 rounded-lg border border-border-subtle bg-panel p-4 shadow-[var(--shadow-card)]">
          <div className="border-b border-border-subtle pb-3">
            <h2 className="text-sm font-semibold tracking-[-0.01em] text-ink">
              Set Design · Multi-Angle
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
              Select a project in the header before generating scenes.
            </Banner>
          ) : null}

          <p className="field-hint text-xs leading-relaxed text-ink-secondary">
            Upload one scene plate. 1728×960 quality mode re-shoots seven selectable views with Qwen
            Edit 2511 multi-angle LoRA. The original plate is saved as master for H3. Filenames use
            your scene name + view, e.g.{" "}
            <code className="rounded bg-elevated px-1 py-0.5 font-mono text-[11px] text-ink">
              {name.trim() || "SceneName"}_01_left_side_view_h270_v0.png
            </code>
            .
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
                placeholder="e.g. Audition_Room"
              />
              {fieldErrors.name ? (
                <span className="field-error text-[11px] text-status-danger">
                  {fieldErrors.name}
                </span>
              ) : null}
            </label>
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Notes</span>
              <Input value={notes} disabled={isRunning} onChange={(e) => setNotes(e.target.value)} />
            </label>
          </div>

          <div className="block flex flex-col gap-3 rounded-md border border-border-subtle bg-elevated/40 p-3">
            <div className="block-title text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              2. Scene reference
            </div>
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

          <div className="block flex flex-col gap-3 rounded-md border border-border-subtle bg-elevated/40 p-3">
            <div className="block-title text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              3. Angles ({angleCount}/{angleLines.length} selected)
            </div>
            <div
              className="angle-toggle-row flex flex-wrap gap-1.5"
              role="group"
              aria-label="Scene angles"
            >
              {angleLines.map((angle, index) => {
                const selected = !disabledAngleIndexes.has(index);
                const label = angleLabel(angle);
                return (
                  <button
                    key={`${index}-${angle}`}
                    type="button"
                    className={cn(
                      "angle-toggle inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                      "disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "selected border-accent/45 bg-accent-soft text-ink"
                        : "border-border-subtle bg-panel text-ink-muted hover:border-border-strong hover:text-ink",
                    )}
                    aria-label={label}
                    aria-pressed={selected}
                    title={angle}
                    disabled={isRunning}
                    onClick={() => toggleAngle(index)}
                  >
                    <span className="angle-toggle-index font-mono text-[10px] text-ink-muted tabular-nums">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span>{label}</span>
                  </button>
                );
              })}
            </div>
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">
                Angle prompts <span className="req text-status-danger">*</span>
              </span>
              <Textarea
                rows={6}
                value={angles}
                disabled={isRunning}
                hasError={Boolean(fieldErrors.angles)}
                onChange={(e) => setAngles(e.target.value)}
                placeholder="One angle per line…"
                className="mono-area font-mono text-xs"
              />
              {fieldErrors.angles ? (
                <span className="field-error text-[11px] text-status-danger">
                  {fieldErrors.angles}
                </span>
              ) : null}
              <span className="field-hint text-[11px] leading-relaxed text-ink-muted">
                Multi-angle LoRA format, e.g.{" "}
                <code className="rounded bg-panel px-1 py-0.5 font-mono text-[10px]">
                  front view, eye level, medium shot (horizontal: 0, vertical: 0, zoom: 5.0)
                </code>
              </span>
            </label>
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Prepend</span>
              <Input
                value={prepend}
                disabled={isRunning}
                onChange={(e) => setPrepend(e.target.value)}
                placeholder="Keep the same set; only change the camera"
              />
            </label>
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Append (optional)</span>
              <Input
                value={append}
                disabled={isRunning}
                onChange={(e) => setAppend(e.target.value)}
              />
            </label>
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
                {busy ? "Submitting…" : `Generate ${angleCount || ""} Angles`}
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
              Each non-empty angle line produces one image in a single Comfy batch. Results appear
              as a multi-view set for the library.
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

          <div className="actions output-actions flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
            <Button
              type="button"
              variant="primary"
              disabled={!job || job.status !== "succeeded" || busy || !!job.scene_id}
              onClick={onSave}
            >
              {job?.scene_id ? "Saved" : "Save to Library"}
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

          {saved || job?.scene_id ? (
            <Banner tone="ok" role="status">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  Saved as Scene ·{" "}
                  <code className="font-mono text-[11px]">{saved?.id || job?.scene_id}</code>
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
