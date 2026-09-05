import { useCallback, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

const DEFAULT_SIZE = 55;
const MIN_SIZE = 34;
const MAX_SIZE = 66;

function clamp(value: number) {
  return Math.min(MAX_SIZE, Math.max(MIN_SIZE, Math.round(value)));
}

function initialSize(storageKey: string) {
  try {
    const saved = Number(localStorage.getItem(storageKey));
    return Number.isFinite(saved) && saved >= MIN_SIZE && saved <= MAX_SIZE
      ? saved
      : DEFAULT_SIZE;
  } catch {
    return DEFAULT_SIZE;
  }
}

export function ResizableWorkspace({
  primary,
  secondary,
  storageKey,
  separatorLabel,
  className = "",
}: {
  primary: ReactNode;
  secondary: ReactNode;
  storageKey: string;
  separatorLabel: string;
  className?: string;
}) {
  const [size, setSize] = useState(() => initialSize(storageKey));

  const updateSize = useCallback((next: number) => {
    const clamped = clamp(next);
    setSize(clamped);
    try {
      localStorage.setItem(storageKey, String(clamped));
    } catch {
      /* local persistence is optional */
    }
  }, [storageKey]);

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
        aria-valuemin={MIN_SIZE}
        aria-valuemax={MAX_SIZE}
        aria-valuenow={size}
        tabIndex={0}
        onPointerDown={startDrag}
        onDoubleClick={() => updateSize(DEFAULT_SIZE)}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            updateSize(size - 2);
          } else if (event.key === "ArrowRight") {
            event.preventDefault();
            updateSize(size + 2);
          } else if (event.key === "Home") {
            event.preventDefault();
            updateSize(MIN_SIZE);
          } else if (event.key === "End") {
            event.preventDefault();
            updateSize(MAX_SIZE);
          }
        }}
      >
        <span aria-hidden="true" />
      </div>
      <div className="resizable-pane secondary-pane">{secondary}</div>
    </div>
  );
}
