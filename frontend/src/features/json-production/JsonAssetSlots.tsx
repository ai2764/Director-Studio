import { ExternalLink, FileAudio, Play, Square, Upload } from "lucide-react";
import {
  Badge,
  Banner,
  Button,
  Panel,
  PanelBody,
  StatusDot,
  buttonVariants,
  cn,
  type StatusDotProps,
} from "../../shared/ui";
import type { JsonPictureRole, JsonProductionShot, JsonShotJobRecord, ShotFileMaps } from "./types";

const ACTIVE = new Set(["queued", "uploading", "running"]);

type Props = {
  shot: JsonProductionShot;
  files: ShotFileMaps;
  picturePreviews: Map<number, string>;
  readinessErrors: string[];
  promptDirty: boolean;
  job: JsonShotJobRecord | null;
  outputVersion: number | null;
  busy: boolean;
  onPictureFile: (index: number, file: File | null) => void;
  onAudioFile: (index: number, file: File | null) => void;
  onGenerate: () => void;
  onCancel: () => void;
  view?: "all" | "references" | "output";
  showActions?: boolean;
};

function titleCaseRole(role: JsonPictureRole): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

export function pictureSlotTitle(index: number, role: JsonPictureRole): string {
  return `Picture ${index} · ${titleCaseRole(role)}`;
}

export function audioSlotTitle(index: number, label: string): string {
  return label.trim() ? `Audio ${index} · ${label}` : `Audio ${index}`;
}

function finalOutputLink(job: JsonShotJobRecord): { href: string; label: string } | null {
  for (const key of ["video", "master", "enhanced"]) {
    const slot = job.outputs?.[key];
    if (slot?.url) return { href: slot.url, label: slot.label || "Output" };
  }
  const fallback = Object.values(job.outputs || {}).find((slot) => slot?.url);
  if (fallback?.url) return { href: fallback.url, label: fallback.label || "Output" };
  return null;
}

function jobTone(status: string): NonNullable<StatusDotProps["tone"]> {
  switch (status) {
    case "running":
    case "uploading":
      return "info";
    case "queued":
      return "accent";
    case "succeeded":
      return "ok";
    case "failed":
    case "cancelled":
      return "danger";
    default:
      return "neutral";
  }
}

export function JsonAssetSlots({
  shot,
  files,
  picturePreviews,
  readinessErrors,
  promptDirty,
  job,
  outputVersion,
  busy,
  onPictureFile,
  onAudioFile,
  onGenerate,
  onCancel,
  view = "all",
  showActions = true,
}: Props) {
  const jobActive = job ? ACTIVE.has(job.status) : false;
  const canGenerate = !busy && !promptDirty && !jobActive && readinessErrors.length === 0;
  const output = job ? finalOutputLink(job) : null;

  return (
    <Panel className="json-asset-panel flex flex-col overflow-hidden">
      <section aria-label="Shot assets" className="flex flex-col">
        <PanelBody className="flex flex-col gap-4 p-4">
          {view !== "output" ? (
            <div className="json-reference-content flex flex-col gap-3.5">
              <div className="flex items-center justify-between border-b border-border-subtle pb-2">
                <h2 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                  References
                </h2>
                <span className="font-mono text-[11px] text-ink-muted tabular-nums">
                  {shot.pictures.length} slots
                </span>
              </div>

              {shot.pictures.map((picture) => {
                const title = pictureSlotTitle(picture.index, picture.role);
                const file = files.pictures.get(picture.index) || null;
                const preview =
                  picturePreviews.get(picture.index) ||
                  (file && !(file instanceof File) ? file.url : undefined);
                const filename = file instanceof File ? file.name : file?.filename;
                return (
                  <div
                    key={`picture-${picture.index}`}
                    className="json-asset-slot json-picture-slot flex flex-col gap-2 rounded-lg border border-border-subtle bg-elevated/40 p-3"
                    role="group"
                    aria-label={`${title} reference`}
                  >
                    <div className="json-asset-slot-head flex items-center justify-between gap-2">
                      <span className="json-asset-slot-title text-xs font-semibold text-ink">
                        {title}
                      </span>
                    </div>
                    <p className="text-[11px] leading-relaxed text-ink-secondary">{picture.label}</p>
                    <div
                      className={cn(
                        "json-asset-slot-body flex flex-col gap-2.5",
                        preview && "has-preview",
                      )}
                    >
                      {preview ? (
                        <div className="overflow-hidden rounded-md border border-border-subtle bg-canvas">
                          <img
                            className="json-asset-preview max-h-44 w-full object-contain"
                            src={preview}
                            alt={`${title} preview`}
                          />
                        </div>
                      ) : null}
                      <div className="json-asset-slot-footer flex flex-wrap items-center justify-between gap-2">
                        {file ? (
                          <div className="filename min-w-0 truncate font-mono text-[11px] text-ink">
                            {filename}
                          </div>
                        ) : (
                          <div className="json-file-state text-[11px] text-ink-muted">
                            No file selected
                          </div>
                        )}
                        <div className="json-asset-slot-actions flex items-center gap-1.5">
                          <label
                            className={cn(
                              "json-upload-control",
                              buttonVariants({ variant: "secondary", size: "xs" }),
                            )}
                          >
                            <input
                              type="file"
                              className="sr-only"
                              aria-label={title}
                              accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
                              onChange={(e) => {
                                const next = e.target.files?.[0] || null;
                                e.target.value = "";
                                if (!next) return;
                                onPictureFile(picture.index, next);
                              }}
                            />
                            <Upload className="h-3 w-3" aria-hidden="true" />
                            <span>{file ? "Replace file" : "Choose file"}</span>
                          </label>
                          {file ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="xs"
                              className="json-asset-clear"
                              aria-label={`Clear ${title}`}
                              disabled={busy}
                              onClick={() => onPictureFile(picture.index, null)}
                            >
                              Clear
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}

              <div className="mt-1 flex items-center justify-between border-b border-border-subtle pb-2">
                <h2 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                  Audio
                </h2>
                <span className="font-mono text-[11px] text-ink-muted tabular-nums">
                  {shot.audio.length} slots
                </span>
              </div>
              {shot.audio.length === 0 ? (
                <p className="text-xs text-ink-muted">No audio slots declared.</p>
              ) : (
                shot.audio.map((audio) => {
                  const title = audioSlotTitle(audio.index, audio.label);
                  const file = files.audio.get(audio.index) || null;
                  const filename = file instanceof File ? file.name : file?.filename;
                  return (
                    <div
                      key={`audio-${audio.index}`}
                      className="json-asset-slot flex flex-col gap-2 rounded-lg border border-border-subtle bg-elevated/40 p-3"
                    >
                      <span className="json-asset-slot-title flex items-center gap-1.5 text-xs font-semibold text-ink">
                        <FileAudio className="h-3.5 w-3.5 text-ink-muted" aria-hidden="true" />
                        {title}
                      </span>
                      {file ? (
                        <div className="json-asset-file-row flex flex-wrap items-center justify-between gap-2">
                          <div className="filename min-w-0 truncate font-mono text-[11px] text-ink">
                            {filename}
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="xs"
                            disabled={busy}
                            onClick={() => onAudioFile(audio.index, null)}
                          >
                            {`Clear ${title}`}
                          </Button>
                        </div>
                      ) : (
                        <div className="json-file-state text-[11px] text-ink-muted">
                          No file selected
                        </div>
                      )}
                      <div>
                        <label
                          className={cn(
                            "json-upload-control",
                            buttonVariants({ variant: "secondary", size: "xs" }),
                          )}
                        >
                          <input
                            type="file"
                            className="sr-only"
                            aria-label={title}
                            accept="audio/wav,audio/mpeg,audio/flac,audio/mp4,.wav,.mp3,.flac,.m4a"
                            onChange={(e) => {
                              const next = e.target.files?.[0] || null;
                              e.target.value = "";
                              if (!next) return;
                              onAudioFile(audio.index, next);
                            }}
                          />
                          <Upload className="h-3 w-3" aria-hidden="true" />
                          <span>{file ? "Replace file" : "Choose file"}</span>
                        </label>
                      </div>
                    </div>
                  );
                })
              )}

              {readinessErrors.length ? (
                <ul className="json-readiness-errors flex flex-col gap-1 rounded-md border border-status-warn/30 bg-status-warn-soft px-3 py-2 text-[11px] text-status-warn">
                  {readinessErrors.map((err) => (
                    <li key={err}>{err}</li>
                  ))}
                </ul>
              ) : null}
              {promptDirty ? (
                <p className="text-[11px] font-medium text-status-warn">
                  Save prompt changes before generating.
                </p>
              ) : null}
            </div>
          ) : null}

          {view !== "references" ? (
            <div className="json-output-content flex flex-col gap-3.5">
              {view === "output" ? (
                <div className="flex items-center justify-between border-b border-border-subtle pb-2">
                  <h2 className="text-xs font-semibold uppercase tracking-[0.05em] text-ink-secondary">
                    Output
                  </h2>
                </div>
              ) : null}
              {job ? (
                <div className="run-job-box flex flex-col gap-2.5 rounded-lg border border-border-subtle bg-elevated/40 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <Badge
                      tone={jobTone(job.status)}
                      className={cn("status-pill", `status-${job.status}`)}
                    >
                      <StatusDot tone={jobTone(job.status)} pulse={jobActive} />
                      {job.status}
                    </Badge>
                    <span className="job-id font-mono text-[10px] text-ink-muted">{job.id}</span>
                  </div>
                  {job.error ? <Banner tone="danger">{job.error}</Banner> : null}
                </div>
              ) : (
                <p className="rounded-md border border-dashed border-border-subtle bg-elevated/30 px-3 py-4 text-center text-xs text-ink-muted">
                  No H3 job yet for this shot.
                </p>
              )}

              {output ? (
                <section
                  className="json-output-panel flex flex-col gap-2.5"
                  aria-label="Shot output"
                >
                  <div className="flex items-center justify-between">
                    <h2 className="text-xs font-semibold text-ink">
                      {outputVersion != null ? `Output v${outputVersion}` : "Output"}
                    </h2>
                  </div>
                  <video
                    className="h3-preview max-h-[380px] w-full rounded-md border border-border-subtle bg-black"
                    controls
                    playsInline
                    src={output.href}
                  />
                  <div className="json-job-outputs flex items-center gap-2">
                    <a
                      className={buttonVariants({ variant: "secondary", size: "sm" })}
                      href={output.href}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink className="h-3 w-3" aria-hidden="true" />
                      {output.label}
                    </a>
                  </div>
                </section>
              ) : null}
            </div>
          ) : null}

          {showActions ? (
            <div className="sticky-actions flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
              <Button
                type="button"
                variant="primary"
                size="sm"
                className="flex-1"
                disabled={!canGenerate}
                onClick={onGenerate}
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {jobActive ? "H3 running…" : "Generate"}
              </Button>
              {jobActive ? (
                <Button type="button" variant="danger" size="sm" disabled={busy} onClick={onCancel}>
                  <Square className="h-3 w-3" aria-hidden="true" />
                  Cancel
                </Button>
              ) : null}
            </div>
          ) : null}
        </PanelBody>
      </section>
    </Panel>
  );
}
