import { useEffect, useRef, useState } from "react";
import { describeUploadImage, importExternalAsset, type LibraryKind } from "./api";

const KIND_LABELS: Record<LibraryKind, string> = {
  actors: "Actors",
  scenes: "Scenes",
  props: "Props",
  costumes: "Costumes",
  layouts: "Layouts",
  voices: "Voices",
};

export function libraryKindLabel(kind: LibraryKind): string {
  return KIND_LABELS[kind];
}

export function AssetImportDialog({
  kind,
  projectId,
  onClose,
  onImported,
}: {
  kind: LibraryKind;
  projectId: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [reading, setReading] = useState(false);
  const [notice, setNotice] = useState("");
  const request = useRef<AbortController | null>(null);
  const notesRevision = useRef(0);
  const generated = useRef("");
  const filenameName = useRef("");
  const isVoice = kind === "voices";
  const label = libraryKindLabel(kind);
  const titleId = `asset-import-${kind}-title`;

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!selectedFile) { setPreview(""); return; }
    const url = URL.createObjectURL(selectedFile);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedFile]);

  const cancelReading = () => {
    request.current?.abort();
    request.current = null;
    setReading(false);
    setNotice("");
  };

  const readImage = async () => {
    if (!selectedFile || busy || reading || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const revision = notesRevision.current;
    const existingDescription = notes.trim();
    setReading(true);
    setError(null);
    setNotice("");
    try {
      const result = await describeUploadImage({
        file: selectedFile, name: name.trim() || filenameName.current,
        existingDescription, kind, projectId, signal: controller.signal,
      });
      if (request.current !== controller || controller.signal.aborted) return;
      if (notesRevision.current === revision) {
        setNotes(result.description);
        generated.current = result.description;
        setNotice(existingDescription ? "Description refined. Review and edit before importing."
          : "Description ready. Review and edit before importing.");
      } else {
        setNotice("Your description edits were kept.");
      }
    } catch (cause) {
      if (!controller.signal.aborted && request.current === controller) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (request.current === controller) { request.current = null; setReading(false); }
    }
  };

  const onImport = async (file: File | null) => {
    if (!file) return;
    if (isVoice && !name.trim()) {
      setError("Name is required for Voice assets.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await importExternalAsset({
        file,
        kind,
        name: name.trim() || undefined,
        notes: notes.trim() || undefined,
        projectId,
      });
      onImported();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="folder-modal asset-import-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={() => { if (!busy) onClose(); }}
    >
      <div className="folder-modal-panel asset-import-modal-panel" onClick={(event) => event.stopPropagation()}>
        <div className="folder-modal-head">
          <div>
            <span className="mobile-eyebrow">Add to project library</span>
            <h2 className="folder-modal-title" id={titleId}>Import {label}</h2>
          </div>
          <button type="button" className="btn secondary sm" onClick={onClose} disabled={busy}>
            Close import
          </button>
        </div>

        {error ? <div className="banner error">{error}</div> : null}
        <p className="field-hint">
          {isVoice
            ? "Upload a clean 2–15 second voice sample. Name and Description help the Director cast it."
            : "The magic wand uses this image, its name and your current description to draft or refine the text. Review before importing."}
        </p>
        <div className="import-form">
          <label className="field field-span-2">
            <span>Name</span>
            <input
              value={name}
              onChange={(event) => {
                cancelReading();
                setName(event.target.value);
              }}
              placeholder={isVoice ? "e.g. Speaker A" : "Asset name"}
              disabled={busy}
              required={isVoice}
            />
          </label>
          {!isVoice ? (
            <label className="field field-span-2">
              <span>Image file</span>
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  cancelReading();
                  setError(null);
                  setSelectedFile(file);
                  const stem = file.name.replace(/\.[^.]+$/, "");
                  if (!name.trim() || name === filenameName.current) setName(stem);
                  filenameName.current = stem;
                  if (notes === generated.current) { setNotes(""); notesRevision.current += 1; }
                  generated.current = "";
                }} />
            </label>
          ) : null}
          {preview ? <img className="asset-import-preview field-span-2" src={preview} alt="Selected reference" /> : null}
          <div className="field field-span-2">
            <div className="asset-description-heading">
              <label htmlFor={`${titleId}-description`}>Description</label>
              {!isVoice ? (
                <button type="button" className="btn secondary sm asset-description-wand"
                  aria-label="Describe selected image" aria-busy={reading}
                  title={notes.trim() ? "Refine using this image, its name and the current description" : "Describe this image using its name as guidance"}
                  disabled={busy || reading || !selectedFile}
                  onClick={() => void readImage()}>🪄{reading ? " Reading…" : ""}</button>
              ) : null}
            </div>
            <textarea
              id={`${titleId}-description`}
              value={notes}
              onChange={(event) => { notesRevision.current += 1; setNotes(event.target.value); setNotice(""); }}
              rows={isVoice ? 3 : 6}
              placeholder={isVoice ? "Neutral speech, language and delivery notes" : "Describe visible details, construction and any uncertainty"}
              disabled={busy}
            />
            {notice ? <span className="field-hint" role="status">{notice}</span> : null}
          </div>
          {isVoice ? <label className="field field-span-2">
            <span>Audio file</span>
            <input
              type="file"
              accept={isVoice
                ? "audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg"
                : "image/png,image/jpeg,image/webp,image/gif"}
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0] || null;
                event.target.value = "";
                void onImport(file);
              }}
            />
          </label> : null}
        </div>
        {!isVoice ? <footer className="asset-metadata-actions">
          <button type="button" className="btn primary" disabled={busy || reading || !selectedFile}
            onClick={() => void onImport(selectedFile)}>{busy ? "Importing…" : "Import image"}</button>
        </footer> : null}
      </div>
    </div>
  );
}
