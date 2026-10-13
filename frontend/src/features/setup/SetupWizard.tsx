import { useState } from "react";
import { completeSetup, selectDirectorModel } from "./api";
import type { ConfigModel } from "./ConfigForm";
import { schemaOf } from "./ConfigForm";
import { DoctorPanel } from "./DoctorPanel";
import { pick, useI18n, type StringKey } from "./i18n";
import { RestartBanner } from "./SaveBar";
import { ComfySection, H3Section, LlmSection } from "./Sections";
import { useConfigModel } from "./useConfig";
import "./setup.css";

const STEPS: StringKey[] = ["stepWelcome", "stepLlm", "stepGen", "stepDoctor", "stepConfirm"];

function describe(value: unknown): string {
  if (value && typeof value === "object" && "secret" in (value as Record<string, unknown>)) {
    const action = (value as { secret: string }).secret;
    return action === "set" ? "••••••" : action;
  }
  return JSON.stringify(value);
}

export function SetupWizard({ onDone, onSkip, initialStep = 0, onOpenDeps }: {
  onDone: () => void;
  /** Leave the wizard for Settings → Workflow dependencies (wizard can be reopened from Settings). */
  onOpenDeps?: () => void;
  onSkip?: () => void;
  initialStep?: number;
}) {
  const { t, lang, setLang } = useI18n();
  const [step, setStep] = useState(initialStep);
  const state = useConfigModel(true);
  const [applied, setApplied] = useState<string[]>([]);
  const [doctorKey, setDoctorKey] = useState(0);
  const [model, setModel] = useState("");
  const [finishing, setFinishing] = useState(false);
  const config = state.config;
  const cm: ConfigModel | null = config ? { config, draft: state.draft, setField: state.setField, errors: state.errors } : null;

  const apply = async () => {
    const result = await state.save();
    if (result) {
      setApplied((prev) => Array.from(new Set([...prev, ...result.applied, ...result.restart_required])));
      setDoctorKey((n) => n + 1);
    }
    return result;
  };
  const finish = async () => {
    setFinishing(true);
    try {
      if (Object.keys(state.draft).length) {
        const result = await apply();
        if (!result) return;
      }
      if (model.trim()) {
        try { await selectDirectorModel(model.trim()); } catch { /* model can be picked later on the Director page */ }
      }
      await completeSetup();
      onDone();
    } finally {
      setFinishing(false);
    }
  };

  return (
    <div className="ds-wizard-backdrop" role="dialog" aria-modal="true" aria-label={t("wizardTitle")}>
      <div className="ds-wizard">
        <header className="ds-wizard-head">
          <h1>{t("wizardTitle")}</h1>
          <select aria-label={t("language")} value={lang} onChange={(e) => setLang(e.target.value === "en" ? "en" : "zh")}>
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
        </header>
        <ol className="ds-steps">
          {STEPS.map((s, i) => (
            <li key={s} className={i === step ? "active" : i < step ? "done" : ""}>
              <span>{i + 1}</span>{t(s)}
            </li>
          ))}
        </ol>
        <div className="ds-wizard-body">
          {state.loadError ? <div className="ds-error">{state.loadError}</div> : null}
          {step === 0 ? (
            <div className="ds-section">
              <p>{t("welcomeIntro")}</p>
              <h3>{t("quickCheck")}</h3>
              <DoctorPanel categories={["system", "agent", "gpu", "comfy"]} />
            </div>
          ) : null}
          {step === 1 && cm ? (
            <>
              <LlmSection model={cm} />
              <div className="ds-field">
                <div className="ds-field-label"><label>{t("directorModel")}</label></div>
                <input type="text" aria-label="director-model" value={model} placeholder="qwen3:32b"
                  onChange={(e) => setModel(e.target.value)} />
                <div className="ds-field-help">{lang === "zh" ? "可先“测试连接”查看模型列表；也可稍后在导演页选择。Ollama 模型请用 `ollama pull` 拉取。" : "Use Test to list models, or pick later on the Director page. Pull Ollama models with `ollama pull`."}</div>
              </div>
            </>
          ) : null}
          {step === 2 && cm ? (
            <>
              <h3>{pick(cm.config.schema.groups.comfy, lang)}</h3>
              <ComfySection model={cm} />
              <h3>{pick(cm.config.schema.groups.h3, lang)}</h3>
              <H3Section model={cm} />
            </>
          ) : null}
          {step === 3 ? (
            <div className="ds-section">
              <p className="ds-field-help">{t("doctorNote")}</p>
              {Object.keys(state.draft).length ? (
                <button type="button" className="btn primary" disabled={state.saving} onClick={() => void apply()}>
                  {state.saving ? t("saving") : t("applyAndCheck")}
                </button>
              ) : null}
              {state.saveError ? <div className="ds-error">{state.saveError}</div> : null}
              <DoctorPanel refreshKey={doctorKey} onOpenDeps={onOpenDeps} />
            </div>
          ) : null}
          {step === 4 && config ? (
            <div className="ds-section">
              <h3>{t("changes")}</h3>
              {Object.keys(state.draft).length ? (
                <table className="ds-diff">
                  <tbody>
                    {Object.entries(state.draft).map(([key, value]) => (
                      <tr key={key}>
                        <td>{pick(schemaOf(config, key)?.label, lang) || key}</td>
                        <td><code>{key}</code></td>
                        <td><code>{describe(value)}</code></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="ds-muted">{t("noChanges")}</p>}
              {applied.length ? <p className="ds-muted">{t("appliedList")}: {applied.join(", ")}</p> : null}
              {model ? <p>{t("directorModel")}: <code>{model}</code></p> : null}
              <RestartBanner keys={config.restart_pending} />
              {state.saveError ? <div className="ds-error">{state.saveError}</div> : null}
            </div>
          ) : null}
        </div>
        <footer className="ds-wizard-foot">
          {onSkip ? <button type="button" className="btn ghost" onClick={onSkip}>{t("skip")}</button> : null}
          <span className="ds-spacer" />
          {step > 0 ? <button type="button" className="btn secondary" onClick={() => setStep(step - 1)}>{t("back")}</button> : null}
          {step < STEPS.length - 1 ? (
            <button type="button" className="btn primary" onClick={() => setStep(step + 1)}>{t("next")}</button>
          ) : (
            <button type="button" className="btn primary" disabled={finishing} onClick={() => void finish()}>{t("finish")}</button>
          )}
        </footer>
      </div>
    </div>
  );
}
