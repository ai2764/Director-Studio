import { useEffect, useState } from "react";
import { useProject } from "../../shared/project/ProjectContext";
import {
  deleteLibraryAsset,
  listLibraryAssets,
  type LibraryAsset,
  type LibraryKind,
} from "../library/api";
import { AssetImportDialog } from "../library/AssetImportDialog";
import { AssetDetailDialog, assetPreviewUrl } from "../library/AssetDetailDialog";
import { AssetMetadataDialog } from "../library/AssetMetadataDialog";

type VisibleLibraryKind = Exclude<LibraryKind, "layouts" | "costumes">;

const GROUPS: { id: VisibleLibraryKind; label: string; empty: string }[] = [
  { id: "actors", label: "Actors", empty: "No cast prepared" },
  { id: "scenes", label: "Scenes", empty: "No locations prepared" },
  { id: "props", label: "Props", empty: "No story objects prepared" },
  { id: "voices", label: "Voices", empty: "No voices prepared" },
];

export function LibraryOverview({ onSelectKind }: {
  onSelectKind: (kind: VisibleLibraryKind) => void;
}) {
  const { projectId } = useProject();
  const [groups, setGroups] = useState<Record<string, LibraryAsset[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [importKind, setImportKind] = useState<VisibleLibraryKind | null>(null);
  const [detailAsset, setDetailAsset] = useState<LibraryAsset | null>(null);
  const [editingAsset, setEditingAsset] = useState<LibraryAsset | null>(null);

  const onMetadataSaved = (updated: LibraryAsset) => {
    const kind = updated.kind as VisibleLibraryKind;
    setGroups((current) => ({
      ...current,
      [kind]: (current[kind] || []).map((asset) => asset.id === updated.id ? updated : asset),
    }));
    setDetailAsset((current) => current?.id === updated.id ? updated : current);
    setEditingAsset(null);
  };

  useEffect(() => {
    let active = true;
    setError(null);
    if (!projectId) {
      setGroups({});
      return () => { active = false; };
    }
    Promise.all(GROUPS.map(async (group) => [group.id, await listLibraryAssets(group.id, projectId)] as const))
      .then((entries) => {
        if (active) setGroups(Object.fromEntries(entries));
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => { active = false; };
  }, [projectId]);

  const onDeleteAsset = async (asset: LibraryAsset) => {
    const fileCount = Object.values(asset.urls || {}).filter(Boolean).length;
    const confirmed = window.confirm(
      `Delete “${asset.name}” and all ${fileCount || "its"} file(s)?\n\nThis cannot be undone.`,
    );
    if (!confirmed || !projectId) return;

    const kind = asset.kind as VisibleLibraryKind;
    setBusy(true);
    setError(null);
    try {
      await deleteLibraryAsset(kind, asset.id);
      setDetailAsset(null);
      const assets = await listLibraryAssets(kind, projectId);
      setGroups((current) => ({ ...current, [kind]: assets }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="library-overview mobile-library-overview" aria-labelledby="library-overview-title">
      <header>
        <div>
          <span className="mobile-eyebrow">Reusable across every Shot</span>
          <h2 id="library-overview-title">Project library</h2>
        </div>
        <span className="mobile-library-total">
          {Object.values(groups).reduce((total, assets) => total + assets.length, 0)} assets
        </span>
      </header>
      {error ? <div className="banner error">{error}</div> : null}
      {!projectId ? <p className="empty-copy">Select a project to browse its library.</p> : null}

      <div className="mobile-library-groups">
        {GROUPS.map((group) => {
          const assets = groups[group.id] || [];
          return (
            <section key={group.id} className="mobile-library-group">
              <header className="mobile-library-group-heading">
                <button className="mobile-library-group-main" type="button" onClick={() => onSelectKind(group.id)} aria-label={`View ${group.label}`}>
                  <strong>{group.label}</strong>
                  <span>{assets.length} · Prepare →</span>
                </button>
                <button
                  type="button"
                  className="asset-import-button"
                  aria-label={`Import ${group.label}`}
                  title={`Import ${group.label}`}
                  disabled={!projectId}
                  onClick={() => setImportKind(group.id)}
                >
                  +
                </button>
              </header>
              {assets.length ? (
                <div className="mobile-library-preview-strip">
                  {assets.slice(0, 6).map((asset) => {
                    const preview = assetPreviewUrl(asset);
                    return (
                      <button
                        key={asset.id}
                        type="button"
                        className="mobile-library-preview-card"
                        aria-label={`View ${asset.name} asset set`}
                        onClick={() => setDetailAsset(asset)}
                      >
                        <div className={`mobile-library-preview${preview ? "" : " empty"}`}>
                          {preview ? <img src={preview} alt="" /> : <span>{group.id === "voices" ? "VOICE" : "ASSET"}</span>}
                        </div>
                        <strong>{asset.name}</strong>
                      </button>
                    );
                  })}
                </div>
              ) : <p>{group.empty}</p>}
            </section>
          );
        })}
      </div>
      {importKind && projectId ? (
        <AssetImportDialog
          kind={importKind}
          projectId={projectId}
          onClose={() => setImportKind(null)}
          onImported={() => {
            setImportKind(null);
            listLibraryAssets(importKind, projectId)
              .then((assets) => setGroups((current) => ({ ...current, [importKind]: assets })))
              .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
          }}
        />
      ) : null}
      {detailAsset ? (
        <AssetDetailDialog
          asset={detailAsset}
          busy={busy}
          onClose={() => setDetailAsset(null)}
          onEdit={() => setEditingAsset(detailAsset)}
          onDelete={() => void onDeleteAsset(detailAsset)}
        />
      ) : null}
      {editingAsset ? (
        <AssetMetadataDialog
          asset={editingAsset}
          onClose={() => setEditingAsset(null)}
          onSaved={onMetadataSaved}
        />
      ) : null}
    </section>
  );
}
