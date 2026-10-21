import { useCallback, useEffect, useState } from "react";
import {
  cancelOllamaPull, deleteOllamaModel, listOllamaModels, listOllamaPulls, pullOllamaModel, selectDirectorModel,
  testLlmCapabilities,
} from "./api";
import { pick, useI18n } from "./i18n";
import type { CapabilityResult, OllamaModels, PullJob } from "./types";

export function formatBytes(value?: number): string {
  if (!value) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Installed models + pull with progress + delete (bundled or your own Ollama). */
export function OllamaModelsPanel({ stale, onModels }: { stale: boolean; onModels?: (names: string[]) => void }) {
  const { t, lang } = useI18n();
  const [data, setData] = useState<OllamaModels | null>(null);
  const [jobs, setJobs] = useState<PullJob[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const models = await listOllamaModels();
      setData(models);
      onModels?.(models.models.map((m) => m.name));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [onModels]);

  useEffect(() => {
    void refresh();
    void listOllamaPulls().then((r) => setJobs(r.jobs)).catch(() => undefined);
  }, [refresh]);

  const running = jobs.some((j) => j.state === "running");
  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const r = await listOllamaPulls();
        setJobs(r.jobs);
        if (!r.jobs.some((j) => j.state === "running")) void refresh();
      } catch {
        /* ignore */
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [running, refresh]);

  const startPull = async (model: string) => {
    setError(null);
    try {
      const job = await pullOllamaModel(model.trim());
      setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
      setName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (stale) return <div className="ds-field-help">{t("ollamaSaveFirst")}</div>;
  return (
    <section className="ds-ollama" aria-label={t("ollamaModels")}>
      <div className="ds-doctor-head">
        <strong>{t("ollamaModels")}</strong>
        {data ? (
          <span className={`ds-pill ds-state-${data.reachable ? "ready" : "failed"}`}>
            {data.runtime.bundled_active ? t("llmBundled") : t("llmSystem")}
            {data.version ? ` ${data.version}` : ""} · {data.base_url}
          </span>
        ) : null}
        <button type="button" className="btn ghost sm" onClick={() => void refresh()}>↻</button>
      </div>
      {data?.runtime.bundled_active ? (
        <div className="ds-kv"><span>{t("modelsDir")}</span><code>{data.runtime.models_dir}</code></div>
      ) : null}
      {data && !data.reachable ? <div className="ds-error">{t("ollamaUnreachable")}{data.error ? `: ${data.error}` : ""}</div> : null}
      {data?.reachable ? (
        data.models.length ? (
          <table className="ds-table">
            <tbody>
              {data.models.map((m) => (
                <tr key={m.name}>
                  <td><code>{m.name}</code>{m.loaded ? <span className="ds-pill ds-state-ready">{t("ollamaLoaded")}</span> : null}</td>
                  <td>{[m.parameter_size, m.quantization, formatBytes(m.size)].filter(Boolean).join(" · ")}</td>
                  <td className="ds-actions">
                    <button type="button" className="btn ghost sm" onClick={async () => {
                      await selectDirectorModel(m.name);
                      setSelected(m.name);
                    }}>{selected === m.name ? "✓ " : ""}{t("useModel")}</button>
                    <button type="button" className="btn ghost sm" onClick={async () => {
                      if (!window.confirm(`${t("ollamaDeleteConfirm")} ${m.name}`)) return;
                      try {
                        await deleteOllamaModel(m.name);
                        await refresh();
                      } catch (err) {
                        setError(err instanceof Error ? err.message : String(err));
                      }
                    }}>{t("ollamaDelete")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="ds-warn">{t("ollamaNone")}</div>
      ) : null}
      <form className="ds-pull" onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) void startPull(name);
      }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("ollamaPullPlaceholder")}
          aria-label={t("ollamaPullPlaceholder")} />
        <button type="submit" className="btn secondary sm" disabled={!name.trim()}>{t("ollamaPull")}</button>
      </form>
      {data?.suggested?.length ? (
        <div className="ds-suggest">
          {t("ollamaSuggested")}{" "}
          {data.suggested.map((s) => (
            <button key={s.name} type="button" className="ds-chip" title={s.note} onClick={() => setName(s.name)}>
              {s.name} <small>{s.size}</small>
            </button>
          ))}
        </div>
      ) : null}
      <div className="ds-field-help">{t("ollamaRegistryNote")}</div>
      {jobs.slice(0, 4).map((job) => (
        <div key={job.id} className={`ds-pull-job ds-job-${job.state}`}>
          <div className="ds-pull-row">
            <code>{job.name}</code>
            <span>{job.state === "running" ? job.status : job.state}</span>
            {job.percent !== null && job.state === "running" ? <span>{job.percent}% · {formatBytes(job.completed)} / {formatBytes(job.total)}</span> : null}
            {job.state === "running" ? (
              <button type="button" className="btn ghost sm" onClick={async () => {
                const v = await cancelOllamaPull(job.id);
                setJobs((prev) => prev.map((j) => (j.id === v.id ? v : j)));
              }}>{t("cancel")}</button>
            ) : null}
          </div>
          {job.state === "running" ? (
            <progress max={100} value={job.percent ?? undefined} aria-label={`${job.name} progress`} />
          ) : null}
          {job.error ? <div className="ds-error">{job.error}</div> : null}
          {job.hint ? <div className="ds-field-help">{pick(job.hint, lang)}</div> : null}
        </div>
      ))}
      {error ? <div className="ds-error">{error}</div> : null}
    </section>
  );
}

function Verdict({ value }: { value: boolean | null | undefined }) {
  const { t } = useI18n();
  if (value === true) return <span className="ds-test-ok">✓ {t("capYes")}</span>;
  if (value === false) return <span className="ds-test-fail">✗ {t("capNo")}</span>;
  return <span className="ds-warn">? {t("capUnknown")}</span>;
}

/** Cheap probes: forced tool call + 16x16 red image. */
export function CapabilityTest({ draft, models }: { draft: Record<string, unknown>; models: string[] }) {
  const { t } = useI18n();
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CapabilityResult | null>(null);
  useEffect(() => {
    if (!model && models.length) setModel(models[0]);
  }, [models, model]);
  return (
    <section className="ds-caps" aria-label={t("capTitle")}>
      <div className="ds-doctor-head"><strong>{t("capTitle")}</strong></div>
      <div className="ds-pull">
        <input list="ds-cap-models" value={model} onChange={(e) => setModel(e.target.value)}
          placeholder={t("capPickModel")} aria-label={t("capModel")} />
        <datalist id="ds-cap-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
        <button type="button" className="btn secondary sm" disabled={busy || !model.trim()} onClick={async () => {
          setBusy(true);
          setResult(null);
          try {
            setResult(await testLlmCapabilities({ ...draft, model: model.trim() }));
          } catch (err) {
            setResult({ ok: false, error: err instanceof Error ? err.message : String(err) });
          } finally {
            setBusy(false);
          }
        }}>{busy ? t("testing") : t("capRun")}</button>
      </div>
      {result && !result.ok ? <div className="ds-error">{result.error}</div> : null}
      {result?.ok ? (
        <>
          <table className="ds-table">
            <tbody>
              <tr><td>{t("capTools")}</td><td><Verdict value={result.tools?.supported} /></td>
                <td><small>{result.tools?.detail} · {result.tools?.latency_ms} ms</small></td></tr>
              <tr><td>{t("capVision")}</td><td><Verdict value={result.vision?.supported} /></td>
                <td><small>{result.vision?.detail}</small></td></tr>
            </tbody>
          </table>
          {result.declared ? <div className="ds-field-help">Ollama capabilities: {result.declared.join(", ")}</div> : null}
          {result.warnings?.includes("harness_requires_tools")
            ? <div className="ds-banner ds-banner-warn" role="alert">{t("capHarnessWarn")}</div>
            : <div className="ds-test-ok">{t("capHarnessOk")}</div>}
          {result.warnings?.includes("no_vision") ? <div className="ds-field-help">{t("capVisionWarn")}</div> : null}
        </>
      ) : null}
    </section>
  );
}
