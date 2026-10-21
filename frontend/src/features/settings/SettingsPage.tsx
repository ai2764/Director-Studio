import { useEffect, useState } from "react";
import { PageShell } from "../../shared/components/PageShell";
import { DepsPanel } from "./DepsPanel";
import { H3WorkflowSetup } from "./H3WorkflowSetup";
import { consumeSettingsTab, OPEN_SETTINGS_EVENT } from "./navigation";
import { getSystemInfo } from "../setup/api";
import { effective, GroupForm, type ConfigModel } from "../setup/ConfigForm";
import { DoctorPanel } from "../setup/DoctorPanel";
import { pick, useI18n, type StringKey } from "../setup/i18n";
import { RestartAppButton, RestartBanner, SaveBar } from "../setup/SaveBar";
import { ComfyRuntimePanel, ComfySection, H3Section, LlmSection } from "../setup/Sections";
import { useConfigModel } from "../setup/useConfig";
import "../setup/setup.css";

export type SettingsTab = "overview" | "llm" | "generation" | "workflows" | "deps" | "advanced" | "system";
const TABS: { id: SettingsTab; label: StringKey }[] = [
  { id: "overview", label: "tabOverview" },
  { id: "llm", label: "tabLlm" },
  { id: "generation", label: "tabGeneration" },
  { id: "workflows", label: "tabWorkflows" },
  { id: "deps", label: "tabDeps" },
  { id: "advanced", label: "tabAdvanced" },
  { id: "system", label: "tabSystem" },
];

function SystemPanel({ model }: { model: ConfigModel }) {
  const { t, lang, setLang } = useI18n();
  const [info, setInfo] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    getSystemInfo().then(setInfo).catch(() => undefined);
  }, []);
  return (
    <div className="ds-section">
      <div className="ds-kv">
        <span>{t("language")}</span>
        <select aria-label={t("language")} value={lang} onChange={(e) => setLang(e.target.value === "en" ? "en" : "zh")}>
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
        <span>{t("version")}</span><code>{String(info?.version ?? "")}</code>
        <span>{t("envFile")}</span><code>{model.config.env_file}</code>
        <span>{t("dataDir")}</span><code>{String(info?.data_dir ?? "")}</code>
        <span>{t("logsDir")}</span><code>{String(info?.logs_dir ?? "")}</code>
        <span>{t("installRoot")}</span><code>{String(info?.install_root ?? "")}</code>
        <span>{t("servingAt")}</span><code>{String(info?.serving_url ?? "")}</code>
      </div>
      <h3>{t("restartApp")}</h3>
      <RestartAppButton variant="secondary" />
      <h3>{pick(model.config.schema.groups.system, lang)}</h3>
      {info?.serving_port && String(info.serving_port) !== String(effective(model, "DS_PORT") ?? "") ? (
        <div className="ds-field-help" role="note">{t("portOverridden").replace("{port}", String(info.serving_port))}</div>
      ) : null}
      <GroupForm model={model} group="system" />
    </div>
  );
}

export function SettingsPage({ active = true, onClose, onRunWizard, initialTab = "overview" }: {
  active?: boolean;
  onClose?: () => void;
  onRunWizard?: () => void;
  initialTab?: SettingsTab;
}) {
  const { t, lang } = useI18n();
  const [tab, setTab] = useState<SettingsTab>(() => (consumeSettingsTab() as SettingsTab | null) ?? initialTab);
  useEffect(() => {
    const onOpen = (event: Event) => {
      const wanted = (event as CustomEvent<{ tab?: string }>).detail?.tab;
      if (wanted && TABS.some((x) => x.id === wanted)) {
        consumeSettingsTab();
        setTab(wanted as SettingsTab);
      }
    };
    window.addEventListener(OPEN_SETTINGS_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, onOpen);
  }, []);
  const state = useConfigModel(active);
  const [refresh, setRefresh] = useState(0);
  const config = state.config;
  const model: ConfigModel | null = config
    ? { config, draft: state.draft, setField: state.setField, errors: state.errors }
    : null;
  const save = async () => {
    const result = await state.save();
    if (result) setRefresh((n) => n + 1);
  };
  return (
    <PageShell
      title={t("settings")}
      subtitle={t(TABS.find((x) => x.id === tab)!.label)}
      className="workflow-settings-page ds-settings-page"
      actions={onClose ? (
        <button type="button" className="settings-close-button" aria-label="Close settings" title={t("close")} onClick={onClose}>×</button>
      ) : null}
    >
      <nav className="ds-tabs" role="tablist" aria-label={t("settings")}>
        {TABS.map((item) => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id}
            className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>
            {t(item.label)}
          </button>
        ))}
      </nav>
      {state.loadError ? <div className="ds-banner ds-error">{state.loadError}</div> : null}
      {config ? <RestartBanner keys={config.restart_pending} /> : null}
      <div className="ds-tab-body">
        {tab === "overview" ? (
          <div className="ds-section">
            <div className="ds-row">
              {onRunWizard ? <button type="button" className="btn secondary" onClick={onRunWizard}>{t("rerunWizard")}</button> : null}
            </div>
            <ComfyRuntimePanel refreshKey={refresh} />
            <DoctorPanel refreshKey={refresh} autoRun={active} onOpenDeps={() => setTab("deps")} />
          </div>
        ) : null}
        {tab === "workflows" ? <H3WorkflowSetup active={active && tab === "workflows"} /> : null}
        {tab === "deps" ? <DepsPanel active={active && tab === "deps"} model={model} /> : null}
        {model && tab === "llm" ? <LlmSection model={model} advanced /> : null}
        {model && tab === "generation" ? (
          <>
            <ComfySection model={model} advanced />
            <ComfyRuntimePanel refreshKey={refresh} />
            <h3>{pick(config!.schema.groups.h3, lang)}</h3>
            <H3Section model={model} advanced />
          </>
        ) : null}
        {model && tab === "advanced" ? (
          <div className="ds-section">
            {Object.keys(config!.schema.groups).filter((g) => g !== "system").map((group) => {
              const has = config!.schema.fields.some((f) => f.group === group && f.tier === "advanced");
              return has ? (
                <details key={group} className="ds-group" open>
                  <summary>{pick(config!.schema.groups[group], lang)}</summary>
                  <GroupForm model={model} group={group} tier="advanced" />
                </details>
              ) : null;
            })}
          </div>
        ) : null}
        {model && tab === "system" ? <SystemPanel model={model} /> : null}
      </div>
      {model && tab !== "overview" && tab !== "workflows" ? (
        <SaveBar dirty={Object.keys(state.draft).length} saving={state.saving} error={state.saveError}
          result={state.lastResult} onSave={save} onDiscard={state.discard} />
      ) : null}
    </PageShell>
  );
}
