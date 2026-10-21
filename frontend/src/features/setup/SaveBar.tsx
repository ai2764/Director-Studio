import { useEffect, useState } from "react";
import { getSetupState, restartApp, waitForRestart } from "./api";
import { useI18n } from "./i18n";
import type { PutResult, SetupState } from "./types";

/** Restart Harness + backend (+ managed ComfyUI/Ollama) through the DS launcher, then reload. */
export function RestartAppButton({ variant = "primary" }: { variant?: "primary" | "secondary" }) {
  const { t } = useI18n();
  const [state, setState] = useState<SetupState | null>(null);
  const [phase, setPhase] = useState<"idle" | "restarting" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getSetupState().then(setState).catch(() => undefined);
  }, []);
  if (!state) return null;
  if (!state.supervisor_available) return <div>{t("restartHint")}</div>;
  return (
    <div className="ds-restart-row">
      <button type="button" className={`btn ${variant} sm`} disabled={phase === "restarting"} onClick={async () => {
        setPhase("restarting");
        setError(null);
        try {
          await restartApp();
          await waitForRestart(state.boot_id ?? "");
          window.location.reload();
        } catch (err) {
          setPhase("error");
          const message = err instanceof Error ? err.message : String(err);
          setError(message.includes("generation job") ? t("restartBusy") : message);
        }
      }}>{phase === "restarting" ? t("restarting") : t("restartNow")}</button>
      {phase === "error" ? <span className="ds-error">{t("restartFailed")}: {error}</span> : null}
    </div>
  );
}

export function RestartBanner({ keys }: { keys: string[] }) {
  const { t } = useI18n();
  if (!keys.length) return null;
  return (
    <div className="ds-banner ds-banner-restart" role="status">
      <strong>⟳ {t("restartNotice")}</strong> <code>{keys.join(", ")}</code>
      <RestartAppButton />
    </div>
  );
}

export function SaveBar({ dirty, saving, error, result, onSave, onDiscard }: {
  dirty: number;
  saving: boolean;
  error: string | null;
  result: PutResult | null;
  onSave: () => void;
  onDiscard: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="ds-savebar">
      {dirty ? <span className="ds-dirty">{dirty} {t("unsaved")}</span> : result ? (
        <span className="ds-test-ok">✓ {t("saved")}{result.applied.length ? ` · ${t("appliedList")}: ${result.applied.length}` : ""}</span>
      ) : null}
      {result?.warnings?.length ? <span className="ds-warn">{result.warnings.join("; ")}</span> : null}
      {error ? <span className="ds-error">{error}</span> : null}
      <span className="ds-spacer" />
      <button type="button" className="btn ghost sm" disabled={!dirty || saving} onClick={onDiscard}>{t("discard")}</button>
      <button type="button" className="btn primary" disabled={!dirty || saving} onClick={onSave}>
        {saving ? t("saving") : t("save")}
      </button>
    </div>
  );
}
