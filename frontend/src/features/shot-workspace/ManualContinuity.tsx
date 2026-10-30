import { useEffect, useState } from "react";
import type { Shot } from "../../shared/api/types";
import { getShot, saveVideoContext, uploadVideoContext } from "./api";
import { VideoContextPanel } from "../director/VideoContextPanel";

export function ManualContinuity({ shot, shots, disabled, onSaved }: {
  shot: Shot; shots: Shot[]; disabled: boolean; onSaved: (shot: Shot) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const index = shots.findIndex((item) => item.id === shot.id);
  const mode = shot.video_context?.mode || "off";
  useEffect(() => { setUploading(false); setError(""); }, [shot.id]);
  async function choose(value: string) {
    setError("");
    if (value === "previous_shot" && index <= 0) { setError("The first shot has no previous shot. Upload a source video instead."); return; }
    if (value === "external_upload") { setUploading(true); return; }
    setUploading(false); setBusy(true);
    try {
      await saveVideoContext(shot.id, value === "off" ? { mode: "off" }
        : { mode: "previous_shot", source_shot_id: shots[index - 1].id });
      onSaved(await getShot(shot.id));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  async function upload(file?: File) {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const record = await uploadVideoContext(shot.project_id, file);
      await saveVideoContext(shot.id, { mode: "external_upload", upload_id: record.upload_id });
      onSaved(await getShot(shot.id)); setUploading(false);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  return <section className="manual-continuity" aria-label="Shot continuity">
    <label>Continuity<select aria-label="Continuity mode" disabled={disabled || busy}
      value={uploading ? "external_upload" : mode} onChange={(event) => void choose(event.target.value)}>
      <option value="off">Standalone shot</option>
      <option value="previous_shot" disabled={index <= 0}>Continue an earlier shot</option>
      <option value="external_upload">Continue an uploaded video</option>
    </select></label>
    {uploading && <label className="manual-upload-context">Upload source video
      <input type="file" aria-label="Upload source video" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
        disabled={disabled || busy} onChange={(event) => void upload(event.target.files?.[0])} />
      <small>Applied after upload and save.</small>
    </label>}
    {busy && <small role="status">Saving continuity…</small>}
    {error && <p role="alert" className="banner error">{error}</p>}
    {mode !== "off" && !uploading && <fieldset disabled={disabled || busy} className="manual-continuity-settings">
      <VideoContextPanel shot={shot} shots={shots} showDisableControl={false} expanded onShotUpdated={onSaved} />
    </fieldset>}
  </section>;
}
