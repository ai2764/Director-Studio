import { useCallback, useEffect, useState } from "react";
import { PageShell } from "../../shared/components/PageShell";
import { useProject } from "../../shared/project/ProjectContext";
import {
  deleteLibraryAsset,
  listLibraryAssets,
  type LibraryAsset,
  type LibraryKind,
} from "./api";
import { AssetImportDialog, libraryKindLabel } from "./AssetImportDialog";
import { AssetDetailDialog, assetPreviewUrl } from "./AssetDetailDialog";
import { AssetMetadataDialog } from "./AssetMetadataDialog";
import { StudioIcon } from "../../shared/components/StudioIcon";
import "./libraryDesign.css";

const KINDS: { id: LibraryKind; label: string }[] = [
  { id: "actors", label: "Actors" },
  { id: "scenes", label: "Scenes" },
  { id: "props", label: "Props" },
  { id: "costumes", label: "Wardrobe" },
  { id: "layouts", label: "Layouts" },
  { id: "voices", label: "Voices" },
];

function previewUrl(a: LibraryAsset): string | null {
  return assetPreviewUrl(a);
}

export function LibraryPage({
  lockedKind,
  mobile = false,
  importOwnedByParent = false,
}: {
  lockedKind?: LibraryKind;
  mobile?: boolean;
  importOwnedByParent?: boolean;
} = {}) {
  const { projectId, project } = useProject();
  const [kind, setKind] = useState<LibraryKind>(lockedKind ?? "actors");
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [query, setQuery] = useState("");
  const visibleAssets = assets.filter((asset) => `${asset.name} ${asset.notes || ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const isVoicePage = kind === "voices";
  const kindLabel = libraryKindLabel(kind);
  /** Folder popup: all outputs of one library asset. */
  const [folderAsset, setFolderAsset] = useState<LibraryAsset | null>(null);
  const [editingAsset, setEditingAsset] = useState<LibraryAsset | null>(null);

  const onMetadataSaved = (updated: LibraryAsset) => {
    setAssets((current) => current.map((asset) => asset.id === updated.id ? updated : asset));
    setFolderAsset((current) => current?.id === updated.id ? updated : current);
    setEditingAsset(null);
  };

  const refresh = useCallback(() => {
    setError(null);
    if (!projectId) {
      setAssets([]);
      return;
    }
    let current = true;
    setAssets([]);
    Promise.all((lockedKind ? [lockedKind] : KINDS.map((item) => item.id)).map((category) => listLibraryAssets(category, projectId)))
      .then((groups) => { if (current) setAssets(groups.flat()); })
      .catch((e) => { if (current) setError(e instanceof Error ? e.message : String(e)); });
    return () => { current = false; };
  }, [projectId, lockedKind]);

  useEffect(() => {
    return refresh();
  }, [refresh]);

  const onDeleteAsset = async (a: LibraryAsset) => {
    const nFiles = Object.keys(a.urls || {}).filter((k) => a.urls[k]).length;
    const ok = window.confirm(
      `Delete “${a.name}” and all ${nFiles || "its"} file(s)?\n\nThis cannot be undone.`,
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await deleteLibraryAsset(a.kind || kind, a.id);
      setFolderAsset(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell
      hideHeader={!lockedKind}
      title={lockedKind === "voices" ? "Voice" : lockedKind ? kindLabel : "Project assets"}
      subtitle={
        projectId ? (
          isVoicePage ? (
            <>
              Project <strong>{project?.name || projectId}</strong> — upload a clean
              2–15 second voice sample. Name and Description help the Director cast it.
            </>
          ) : (
            <>
              Project <strong>{project?.name || projectId}</strong> — external imports
              only need a file + optional name/notes.
            </>
          )
        ) : (
          "Select a project in the header."
        )
      }
      actions={
        projectId ? (
          <>
            {lockedKind && !mobile && !importOwnedByParent ? (
              <button
                type="button"
                className="asset-import-button"
                aria-label={`Import ${kindLabel}`}
                title={`Import ${kindLabel}`}
                onClick={() => setShowImport(true)}
              >
                <StudioIcon name="upload" /><span>Import</span>
              </button>
            ) : null}
            <button
              type="button"
              className="btn secondary"
              onClick={refresh}
              disabled={!projectId}
            >
              <StudioIcon name="refresh" /><span>Refresh</span>
            </button>
          </>
        ) : null
      }
      className={`library-page${mobile ? " mobile-library-page" : ""}`}
    >
      {error ? <div className="banner error">{error}</div> : null}

      {lockedKind && mobile && projectId && !importOwnedByParent ? (
        <div className="mobile-library-toolbar">
          <span>{assets.length} in this category</span>
          <div>
            <button
              type="button"
              className="asset-import-button"
              aria-label={`Import ${kindLabel}`}
              title={`Import ${kindLabel}`}
              onClick={() => setShowImport(true)}
            >
              <StudioIcon name="upload" /><span>Import</span>
            </button>
            <button type="button" className="btn secondary sm" onClick={refresh}><StudioIcon name="refresh" />Refresh</button>
          </div>
        </div>
      ) : null}

      {projectId && <div className="library-search-row"><label className="library-search"><StudioIcon name="search" />
        <input type="search" aria-label="Search assets" placeholder="Search names and descriptions" value={query} onChange={(event) => setQuery(event.target.value)} />
      </label><span>{visibleAssets.length} {visibleAssets.length === 1 ? "asset" : "assets"}</span>
        {!lockedKind && <button type="button" className="library-refresh" aria-label="Refresh library" title="Refresh library" onClick={refresh}><StudioIcon name="refresh" /></button>}
      </div>}

      {!projectId ? <p className="empty-copy">Select a project to browse its library.</p> :
        (lockedKind ? KINDS.filter((item) => item.id === lockedKind) : KINDS).map((category) => <section className="library-category-section" key={category.id} aria-label={category.label}>
          <header className="library-category-heading"><div><span className="library-category-icon"><StudioIcon name={category.id === "costumes" ? "props" : category.id} size={20} /></span>
            <h2>{category.label}</h2><span className="library-category-count">{visibleAssets.filter((asset) => asset.kind === category.id).length}</span></div>
            {!lockedKind && !importOwnedByParent && <button type="button" className="asset-section-import" aria-label={`Import ${category.label}`} onClick={() => { setKind(category.id); setShowImport(true); }}><StudioIcon name="upload" /><span>Import</span></button>}
          </header>
          {!visibleAssets.some((asset) => asset.kind === category.id) && <p className="library-category-empty">{query.trim() ? "No matches in this section." : "No assets yet. Import a reference to get started."}</p>}
          <div className="library-grid">
          {visibleAssets.filter((asset) => asset.kind === category.id).map((a) => {
            const thumb = previewUrl(a);
            const srcFn = (a.meta?.source_filename as string) || "";
            const external = a.pipeline_id === "external" || a.meta?.external;
            const nFiles = Object.keys(a.urls || {}).filter((k) => a.urls[k]).length;
            const isVoice = a.kind === "voices";
            const duration = Number(a.meta?.duration_s);
            return (
              <article key={a.id} className={`library-card ${isVoice ? "voice-card" : ""}`}>
                <button
                  type="button"
                  className="library-card-edit"
                  disabled={busy}
                  aria-label={`Edit ${a.name} metadata`}
                  title="Edit name and notes"
                  onClick={(event) => {
                    event.stopPropagation();
                    setEditingAsset(a);
                  }}
                >
                  <StudioIcon name="edit" />
                </button>
                <button
                  type="button"
                  className="library-card-delete"
                  aria-label={`Delete ${a.name}`}
                  disabled={busy}
                  title="Delete this asset"
                  onClick={(e) => {
                    e.stopPropagation();
                    void onDeleteAsset(a);
                  }}
                >
                  <StudioIcon name="delete" />
                </button>
                {isVoice ? (
                  <div className="library-card-open voice-card-open">
                    <div className="voice-signal-rail" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                      <span />
                      <span />
                    </div>
                    <div className="voice-card-head">
                      <div>
                        <h3>{a.name}</h3>
                        <div className="muted tiny">
                          {Number.isFinite(duration) ? `${duration.toFixed(1)}s` : "Voice reference"}
                        </div>
                      </div>
                      {a.meta?.h3_ready ? <span className="voice-ready">H3 Ready</span> : null}
                    </div>
                    <audio controls preload="metadata" src={a.urls?.reference || undefined} />
                    <span className={`library-description-status${a.notes ? " has-description" : ""}`}>{a.notes ? "Description saved" : "Add description"}</span>
                    <button
                      type="button"
                      className="btn secondary sm voice-details-button"
                      onClick={() => setFolderAsset(a)}
                    >
                      View files
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="library-card-open"
                    onClick={() => setFolderAsset(a)}
                    title="View image and description"
                  >
                  <div className="library-thumb">
                    {thumb ? (
                      <img src={thumb} alt={a.name} />
                    ) : (
                      <div className="output-empty">No preview</div>
                    )}
                    {nFiles > 1 ? (
                      <span className="library-file-badge">{nFiles} files</span>
                    ) : null}
                  </div>
                  <div className="library-meta">
                    <h3>{a.name}</h3>
                    <div className="muted tiny">
                      {external ? "external" : a.pipeline_id}
                    </div>
                    {srcFn ? (
                      <div className="muted tiny ellipsis library-source-file"><StudioIcon name="file" size={13} /> {srcFn}</div>
                    ) : null}
                    <span className={`library-description-status${a.notes ? " has-description" : ""}`}>{a.notes ? "Description saved" : "Add description"}</span>
                  </div>
                  </button>
                )}
              </article>
            );
          })}
        </div></section>)}

      {assets.length > 0 && !visibleAssets.length && <p className="empty-copy">No matching assets. Try another name or description.</p>}

      {folderAsset ? (
        <AssetDetailDialog
          asset={folderAsset}
          busy={busy}
          onClose={() => setFolderAsset(null)}
          onEdit={() => setEditingAsset(folderAsset)}
          onDelete={() => void onDeleteAsset(folderAsset)}
        />
      ) : null}

      {editingAsset ? (
        <AssetMetadataDialog
          asset={editingAsset}
          onClose={() => setEditingAsset(null)}
          onSaved={onMetadataSaved}
        />
      ) : null}

      {showImport && projectId ? (
        <AssetImportDialog
          kind={kind}
          projectId={projectId}
          onClose={() => setShowImport(false)}
          onImported={() => {
            setShowImport(false);
            refresh();
          }}
        />
      ) : null}

    </PageShell>
  );
}
