import { useCallback, useEffect, useState } from "react";
import { requestSettingsTab } from "../settings/navigation";
import { getDoctor } from "./api";
import { pick, useI18n } from "./i18n";
import type { DoctorCheck, DoctorReport } from "./types";

const ICON: Record<DoctorCheck["status"], string> = { ok: "✓", warn: "!", fail: "✗", skip: "–" };

export function DoctorPanel({ categories, autoRun = true, refreshKey = 0, onOpenDeps }: {
  categories?: string[];
  autoRun?: boolean;
  refreshKey?: number;
  /** Open Settings → Workflow dependencies (defaults to a global settings-navigation request). */
  onOpenDeps?: () => void;
}) {
  const { lang, t } = useI18n();
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      setReport(await getDoctor());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }, []);
  useEffect(() => {
    if (autoRun) void run();
  }, [autoRun, run, refreshKey]);
  const checks = (report?.checks ?? []).filter((c) => !categories || categories.includes(c.category));
  const missingDeps = checks.some((c) => (c.missing_nodes?.length ?? 0) + (c.missing_models?.length ?? 0) > 0);
  const openDeps = onOpenDeps ?? (() => requestSettingsTab("deps"));
  return (
    <section className="ds-doctor" aria-label={t("doctor")}>
      <div className="ds-doctor-head">
        <strong>{t("doctor")}</strong>
        {report ? (
          <span className="ds-doctor-summary">
            <span className="ds-st-ok">✓ {report.summary.ok}</span>
            <span className="ds-st-warn">! {report.summary.warn}</span>
            <span className="ds-st-fail">✗ {report.summary.fail}</span>
          </span>
        ) : null}
        <button type="button" className="btn secondary sm" onClick={run} disabled={running}>
          {running ? t("doctorRunning") : t("runDoctor")}
        </button>
      </div>
      {error ? <div className="ds-error">{error}</div> : null}
      {missingDeps ? (
        <div className="ds-row ds-doctor-deps">
          <button type="button" className="btn primary sm" onClick={openDeps}>{t("openDeps")}</button>
        </div>
      ) : null}
      <ul className="ds-doctor-list">
        {checks.map((c) => (
          <li key={c.id} className={`ds-check-item ds-st-${c.status}`}>
            <span className="ds-check-icon" aria-hidden>{ICON[c.status]}</span>
            <div className="ds-check-body">
              <div className="ds-check-title">
                {pick(c.title, lang)}
                {c.optional ? <span className="ds-badge">{t("optional")}</span> : null}
              </div>
              {c.detail ? <div className="ds-check-detail">{c.detail}</div> : null}
              {c.missing_nodes && c.missing_nodes.length ? (
                <div className="ds-check-sub">
                  <em>{t("missingNodes")}:</em>
                  <ul>
                    {c.missing_nodes.map((n) => (
                      <li key={n.class_type}>
                        <code>{n.class_type}</code> — {n.repo ? <a href={n.repo} target="_blank" rel="noreferrer">{n.pack}</a> : n.pack}
                        {n.license === null && n.repo ? <span className="ds-badge">{t("noLicense")}</span> : null}: {pick(n.fix, lang)}
                        {n.install_dir && !n.bundled ? <> {t("userNodesDir")}: <code>{n.install_dir}</code></> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {c.missing_models && c.missing_models.length ? (
                <div className="ds-check-sub">
                  <em>{t("missingModels")}:</em>
                  <ul>
                    {c.missing_models.map((m) => (
                      <li key={`${m.folder}/${m.file}`}>
                        <code>{m.file}</code> → {t("putModelAt")} <code>{m.expected_path}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {c.fix && c.status !== "ok" ? <div className="ds-check-fix">→ {pick(c.fix, lang)}</div> : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
