import { useEffect, useState } from "react";
import { Plus, Trash2, X } from "lucide-react";
import type { RefRole, Shot } from "../../shared/api/types";
import { Badge, Banner, Button, Textarea, cn } from "../../shared/ui";
import {
  listLibraryAssets,
  type LibraryAsset,
  type LibraryKind,
} from "../library/api";
import { replaceShotMaterials, type ShotMaterialSelection } from "./api";
import { VideoDependencyIndicator } from "./VideoDependencyIndicator";

type PictureKind = Exclude<LibraryKind, "voices">;

const PICTURE_KINDS: PictureKind[] = ["actors", "scenes", "costumes", "props", "layouts"];
const KIND_LABELS: Record<PictureKind, string> = {
  actors: "Actors",
  scenes: "Scenes",
  costumes: "Costumes",
  props: "Props",
  layouts: "Layouts",
};

const ROLE_BY_KIND: Record<LibraryKind, RefRole | null> = {
  actors: "actor",
  scenes: "scene",
  costumes: "costume",
  props: "prop",
  layouts: "layout_ref_frame",
  voices: null,
};

function preferredFileKey(asset: LibraryAsset): string | null {
  const preferred =
    asset.kind === "layouts"
      ? ["layout", "master"]
      : ["master", "fullbody_threeview", "bust_threeview", "input_scene", "wide"];
  return (
    preferred.find((key) => asset.files[key]) ||
    Object.keys(asset.files).find((key) => asset.files[key]) ||
    null
  );
}

function materialKey(material: ShotMaterialSelection): string {
  return `${material.role}:${material.asset_id}:${material.file_key || ""}`;
}

function fileVariants(asset: LibraryAsset): { fileKey: string; preview: string }[] {
  const available = Object.keys(asset.files)
    .filter((fileKey) => Boolean(asset.files[fileKey] && asset.urls[fileKey]))
    .map((fileKey) => ({ fileKey, preview: asset.urls[fileKey] }));
  if (asset.kind !== "layouts") return available;
  const preferred = preferredFileKey(asset);
  return preferred ? available.filter((variant) => variant.fileKey === preferred) : [];
}

function normalizeMaterials(materials: ShotMaterialSelection[]): ShotMaterialSelection[] {
  return [
    ...materials.filter((material) => material.role !== "layout_ref_frame"),
    ...materials.filter((material) => material.role === "layout_ref_frame"),
  ];
}

export function ShotMaterialEditor({
  shot,
  shotNumber,
  shots,
  onClose,
  onOpenImage,
  onSaved,
}: {
  shot: Shot;
  shotNumber: number;
  shots?: Shot[];
  onClose: () => void;
  onOpenImage: (url: string) => void;
  onSaved?: (shot: Shot, message: string, notifyAgent: boolean) => void;
}) {
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [materials, setMaterials] = useState<ShotMaterialSelection[]>(() =>
    normalizeMaterials(
      [...shot.refs]
        .sort((a, b) => a.picture_index - b.picture_index)
        .map(({ role, asset_id, file_key }) => ({ role, asset_id, file_key })),
    ),
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState<LibraryKind | "all">("all");
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all(PICTURE_KINDS.map((kind) => listLibraryAssets(kind, shot.project_id)))
      .then((groups) => {
        if (active) setAssets(groups.flat());
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      active = false;
    };
  }, [shot.project_id]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose, saving]);

  const assetById = new Map(assets.map((asset) => [asset.id, asset]));
  const selectedKeys = new Set(materials.map(materialKey));
  const originalMaterials = normalizeMaterials(
    [...shot.refs]
      .sort((a, b) => a.picture_index - b.picture_index)
      .map(({ role, asset_id, file_key }) => ({ role, asset_id, file_key })),
  );
  const originalKeys = originalMaterials.map(materialKey);
  const currentKeys = materials.map(materialKey);
  const addedCount = currentKeys.filter((key) => !originalKeys.includes(key)).length;
  const removedCount = originalKeys.filter((key) => !currentKeys.includes(key)).length;
  const reorderedCount = currentKeys.filter(
    (key, index) => originalKeys.includes(key) && originalKeys.indexOf(key) !== index,
  ).length;
  const hasChanges = addedCount > 0 || removedCount > 0 || reorderedCount > 0;

  const remove = (index: number) => {
    setMaterials((current) => current.filter((_, candidate) => candidate !== index));
  };

  const add = (asset: LibraryAsset, fileKey: string) => {
    const role = ROLE_BY_KIND[asset.kind as LibraryKind];
    if (!role || materials.length >= 9) return;
    const next: ShotMaterialSelection = {
      role,
      asset_id: asset.id,
      file_key: fileKey,
    };
    if (selectedKeys.has(materialKey(next))) return;
    setMaterials((current) => normalizeMaterials([...current, next]));
  };

  const save = async (notifyAgent: boolean) => {
    setSaving(true);
    setError("");
    try {
      const updated = await replaceShotMaterials(shot.id, normalizeMaterials(materials));
      onSaved?.(updated, message.trim(), notifyAgent);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="shot-material-editor-backdrop fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3 backdrop-blur-[2px] sm:p-5"
      role="presentation"
    >
      <section
        className="shot-material-editor flex max-h-[calc(100dvh-24px)] w-[min(920px,100%)] flex-col overflow-hidden rounded-xl border border-border-subtle bg-panel text-ink shadow-[var(--shadow-popover)]"
        role="dialog"
        aria-modal="true"
        aria-label={`Edit Shot ${String(shotNumber).padStart(2, "0")} materials`}
      >
        <header className="shot-material-editor-header flex shrink-0 items-start justify-between gap-4 border-b border-border-subtle px-5 py-3.5">
          <div>
            <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-ink-muted">
              Shot {String(shotNumber).padStart(2, "0")}
            </span>
            <h2 className="mt-0.5 text-sm font-semibold tracking-[-0.01em] text-ink">
              {confirming ? "Review reference changes" : shot.title}
            </h2>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close material editor"
            onClick={onClose}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <VideoDependencyIndicator shot={shot} shotNumber={shotNumber} shots={shots} />

          {confirming ? (
            <div className="shot-material-save-review space-y-4">
              <div className="flex items-center justify-between gap-2 rounded-lg border border-border-subtle bg-elevated/50 px-3.5 py-2.5">
                <span className="text-xs font-medium text-ink-muted">Reference delta</span>
                <strong className="text-xs font-semibold text-ink">{shot.title}</strong>
              </div>
              <dl
                className="grid grid-cols-3 gap-2.5 rounded-lg border border-border-subtle bg-elevated/35 p-3 text-xs"
                aria-label="Reference change summary"
              >
                <div>
                  <dt className="text-[11px] text-ink-muted">Added</dt>
                  <dd className="mt-0.5 font-mono text-sm font-semibold text-ink tabular-nums">
                    {addedCount}
                  </dd>
                </div>
                <div>
                  <dt className="text-[11px] text-ink-muted">Removed</dt>
                  <dd className="mt-0.5 font-mono text-sm font-semibold text-ink tabular-nums">
                    {removedCount}
                  </dd>
                </div>
                <div>
                  <dt className="text-[11px] text-ink-muted">Reordered</dt>
                  <dd className="mt-0.5 font-mono text-sm font-semibold text-ink tabular-nums">
                    {reorderedCount}
                  </dd>
                </div>
              </dl>
              <label className="flex flex-col gap-1.5 text-xs font-medium text-ink-secondary">
                <span>Message to Agent (optional)</span>
                <Textarea
                  autoFocus
                  rows={5}
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="Describe what changed creatively, what must stay, or the next action you want."
                  disabled={saving}
                />
              </label>
              {error ? (
                <Banner tone="danger" className="banner error">
                  {error}
                </Banner>
              ) : null}
              <p className="text-xs leading-relaxed text-ink-muted">
                Agent will review every current Picture together with your message, then decide
                whether the brief or H3 prompt should change.
              </p>
            </div>
          ) : (
            <>
              <div className="shot-material-editor-toolbar flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle bg-elevated/45 px-3.5 py-2">
                <div className="shot-material-editor-count font-mono text-xs font-semibold text-ink tabular-nums">
                  Pictures {materials.length} / 9
                </div>
                <p className="text-xs text-ink-muted">
                  Layout uses the same Picture budget. Audio stays separate.
                </p>
              </div>

              <div
                className="shot-material-editor-selected grid min-h-0 grid-cols-1 gap-2 p-0 sm:grid-cols-2 lg:grid-cols-3"
                aria-label="Selected Picture materials"
              >
                {materials.map((material, index) => {
                  const asset = assetById.get(material.asset_id);
                  const name = asset?.name || material.asset_id;
                  const preview =
                    asset && material.file_key ? asset.urls[material.file_key] : null;
                  return (
                    <div
                      className="shot-material-selected-card flex items-center gap-2.5 rounded-lg border border-border-subtle bg-elevated/50 p-2"
                      key={materialKey(material)}
                    >
                      <div className="shot-material-picture-index inline-flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent-soft font-mono text-[11px] font-semibold text-accent">
                        P{index + 1}
                      </div>
                      {preview ? (
                        <button
                          type="button"
                          className="shot-material-preview-button shot-material-selected-preview h-10 w-10 shrink-0 cursor-pointer overflow-hidden rounded border border-border-subtle bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                          aria-label={`Open Picture ${index + 1} · ${name}`}
                          onClick={() => onOpenImage(preview)}
                        >
                          <img src={preview} alt="" className="h-full w-full object-cover" />
                        </button>
                      ) : (
                        <div className="shot-material-preview-empty h-10 w-10 shrink-0 rounded border border-dashed border-border-subtle bg-canvas" />
                      )}
                      <span className="min-w-0 flex-1">
                        <strong className="block truncate text-xs font-medium text-ink">
                          {name}
                        </strong>
                        <small className="block truncate text-[11px] text-ink-muted">
                          {material.role.replaceAll("_", " ")}
                          {material.file_key ? ` · ${material.file_key}` : ""}
                        </small>
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="shot-material-remove h-7 w-7 shrink-0 text-ink-muted hover:text-status-danger"
                        aria-label={`Remove Picture ${index + 1} · ${name}`}
                        onClick={() => remove(index)}
                        disabled={saving}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </Button>
                    </div>
                  );
                })}
                {materials.length === 0 ? (
                  <p className="empty-copy col-span-full rounded-lg border border-dashed border-border-subtle py-4 text-center text-xs text-ink-muted">
                    No Pictures selected for this Shot.
                  </p>
                ) : null}
              </div>

              <div
                className="shot-material-editor-library space-y-3 border-t border-border-subtle pt-4"
                aria-label="Project Library materials"
              >
                <div className="shot-material-library-heading flex items-center justify-between gap-2">
                  <div>
                    <span className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                      Project inventory
                    </span>
                    <h3 className="text-xs font-semibold text-ink">Library</h3>
                  </div>
                  {materials.length >= 9 ? (
                    <Badge tone="warn">Picture limit reached</Badge>
                  ) : null}
                </div>

                {error ? (
                  <Banner tone="danger" className="banner error">
                    {error}
                  </Banner>
                ) : null}
                {!error && assets.length === 0 ? (
                  <p className="empty-copy text-xs text-ink-muted">Loading project materials…</p>
                ) : null}

                <div
                  className="shot-material-library-filters flex flex-wrap items-center gap-1.5"
                  aria-label="Filter Library materials"
                >
                  <button
                    type="button"
                    aria-pressed={filter === "all"}
                    onClick={() => setFilter("all")}
                    className={cn(
                      "h-6 cursor-pointer rounded-md border px-2.5 text-[11px] font-medium transition-colors",
                      filter === "all"
                        ? "border-accent bg-accent-soft text-accent"
                        : "border-border-subtle bg-elevated text-ink-secondary hover:bg-hover hover:text-ink",
                    )}
                  >
                    All
                  </button>
                  {PICTURE_KINDS.map((kind) => (
                    <button
                      type="button"
                      key={kind}
                      aria-pressed={filter === kind}
                      onClick={() => setFilter(kind)}
                      className={cn(
                        "h-6 cursor-pointer rounded-md border px-2.5 text-[11px] font-medium transition-colors",
                        filter === kind
                          ? "border-accent bg-accent-soft text-accent"
                          : "border-border-subtle bg-elevated text-ink-secondary hover:bg-hover hover:text-ink",
                      )}
                    >
                      {KIND_LABELS[kind]}
                    </button>
                  ))}
                </div>

                <div className="shot-material-library-grid space-y-3">
                  {assets
                    .filter((asset) => filter === "all" || asset.kind === filter)
                    .map((asset) => {
                      const role = ROLE_BY_KIND[asset.kind as LibraryKind];
                      if (!role) return null;
                      const variants = fileVariants(asset);
                      if (!variants.length) return null;
                      return (
                        <section
                          className="shot-material-library-group space-y-2 rounded-lg border border-border-subtle bg-elevated/30 p-3"
                          key={asset.id}
                        >
                          <header className="flex items-center justify-between gap-2 text-xs">
                            <div className="flex min-w-0 items-center gap-2">
                              <Badge tone="neutral">
                                {asset.kind === "layouts" ? "Layout" : asset.kind.slice(0, -1)}
                              </Badge>
                              <strong className="truncate font-semibold text-ink">
                                {asset.name}
                              </strong>
                            </div>
                            <small className="shrink-0 font-mono text-[11px] text-ink-muted">
                              {variants.length} {variants.length === 1 ? "image" : "images"}
                            </small>
                          </header>
                          <div className="shot-material-library-variants grid grid-cols-1 gap-2 sm:grid-cols-2">
                            {variants.map(({ fileKey, preview }) => {
                              const key = materialKey({
                                role,
                                asset_id: asset.id,
                                file_key: fileKey,
                              });
                              const selected = selectedKeys.has(key);
                              const label =
                                variants.length > 1 ? `${asset.name} · ${fileKey}` : asset.name;
                              return (
                                <article
                                  className={cn(
                                    "flex items-center gap-2.5 rounded-md border bg-panel p-2 transition-colors",
                                    selected
                                      ? "selected border-accent/40 bg-accent-soft/40"
                                      : "border-border-subtle",
                                  )}
                                  key={fileKey}
                                >
                                  <button
                                    type="button"
                                    className="shot-material-preview-button shot-material-library-preview h-11 w-11 shrink-0 cursor-pointer overflow-hidden rounded border border-border-subtle bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                                    aria-label={`Open ${label} preview`}
                                    onClick={() => onOpenImage(preview)}
                                  >
                                    <img
                                      src={preview}
                                      alt=""
                                      className="h-full w-full object-cover"
                                    />
                                  </button>
                                  <div className="min-w-0 flex-1 text-xs">
                                    <span className="block truncate font-mono text-[10px] uppercase tracking-wider text-ink-muted">
                                      {fileKey.replaceAll("_", " ")}
                                    </span>
                                    <strong className="block truncate font-medium text-ink">
                                      {asset.name}
                                    </strong>
                                    <small className="block truncate text-[11px] text-ink-muted">
                                      {asset.notes || "No description"}
                                    </small>
                                  </div>
                                  <Button
                                    type="button"
                                    variant={selected ? "secondary" : "outline"}
                                    size="xs"
                                    className="shot-material-add shrink-0"
                                    aria-label={selected ? `${label} selected` : `Add ${label}`}
                                    disabled={selected || materials.length >= 9 || saving}
                                    onClick={() => add(asset, fileKey)}
                                  >
                                    {!selected ? (
                                      <Plus className="h-3 w-3" aria-hidden="true" />
                                    ) : null}
                                    {selected ? "Selected" : "Add"}
                                  </Button>
                                </article>
                              );
                            })}
                          </div>
                        </section>
                      );
                    })}
                </div>
              </div>
            </>
          )}
        </div>

        <footer className="shot-material-editor-actions flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border-subtle bg-elevated/45 px-5 py-3">
          {confirming ? (
            <>
              <span className="mr-auto text-xs text-ink-muted">
                Save directly, or notify Agent for a reference review.
              </span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setConfirming(false)}
                disabled={saving}
              >
                Back
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => void save(false)}
                disabled={saving}
              >
                {saving ? "Saving…" : "Save"}
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                className="shot-material-notify-button"
                onClick={() => void save(true)}
                disabled={saving}
              >
                {saving ? "Saving…" : "Save & send to Agent"}
              </Button>
            </>
          ) : (
            <>
              <span className="mr-auto text-xs text-ink-muted">
                Review the changes with Agent before they are saved.
              </span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={onClose}
                disabled={saving}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => setConfirming(true)}
                disabled={saving || !hasChanges}
              >
                Save changes
              </Button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}
