import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useProject } from "../../shared/project/ProjectContext";
import { DirectorPage } from "../director/DirectorPage";
import {
  getSongSegments, previewSongSegments, saveSongSegments, songAudioUrl,
  type SegmentDraft, type SegmentSelection, type SongSegmentsDocument,
} from "./api";

const MAX_SOURCE_BYTES = 256 * 1024;

function timeLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
}

export function MusicPage({ mobile = false }: { mobile?: boolean }) {
  const { projectId, project } = useProject();
  const [document, setDocument] = useState<SongSegmentsDocument | null>(null);
  const [masterStale, setMasterStale] = useState(false);
  const [rawInput, setRawInput] = useState("");
  const [rows, setRows] = useState<SegmentDraft[]>([]);
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
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
    setSelectedIds([]);
    setDirty(false);
    setMasterStale(false);
    setBusy(false);
    setSaving(false);
    setError(null);
    if (projectId && project?.mode === "mv") {
      getSongSegments(projectId).then((state) => {
        if (!live) return;
        setDocument(state.document);
        setMasterStale(state.master_stale);
        if (editVersion.current === editAtLoad) {
          setRawInput(state.document?.raw_input || "");
          setRows(state.document?.segments || []);
        }
      }).catch((cause) => {
        if (live) setError(cause instanceof Error ? cause.message : String(cause));
      });
    }
    return () => { live = false; };
  }, [projectId, project?.mode]);

  if (project?.mode !== "mv") return null;
  const stale = masterStale || Boolean(document && (
    !project.music_master || document.master_sha256 !== project.music_master.content_sha256
  ));

  const updateRow = (index: number, patch: Partial<SegmentDraft>) => {
    previewVersion.current += 1;
    editVersion.current += 1;
    setRows((current) => current.map((row, i) => i === index ? { ...row, ...patch } : row));
    setBusy(false);
    setDirty(true);
    setSelectedIds([]);
  };

  const addRow = () => {
    previewVersion.current += 1;
    editVersion.current += 1;
    setBusy(false);
    setRows((current) => [...current, {
      id: `seg_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`,
      start_s: current.at(-1)?.end_s ?? null,
      end_s: null,
      text: "",
    }]);
    setDirty(true);
    setSelectedIds([]);
  };

  const removeRow = (index: number) => {
    previewVersion.current += 1;
    editVersion.current += 1;
    setBusy(false);
    setRows((current) => current.filter((_, i) => i !== index));
    setDirty(true);
    setSelectedIds([]);
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
      setSelectedIds([]);
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
      setSelectedIds([]);
    } catch (cause) {
      if (activeProjectId.current === projectAtPreview && previewVersion.current === version) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (activeProjectId.current === projectAtPreview && previewVersion.current === version) setBusy(false);
    }
  };

  const save = async () => {
    if (!projectId || !project?.music_master) return;
    const projectAtSave = projectId;
    const editAtSave = editVersion.current;
    if (rows.some((row) =>
      row.start_s === null || row.end_s === null || !row.text.trim()
    )) {
      setError("Give every segment a start, end, and lyric before saving.");
      return;
    }
    setBusy(true);
    setSaving(true);
    setError(null);
    try {
      const saved = await saveSongSegments(projectId, document?.revision ?? 0, rawInput,
        rows.map((row) => ({
          id: row.id, start_s: row.start_s!, end_s: row.end_s!, text: row.text.trim(),
        })),
      );
      if (activeProjectId.current !== projectAtSave) return;
      setDocument(saved);
      setMasterStale(false);
      setSelectedIds([]);
      if (editVersion.current === editAtSave) {
        setRows(saved.segments);
        setUnresolved([]);
        setDirty(false);
      }
    } catch (cause) {
      if (activeProjectId.current === projectAtSave) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (activeProjectId.current === projectAtSave) {
        setBusy(false);
        setSaving(false);
      }
    }
  };

  const selectRow = (id: string) => {
    if (!document || dirty || stale) return;
    const ids = document.segments.map((row) => row.id);
    let next: string[];
    if (selectedIds.length === 1 && selectedIds[0] === id) next = [];
    else if (!selectedIds.length || selectedIds.includes(id)) next = [id];
    else {
      const start = ids.indexOf(selectedIds[0]);
      const end = ids.indexOf(id);
      next = ids.slice(Math.min(start, end), Math.max(start, end) + 1);
    }
    if (next.length > 32) {
      setError("Select at most 32 adjacent sections for one discussion.");
      return;
    }
    setSelectedIds(next);
    setError(null);
  };

  const selection: SegmentSelection | undefined = document && selectedIds.length && !dirty && !stale
    ? { revision: document.revision, ids: selectedIds } : undefined;

  return (
    <main className={`music-workspace${mobile ? " mobile" : ""}`} aria-label="Music video song workspace">
      <header className="music-workspace-header">
        <div>
          <h1>Music</h1>
          <p>Shape the song into timed sections, then discuss a section with Director.</p>
        </div>
        {project.music_master ? <div className="music-source">
          <span>{project.music_master.filename}</span>
          <audio controls preload="metadata" src={projectId ? songAudioUrl(projectId) : undefined} aria-label="Original song" />
        </div> : <p className="music-source-empty">Import the original song in Assets → Music first.</p>}
      </header>

      {stale ? <div className="banner" role="alert">The song has changed. Check the times and save these segments against the new song.</div> : null}
      {error ? <div className="banner" role="alert">{error}</div> : null}

      <div className="music-workspace-grid">
        <div className="music-left-column">
        <section className="music-import-panel" aria-label="Prepare song segments">
          <h2>Lyrics and timing</h2>
          <p>Paste notes, CSV, subtitles, or Whisper JSON. You can correct words and times before saving.</p>
          <textarea
            aria-label="Lyrics and time notes"
            placeholder="00:04.54–00:13.08  First line…"
            value={rawInput}
            disabled={saving}
            onChange={(event) => {
              previewVersion.current += 1;
              editVersion.current += 1;
              setRawInput(event.target.value);
              setRows([]);
              setUnresolved([]);
              setSelectedIds([]);
              setDirty(true);
              setBusy(false);
            }}
          />
          <div className="music-import-actions">
            <label className="btn secondary music-text-file">
              <input type="file" accept=".txt,.md,.csv,.tsv,.json,.lrc,.srt,.vtt,text/*,application/json" disabled={saving} onChange={(event) => void onTextFile(event)} aria-label="Import song notes file" />
              Import text file
            </label>
            <button type="button" className="btn" disabled={busy || !rawInput.trim()} onClick={() => void prepare()}>Prepare segments</button>
          </div>
          {unresolved.length ? <div className="music-unresolved">
            <strong>Check before saving</strong>
            <ul>{unresolved.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
          </div> : null}
          {rows.length || document ? <div className="music-row-editor">
            <div className="music-row-heading"><h3>Review segments</h3><span>{rows.length} sections</span></div>
            {rows.map((row, index) => <div className="music-edit-row" key={row.id}>
              <span className="music-edit-index">{String(index + 1).padStart(2, "0")}</span>
              <div className="music-edit-fields">
                <div className="music-time-fields">
                  <label>Start (s) <input aria-label={`Segment ${index + 1} start`} type="number" min="0" step="0.01" value={row.start_s ?? ""} disabled={saving} onChange={(event) => updateRow(index, { start_s: event.target.value === "" ? null : Number(event.target.value) })} /></label>
                  <label>End (s) <input aria-label={`Segment ${index + 1} end`} type="number" min="0" step="0.01" value={row.end_s ?? ""} disabled={saving} onChange={(event) => updateRow(index, { end_s: event.target.value === "" ? null : Number(event.target.value) })} /></label>
                </div>
                <textarea aria-label={`Segment ${index + 1} lyrics`} value={row.text} disabled={saving} onChange={(event) => updateRow(index, { text: event.target.value })} />
              </div>
              <button type="button" className="music-remove-row" aria-label={`Remove segment ${index + 1}`} disabled={saving} onClick={() => removeRow(index)}>×</button>
            </div>)}
            <button type="button" className="btn secondary music-add-row" disabled={saving} onClick={addRow}>Add segment</button>
            <button type="button" className="btn music-save" disabled={busy || !project.music_master} onClick={() => void save()}>Save segments</button>
          </div> : null}
        </section>

        <section className="music-segment-panel" aria-label="Song segments">
          <div className="music-segment-heading"><h2>Song map</h2>{document ? <span>Revision {document.revision}</span> : null}</div>
          {!document ? <p className="music-empty">Prepare and save your lyric segments to build the song map.</p> : null}
          {document && !document.segments.length ? <p className="music-empty">No timed sections yet.</p> : null}
          {document?.segments.map((row, index) => (
            <button
              type="button" key={row.id}
              className={`music-segment${selectedIds.includes(row.id) ? " selected" : ""}`}
              disabled={dirty || stale}
              aria-pressed={selectedIds.includes(row.id)}
              onClick={() => selectRow(row.id)}
            >
              <span className="music-segment-number">{String(index + 1).padStart(2, "0")}</span>
              <span className="music-segment-content"><span>{row.text}</span><small>{timeLabel(row.start_s)} – {timeLabel(row.end_s)}</small></span>
            </button>
          ))}
          {dirty && document ? <p className="music-empty">Save your edits before selecting a section.</p> : null}
        </section>
        </div>

        <section className="music-discussion-panel" aria-label="Discuss song segment">
          <div className="music-discussion-heading">
            <h2>Discuss with Director</h2>
            <p>{selection ? `${selection.ids.length} selected section${selection.ids.length === 1 ? "" : "s"} will accompany your message.` : "Select a saved section from the song map."}</p>
          </div>
          <DirectorPage chatOnly mobile={mobile} segmentSelection={selection} />
        </section>
      </div>
    </main>
  );
}
