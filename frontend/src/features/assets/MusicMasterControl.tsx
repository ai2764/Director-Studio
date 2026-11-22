import { useState, type ChangeEvent } from "react";
import { Music, Upload } from "lucide-react";
import type { ProjectMusicMaster } from "../../shared/api/types";
import { useProject } from "../../shared/project/ProjectContext";
import { buttonVariants, cn } from "../../shared/ui";
import { uploadMusicMaster } from "../director/api";

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
    <div className="music-master-control flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle bg-panel px-3.5 py-2.5 shadow-[var(--shadow-card)]">
      <div className="music-master-copy flex min-w-0 items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border-subtle bg-elevated text-ink-secondary">
          <Music className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
        <div className="flex min-w-0 flex-col">
          <span className="music-master-label text-[11px] font-medium text-ink-muted">
            Song master
          </span>
          {master ? (
            <span className="music-master-status flex items-center gap-2 text-xs text-ink">
              <strong className="truncate font-medium">{master.filename}</strong>
              <span className="font-mono text-[11px] text-ink-muted tabular-nums">
                {formatDuration(master.duration_s)}
              </span>
            </span>
          ) : (
            <span className="music-master-status muted text-xs text-ink-muted">
              No song imported
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        {error ? (
          <span className="music-master-error text-xs text-status-danger" role="alert">
            {error}
          </span>
        ) : null}
        <label
          className={cn(
            buttonVariants({ variant: "secondary", size: "sm" }),
            "music-master-action relative overflow-hidden",
            uploading && "pointer-events-none opacity-50",
          )}
        >
          <input
            type="file"
            aria-label="Song master"
            accept=".wav,.mp3,.m4a,.aac,.flac,.ogg,audio/wav,audio/mpeg,audio/mp4,audio/aac,audio/flac,audio/ogg"
            disabled={uploading}
            className="sr-only"
            onChange={(event) => void onFileChange(event)}
          />
          <Upload className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{uploading ? "Importing…" : master ? "Replace song" : "Import song"}</span>
        </label>
      </div>
    </div>
  );
}
