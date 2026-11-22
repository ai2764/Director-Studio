import { useEffect, useState } from "react";
import type { Shot } from "../../shared/api/types";
import { getShot, saveVideoContext, uploadVideoContext } from "./api";
import { VideoContextPanel } from "../director/VideoContextPanel";
import { Banner, Select } from "../../shared/ui";

export function ManualContinuity({
  shot,
  shots,
  disabled,
  onSaved,
}: {
  shot: Shot;
  shots: Shot[];
  disabled: boolean;
  onSaved: (shot: Shot) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const index = shots.findIndex((item) => item.id === shot.id);
  const mode = shot.video_context?.mode || "off";

  useEffect(() => {
    setUploading(false);
    setError("");
  }, [shot.id]);

  async function choose(value: string) {
    setError("");
    if (value === "previous_shot" && index <= 0) {
      setError("The first shot has no previous shot. Upload a source video instead.");
      return;
    }
    if (value === "external_upload") {
      setUploading(true);
      return;
    }
    setUploading(false);
    setBusy(true);
    try {
      await saveVideoContext(
        shot.id,
        value === "off"
          ? { mode: "off" }
          : { mode: "previous_shot", source_shot_id: shots[index - 1].id },
      );
      onSaved(await getShot(shot.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file?: File) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const record = await uploadVideoContext(shot.project_id, file);
      await saveVideoContext(shot.id, { mode: "external_upload", upload_id: record.upload_id });
      onSaved(await getShot(shot.id));
      setUploading(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="grid gap-2.5 border-t border-border-subtle pt-3 text-xs"
      aria-label="Shot continuity"
    >
      <label className="flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-ink-secondary">
        <span>Continuity</span>
        <Select
          size="sm"
          wrapperClassName="min-w-[200px] max-w-full flex-1 sm:flex-initial sm:w-64"
          aria-label="Continuity mode"
          disabled={disabled || busy}
          value={uploading ? "external_upload" : mode}
          onChange={(event) => void choose(event.target.value)}
        >
          <option value="off">Standalone shot</option>
          <option value="previous_shot" disabled={index <= 0}>
            Continue an earlier shot
          </option>
          <option value="external_upload">Continue an uploaded video</option>
        </Select>
      </label>
      {uploading && (
        <label className="grid gap-1.5 rounded-md border border-dashed border-border-strong bg-elevated/40 p-2.5 text-xs font-medium text-ink">
          <span>Upload source video</span>
          <input
            type="file"
            aria-label="Upload source video"
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
            disabled={disabled || busy}
            onChange={(event) => void upload(event.target.files?.[0])}
            className="text-xs text-ink-secondary file:mr-2.5 file:cursor-pointer file:rounded file:border file:border-border-subtle file:bg-panel file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-ink hover:file:bg-hover"
          />
          <small className="text-[11px] font-normal text-ink-muted">
            Applied after upload and save.
          </small>
        </label>
      )}
      {busy && (
        <small role="status" className="text-[11px] text-ink-muted">
          Saving continuity…
        </small>
      )}
      {error && <Banner tone="danger">{error}</Banner>}
      {mode !== "off" && !uploading && (
        <fieldset disabled={disabled || busy} className="m-0 min-w-0 border-0 p-0">
          <VideoContextPanel
            shot={shot}
            shots={shots}
            showDisableControl={false}
            expanded
            onShotUpdated={onSaved}
          />
        </fieldset>
      )}
    </section>
  );
}
