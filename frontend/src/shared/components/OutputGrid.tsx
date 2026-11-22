import { Download } from "lucide-react";
import type { JobStatus, OutputSlot } from "../api/types";
import { cn } from "../ui/cn";

const DEFAULT_MAIN_KEYS = ["master", "bust_threeview", "fullbody_threeview", "asset_sheet"] as const;

const DEFAULT_LABELS: Record<string, string> = {
  master: "Actor Master",
  bust_threeview: "Bust Three-view",
  fullbody_threeview: "Full-body Three-view",
  asset_sheet: "Asset Sheet",
  wardrobe_ref: "Wardrobe Reference",
};

interface Props {
  status: JobStatus | "idle";
  outputs: Record<string, OutputSlot | null | undefined> | null;
  mainKeys?: readonly string[];
  labels?: Record<string, string>;
  featuredKey?: string | null;
  secondaryKey?: string | null;
  showSecondary?: boolean;
  onOpen: (slot: OutputSlot, all: OutputSlot[]) => void;
}

export function OutputGrid({
  status,
  outputs,
  mainKeys = DEFAULT_MAIN_KEYS,
  labels = DEFAULT_LABELS,
  featuredKey = null,
  secondaryKey = "wardrobe_ref",
  showSecondary = false,
  onOpen,
}: Props) {
  const running = status === "queued" || status === "uploading" || status === "running";

  const mainSlots = mainKeys.map((key) => {
    const slot = outputs?.[key] || null;
    return { key, label: slot?.label || labels[key] || key, slot };
  });

  const featured = featuredKey ? mainSlots.find(({ key }) => key === featuredKey) || null : null;
  const supporting = featured ? mainSlots.filter(({ key }) => key !== featured.key) : mainSlots;
  const secondary = secondaryKey ? outputs?.[secondaryKey] || null : null;
  const openable = (featured ? [featured, ...supporting] : supporting)
    .map(({ slot }) => slot)
    .filter((slot): slot is OutputSlot => !!slot?.url)
    .concat(showSecondary && secondary?.url ? [secondary] : []);
  const dense = supporting.length > 4;

  return (
    <div className="output-grid-wrap flex flex-col gap-3">
      {featured ? (
        <div className="output-featured" role="group" aria-label="Final output">
          <OutputCard
            label={featured.label}
            slot={featured.slot}
            running={running}
            featured
            onOpen={() => featured.slot?.url && onOpen(featured.slot, openable)}
          />
        </div>
      ) : null}

      <div
        className={cn(
          "output-grid grid gap-3",
          dense ? "dense grid-cols-2 sm:grid-cols-3" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
          featured && "supporting",
        )}
        role="group"
        aria-label={featured ? "Supporting outputs" : "Outputs"}
      >
        {supporting.map(({ key, label, slot }) => (
          <OutputCard
            key={key}
            label={label}
            slot={slot}
            running={running}
            onOpen={() => slot?.url && onOpen(slot, openable)}
          />
        ))}
      </div>

      {showSecondary ? (
        <div className="output-secondary" role="group" aria-label="Reference input">
          <OutputCard
            label={secondary?.label || (secondaryKey ? labels[secondaryKey] : "") || secondaryKey || ""}
            slot={secondary}
            running={running}
            compact
            onOpen={() => secondary?.url && onOpen(secondary, openable)}
          />
        </div>
      ) : null}
    </div>
  );
}

function OutputCard({
  label,
  slot,
  running,
  compact,
  featured,
  onOpen,
}: {
  label: string;
  slot: OutputSlot | null | undefined;
  running: boolean;
  compact?: boolean;
  featured?: boolean;
  onOpen: () => void;
}) {
  return (
    <article
      className={cn(
        "output-card flex flex-col overflow-hidden rounded-lg border border-border-subtle bg-panel shadow-[var(--shadow-card)]",
        compact && "compact max-w-[260px]",
        featured && "featured border-border-strong",
      )}
    >
      <div className="output-card-label flex items-center justify-between border-b border-border-subtle bg-elevated/50 px-3 py-1.5 text-[11px] font-medium text-ink-secondary">
        <span className="truncate">{label}</span>
      </div>
      <div
        className={cn(
          "output-card-body relative flex items-center justify-center bg-canvas/50",
          featured ? "min-h-[260px]" : compact ? "min-h-[140px]" : "min-h-[190px]",
        )}
      >
        {slot?.url ? (
          <button
            type="button"
            className="output-thumb group relative flex h-full w-full cursor-pointer items-center justify-center overflow-hidden p-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            onClick={onOpen}
          >
            <img
              src={slot.url}
              alt={label}
              className={cn(
                "max-w-full rounded object-contain transition-transform duration-150 group-hover:scale-[1.01]",
                featured ? "max-h-[340px]" : compact ? "max-h-[160px]" : "max-h-[220px]",
              )}
            />
          </button>
        ) : running ? (
          <div className="output-skeleton flex h-full min-h-[140px] w-full items-center justify-center p-3">
            <div className="pulse h-full w-full animate-pulse rounded bg-elevated" />
          </div>
        ) : (
          <div className="output-empty text-xs text-ink-muted">—</div>
        )}
      </div>
      {slot?.url ? (
        <div className="output-card-actions flex items-center justify-end border-t border-border-subtle px-2.5 py-1.5">
          <a
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-ink-secondary transition-colors hover:bg-hover hover:text-ink"
            href={slot.url}
            download={slot.filename || undefined}
          >
            <Download className="h-3 w-3" aria-hidden="true" />
            Download
          </a>
        </div>
      ) : null}
    </article>
  );
}
