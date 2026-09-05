import { useEffect, useState } from "react";
import type { RefRole, Shot } from "../../shared/api/types";
import {
  listLibraryAssets,
  type LibraryAsset,
  type LibraryKind,
} from "../library/api";
import { replaceShotMaterials, type ShotMaterialSelection } from "./api";

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
  const preferred = asset.kind === "layouts"
    ? ["layout", "master"]
    : ["master", "fullbody_threeview", "bust_threeview", "input_scene", "wide"];
  return preferred.find((key) => asset.files[key])
    || Object.keys(asset.files).find((key) => asset.files[key])
    || null;
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

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" />
    </svg>
  );
}

export function ShotMaterialEditor({
  shot,
  shotNumber,
  onClose,
  onOpenImage,
  onSaved,
}: {
  shot: Shot;
  shotNumber: number;
  onClose: () => void;
  onOpenImage: (url: string) => void;
  onSaved?: (shot: Shot) => void;
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

  useEffect(() => {
    let active = true;
    Promise.all(
      PICTURE_KINDS.map((kind) => listLibraryAssets(kind, shot.project_id)),
    )
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
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const updated = await replaceShotMaterials(shot.id, normalizeMaterials(materials));
      onSaved?.(updated);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="shot-material-editor-backdrop" role="presentation">
      <section
        className="shot-material-editor"
        role="dialog"
        aria-modal="true"
        aria-label={`Edit Shot ${String(shotNumber).padStart(2, "0")} materials`}
      >
        <header className="shot-material-editor-header">
          <div>
            <span>Shot {String(shotNumber).padStart(2, "0")}</span>
            <h2>{shot.title}</h2>
          </div>
          <button type="button" aria-label="Close material editor" onClick={onClose}>×</button>
        </header>
        <div className="shot-material-editor-toolbar">
          <div className="shot-material-editor-count">Pictures {materials.length} / 9</div>
          <p>Layout uses the same Picture budget. Audio stays separate.</p>
        </div>
        <div className="shot-material-editor-selected" aria-label="Selected Picture materials">
          {materials.map((material, index) => {
            const asset = assetById.get(material.asset_id);
            const name = asset?.name || material.asset_id;
            const preview = asset && material.file_key ? asset.urls[material.file_key] : null;
            return (
              <div className="shot-material-selected-card" key={materialKey(material)}>
                <div className="shot-material-picture-index">P{index + 1}</div>
                {preview ? (
                  <button
                    type="button"
                    className="shot-material-preview-button shot-material-selected-preview"
                    aria-label={`Open Picture ${index + 1} · ${name}`}
                    onClick={() => onOpenImage(preview)}
                  >
                    <img src={preview} alt="" />
                  </button>
                ) : <div className="shot-material-preview-empty" />}
                <span>
                  <strong>{name}</strong>
                  <small>
                    {material.role.replaceAll("_", " ")}
                    {material.file_key ? ` · ${material.file_key}` : ""}
                  </small>
                </span>
                <button
                  type="button"
                  className="shot-material-remove"
                  aria-label={`Remove Picture ${index + 1} · ${name}`}
                  onClick={() => remove(index)}
                  disabled={saving}
                >
                  <TrashIcon />
                </button>
              </div>
            );
          })}
          {materials.length === 0 ? <p className="empty-copy">No Pictures selected for this Shot.</p> : null}
        </div>
        <div className="shot-material-editor-library" aria-label="Project Library materials">
          <div className="shot-material-library-heading">
            <div><span>Project inventory</span><h3>Library</h3></div>
            {materials.length >= 9 ? <strong>Picture limit reached</strong> : null}
          </div>
          {error ? <div className="banner error">{error}</div> : null}
          {!error && assets.length === 0 ? <p className="empty-copy">Loading project materials…</p> : null}
          <div className="shot-material-library-filters" aria-label="Filter Library materials">
            <button
              type="button"
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              All
            </button>
            {PICTURE_KINDS.map((kind) => (
              <button
                type="button"
                key={kind}
                aria-pressed={filter === kind}
                onClick={() => setFilter(kind)}
              >
                {KIND_LABELS[kind]}
              </button>
            ))}
          </div>
          <div className="shot-material-library-grid">
            {assets.filter((asset) => filter === "all" || asset.kind === filter).map((asset) => {
              const role = ROLE_BY_KIND[asset.kind as LibraryKind];
              if (!role) return null;
              const variants = fileVariants(asset);
              if (!variants.length) return null;
              return (
                <section className="shot-material-library-group" key={asset.id}>
                  <header>
                    <span>{asset.kind === "layouts" ? "Layout" : asset.kind.slice(0, -1)}</span>
                    <strong>{asset.name}</strong>
                    <small>{variants.length} {variants.length === 1 ? "image" : "images"}</small>
                  </header>
                  <div className="shot-material-library-variants">
                    {variants.map(({ fileKey, preview }) => {
                      const key = materialKey({ role, asset_id: asset.id, file_key: fileKey });
                      const selected = selectedKeys.has(key);
                      const label = variants.length > 1 ? `${asset.name} · ${fileKey}` : asset.name;
                      return (
                        <article className={selected ? "selected" : ""} key={fileKey}>
                          <button
                            type="button"
                            className="shot-material-preview-button shot-material-library-preview"
                            aria-label={`Open ${label} preview`}
                            onClick={() => onOpenImage(preview)}
                          >
                            <img src={preview} alt="" />
                          </button>
                          <div>
                            <span>{fileKey.replaceAll("_", " ")}</span>
                            <strong>{asset.name}</strong>
                            <small>{asset.notes || "No description"}</small>
                          </div>
                          <button
                            type="button"
                            className="shot-material-add"
                            aria-label={selected ? `${label} selected` : `Add ${label}`}
                            disabled={selected || materials.length >= 9 || saving}
                            onClick={() => add(asset, fileKey)}
                          >
                            {selected ? "Selected" : "Add"}
                          </button>
                        </article>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
        <footer className="shot-material-editor-actions">
          <span>Saving changes opens Director to review the selected Pictures.</span>
          <button type="button" className="btn secondary" onClick={onClose} disabled={saving}>Cancel</button>
          <button type="button" className="btn primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </footer>
      </section>
    </div>
  );
}
