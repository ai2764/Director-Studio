import { useEffect, useState } from "react";
import { completeSetup, getSetupState } from "./api";
import { useI18n } from "./i18n";
import { SetupWizard } from "./SetupWizard";
import { requestSettingsTab } from "../settings/navigation";
import type { SetupState } from "./types";
import "./setup.css";

/** First-run wizard / legacy-.env banner. Desktop only; hidden when config is not admin-accessible. */
export function SetupGate({ forceOpen, onClosed }: { forceOpen?: boolean; onClosed?: () => void }) {
  const { t } = useI18n();
  const [state, setState] = useState<SetupState | null>(null);
  const [open, setOpen] = useState(false);
  const [bannerHidden, setBannerHidden] = useState(false);
  useEffect(() => {
    let alive = true;
    getSetupState()
      .then((s) => {
        if (!alive) return;
        setState(s);
        if (s.admin_allowed && !s.completed && !s.legacy_env_detected) setOpen(true);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    if (forceOpen) setOpen(true);
  }, [forceOpen]);
  const close = () => {
    setOpen(false);
    onClosed?.();
  };
  return (
    <>
      {state && state.admin_allowed && !state.completed && state.legacy_env_detected && !bannerHidden && !open ? (
        <div className="ds-legacy-banner" role="status">
          <span>{t("legacyBanner")}</span>
          <button type="button" className="btn secondary sm" onClick={() => setOpen(true)}>{t("openWizard")}</button>
          <button type="button" className="btn ghost sm" onClick={() => setBannerHidden(true)}>{t("dismiss")}</button>
        </div>
      ) : null}
      {open ? (
        <SetupWizard
          onOpenDeps={() => {
            close();
            requestSettingsTab("deps");
          }}
          onDone={() => {
            setState((s) => (s ? { ...s, completed: true } : s));
            close();
          }}
          onSkip={() => {
            // persist the skip so the wizard does not reappear after a restart;
            // Settings → Overview → "Run setup wizard" reopens it
            if (state && !state.completed) {
              completeSetup(true).then(setState).catch(() => undefined);
            }
            close();
          }}
        />
      ) : null}
    </>
  );
}
