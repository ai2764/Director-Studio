import { useState, type ChangeEvent } from "react";
import type { ProjectMusicMaster } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import { uploadMusicMaster } from "./api";

function formatDuration(durationS: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationS));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function MusicMasterControl() {
  const { project, projectId, refreshProjects } = useProject();
  const [uploaded, setUploaded] = useState<{
    projectId: string;
    master: ProjectMusicMaster;
  } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!projectId || project?.mode !== "mv") return null;
  const master = uploaded?.projectId === projectId
    ? uploaded.master
    : project.music_master;

  const onFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const updated = await uploadMusicMaster(projectId, file);
      if (updated.music_master) {
        setUploaded({ projectId, master: updated.music_master });
      }
      await refreshProjects();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setUploading(false);
      input.value = "";
    }
  };

  return (
    <div className="music-master-control">
      <div className="music-master-copy">
        <span className="music-master-label">Song master</span>
        {master ? (
          <span className="music-master-status">
            <strong>{master.filename}</strong>
            <span>{formatDuration(master.duration_s)}</span>
          </span>
        ) : (
          <span className="music-master-status muted">No song imported</span>
        )}
      </div>
      {error ? <span className="music-master-error" role="alert">{error}</span> : null}
      <label className={`btn music-master-action${uploading ? " disabled" : ""}`}>
        <input
          type="file"
          aria-label="Song master"
          accept=".wav,.mp3,.m4a,.aac,.flac,.ogg,audio/wav,audio/mpeg,audio/mp4,audio/aac,audio/flac,audio/ogg"
          disabled={uploading}
          onChange={(event) => void onFileChange(event)}
        />
        <span>{uploading ? "Importing…" : master ? "Replace song" : "Import song"}</span>
      </label>
    </div>
  );
}
