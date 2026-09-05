import { useState } from "react";
import type { LibraryAsset } from "./api";
import { updateLibraryAsset } from "./api";

export function AssetMetadataDialog({
  asset,
  onClose,
  onSaved,
}: {
  asset: LibraryAsset;
  onClose: () => void;
  onSaved: (asset: LibraryAsset) => void;
}) {
  const [name, setName] = useState(asset.name);
  const [notes, setNotes] = useState(asset.notes || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError("");
    try {
      const updated = await updateLibraryAsset(asset.kind, asset.id, {
        name: name.trim(),
        notes: notes.trim(),
      });
      onSaved(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="folder-modal asset-metadata-modal"
      role="dialog"
      aria-modal="true"
      aria-label={`Edit ${asset.name} metadata`}
      onClick={() => { if (!saving) onClose(); }}
    >
      <section
        className="folder-modal-panel asset-metadata-panel"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="asset-metadata-heading">
          <div>
            <span>Library metadata</span>
            <h2>Edit asset</h2>
          </div>
          <button type="button" aria-label="Close metadata editor" disabled={saving} onClick={onClose}>×</button>
        </header>
        <div className="asset-metadata-fields">
          <label>
            <span>Name</span>
            <input
              aria-label="Name"
              value={name}
              required
              autoFocus
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            <span>Notes</span>
            <textarea
              aria-label="Notes"
              value={notes}
              rows={5}
              placeholder="Identity, continuity, wardrobe, location, prop, or voice guidance"
              onChange={(event) => setNotes(event.target.value)}
            />
          </label>
        </div>
        <p className="asset-metadata-impact">
          Shots using this asset will need their H3 prompt reviewed again.
        </p>
        {error ? <div className="banner error">{error}</div> : null}
        <footer className="asset-metadata-actions">
          <button type="button" className="btn secondary" disabled={saving} onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn primary"
            disabled={saving || !name.trim()}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </footer>
      </section>
    </div>
  );
}
