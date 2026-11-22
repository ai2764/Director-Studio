import { useEffect, useState } from "react";
import { ArrowRight, Plus } from "lucide-react";
import { useProject } from "../../shared/project/ProjectContext";
import { Badge, Banner, Button } from "../../shared/ui";
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

export function LibraryOverview({
  onSelectKind,
}: {
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
      [kind]: (current[kind] || []).map((asset) => (asset.id === updated.id ? updated : asset)),
    }));
    setDetailAsset((current) => (current?.id === updated.id ? updated : current));
    setEditingAsset(null);
  };

  useEffect(() => {
    let active = true;
    setError(null);
    if (!projectId) {
      setGroups({});
      return () => {
        active = false;
      };
    }
    Promise.all(
      GROUPS.map(async (group) => [group.id, await listLibraryAssets(group.id, projectId)] as const),
    )
      .then((entries) => {
        if (active) setGroups(Object.fromEntries(entries));
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      active = false;
    };
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

  const totalCount = Object.values(groups).reduce((total, assets) => total + assets.length, 0);

  return (
    <section
      className="library-overview mobile-library-overview flex flex-col gap-4"
      aria-labelledby="library-overview-title"
    >
      <header className="flex items-end justify-between gap-3 border-b border-border-subtle pb-3">
        <div>
          <span className="mobile-eyebrow block text-[11px] font-medium uppercase tracking-wider text-ink-muted">
            Reusable across every Shot
          </span>
          <h2
            id="library-overview-title"
            className="text-sm font-semibold tracking-[-0.01em] text-ink"
          >
            Project library
          </h2>
        </div>
        <Badge tone="neutral" className="mobile-library-total font-mono tabular-nums">
          {totalCount} assets
        </Badge>
      </header>

      {error ? (
        <Banner tone="danger" role="alert">
          {error}
        </Banner>
      ) : null}
      {!projectId ? (
        <p className="empty-copy text-xs text-ink-muted">Select a project to browse its library.</p>
      ) : null}

      <div className="mobile-library-groups flex flex-col gap-4">
        {GROUPS.map((group) => {
          const assets = groups[group.id] || [];
          return (
            <section
              key={group.id}
              className="mobile-library-group flex flex-col gap-2.5 rounded-lg border border-border-subtle bg-panel p-3 shadow-[var(--shadow-card)]"
            >
              <header className="mobile-library-group-heading flex items-center justify-between gap-2">
                <button
                  className="mobile-library-group-main flex min-w-0 flex-1 cursor-pointer items-center justify-between gap-2 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-hover"
                  type="button"
                  onClick={() => onSelectKind(group.id)}
                  aria-label={`View ${group.label}`}
                >
                  <strong className="text-[13px] font-semibold text-ink">{group.label}</strong>
                  <span className="inline-flex items-center gap-1 text-xs text-ink-secondary">
                    {assets.length} · Prepare
                    <ArrowRight className="h-3 w-3" aria-hidden="true" />
                  </span>
                </button>
                <Button
                  type="button"
                  variant="secondary"
                  size="icon"
                  className="asset-import-button"
                  aria-label={`Import ${group.label}`}
                  title={`Import ${group.label}`}
                  disabled={!projectId}
                  onClick={() => setImportKind(group.id)}
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </header>

              {assets.length ? (
                <div className="mobile-library-preview-strip flex gap-2.5 overflow-x-auto pb-1">
                  {assets.map((asset) => {
                    const preview = assetPreviewUrl(asset);
                    return (
                      <button
                        key={asset.id}
                        type="button"
                        className="mobile-library-preview-card flex w-28 shrink-0 cursor-pointer flex-col gap-1.5 rounded-md border border-border-subtle bg-elevated/40 p-1.5 text-left transition-colors hover:border-border-strong hover:bg-hover/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        aria-label={`View ${asset.name} asset set`}
                        onClick={() => setDetailAsset(asset)}
                      >
                        <div className="mobile-library-preview flex h-20 w-full items-center justify-center overflow-hidden rounded bg-canvas/70">
                          {preview ? (
                            <img
                              src={preview}
                              alt=""
                              className="h-full w-full object-contain"
                            />
                          ) : (
                            <span className="font-mono text-[10px] font-medium text-ink-muted">
                              {group.id === "voices" ? "VOICE" : "ASSET"}
                            </span>
                          )}
                        </div>
                        <strong className="truncate text-xs font-medium text-ink">
                          {asset.name}
                        </strong>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p className="px-1 text-xs text-ink-muted">{group.empty}</p>
              )}
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
