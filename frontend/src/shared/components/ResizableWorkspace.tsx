import { useCallback, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { cn } from "../ui/cn";

const DEFAULT_SIZE = 55;
const MIN_SIZE = 34;
const MAX_SIZE = 66;

function clamp(value: number, minSize: number, maxSize: number) {
  return Math.min(maxSize, Math.max(minSize, Math.round(value)));
}

function initialSize(storageKey: string, defaultSize: number, minSize: number, maxSize: number) {
  try {
    const saved = Number(localStorage.getItem(storageKey));
    return Number.isFinite(saved) && saved >= minSize && saved <= maxSize
      ? saved
      : defaultSize;
  } catch {
    return defaultSize;
  }
}

export function ResizableWorkspace({
  primary,
  secondary,
  storageKey,
  separatorLabel,
  className = "",
  defaultSize = DEFAULT_SIZE,
  minSize = MIN_SIZE,
  maxSize = MAX_SIZE,
}: {
  primary: ReactNode;
  secondary: ReactNode;
  storageKey: string;
  separatorLabel: string;
  className?: string;
  defaultSize?: number;
  minSize?: number;
  maxSize?: number;
}) {
  const [size, setSize] = useState(() => initialSize(storageKey, defaultSize, minSize, maxSize));
  const [dragging, setDragging] = useState(false);

  const updateSize = useCallback(
    (next: number) => {
      const clamped = clamp(next, minSize, maxSize);
      setSize(clamped);
      try {
        localStorage.setItem(storageKey, String(clamped));
      } catch {
        /* local persistence is optional */
      }
    },
    [maxSize, minSize, storageKey],
  );

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const split = event.currentTarget.parentElement;
    if (!split) return;
    setDragging(true);
    const onMove = (moveEvent: PointerEvent) => {
      const bounds = split.getBoundingClientRect();
      if (!bounds.width) return;
      updateSize(((moveEvent.clientX - bounds.left) / bounds.width) * 100);
    };
    const onUp = () => {
      setDragging(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };

  return (
    <div
      className={cn("resizable-workspace grid min-h-0 w-full min-w-0 flex-1", className)}
      style={{ gridTemplateColumns: `${size}fr 10px ${100 - size}fr` }}
    >
      <div className="resizable-pane primary-pane min-h-0 min-w-0">{primary}</div>
      <div
        className="resize-separator group relative flex cursor-col-resize touch-none items-center justify-center select-none focus-visible:outline-none"
        role="separator"
        aria-label={separatorLabel}
        aria-orientation="vertical"
        aria-valuemin={minSize}
        aria-valuemax={maxSize}
        aria-valuenow={size}
        tabIndex={0}
        onPointerDown={startDrag}
        onDoubleClick={() => updateSize(defaultSize)}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            updateSize(size - 2);
          } else if (event.key === "ArrowRight") {
            event.preventDefault();
            updateSize(size + 2);
          } else if (event.key === "Home") {
            event.preventDefault();
            updateSize(minSize);
          } else if (event.key === "End") {
            event.preventDefault();
            updateSize(maxSize);
          }
        }}
      >
        <span
          aria-hidden="true"
          className={cn(
            "h-12 w-[3px] rounded-full transition-all duration-150",
            dragging
              ? "h-20 bg-accent opacity-100"
              : "bg-border-strong opacity-0 group-hover:bg-accent/80 group-hover:opacity-100 group-focus-visible:bg-accent group-focus-visible:opacity-100",
          )}
        />
      </div>
      <div className="resizable-pane secondary-pane min-h-0 min-w-0">{secondary}</div>
    </div>
  );
}
