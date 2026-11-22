import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Plus, Upload, X } from "lucide-react";
import { useProject } from "../../shared/project/ProjectContext";
import { Banner, Button, Input, SectionCard, Textarea, buttonVariants, cn } from "../../shared/ui";
import {
  getSongSegments,
  previewSongSegments,
  saveSongSegments,
  type SegmentDraft,
  type SongSegmentsDocument,
} from "./api";

const MAX_SOURCE_BYTES = 256 * 1024;

function normalizedSegmentInput(segments: SongSegmentsDocument["segments"]): string {
  return JSON.stringify(
    {
      segments: segments.map(({ start_s, end_s, text }) => ({ start_s, end_s, text })),
    },
    null,
    2,
  );
}

export function SongSegmentsEditor() {
  const { projectId, project } = useProject();
  const [document, setDocument] = useState<SongSegmentsDocument | null>(null);
  const [masterStale, setMasterStale] = useState(false);
  const [rawInput, setRawInput] = useState("");
  const [rows, setRows] = useState<SegmentDraft[]>([]);
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewVersion = useRef(0);
  const editVersion = useRef(0);
  const activeProjectId = useRef(projectId);
  activeProjectId.current = projectId;

  useEffect(() => {
    let live = true;
    previewVersion.current += 1;
    editVersion.current += 1;
    const editAtLoad = editVersion.current;
    setDocument(null);
    setRows([]);
    setRawInput("");
    setDirty(false);
    setMasterStale(false);
    setBusy(false);
    setSaving(false);
    setError(null);
    if (projectId && project?.mode === "mv") {
      getSongSegments(projectId)
        .then((state) => {
          if (!live) return;
          setDocument(state.document);
          setMasterStale(state.master_stale);
          if (editVersion.current === editAtLoad) {
            setRawInput(
              state.document
                ? state.document.raw_input || normalizedSegmentInput(state.document.segments)
                : "",
            );
            setRows(state.document?.segments || []);
          }
        })
        .catch((cause) => {
          if (live) setError(cause instanceof Error ? cause.message : String(cause));
        });
    }
    return () => {
      live = false;
    };
  }, [projectId, project?.mode]);

  if (project?.mode !== "mv") return null;
  const stale =
    masterStale ||
    Boolean(
      document &&
        (!project.music_master || document.master_sha256 !== project.music_master.content_sha256),
    );

  const updateRow = (index: number, patch: Partial<SegmentDraft>) => {
    previewVersion.current += 1;
    editVersion.current += 1;
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    setBusy(false);
    setDirty(true);
  };

  const addRow = () => {
    previewVersion.current += 1;
    editVersion.current += 1;
    setBusy(false);
    setRows((current) => [
      ...current,
      {
        id: `seg_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`,
        start_s: current.at(-1)?.end_s ?? null,
        end_s: null,
        text: "",
      },
    ]);
    setDirty(true);
  };

  const removeRow = (index: number) => {
    previewVersion.current += 1;
    editVersion.current += 1;
    setBusy(false);
    setRows((current) => current.filter((_, i) => i !== index));
    setDirty(true);
  };

  const onTextFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    const projectAtImport = projectId;
    const version = ++previewVersion.current;
    editVersion.current += 1;
    setBusy(false);
    if (file.size > MAX_SOURCE_BYTES) {
      setError("The notes file must be under 256 KiB.");
      return;
    }
    try {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      if (activeProjectId.current !== projectAtImport || previewVersion.current !== version) return;
      if (text.includes("\0")) throw new Error("Choose a UTF-8 text file.");
      setRawInput(text);
      setRows([]);
      setUnresolved([]);
      setDirty(true);
      setError(null);
    } catch (cause) {
      if (activeProjectId.current === projectAtImport && previewVersion.current === version) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    }
  };

  const prepare = async () => {
    if (!projectId) return;
    const projectAtPreview = projectId;
    const version = ++previewVersion.current;
    setBusy(true);
    setError(null);
    try {
      const preview = await previewSongSegments(projectId, rawInput);
      if (activeProjectId.current !== projectAtPreview || previewVersion.current !== version) return;
      editVersion.current += 1;
      setRows(preview.rows);
      setUnresolved(preview.unresolved);
      setDirty(true);
    } catch (cause) {
      if (activeProjectId.current === projectAtPreview && previewVersion.current === version) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (activeProjectId.current === projectAtPreview && previewVersion.current === version) {
        setBusy(false);
      }
    }
  };

  const save = async () => {
    if (!projectId || !project?.music_master) return;
    const projectAtSave = projectId;
    const editAtSave = editVersion.current;
    if (rows.some((row) => row.start_s === null || row.end_s === null || !row.text.trim())) {
      setError("Give every segment a start, end, and lyric before saving.");
      return;
    }
    setBusy(true);
    setSaving(true);
    setError(null);
    try {
      const saved = await saveSongSegments(
        projectId,
        document?.revision ?? 0,
        rawInput,
        rows.map((row) => ({
          id: row.id,
          start_s: row.start_s!,
          end_s: row.end_s!,
          text: row.text.trim(),
        })),
      );
      if (activeProjectId.current !== projectAtSave) return;
      setDocument(saved);
      window.dispatchEvent(new CustomEvent("song-segments-changed", { detail: projectId }));
      setMasterStale(false);
      if (editVersion.current === editAtSave) {
        setRows(saved.segments);
        setUnresolved([]);
        setDirty(false);
      }
    } catch (cause) {
      if (activeProjectId.current === projectAtSave) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (activeProjectId.current === projectAtSave) {
        setBusy(false);
        setSaving(false);
      }
    }
  };

  return (
    <div className="song-segments-editor flex flex-col gap-3">
      {stale ? (
        <Banner tone="warn" role="alert">
          The song has changed. Check the times and save these segments against the new song.
        </Banner>
      ) : null}
      {error ? (
        <Banner tone="danger" role="alert">
          {error}
        </Banner>
      ) : null}

      <SectionCard
        className="music-import-panel"
        aria-label="Prepare song segments"
        title="Lyrics and timing"
        description="Paste notes, CSV, subtitles, or Whisper JSON. You can correct words and times before saving."
      >
        <div className="flex flex-col gap-3">
          <Textarea
            aria-label="Lyrics and time notes"
            placeholder="00:04.54–00:13.08  First line…"
            value={rawInput}
            disabled={saving}
            rows={5}
            className="font-mono text-xs"
            onChange={(event) => {
              previewVersion.current += 1;
              editVersion.current += 1;
              setRawInput(event.target.value);
              setRows([]);
              setUnresolved([]);
              setDirty(true);
              setBusy(false);
            }}
          />

          <div className="music-import-actions flex flex-wrap items-center gap-2">
            <label
              className={cn(
                buttonVariants({ variant: "secondary", size: "sm" }),
                "music-text-file relative overflow-hidden",
                saving && "pointer-events-none opacity-50",
              )}
            >
              <input
                type="file"
                accept=".txt,.md,.csv,.tsv,.json,.lrc,.srt,.vtt,text/*,application/json"
                disabled={saving}
                className="sr-only"
                onChange={(event) => void onTextFile(event)}
                aria-label="Import song notes file"
              />
              <Upload className="h-3.5 w-3.5" aria-hidden="true" />
              Import text file
            </label>
            <Button
              type="button"
              variant="primary"
              size="sm"
              disabled={busy || !rawInput.trim()}
              onClick={() => void prepare()}
            >
              Prepare segments
            </Button>
          </div>

          {unresolved.length ? (
            <Banner tone="warn" title="Check before saving">
              <ul className="mt-1 list-disc pl-4 text-xs">
                {unresolved.map((item, index) => (
                  <li key={`${item}-${index}`}>{item}</li>
                ))}
              </ul>
            </Banner>
          ) : null}

          {rows.length || document ? (
            <div className="music-row-editor mt-1 flex flex-col gap-3 border-t border-border-subtle pt-3">
              <div className="music-row-heading flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold text-ink">Review segments</h3>
                <span className="font-mono text-[11px] text-ink-muted tabular-nums">
                  {rows.length} sections{dirty ? " · Unsaved changes" : ""}
                </span>
              </div>

              <div className="music-edit-list flex flex-col gap-2">
                {rows.map((row, index) => (
                  <div
                    className="music-edit-row flex items-start gap-2.5 rounded-md border border-border-subtle bg-elevated/40 p-2.5"
                    key={row.id}
                  >
                    <span className="music-edit-index mt-1.5 font-mono text-[11px] font-medium text-ink-muted tabular-nums">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div className="music-edit-fields flex min-w-0 flex-1 flex-col gap-2">
                      <div className="music-time-fields grid grid-cols-2 gap-2">
                        <label className="flex flex-col gap-1 text-[11px] font-medium text-ink-secondary">
                          <span>Start (s)</span>
                          <Input
                            aria-label={`Segment ${index + 1} start`}
                            type="number"
                            min="0"
                            step="0.01"
                            value={row.start_s ?? ""}
                            disabled={saving}
                            className="h-7 font-mono text-xs"
                            onChange={(event) =>
                              updateRow(index, {
                                start_s: event.target.value === "" ? null : Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-[11px] font-medium text-ink-secondary">
                          <span>End (s)</span>
                          <Input
                            aria-label={`Segment ${index + 1} end`}
                            type="number"
                            min="0"
                            step="0.01"
                            value={row.end_s ?? ""}
                            disabled={saving}
                            className="h-7 font-mono text-xs"
                            onChange={(event) =>
                              updateRow(index, {
                                end_s: event.target.value === "" ? null : Number(event.target.value),
                              })
                            }
                          />
                        </label>
                      </div>
                      <Textarea
                        aria-label={`Segment ${index + 1} lyrics`}
                        value={row.text}
                        disabled={saving}
                        rows={2}
                        className="min-h-[52px] text-xs"
                        onChange={(event) => updateRow(index, { text: event.target.value })}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="music-remove-row h-7 w-7 shrink-0 text-ink-muted hover:bg-status-danger-soft hover:text-status-danger"
                      aria-label={`Remove segment ${index + 1}`}
                      disabled={saving}
                      onClick={() => removeRow(index)}
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="music-add-row"
                  disabled={saving}
                  onClick={addRow}
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                  Add segment
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  className="music-save"
                  disabled={busy || !project.music_master}
                  onClick={() => void save()}
                >
                  Save segments
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
}
