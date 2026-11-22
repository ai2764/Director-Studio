import { useEffect, useRef, useState } from "react";
import { ChevronRight, Trash2 } from "lucide-react";
import type { Shot, ShotVoiceRef } from "../../shared/api/types";
import { parseError } from "../../shared/api/client";
import { listLibraryAssets, type LibraryAsset } from "../library/api";
import { AssetImportDialog } from "../library/AssetImportDialog";
import { WorkspaceMascot } from "./WorkspaceMascot";
import { useLibraryRevision } from "../library/libraryChanges";
import { StudioIcon } from "../../shared/components/StudioIcon";
import { Banner, Button, Input, Select, cn } from "../../shared/ui";

export function VoiceReferences({
  shot,
  disabled,
  onSaved,
}: {
  shot: Shot;
  disabled: boolean;
  onSaved: (shot: Shot) => void;
}) {
  const [open, setOpen] = useState(false);
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [refs, setRefs] = useState<ShotVoiceRef[]>(shot.voice_refs);
  const confirmed = useRef(shot.voice_refs);
  const saving = useRef(false);
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  const { revision: libraryRevision, refresh } = useLibraryRevision(open);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    confirmed.current = shot.voice_refs;
    setRefs(shot.voice_refs);
  }, [shot.voice_refs]);

  useEffect(() => {
    if (!open) return;
    let current = true;
    setLoading(true);
    setError("");
    void listLibraryAssets("voices", shot.project_id)
      .then((items) => {
        if (current) setAssets(items);
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
  }, [open, shot.project_id, revision, libraryRevision]);

  async function save(next: ShotVoiceRef[]) {
    if (saving.current || disabled || JSON.stringify(next) === JSON.stringify(confirmed.current)) return;
    saving.current = true;
    setRefs(next);
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/shot-workspace/shots/${shot.id}/voice-references`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_refs: next, expected_refs: confirmed.current }),
      });
      if (!response.ok) throw new Error(await parseError(response));
      const saved = (await response.json()) as Shot;
      confirmed.current = saved.voice_refs;
      setRefs(saved.voice_refs);
      onSaved(saved);
    } catch (cause) {
      setRefs(confirmed.current);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <details
      className="rounded-lg border border-border-subtle bg-elevated/40 px-3 py-2.5 text-xs transition-colors"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 font-medium text-ink select-none [&::-webkit-details-marker]:hidden">
        <span className="inline-flex items-center gap-2">
          <WorkspaceMascot role="voice" className="h-6 w-6 rounded" />
          <span>Voice references · {shot.voice_refs.length} / 3</span>
        </span>
        <ChevronRight
          className={cn("h-3.5 w-3.5 text-ink-muted transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
      </summary>
      {open && (
        <div className="mt-3 grid gap-3 border-t border-border-subtle pt-3">
          <p className="m-0 text-xs leading-relaxed text-ink-secondary">
            Select a voice to link it immediately. Speakers are matched from sample names or filenames when writing the
            prompt. Up to 3 voices, 15 seconds total.
          </p>
          {shot.source_audio_path && (
            <Banner tone="warn" role="status">
              This shot uses native source audio. Remove it before linking voices.
            </Banner>
          )}
          {refs.map((ref, index) => {
            const asset = assets.find((item) => item.id === ref.asset_id);
            return (
              <div
                className="grid gap-2.5 rounded-md border border-border-subtle bg-panel p-2.5"
                key={ref.asset_id}
              >
                <div className="flex items-center justify-between gap-2">
                  <strong className="text-xs font-semibold text-ink">
                    Audio {index + 1} · {asset?.name || ref.asset_id}
                  </strong>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    className="text-ink-muted hover:text-status-danger"
                    disabled={disabled || busy}
                    onClick={() =>
                      void save(
                        refs
                          .filter((_, i) => i !== index)
                          .map((value, i) => ({ ...value, audio_index: i + 1 })),
                      )
                    }
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                    <span>Remove voice {index + 1}</span>
                  </Button>
                </div>
                {asset?.urls[ref.file_key] && (
                  <audio
                    controls
                    preload="none"
                    src={asset.urls[ref.file_key]}
                    aria-label={`Preview voice ${index + 1}`}
                    className="h-8 w-full"
                  />
                )}
                <details className="rounded border border-border-subtle bg-elevated/40 px-2.5 py-1.5">
                  <summary className="cursor-pointer text-[11px] font-medium text-ink-secondary">
                    Optional voice settings
                  </summary>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <label className="grid gap-1 text-[11px] font-medium text-ink-secondary">
                      <span>Speaker override (optional)</span>
                      <Input
                        aria-label={`Voice ${index + 1} speaker`}
                        value={ref.speaker}
                        placeholder="Automatic from sample name or filename"
                        disabled={disabled || busy}
                        onBlur={() => void save(refs)}
                        onChange={(event) =>
                          setRefs((current) =>
                            current.map((value, i) =>
                              i === index ? { ...value, speaker: event.target.value } : value,
                            ),
                          )
                        }
                      />
                    </label>
                    <label className="grid gap-1 text-[11px] font-medium text-ink-secondary">
                      <span>Voice direction</span>
                      <Input
                        aria-label={`Voice ${index + 1} direction`}
                        value={ref.notes || ""}
                        disabled={disabled || busy}
                        onBlur={() => void save(refs)}
                        onChange={(event) =>
                          setRefs((current) =>
                            current.map((value, i) =>
                              i === index ? { ...value, notes: event.target.value } : value,
                            ),
                          )
                        }
                      />
                    </label>
                  </div>
                </details>
              </div>
            );
          })}
          <label className="grid gap-1.5 text-xs font-medium text-ink-secondary">
            <span>Add voice</span>
            <Select
              size="md"
              wrapperClassName="w-full"
              aria-label="Add voice reference"
              value=""
              disabled={disabled || busy || loading || refs.length >= 3 || !!shot.source_audio_path}
              onChange={(event) => {
                const asset = assets.find((item) => item.id === event.target.value);
                if (asset) {
                  void save([
                    ...refs,
                    { asset_id: asset.id, audio_index: refs.length + 1, file_key: "reference", speaker: "", notes: "" },
                  ]);
                }
              }}
            >
              <option value="">{loading ? "Loading voices…" : "Choose a voice"}</option>
              {assets
                .filter(
                  (asset) =>
                    asset.meta.h3_ready &&
                    asset.urls.reference &&
                    !refs.some((ref) => ref.asset_id === asset.id),
                )
                .map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name}
                  </option>
                ))}
            </Select>
          </label>
          {!loading && !assets.length && (
            <small className="text-[11px] text-ink-muted">No voice samples yet. Import one below.</small>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={disabled || busy}
              onClick={() => setImporting(true)}
            >
              Import voice
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Refresh voice library"
              title="Refresh voices"
              disabled={busy || loading}
              onClick={refresh}
            >
              <StudioIcon name="refresh" size={14} />
            </Button>
            {busy && (
              <small role="status" className="text-[11px] text-ink-muted">
                Saving voice changes…
              </small>
            )}
          </div>
          {error && <Banner tone="danger">{error}</Banner>}
        </div>
      )}
      {importing && (
        <AssetImportDialog
          kind="voices"
          projectId={shot.project_id}
          onClose={() => setImporting(false)}
          onImported={() => {
            setImporting(false);
            setRevision((value) => value + 1);
          }}
        />
      )}
    </details>
  );
}
