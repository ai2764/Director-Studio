import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, ChevronRight } from "lucide-react";
import type { RefRole, Shot } from "../../shared/api/types";
import { listLibraryAssets, type LibraryAsset, type LibraryKind } from "../library/api";
import { replaceShotMaterials, type ShotMaterialSelection } from "./api";
import { StudioIcon } from "../../shared/components/StudioIcon";
import { useLibraryRevision } from "../library/libraryChanges";
import { Badge, Banner, Button, EmptyState, Input, Select, Textarea, cn } from "../../shared/ui";

const KINDS: LibraryKind[] = ["actors", "costumes", "scenes", "props", "layouts"];
const ROLES: Record<string, RefRole> = {
  actors: "actor",
  costumes: "costume",
  scenes: "scene",
  props: "prop",
  layouts: "layout_ref_frame",
};
const LABELS: Record<string, string> = {
  actors: "Actors",
  costumes: "Wardrobe",
  scenes: "Scenes",
  props: "Props",
  layouts: "Layouts",
};
const identity = (value: ShotMaterialSelection) => `${value.asset_id}:${value.file_key || ""}`;

export function ShotMaterialEditor({
  shot,
  onClose,
  onOpenImage,
  onSaved,
}: {
  shot: Shot;
  shots?: Shot[];
  shotNumber?: number;
  manualReferences?: boolean;
  onClose: () => void;
  onOpenImage: (url: string) => void;
  onSaved?: (shot: Shot, note: string, write: boolean) => void;
}) {
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [materials, setMaterials] = useState<ShotMaterialSelection[]>(shot.refs.map((ref) => ({ ...ref })));
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { revision, refresh } = useLibraryRevision();

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void Promise.all(KINDS.map((kind) => listLibraryAssets(kind, shot.project_id, true)))
      .then((groups) => {
        if (current) setAssets(groups.flat());
      })
      .catch((cause) => {
        if (current) setError(String(cause));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [shot.project_id, revision]);

  async function save(write: boolean) {
    setBusy(true);
    setError("");
    try {
      const ordered = [
        ...materials.filter((ref) => ref.role !== "layout_ref_frame"),
        ...materials.filter((ref) => ref.role === "layout_ref_frame"),
      ];
      const saved = await replaceShotMaterials(shot.id, ordered, { expectedRefs: shot.refs, revisionRequest: note });
      onSaved?.(saved, note, write);
      onClose();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  function move(index: number, delta: number) {
    setMaterials((current) => {
      const values = [...current];
      [values[index], values[index + delta]] = [values[index + delta], values[index]];
      return values;
    });
  }

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredCards = assets
    .filter(
      (asset) =>
        (filter === "all" || asset.kind === filter) &&
        `${asset.name} ${asset.notes}`.toLocaleLowerCase().includes(normalizedQuery),
    )
    .flatMap((asset) =>
      Object.keys(asset.files)
        .filter((key) => /\.(png|jpe?g|webp|gif)$/i.test(asset.files[key] || "") && asset.urls[key])
        .map((key) => ({ asset, key })),
    );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-2 backdrop-blur-[2px] sm:p-6">
      <section
        role="dialog"
        aria-modal="true"
        aria-label="Shot references"
        className="flex h-[min(860px,92dvh)] w-[min(900px,100%)] flex-col overflow-hidden rounded-xl border border-border-subtle bg-panel text-ink shadow-[var(--shadow-popover)]"
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border-subtle bg-elevated/60 px-4 py-3.5 sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border-subtle bg-accent-soft text-accent">
              <StudioIcon name="library" size={18} />
            </span>
            <div className="min-w-0">
              <h2 className="m-0 text-sm font-semibold tracking-[-0.01em] text-ink">Link references</h2>
              <p className="mt-0.5 mb-0 text-xs text-ink-secondary">
                Choose the images that define this shot.
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close references"
            disabled={busy}
            onClick={onClose}
          >
            <StudioIcon name="close" size={16} />
          </Button>
        </header>

        <div className="min-h-0 min-w-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden overscroll-contain p-4 sm:p-5">
          <details
            className="rounded-lg border border-border-subtle bg-elevated/40 p-3"
            open={materials.length > 0}
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-xs font-semibold text-ink select-none [&::-webkit-details-marker]:hidden">
              <span className="inline-flex items-center gap-2">
                <ChevronRight className="h-3.5 w-3.5 text-ink-muted" aria-hidden="true" />
                <span>Selected references</span>
                <Badge tone={materials.length > 0 ? "accent" : "neutral"} className="font-mono">
                  {materials.length} / 9
                </Badge>
              </span>
            </summary>
            <section
              aria-label="Selected references"
              className="mt-3 flex flex-col gap-2.5"
            >
              {materials.map((material, index) => {
                const asset = assets.find((value) => value.id === material.asset_id);
                const url = asset?.urls[material.file_key || "master"];
                return (
                  <article
                    key={identity(material)}
                    className="grid grid-cols-[52px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg border border-border-subtle bg-panel p-2.5 sm:grid-cols-[64px_minmax(0,1fr)_auto]"
                  >
                    <button
                      type="button"
                      className="flex h-16 w-full cursor-pointer items-center justify-center overflow-hidden rounded-md border border-border-subtle bg-canvas p-0 disabled:cursor-default"
                      disabled={!url}
                      onClick={() => url && onOpenImage(url)}
                    >
                      {url ? (
                        <img
                          src={url}
                          alt={`Picture ${index + 1}`}
                          className="h-full w-full object-contain"
                        />
                      ) : (
                        <StudioIcon name="scenes" size={18} />
                      )}
                    </button>
                    <div className="min-w-0 space-y-1.5">
                      <strong className="block truncate text-xs font-semibold text-ink">
                        P{index + 1} · {asset?.name || "Selected reference"}
                      </strong>
                      <Textarea
                        aria-label={`Reference ${index + 1} note`}
                        rows={2}
                        value={material.notes || ""}
                        disabled={busy}
                        placeholder="Optional direction for this reference"
                        className="min-h-[54px] text-xs"
                        onChange={(event) =>
                          setMaterials((current) =>
                            current.map((ref, position) =>
                              position === index ? { ...ref, notes: event.target.value } : ref,
                            ),
                          )
                        }
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        className="h-6 w-7 p-0"
                        disabled={busy || index === 0}
                        onClick={() => move(index, -1)}
                        aria-label={`Move reference ${index + 1} up`}
                      >
                        <ArrowUp className="h-3 w-3" aria-hidden="true" />
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        className="h-6 w-7 p-0"
                        disabled={busy || index === materials.length - 1}
                        onClick={() => move(index, 1)}
                        aria-label={`Move reference ${index + 1} down`}
                      >
                        <ArrowDown className="h-3 w-3" aria-hidden="true" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className="h-6 w-7 p-0 text-ink-muted hover:text-status-danger"
                        disabled={busy}
                        aria-label={`Remove reference ${index + 1}`}
                        title="Remove reference"
                        onClick={() =>
                          setMaterials((current) => current.filter((_, position) => position !== index))
                        }
                      >
                        <StudioIcon name="close" size={13} />
                      </Button>
                    </div>
                  </article>
                );
              })}
            </section>
          </details>

          <div className="grid gap-2.5 sm:grid-cols-[minmax(0,1fr)_auto]">
            <label className="relative flex min-w-0 items-center">
              <span className="pointer-events-none absolute left-2.5 text-ink-muted">
                <StudioIcon name="search" size={14} />
              </span>
              <Input
                type="search"
                aria-label="Search references"
                placeholder="Search references"
                value={query}
                className="pl-8"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <div className="flex items-center gap-2">
              <Select
                size="md"
                wrapperClassName="flex-1 sm:w-40"
                aria-label="Reference category"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              >
                <option value="all">All images</option>
                {KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {LABELS[kind]}
                  </option>
                ))}
              </Select>
              <Button
                type="button"
                variant="secondary"
                size="icon"
                className="h-8 w-8 shrink-0"
                aria-label="Refresh reference library"
                title="Refresh library"
                disabled={loading || busy}
                onClick={refresh}
              >
                <StudioIcon name="refresh" size={14} />
              </Button>
            </div>
          </div>

          {loading && (
            <p role="status" className="py-8 text-center text-xs text-ink-muted">
              Loading library…
            </p>
          )}
          {!loading && filteredCards.length === 0 && (
            <EmptyState
              icon={<StudioIcon name="scenes" size={18} />}
              title="No matching images. Import images in Assets, or try another search."
            />
          )}

          {filteredCards.length > 0 && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
              {filteredCards.map(({ asset, key }) => {
                const selected = materials.some((ref) => identity(ref) === `${asset.id}:${key}`);
                return (
                  <article
                    key={`${asset.id}:${key}`}
                    className={cn(
                      "flex flex-col overflow-hidden rounded-lg border bg-panel transition-colors",
                      selected
                        ? "border-accent ring-1 ring-accent"
                        : "border-border-subtle hover:border-border-strong",
                    )}
                  >
                    <button
                      type="button"
                      className="flex h-32 w-full cursor-zoom-in items-center justify-center border-b border-border-subtle bg-canvas p-2"
                      aria-label={`Preview ${asset.name} ${key}`}
                      onClick={() => onOpenImage(asset.urls[key])}
                    >
                      <img
                        src={asset.urls[key]}
                        alt={asset.name}
                        loading="lazy"
                        className="h-full w-full object-contain"
                      />
                    </button>
                    <div className="flex flex-1 flex-col gap-1.5 p-2.5">
                      <strong className="line-clamp-2 text-xs font-semibold text-ink">{asset.name}</strong>
                      <small className="font-mono text-[10px] text-ink-muted">
                        {key.replaceAll("_", " ")}
                      </small>
                      {asset.notes && (
                        <details className="text-[11px] text-ink-secondary">
                          <summary className="cursor-pointer font-medium text-ink-muted hover:text-ink">
                            Description
                          </summary>
                          <p className="mt-1 mb-0 max-h-32 overflow-y-auto whitespace-pre-wrap break-words leading-relaxed">
                            {asset.notes}
                          </p>
                        </details>
                      )}
                      <Button
                        type="button"
                        variant={selected ? "secondary" : "outline"}
                        size="xs"
                        className={cn("mt-auto w-full", selected && "border-accent/40 bg-accent-soft text-accent")}
                        aria-label={selected ? `${asset.name} ${key} selected` : `Add ${asset.name} ${key}`}
                        disabled={busy || selected || materials.length >= 9}
                        onClick={() =>
                          setMaterials((current) =>
                            current.some((ref) => identity(ref) === `${asset.id}:${key}`) || current.length >= 9
                              ? current
                              : [...current, { role: ROLES[asset.kind], asset_id: asset.id, file_key: key, notes: "" }],
                          )
                        }
                      >
                        <StudioIcon name={selected ? "check" : "plus"} size={13} />
                        <span>{selected ? "Selected" : "Add reference"}</span>
                      </Button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          <details className="border-t border-border-subtle pt-3 text-xs">
            <summary className="cursor-pointer font-medium text-ink-secondary">
              Notes for this reference set <span className="ml-1.5 text-[11px] text-ink-muted">Optional</span>
            </summary>
            <Textarea
              aria-label="Reference-set note"
              placeholder="How should these references be used together?"
              value={note}
              rows={2}
              disabled={busy}
              className="mt-2 min-h-[60px] text-xs"
              onChange={(event) => setNote(event.target.value)}
            />
          </details>
          {error && <Banner tone="danger">{error}</Banner>}
        </div>

        <footer className="grid shrink-0 grid-cols-2 gap-2.5 border-t border-border-subtle bg-elevated/60 px-4 py-3 sm:px-5">
          <Button
            type="button"
            variant="secondary"
            size="lg"
            className="w-full"
            disabled={busy || loading}
            onClick={() => void save(false)}
          >
            {busy ? "Saving…" : "Save references"}
          </Button>
          <Button
            type="button"
            variant="primary"
            size="lg"
            className="w-full"
            disabled={busy || loading || !materials.length}
            onClick={() => void save(true)}
          >
            Save &amp; write prompt
          </Button>
        </footer>
      </section>
    </div>
  );
}
