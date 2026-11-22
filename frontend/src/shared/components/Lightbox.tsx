import { useEffect } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import type { OutputSlot } from "../api/types";
import { Button } from "../ui/button";

interface Props {
  slots: OutputSlot[];
  index: number;
  onClose: () => void;
  onIndex: (i: number) => void;
}

export function Lightbox({ slots, index, onClose, onIndex }: Props) {
  const slot = slots[index];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") onIndex((index + 1) % slots.length);
      if (e.key === "ArrowLeft") onIndex((index - 1 + slots.length) % slots.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, onClose, onIndex, slots.length]);

  if (!slot?.url) return null;

  return (
    <div
      className="lightbox fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={slot.label || "Image preview"}
    >
      <div
        className="lightbox-inner flex max-h-[92vh] max-w-[92vw] flex-col overflow-hidden rounded-xl border border-border-subtle bg-panel text-ink shadow-[var(--shadow-popover)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lightbox-top flex items-center justify-between gap-4 border-b border-border-subtle px-4 py-2.5">
          <span className="truncate text-xs font-medium text-ink">{slot.label}</span>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            Close
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center bg-canvas/70 p-3">
          <img
            src={slot.url}
            alt={slot.label}
            className="max-h-[76vh] max-w-full rounded object-contain"
          />
        </div>
        {slots.length > 1 ? (
          <div className="lightbox-nav flex items-center justify-between gap-3 border-t border-border-subtle px-4 py-2.5">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => onIndex((index - 1 + slots.length) % slots.length)}
            >
              <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
              Prev
            </Button>
            <span className="font-mono text-xs text-ink-muted tabular-nums">
              {index + 1} / {slots.length}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => onIndex((index + 1) % slots.length)}
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
