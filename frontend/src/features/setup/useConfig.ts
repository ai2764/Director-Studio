import { useCallback, useEffect, useState } from "react";
import { ApiError, getConfig, putConfig } from "./api";
import type { ConfigResponse, Draft, PutResult } from "./types";

export function useConfigModel(active = true) {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [lastResult, setLastResult] = useState<PutResult | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setConfig(await getConfig());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (active) void reload();
  }, [active, reload]);

  const setField = useCallback((key: string, value: unknown) => {
    setDraft((prev) => {
      const next = { ...prev };
      if (value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });
    setErrors((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const save = useCallback(async (): Promise<PutResult | null> => {
    if (!config || Object.keys(draft).length === 0) return null;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await putConfig(config.revision, draft);
      setLastResult(result);
      setDraft({});
      setErrors({});
      await reload();
      return result;
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields);
        setSaveError(err.message);
        if (err.status === 409) await reload();
      } else {
        setSaveError(String(err));
      }
      return null;
    } finally {
      setSaving(false);
    }
  }, [config, draft, reload]);

  return {
    config, draft, errors, loadError, saving, saveError, lastResult,
    setField, save, reload, discard: () => { setDraft({}); setErrors({}); },
  };
}
