import { useEffect } from "react";
import type { OutputSlot } from "../api/types";

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
    <div className="lightbox" onClick={onClose} role="dialog" aria-modal="true">
      <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
        <div className="lightbox-top">
          <span>{slot.label}</span>
          <button type="button" className="btn ghost sm" onClick={onClose}>
            Close
          </button>
        </div>
        <img src={slot.url} alt={slot.label} />
        {slots.length > 1 ? (
          <div className="lightbox-nav">
            <button type="button" className="btn secondary" onClick={() => onIndex((index - 1 + slots.length) % slots.length)}>
              ← Prev
            </button>
            <span className="muted">
              {index + 1} / {slots.length}
            </span>
            <button type="button" className="btn secondary" onClick={() => onIndex((index + 1) % slots.length)}>
              Next →
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
