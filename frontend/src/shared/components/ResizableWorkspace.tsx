import { useCallback, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

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

  const updateSize = useCallback((next: number) => {
    const clamped = clamp(next, minSize, maxSize);
    setSize(clamped);
    try {
      localStorage.setItem(storageKey, String(clamped));
    } catch {
      /* local persistence is optional */
    }
  }, [maxSize, minSize, storageKey]);

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const split = event.currentTarget.parentElement;
    if (!split) return;
    const onMove = (moveEvent: PointerEvent) => {
      const bounds = split.getBoundingClientRect();
      if (!bounds.width) return;
      updateSize(((moveEvent.clientX - bounds.left) / bounds.width) * 100);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };

  return (
    <div
      className={`resizable-workspace ${className}`.trim()}
      style={{ gridTemplateColumns: `${size}fr 10px ${100 - size}fr` }}
    >
      <div className="resizable-pane primary-pane">{primary}</div>
      <div
        className="resize-separator"
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
        <span aria-hidden="true" />
      </div>
      <div className="resizable-pane secondary-pane">{secondary}</div>
    </div>
  );
}
