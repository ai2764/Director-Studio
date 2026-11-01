import { useCallback, useEffect, useState } from "react";

const KEY = "ds.library.changed";
export const LIBRARY_CHANGED = "ds:library-changed";

export function notifyLibraryChanged() {
  window.dispatchEvent(new Event(LIBRARY_CHANGED));
  try { localStorage.setItem(KEY, `${Date.now()}-${Math.random()}`); } catch { /* Same-tab updates still work. */ }
}

export function useLibraryRevision(enabled = true) {
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    if (!enabled) return;
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    const storage = (event: StorageEvent) => { if (event.key === KEY) refresh(); };
    window.addEventListener(LIBRARY_CHANGED, refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener("storage", storage);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener(LIBRARY_CHANGED, refresh);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("storage", storage);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [enabled, refresh]);
  return { revision, refresh };
}
