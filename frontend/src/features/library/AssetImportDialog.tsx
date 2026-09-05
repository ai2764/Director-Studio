import { useState } from "react";
import { importExternalAsset, type LibraryKind } from "./api";

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
  const isVoice = kind === "voices";
  const label = libraryKindLabel(kind);
  const titleId = `asset-import-${kind}-title`;

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
      onClick={onClose}
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
            : "Choose a clean reference file. A clear name and short notes help the Director assign it."}
        </p>
        <div className="import-form">
          <label className="field">
            <span>Name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={isVoice ? "e.g. Mia" : "Asset name"}
              disabled={busy}
              required={isVoice}
            />
          </label>
          <label className="field field-span-2">
            <span>{isVoice ? "Description" : "Notes"}</span>
            <input
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder={isVoice ? "Warm · intimate calm delivery" : "Continuity or usage notes"}
              disabled={busy}
            />
          </label>
          <label className="field field-span-2">
            <span>{isVoice ? "Audio file" : "Image file"}</span>
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
          </label>
        </div>
      </div>
    </div>
  );
}
