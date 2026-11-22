import { useCallback, useEffect, useState } from "react";
import { PageShell } from "../../shared/components/PageShell";
import { StudioIcon } from "../../shared/components/StudioIcon";
import { useProject } from "../../shared/project/ProjectContext";
import { Badge, Banner, Button, EmptyState, cn } from "../../shared/ui";
import {
  deleteLibraryAsset,
  listLibraryAssets,
  type LibraryAsset,
  type LibraryKind,
} from "./api";
import { AssetDetailDialog, assetPreviewUrl } from "./AssetDetailDialog";
import { AssetImportDialog, libraryKindLabel } from "./AssetImportDialog";
import { AssetMetadataDialog } from "./AssetMetadataDialog";

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
  const visibleAssets = assets.filter((asset) =>
    `${asset.name} ${asset.notes || ""}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  const isVoicePage = kind === "voices";
  const kindLabel = libraryKindLabel(kind);
  /** Folder popup: all outputs of one library asset. */
  const [folderAsset, setFolderAsset] = useState<LibraryAsset | null>(null);
  const [editingAsset, setEditingAsset] = useState<LibraryAsset | null>(null);

  const onMetadataSaved = (updated: LibraryAsset) => {
    setAssets((current) => current.map((asset) => (asset.id === updated.id ? updated : asset)));
    setFolderAsset((current) => (current?.id === updated.id ? updated : current));
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
    Promise.all(
      (lockedKind ? [lockedKind] : KINDS.map((item) => item.id)).map((category) =>
        listLibraryAssets(category, projectId),
      ),
    )
      .then((groups) => {
        if (current) setAssets(groups.flat());
      })
      .catch((e) => {
        if (current) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      current = false;
    };
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
      width="wide"
      title={lockedKind === "voices" ? "Voice" : lockedKind ? kindLabel : "Project assets"}
      subtitle={
        projectId ? (
          isVoicePage ? (
            <>
              Project <strong className="font-medium text-ink">{project?.name || projectId}</strong>{" "}
              — upload a clean 2–15 second voice sample. Name and Description help the Director cast
              it.
            </>
          ) : (
            <>
              Project <strong className="font-medium text-ink">{project?.name || projectId}</strong>{" "}
              — external imports only need a file + optional name/notes.
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
              <Button
                type="button"
                variant="primary"
                size="sm"
                className="asset-import-button"
                aria-label={`Import ${kindLabel}`}
                title={`Import ${kindLabel}`}
                onClick={() => setShowImport(true)}
              >
                <StudioIcon name="upload" size={14} />
                <span>Import</span>
              </Button>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={refresh}
              disabled={!projectId}
            >
              <StudioIcon name="refresh" size={14} />
              <span>Refresh</span>
            </Button>
          </>
        ) : null
      }
      className={cn("library-page", mobile && "mobile-library-page")}
    >
      {error ? (
        <Banner tone="danger" role="alert" className="mb-4">
          {error}
        </Banner>
      ) : null}

      {lockedKind && mobile && projectId && !importOwnedByParent ? (
        <div className="mobile-library-toolbar mb-4 flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-panel px-3 py-2">
          <span className="text-xs text-ink-secondary">{assets.length} in this category</span>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="asset-import-button"
              aria-label={`Import ${kindLabel}`}
              title={`Import ${kindLabel}`}
              onClick={() => setShowImport(true)}
            >
              <StudioIcon name="upload" size={14} />
              <span>Import</span>
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={refresh}>
              <StudioIcon name="refresh" size={14} />
              Refresh
            </Button>
          </div>
        </div>
      ) : null}

      {projectId ? (
        <div className="library-search-row mb-6 flex flex-wrap items-center gap-2.5">
          <label className="library-search flex h-8 w-full max-w-[400px] items-center gap-2 rounded-md border border-border-subtle bg-panel px-2.5 text-ink shadow-[var(--shadow-card)] transition-colors focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/30">
            <span className="inline-flex shrink-0 text-ink-muted">
              <StudioIcon name="search" size={14} />
            </span>
            <input
              type="search"
              aria-label="Search assets"
              placeholder="Search names and descriptions"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="w-full min-w-0 border-0 bg-transparent text-xs text-ink placeholder:text-ink-muted focus:outline-none"
            />
          </label>
          <span className="ml-auto font-mono text-[11px] text-ink-muted tabular-nums">
            {visibleAssets.length} {visibleAssets.length === 1 ? "asset" : "assets"}
          </span>
          {!lockedKind ? (
            <Button
              type="button"
              variant="secondary"
              size="icon"
              className="library-refresh"
              aria-label="Refresh library"
              title="Refresh library"
              onClick={refresh}
            >
              <StudioIcon name="refresh" size={14} />
            </Button>
          ) : null}
        </div>
      ) : null}

      {!projectId ? (
        <div className="py-10">
          <EmptyState
            icon={<StudioIcon name="library" size={16} />}
            title="No project selected"
            description={
              <span className="empty-copy">Select a project to browse its library.</span>
            }
            className="mx-auto max-w-md"
          />
        </div>
      ) : (
        <div className="flex flex-col gap-7">
          {(lockedKind ? KINDS.filter((item) => item.id === lockedKind) : KINDS).map((category) => {
            const categoryAssets = visibleAssets.filter((asset) => asset.kind === category.id);
            return (
              <section
                className="library-category-section flex flex-col gap-3"
                key={category.id}
                aria-label={category.label}
              >
                <header className="library-category-heading flex items-center justify-between gap-3 border-b border-border-subtle pb-2.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="library-category-icon flex h-6 w-6 items-center justify-center rounded-md border border-border-subtle bg-elevated text-ink-secondary">
                      <StudioIcon
                        name={category.id === "costumes" ? "props" : category.id}
                        size={14}
                      />
                    </span>
                    <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-ink">
                      {category.label}
                    </h2>
                    <Badge
                      tone="neutral"
                      className="library-category-count font-mono text-[11px] tabular-nums"
                    >
                      {categoryAssets.length}
                    </Badge>
                  </div>
                  {!lockedKind && !importOwnedByParent ? (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="asset-section-import"
                      aria-label={`Import ${category.label}`}
                      onClick={() => {
                        setKind(category.id);
                        setShowImport(true);
                      }}
                    >
                      <StudioIcon name="upload" size={13} />
                      <span>Import</span>
                    </Button>
                  ) : null}
                </header>

                {!categoryAssets.length ? (
                  <p className="library-category-empty py-2 text-xs text-ink-muted">
                    {query.trim()
                      ? "No matches in this section."
                      : "No assets yet. Import a reference to get started."}
                  </p>
                ) : (
                  <div className="library-grid grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(196px,1fr))]">
                    {categoryAssets.map((a) => {
                      const thumb = previewUrl(a);
                      const srcFn = (a.meta?.source_filename as string) || "";
                      const external = a.pipeline_id === "external" || a.meta?.external;
                      const nFiles = Object.keys(a.urls || {}).filter((k) => a.urls[k]).length;
                      const isVoice = a.kind === "voices";
                      const duration = Number(a.meta?.duration_s);

                      return (
                        <article
                          key={a.id}
                          className={cn(
                            "library-card group relative flex min-w-0 flex-col overflow-hidden rounded-lg border border-border-subtle bg-panel shadow-[var(--shadow-card)] transition-colors hover:border-border-strong",
                            isVoice && "voice-card",
                          )}
                        >
                          <div className="library-card-tools flex items-center justify-end gap-1 border-b border-border-subtle bg-elevated/40 px-2 py-1.5">
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="library-card-edit h-6 w-6"
                              disabled={busy}
                              aria-label={`Edit ${a.name} metadata`}
                              title="Edit name and notes"
                              onClick={(event) => {
                                event.stopPropagation();
                                setEditingAsset(a);
                              }}
                            >
                              <StudioIcon name="edit" size={13} />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="library-card-delete h-6 w-6 text-ink-muted hover:bg-status-danger-soft hover:text-status-danger"
                              aria-label={`Delete ${a.name}`}
                              disabled={busy}
                              title="Delete this asset"
                              onClick={(e) => {
                                e.stopPropagation();
                                void onDeleteAsset(a);
                              }}
                            >
                              <StudioIcon name="delete" size={13} />
                            </Button>
                          </div>

                          {isVoice ? (
                            <div className="library-card-open voice-card-open flex flex-1 flex-col gap-2.5 p-3">
                              <div
                                className="voice-signal-rail flex h-3 items-end gap-0.5"
                                aria-hidden="true"
                              >
                                <span className="h-1.5 w-1 rounded-full bg-accent/60" />
                                <span className="h-2.5 w-1 rounded-full bg-accent/80" />
                                <span className="h-3 w-1 rounded-full bg-accent" />
                                <span className="h-2 w-1 rounded-full bg-accent/80" />
                                <span className="h-1.5 w-1 rounded-full bg-accent/60" />
                              </div>

                              <div className="voice-card-head flex items-start justify-between gap-2">
                                <div className="min-w-0">
                                  <h3 className="truncate text-[13px] font-semibold text-ink">
                                    {a.name}
                                  </h3>
                                  <div className="font-mono text-[11px] text-ink-muted tabular-nums">
                                    {Number.isFinite(duration)
                                      ? `${duration.toFixed(1)}s`
                                      : "Voice reference"}
                                  </div>
                                </div>
                                {a.meta?.h3_ready ? (
                                  <Badge tone="ok" className="voice-ready shrink-0 text-[10px]">
                                    H3 Ready
                                  </Badge>
                                ) : null}
                              </div>

                              <audio
                                controls
                                preload="metadata"
                                src={a.urls?.reference || undefined}
                                className="h-8 w-full"
                              />

                              <span
                                className={cn(
                                  "library-description-status text-[11px] font-medium",
                                  a.notes ? "has-description text-status-ok" : "text-ink-muted",
                                )}
                              >
                                {a.notes ? "Description saved" : "Add description"}
                              </span>

                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="voice-details-button mt-auto w-full"
                                onClick={() => setFolderAsset(a)}
                              >
                                View files
                              </Button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="library-card-open flex flex-1 cursor-pointer flex-col text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                              onClick={() => setFolderAsset(a)}
                              title="View image and description"
                            >
                              <div className="library-thumb relative flex aspect-square w-full items-center justify-center overflow-hidden border-b border-border-subtle bg-canvas/60 p-2.5 sm:aspect-auto sm:h-44">
                                {thumb ? (
                                  <img
                                    src={thumb}
                                    alt={a.name}
                                    className="max-h-full max-w-full object-contain transition-transform duration-150 group-hover:scale-[1.02]"
                                  />
                                ) : (
                                  <div className="output-empty text-xs text-ink-muted">
                                    No preview
                                  </div>
                                )}
                                {nFiles > 1 ? (
                                  <Badge
                                    tone="neutral"
                                    className="library-file-badge absolute bottom-2 right-2 bg-panel/90 font-mono text-[10px] backdrop-blur-xs"
                                  >
                                    {nFiles} files
                                  </Badge>
                                ) : null}
                              </div>
                              <div className="library-meta flex flex-1 flex-col gap-1 p-3">
                                <h3 className="break-words text-[13px] font-semibold leading-snug text-ink">
                                  {a.name}
                                </h3>
                                <div className="font-mono text-[11px] text-ink-muted">
                                  {external ? "external" : a.pipeline_id}
                                </div>
                                {srcFn ? (
                                  <div className="library-source-file hidden items-center gap-1 truncate text-[11px] text-ink-muted sm:flex">
                                    <StudioIcon name="file" size={12} />
                                    <span className="truncate">{srcFn}</span>
                                  </div>
                                ) : null}
                                <span
                                  className={cn(
                                    "library-description-status mt-auto pt-1.5 text-[11px] font-medium",
                                    a.notes ? "has-description text-status-ok" : "text-ink-muted",
                                  )}
                                >
                                  {a.notes ? "Description saved" : "Add description"}
                                </span>
                              </div>
                            </button>
                          )}
                        </article>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      {assets.length > 0 && !visibleAssets.length ? (
        <p className="empty-copy mt-4 text-xs text-ink-muted">
          No matching assets. Try another name or description.
        </p>
      ) : null}

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
