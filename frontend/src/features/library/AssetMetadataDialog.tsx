import { useState } from "react";
import { X } from "lucide-react";
import { useProject } from "../../shared/project/ProjectContext";
import {
  Banner,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Textarea,
} from "../../shared/ui";
import type { LibraryAsset } from "./api";
import { updateLibraryAsset } from "./api";
import { LibraryDescriptionWand } from "./LibraryDescriptionWand";

export function AssetMetadataDialog({
  asset,
  onClose,
  onSaved,
}: {
  asset: LibraryAsset;
  onClose: () => void;
  onSaved: (asset: LibraryAsset) => void;
}) {
  const { projectId } = useProject();
  const [name, setName] = useState(asset.name);
  const [notes, setNotes] = useState(asset.notes || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const titleId = `asset-metadata-title-${asset.id}`;
  const descId = `asset-metadata-desc-${asset.id}`;

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
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <DialogContent
        hideClose
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="folder-modal-panel asset-metadata-panel w-[min(520px,calc(100vw-24px))] overflow-y-auto"
      >
        <DialogTitle id={titleId} className="sr-only">
          Edit {asset.name} metadata
        </DialogTitle>
        <DialogDescription id={descId} className="sr-only">
          Edit asset name and description for library reuse.
        </DialogDescription>

        <DialogHeader className="asset-metadata-heading flex-row items-start justify-between gap-3 pr-0">
          <div>
            <span className="block text-[11px] font-medium uppercase tracking-wider text-ink-muted">
              Library metadata
            </span>
            <h2 className="mt-0.5 text-sm font-semibold tracking-[-0.01em] text-ink">
              Edit asset
            </h2>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close metadata editor"
            disabled={saving}
            onClick={onClose}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </DialogHeader>

        <div className="asset-metadata-fields mt-4 flex flex-col gap-3.5">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-secondary">Name</span>
            <Input
              aria-label="Name"
              value={name}
              required
              autoFocus
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-secondary">Description</span>
            <Textarea
              aria-label="Description"
              value={notes}
              rows={5}
              placeholder="Identity, continuity, wardrobe, location, prop, or voice guidance"
              onChange={(event) => setNotes(event.target.value)}
            />
          </label>
          <LibraryDescriptionWand
            asset={asset}
            name={name}
            description={notes}
            projectId={asset.project_id || projectId}
            disabled={saving}
            onDescription={setNotes}
          />
        </div>

        <p className="asset-metadata-impact mt-3 rounded-md border border-border-subtle bg-elevated/40 px-3 py-2 text-[11px] text-ink-muted">
          Shots using this asset will need their H3 prompt reviewed again.
        </p>

        {error ? (
          <Banner tone="danger" role="alert" className="mt-3">
            {error}
          </Banner>
        ) : null}

        <DialogFooter className="asset-metadata-actions">
          <Button type="button" variant="secondary" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="primary"
            disabled={saving || !name.trim()}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
