import { useEffect, useRef, useState } from "react";
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
  const descId = `asset-import-${kind}-desc`;

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!selectedFile) {
      setPreview("");
      return;
    }
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
        file: selectedFile,
        name: name.trim() || filenameName.current,
        existingDescription,
        kind,
        projectId,
        signal: controller.signal,
      });
      if (request.current !== controller || controller.signal.aborted) return;
      if (notesRevision.current === revision) {
        setNotes(result.description);
        generated.current = result.description;
        setNotice(
          existingDescription
            ? "Description refined. Review and edit before importing."
            : "Description ready. Review and edit before importing.",
        );
      } else {
        setNotice("Your description edits were kept.");
      }
    } catch (cause) {
      if (!controller.signal.aborted && request.current === controller) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        setReading(false);
      }
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
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent
        hideClose
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="folder-modal-panel asset-import-modal-panel w-[min(540px,calc(100vw-24px))] overflow-y-auto"
      >
        <DialogHeader className="flex-row items-start justify-between gap-3 pr-0">
          <div className="min-w-0">
            <span className="mobile-eyebrow block text-[11px] font-medium uppercase tracking-wider text-ink-muted">
              Add to project library
            </span>
            <DialogTitle id={titleId} className="folder-modal-title mt-0.5">
              Import {label}
            </DialogTitle>
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>
            Close import
          </Button>
        </DialogHeader>

        <DialogDescription id={descId} className="mt-2">
          {isVoice
            ? "Upload a clean 2–15 second voice sample. Name and Description help the Director cast it."
            : "The magic wand uses this image, its name and your current description to draft or refine the text. Review before importing."}
        </DialogDescription>

        {error ? (
          <Banner tone="danger" role="alert" className="mt-3">
            {error}
          </Banner>
        ) : null}

        <div className="import-form mt-4 flex flex-col gap-3.5">
          <label className="field flex flex-col gap-1.5">
            <span className="text-xs font-medium text-ink-secondary">Name</span>
            <Input
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
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Image file</span>
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                disabled={busy}
                className="block w-full cursor-pointer rounded-md border border-border-subtle bg-elevated/40 px-2.5 py-1.5 text-xs text-ink file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-panel file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-ink hover:border-border-strong"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  cancelReading();
                  setError(null);
                  setSelectedFile(file);
                  const stem = file.name.replace(/\.[^.]+$/, "");
                  if (!name.trim() || name === filenameName.current) setName(stem);
                  filenameName.current = stem;
                  if (notes === generated.current) {
                    setNotes("");
                    notesRevision.current += 1;
                  }
                  generated.current = "";
                }}
              />
            </label>
          ) : null}

          {preview ? (
            <div className="flex items-center justify-center overflow-hidden rounded-lg border border-border-subtle bg-canvas/70 p-2">
              <img
                className="asset-import-preview max-h-44 max-w-full rounded object-contain"
                src={preview}
                alt="Selected reference"
              />
            </div>
          ) : null}

          <div className="field flex flex-col gap-1.5">
            <div className="asset-description-heading flex items-center justify-between gap-2">
              <label
                htmlFor={`${titleId}-description`}
                className="text-xs font-medium text-ink-secondary"
              >
                Description
              </label>
              {!isVoice ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  className="asset-description-wand"
                  aria-label="Describe selected image"
                  aria-busy={reading}
                  title={
                    notes.trim()
                      ? "Refine using this image, its name and the current description"
                      : "Describe this image using its name as guidance"
                  }
                  disabled={busy || reading || !selectedFile}
                  onClick={() => void readImage()}
                >
                  🪄{reading ? " Reading…" : ""}
                </Button>
              ) : null}
            </div>
            <Textarea
              id={`${titleId}-description`}
              value={notes}
              onChange={(event) => {
                notesRevision.current += 1;
                setNotes(event.target.value);
                setNotice("");
              }}
              rows={isVoice ? 3 : 5}
              placeholder={
                isVoice
                  ? "Neutral speech, language and delivery notes"
                  : "Describe visible details, construction and any uncertainty"
              }
              disabled={busy}
            />
            {notice ? (
              <span className="field-hint text-[11px] text-ink-muted" role="status">
                {notice}
              </span>
            ) : null}
          </div>

          {isVoice ? (
            <label className="field flex flex-col gap-1.5">
              <span className="text-xs font-medium text-ink-secondary">Audio file</span>
              <input
                type="file"
                accept="audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg"
                disabled={busy}
                className="block w-full cursor-pointer rounded-md border border-border-subtle bg-elevated/40 px-2.5 py-1.5 text-xs text-ink file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-panel file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-ink hover:border-border-strong"
                onChange={(event) => {
                  const file = event.target.files?.[0] || null;
                  event.target.value = "";
                  void onImport(file);
                }}
              />
            </label>
          ) : null}
        </div>

        {!isVoice ? (
          <DialogFooter className="asset-metadata-actions">
            <Button
              type="button"
              variant="primary"
              disabled={busy || reading || !selectedFile}
              onClick={() => void onImport(selectedFile)}
            >
              {busy ? "Importing…" : "Import image"}
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
