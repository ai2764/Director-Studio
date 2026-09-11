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
  const canGenerate =
    !busy && !promptDirty && !jobActive && readinessErrors.length === 0;
  const output = job ? finalOutputLink(job) : null;

  return (
    <section className="section-card compact-card json-asset-panel" aria-label="Shot assets">
      {view !== "output" ? <div className="json-reference-content">
      <div className="section-card-head">
        <h2 className="section-card-title">References</h2>
      </div>

      {shot.pictures.map((picture) => {
        const title = pictureSlotTitle(picture.index, picture.role);
        const file = files.pictures.get(picture.index) || null;
        const preview = picturePreviews.get(picture.index) || (file && !(file instanceof File) ? file.url : undefined);
        const filename = file instanceof File ? file.name : file?.filename;
        return (
          <div
            key={`picture-${picture.index}`}
            className="field json-asset-slot json-picture-slot"
            role="group"
            aria-label={`${title} reference`}
          >
            <div className="json-asset-slot-head">
              <span className="json-asset-slot-title">{title}</span>
            </div>
            <p className="field-hint">{picture.label}</p>
            <div className={preview ? "json-asset-slot-body has-preview" : "json-asset-slot-body"}>
              {preview ? (
                <img className="json-asset-preview" src={preview} alt={`${title} preview`} />
              ) : null}
              <div className="json-asset-slot-footer">
                {file ? (
                  <div className="filename">{filename}</div>
                ) : (
                  <div className="muted tiny json-file-state">No file selected</div>
                )}
                <div className="json-asset-slot-actions">
                  <label className="json-upload-control">
                    <input
                      type="file"
                      aria-label={title}
                      accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
                      onChange={(e) => {
                        const next = e.target.files?.[0] || null;
                        e.target.value = "";
                        if (!next) return;
                        onPictureFile(picture.index, next);
                      }}
                    />
                    <span>{file ? "Replace file" : "Choose file"}</span>
                  </label>
                  {file ? (
                    <button
                      type="button"
                      className="btn ghost sm json-asset-clear"
                      aria-label={`Clear ${title}`}
                      disabled={busy}
                      onClick={() => onPictureFile(picture.index, null)}
                    >
                      Clear
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        );
      })}

      <div className="section-card-head">
        <h2 className="section-card-title">Audio</h2>
      </div>
      {shot.audio.length === 0 ? (
        <p className="empty-copy">No audio slots declared.</p>
      ) : (
        shot.audio.map((audio) => {
          const title = audioSlotTitle(audio.index, audio.label);
          const file = files.audio.get(audio.index) || null;
          const filename = file instanceof File ? file.name : file?.filename;
          return (
            <div key={`audio-${audio.index}`} className="field json-asset-slot">
              <span className="json-asset-slot-title">{title}</span>
              {file ? (
                <div className="json-asset-file-row">
                  <div className="filename">{filename}</div>
                  <button
                    type="button"
                    className="btn ghost sm"
                    disabled={busy}
                    onClick={() => onAudioFile(audio.index, null)}
                  >
                    {`Clear ${title}`}
                  </button>
                </div>
              ) : (
                <div className="muted tiny json-file-state">No file selected</div>
              )}
              <label className="json-upload-control">
                <input
                  type="file"
                  aria-label={title}
                  accept="audio/wav,audio/mpeg,audio/flac,audio/mp4,.wav,.mp3,.flac,.m4a"
                  onChange={(e) => {
                    const next = e.target.files?.[0] || null;
                    e.target.value = "";
                    if (!next) return;
                    onAudioFile(audio.index, next);
                  }}
                />
                <span>{file ? "Replace file" : "Choose file"}</span>
              </label>
            </div>
          );
        })
      )}

      {readinessErrors.length ? (
        <ul className="json-readiness-errors">
          {readinessErrors.map((err) => (
            <li key={err}>{err}</li>
          ))}
        </ul>
      ) : null}
      {promptDirty ? (
        <p className="field-hint">Save prompt changes before generating.</p>
      ) : null}
      </div> : null}

      {view !== "references" ? <div className="json-output-content">
      {view === "output" ? <div className="section-card-head">
        <h2 className="section-card-title">Output</h2>
      </div> : null}
      {job ? (
        <div className="run-job-box">
          <div className={`status-pill status-${job.status}`}>
            <span className="dot" />
            {job.status}
            <span className="job-id">{job.id}</span>
          </div>
          {job.error ? <div className="banner error">{job.error}</div> : null}
        </div>
      ) : (
        <p className="field-hint">No H3 job yet for this shot.</p>
      )}

      {output ? (
        <section className="json-output-panel" aria-label="Shot output">
          <div className="section-card-head">
            <h2 className="section-card-title">
              {outputVersion != null ? `Output v${outputVersion}` : "Output"}
            </h2>
          </div>
          <video className="h3-preview" controls playsInline src={output.href} />
          <div className="json-job-outputs">
            <a className="btn secondary sm" href={output.href} target="_blank" rel="noreferrer">
              {output.label}
            </a>
          </div>
        </section>
      ) : null}
      </div> : null}

      {showActions ? <div className="sticky-actions">
        <button
          type="button"
          className="btn primary"
          disabled={!canGenerate}
          onClick={onGenerate}
        >
          {jobActive ? "H3 running…" : "Generate"}
        </button>
        {jobActive ? (
          <button type="button" className="btn danger" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        ) : null}
      </div> : null}
    </section>
  );
}
