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

const KINDS: { id: LibraryKind; label: string }[] = [
  { id: "actors", label: "Actors" },
  { id: "scenes", label: "Scenes" },
  { id: "props", label: "Props" },
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
    listLibraryAssets(kind, projectId)
      .then(setAssets)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [projectId, kind]);

  useEffect(() => {
    refresh();
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
      title={lockedKind === "voices" ? "Voice" : mobile || lockedKind ? kindLabel : "Library"}
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
            {!mobile && !importOwnedByParent ? (
              <button
                type="button"
                className="asset-import-button"
                aria-label={`Import ${kindLabel}`}
                title={`Import ${kindLabel}`}
                onClick={() => setShowImport(true)}
              >
                +
              </button>
            ) : null}
            <button
              type="button"
              className="btn secondary"
              onClick={refresh}
              disabled={!projectId}
            >
              Refresh
            </button>
          </>
        ) : null
      }
      className={`library-page${mobile ? " mobile-library-page" : ""}`}
    >
      {error ? <div className="banner error">{error}</div> : null}

      {mobile && projectId && !importOwnedByParent ? (
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
              +
            </button>
            <button type="button" className="btn secondary sm" onClick={refresh}>Refresh</button>
          </div>
        </div>
      ) : null}

      {lockedKind ? null : (
      <div className="kind-tabs">
        {KINDS.map((k) => (
          <button
            key={k.id}
            type="button"
            className={`kind-tab ${kind === k.id ? "active" : ""}`}
            onClick={() => setKind(k.id)}
          >
            {k.label}
            {kind === k.id ? (
              <span className="kind-count">{assets.length}</span>
            ) : null}
          </button>
        ))}
      </div>
      )}

      {!projectId ? (
        <p className="empty-copy">No project selected.</p>
      ) : assets.length === 0 ? (
        <div className="section-card empty-state-card">
          <p className="empty-copy">
            {isVoicePage
              ? "No voices yet. Use + to upload a 2–15 second sample."
              : `No ${kind} yet. Use + to import, or generate it with the preparation workflow.`}
          </p>
        </div>
      ) : (
        <div className="library-grid">
          {assets.map((a) => {
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
                  Edit
                </button>
                <button
                  type="button"
                  className="library-card-delete"
                  disabled={busy}
                  title="Delete this asset"
                  onClick={(e) => {
                    e.stopPropagation();
                    void onDeleteAsset(a);
                  }}
                >
                  Delete
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
                    {a.notes ? <p className="notes">{a.notes}</p> : null}
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
                    title="Open asset folder"
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
                      <div className="muted tiny ellipsis">📄 {srcFn}</div>
                    ) : null}
                    {a.notes ? <p className="notes">{a.notes}</p> : null}
                  </div>
                  </button>
                )}
              </article>
            );
          })}
        </div>
      )}

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
