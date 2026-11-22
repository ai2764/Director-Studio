import { useEffect, useMemo, useState } from "react";
import type { OutputSlot } from "../../shared/api/types";
import { Lightbox } from "../../shared/components/Lightbox";
import { Button, cn } from "../../shared/ui";
import type { LibraryAsset } from "./api";

const PREVIEW_KEYS = [
  "layout",
  "fullbody_threeview",
  "master",
  "asset_sheet",
  "bust_threeview",
  "wardrobe_ref",
  "image",
  "plate",
  "video",
] as const;

const KEY_LABELS: Record<string, string> = {
  master: "Master",
  fullbody_threeview: "Full-body three-view",
  bust_threeview: "Bust three-view",
  asset_sheet: "Asset sheet",
  wardrobe_ref: "Wardrobe ref",
  layout: "Layout",
  image: "Image",
  plate: "Plate",
  video: "Video",
  input_actor: "Input · actor",
  input_wardrobe: "Input · wardrobe",
  input_scene: "Input · scene",
};

function labelForKey(key: string): string {
  if (KEY_LABELS[key]) return KEY_LABELS[key];
  if (key.startsWith("input_")) return `Input · ${key.slice(6)}`;
  return key.replace(/_/g, " ");
}

export function assetPreviewUrl(asset: LibraryAsset): string | null {
  if (asset.kind === "voices") return null;
  const urls = asset.urls || {};
  for (const key of PREVIEW_KEYS) {
    if (urls[key]) return urls[key];
  }
  return Object.values(urls).find(Boolean) || null;
}

export function assetSlots(asset: LibraryAsset): OutputSlot[] {
  const urls = asset.urls || {};
  const files = asset.files || {};
  const keys = new Set([...Object.keys(urls), ...Object.keys(files)]);
  const ordered = [
    ...PREVIEW_KEYS.filter((key) => keys.has(key)),
    ...[...keys]
      .filter((key) => !(PREVIEW_KEYS as readonly string[]).includes(key))
      .sort(),
  ];
  return ordered
    .filter((key) => urls[key])
    .map((key) => ({
      key,
      label: labelForKey(key),
      filename: files[key] || null,
      url: urls[key],
    }));
}

export function AssetDetailDialog({
  asset,
  busy = false,
  onClose,
  onDelete,
  onEdit,
}: {
  asset: LibraryAsset;
  busy?: boolean;
  onClose: () => void;
  onDelete?: () => void;
  onEdit?: () => void;
}) {
  const slots = useMemo(() => assetSlots(asset), [asset]);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && lightboxIndex == null) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [lightboxIndex, onClose]);

  return (
    <>
      <div
        className="folder-modal asset-detail-modal fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-[2px]"
        role="dialog"
        aria-modal="true"
        aria-label={`${asset.name} assets`}
        onClick={onClose}
      >
        <div
          className="folder-modal-panel flex max-h-[calc(100dvh-32px)] w-[min(880px,calc(100vw-24px))] flex-col overflow-hidden rounded-xl border border-border-subtle bg-panel text-ink shadow-[var(--shadow-popover)]"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="folder-modal-head flex flex-wrap items-start justify-between gap-4 border-b border-border-subtle px-5 py-4">
            <div className="min-w-0 flex-1">
              <h2 className="folder-modal-title truncate text-sm font-semibold tracking-[-0.01em] text-ink">
                {asset.name}
              </h2>
              <p className="mt-0.5 font-mono text-[11px] text-ink-muted">
                {asset.kind} · {asset.pipeline_id || "asset"}
                {asset.job_id ? ` · ${asset.job_id}` : ""}
                {asset.seed != null ? ` · seed ${asset.seed}` : ""}
              </p>
            </div>
            <div className="folder-modal-actions flex shrink-0 flex-wrap items-center gap-2">
              {onEdit ? (
                <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onEdit}>
                  Edit
                </Button>
              ) : null}
              {onDelete ? (
                <Button type="button" variant="danger" size="sm" disabled={busy} onClick={onDelete}>
                  Delete
                </Button>
              ) : null}
              <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>

          <div
            className={cn(
              "asset-detail-content grid min-h-0 flex-1 gap-5 overflow-y-auto p-5",
              asset.notes && "has-description md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]",
            )}
          >
            {slots.length === 0 ? (
              <p className="empty-copy text-xs text-ink-muted">No files in this asset.</p>
            ) : (
              <div className="folder-file-grid grid min-w-0 grid-cols-[repeat(auto-fit,minmax(min(100%,180px),1fr))] gap-3 self-start">
                {slots.map((slot, index) =>
                  asset.kind === "voices" ? (
                    <div
                      key={slot.key}
                      className="folder-file-card voice-file-card flex flex-col gap-2 rounded-lg border border-border-subtle bg-elevated/40 p-3"
                    >
                      <div className="folder-file-label text-xs font-medium text-ink">
                        {slot.label}
                      </div>
                      <audio
                        controls
                        preload="metadata"
                        src={slot.url || undefined}
                        className="h-8 w-full"
                      />
                      {slot.filename ? (
                        <div className="truncate font-mono text-[11px] text-ink-muted">
                          {slot.filename}
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <button
                      key={slot.key}
                      type="button"
                      className="folder-file-card group flex cursor-pointer flex-col overflow-hidden rounded-lg border border-border-subtle bg-elevated/40 text-left transition-colors hover:border-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      onClick={() => setLightboxIndex(index)}
                    >
                      <div className="folder-file-thumb flex aspect-square w-full items-center justify-center bg-canvas/60 p-2">
                        {slot.url ? (
                          <img
                            src={slot.url}
                            alt={slot.label}
                            className="h-full w-full object-contain transition-transform duration-150 group-hover:scale-[1.02]"
                          />
                        ) : (
                          <div className="output-empty text-xs text-ink-muted">—</div>
                        )}
                      </div>
                      <div className="border-t border-border-subtle px-2.5 py-2">
                        <div className="folder-file-label truncate text-xs font-medium text-ink">
                          {slot.label}
                        </div>
                        {slot.filename ? (
                          <div className="mt-0.5 truncate font-mono text-[10px] text-ink-muted">
                            {slot.filename}
                          </div>
                        ) : null}
                      </div>
                    </button>
                  ),
                )}
              </div>
            )}

            {asset.notes ? (
              <section
                className="asset-detail-description min-w-0 rounded-lg border border-border-subtle bg-elevated/30 p-4"
                aria-label="Asset description"
              >
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
                  Description
                </h3>
                <p className="folder-modal-notes whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink">
                  {asset.notes}
                </p>
              </section>
            ) : null}
          </div>
        </div>
      </div>

      {lightboxIndex != null ? (
        <Lightbox
          slots={slots}
          index={lightboxIndex}
          onClose={() => setLightboxIndex(null)}
          onIndex={setLightboxIndex}
        />
      ) : null}
    </>
  );
}
