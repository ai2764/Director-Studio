import { useEffect, useState } from "react";
import { getComfyRuntime, getLaunchPreview, restartComfy, testComfy, testLlm, testMinimax } from "./api";
import { effective, FieldList, type ConfigModel } from "./ConfigForm";
import { useI18n } from "./i18n";
import { CapabilityTest, OllamaModelsPanel } from "./LlmTools";
import type { ComfyRuntimeStatus, TestResult } from "./types";

function TestButton({ run, label, onResult }: {
  run: () => Promise<TestResult>;
  label?: string;
  onResult?: (result: TestResult) => void;
}) {
  const { t, lang } = useI18n();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  return (
    <div className="ds-test">
      <button type="button" className="btn secondary sm" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          const value = await run();
          setResult(value);
          onResult?.(value);
        } catch (err) {
          setResult({ ok: false, error: err instanceof Error ? err.message : String(err) });
        } finally {
          setBusy(false);
        }
      }}>
        {busy ? t("testing") : label ?? t("test")}
      </button>
      {result ? (
        <span className={result.ok ? "ds-test-ok" : "ds-test-fail"} role="status">
          {result.ok ? `✓ ${t("testOk")}` : `✗ ${t("testFail")}: ${result.error ?? result.preview?.error ?? ""}`}
          {result.ok && result.system?.comfyui_version ? ` · ComfyUI ${result.system.comfyui_version}` : ""}
          {result.ok && result.system?.devices?.length ? ` · ${result.system.devices.map((d) => d.name).join(", ")}` : ""}
          {result.ok && result.ws_ok !== undefined ? ` · WebSocket ${result.ws_ok ? "✓" : "✗"}` : ""}
          {result.ok && result.models ? ` · ${result.models.length} ${t("models")}` : ""}
          {result.ok && result.latency_ms !== undefined ? ` · ${result.latency_ms} ms` : ""}
          {result.ok && (result.note || result.note_zh) ? ` · ${lang === "zh" ? result.note_zh || result.note : result.note}` : ""}
        </span>
      ) : null}
      {result?.ok && result.models && result.models.length ? (
        <details className="ds-models"><summary>{t("models")}</summary><ul>{result.models.map((m) => <li key={m}><code>{m}</code></li>)}</ul></details>
      ) : null}
      {result?.preview?.args ? <pre className="ds-pre">{result.preview.args.join(" ")}</pre> : null}
    </div>
  );
}

function secretDraft(model: ConfigModel, key: string): unknown {
  return key in model.draft ? model.draft[key] : { secret: "keep" };
}

// Default: the built-in llama.cpp (one llama-swap process DS starts/stops). Ollama is only an
// external URL now; a legacy DS_OLLAMA_MODE=bundled config still shows as "system" (your Ollama).
const LLM_SOURCES = [
  { id: "llama-bundled", provider: "llama-swap", swapMode: "bundled", title: "llmLlamaBuiltin", desc: "llmLlamaBuiltinDesc" },
  { id: "llama-swap", provider: "llama-swap", swapMode: "remote", title: "llmLlamaSwap", desc: "llmLlamaSwapDesc" },
  { id: "system", provider: "ollama", mode: "system", title: "llmSystem", desc: "llmSystemDesc" },
  { id: "lm-studio", provider: "lm-studio", title: "llmLmStudio", desc: "llmLmStudioDesc" },
  { id: "openai-compatible", provider: "openai-compatible", title: "llmOpenAI", desc: "llmOpenAIDesc" },
] as const;

export function llmSourceOf(provider: string, ollamaMode: string, swapMode = "remote"): string {
  if (provider === "llama-swap") return swapMode === "bundled" ? "llama-bundled" : "llama-swap";
  return provider === "ollama" ? (ollamaMode === "bundled" ? "bundled" : "system") : provider;
}

export function LlmSection({ model, advanced = false }: { model: ConfigModel; advanced?: boolean }) {
  const { t } = useI18n();
  const provider = String(effective(model, "DS_LLM_PROVIDER") ?? "llama-swap");
  const ollamaMode = String(effective(model, "DS_OLLAMA_MODE") ?? "system");
  const swapMode = String(effective(model, "DS_LLAMA_SWAP_MODE") ?? "bundled");
  const source = llmSourceOf(provider, ollamaMode, swapMode);
  const locked = model.config.fields.find((f) => f.key === "DS_LLM_PROVIDER")?.locked;
  const [models, setModels] = useState<string[]>([]);
  const keys: string[] = [];
  if (source === "llama-bundled") {
    if (advanced) keys.push("DS_LLAMA_MODELS_DIR", "DS_LLAMA_GPU_LAYERS", "DS_LLAMA_MTP", "DS_LLAMA_SWAP_PORT",
      "DS_LLAMA_SWAP_DIR", "DS_LLAMA_CPP_DIR");
  } else if (source === "bundled") {
    if (advanced) keys.push("DS_OLLAMA_MODELS_DIR", "DS_OLLAMA_DIR", "DS_OLLAMA_PORT");
  } else if (source === "system") {
    keys.push("DS_OLLAMA_BASE_URL", "DS_LLM_EXTRA_HEADERS");
  } else {
    keys.push("DS_LLM_BASE_URL", "DS_LLM_API_KEY", "DS_LLM_EXTRA_HEADERS");
  }
  keys.push("DS_DIRECTOR_AGENT_RUNTIME");
  if (advanced) keys.push("DS_LLM_TIMEOUT_SEC", "DS_DIRECTOR_NUM_CTX", "DS_DIRECTOR_NUM_PREDICT", "DS_LLM_KEEP_LOADED");
  const draft = {
    provider,
    ollama_mode: ollamaMode,
    llama_swap_mode: swapMode,
    base_url: effective(model, "DS_LLM_BASE_URL"),
    ollama_base_url: effective(model, "DS_OLLAMA_BASE_URL"),
    api_key: secretDraft(model, "DS_LLM_API_KEY"),
    extra_headers: secretDraft(model, "DS_LLM_EXTRA_HEADERS"),
    agent_runtime: effective(model, "DS_DIRECTOR_AGENT_RUNTIME"),
  };
  const stale = ["DS_LLM_PROVIDER", "DS_OLLAMA_MODE", "DS_OLLAMA_BASE_URL", "DS_OLLAMA_DIR", "DS_OLLAMA_MODELS_DIR",
    "DS_OLLAMA_PORT", "DS_LLM_EXTRA_HEADERS", "DS_LLAMA_SWAP_MODE"].some((k) => k in model.draft);
  return (
    <div className="ds-section">
      <div className="ds-cards ds-cards-5" role="radiogroup" aria-label="LLM source">
        {LLM_SOURCES.map((c) => (
          <button key={c.id} type="button" role="radio" aria-checked={source === c.id} disabled={locked}
            className={`ds-card${source === c.id ? " selected" : ""}`}
            onClick={() => {
              model.setField("DS_LLM_PROVIDER", c.provider);
              if ("mode" in c) model.setField("DS_OLLAMA_MODE", c.mode);
              if ("swapMode" in c) model.setField("DS_LLAMA_SWAP_MODE", c.swapMode);
            }}>
            <strong>{t(c.title)}</strong>
            <span>{t(c.desc)}</span>
          </button>
        ))}
      </div>
      <FieldList model={model} keys={keys} />
      <TestButton run={() => testLlm(draft)} onResult={(r) => r.ok && r.models && setModels(r.models)} />
      {provider === "openai-compatible" ? <div className="ds-field-help">{t("costNote")}</div> : null}
      {provider === "ollama" ? <OllamaModelsPanel stale={stale} onModels={setModels} /> : null}
      {source === "llama-bundled" ? <div className="ds-field-help">{t("llmLlamaBuiltinModels")}</div> : null}
      <CapabilityTest draft={draft} models={models} />
    </div>
  );
}

const LOCAL_GPU_LLM = ["ollama", "lm-studio", "llama-swap"];
const LLM_DEFAULT_URL: Record<string, string> = {
  "lm-studio": "http://127.0.0.1:1234/v1",
  "llama-swap": "http://127.0.0.1:11435/v1",
};

function hostOf(url: unknown): string {
  try {
    return new URL(String(url ?? "")).hostname.toLowerCase().replace(/^\[|\]$/g, "");
  } catch {
    return "";
  }
}

const isLoopbackHost = (h: string) => ["localhost", "::1", "0.0.0.0", "::"].includes(h) || h.startsWith("127.");

/** Mirror of backend app/core/vram/shared_gpu.py for DS_VRAM_SHARED_GPU=auto (remote mode). */
export function sharedGpuAuto(model: Pick<ConfigModel, "config" | "draft">): boolean {
  if (String(effective(model, "DS_COMFY_MODE") ?? "external-local") !== "remote") return true;
  const provider = String(effective(model, "DS_LLM_PROVIDER") ?? "llama-swap");
  const llmUrl = provider === "ollama"
    ? (effective(model, "DS_OLLAMA_MODE") === "bundled" ? "http://127.0.0.1:11434" : effective(model, "DS_OLLAMA_BASE_URL") || "http://127.0.0.1:11434")
    : provider === "llama-swap" && effective(model, "DS_LLAMA_SWAP_MODE") === "bundled"
      ? "http://127.0.0.1:11435/v1" // built-in llama.cpp runs on this machine
      : effective(model, "DS_LLM_BASE_URL") || LLM_DEFAULT_URL[provider] || "";
  const comfy = hostOf(effective(model, "DS_COMFY_REMOTE_URL"));
  const llm = hostOf(llmUrl);
  if (!comfy || !llm) return false;
  const comfyLo = isLoopbackHost(comfy), llmLo = isLoopbackHost(llm);
  if (comfyLo && llmLo) return true;
  if (comfyLo !== llmLo) return false;
  return comfy === llm;
}

function SharedGpuToggle({ model }: { model: ConfigModel }) {
  const { t } = useI18n();
  const provider = String(effective(model, "DS_LLM_PROVIDER") ?? "ollama");
  if (!LOCAL_GPU_LLM.includes(provider)) return null; // cloud/remote API LLMs never hold local VRAM
  const field = model.config.fields.find((f) => f.key === "DS_VRAM_SHARED_GPU");
  if (!field) return null;
  const value = String(effective(model, "DS_VRAM_SHARED_GPU") ?? "auto");
  const checked = value === "auto" ? sharedGpuAuto(model) : value === "true";
  return (
    <label className="ds-field-check">
      <input type="checkbox" aria-label="DS_VRAM_SHARED_GPU" checked={checked} disabled={field.locked}
        onChange={(e) => model.setField("DS_VRAM_SHARED_GPU", e.target.checked ? "true" : "false")} />
      <span>
        {t("sharedGpu")}{value === "auto" ? ` (${t("sharedGpuAuto")})` : ""}
        <span className="ds-field-help">{t("sharedGpuHelp")}</span>
      </span>
    </label>
  );
}

const MODES = [
  { id: "managed", title: "comfyModeManaged", desc: "comfyModeManagedDesc" },
  { id: "external-local", title: "comfyModeLocal", desc: "comfyModeLocalDesc" },
  { id: "remote", title: "comfyModeRemote", desc: "comfyModeRemoteDesc" },
] as const;

export function ComfySection({ model, showLaunch = true, advanced = false }: {
  model: ConfigModel;
  showLaunch?: boolean;
  advanced?: boolean;
}) {
  const { t } = useI18n();
  const mode = String(effective(model, "DS_COMFY_MODE") ?? "external-local");
  const locked = model.config.fields.find((f) => f.key === "DS_COMFY_MODE")?.locked;
  const keys =
    mode === "managed"
      ? ["DS_COMFY_DIR", "DS_COMFY_MODELS_DIR", ...(advanced ? ["DS_COMFY_PYTHON", "DS_COMFY_PORT", "DS_COMFY_STARTUP_TIMEOUT_SEC"] : [])]
      : mode === "remote"
        ? ["DS_COMFY_REMOTE_URL", "DS_COMFY_REMOTE_AUTH_TYPE", "DS_COMFY_REMOTE_AUTH", "DS_COMFY_REMOTE_VERIFY_TLS",
          ...(advanced ? ["DS_COMFY_PROXY_PORT"] : [])]
        : ["DS_COMFY_BASE_URL"];
  const launchUser = ["DS_COMFY_VRAM_PRESET", "DS_COMFY_RESERVE_VRAM_GB", "DS_COMFY_CUDA_DEVICE", "DS_COMFY_RAM_PRESET",
    "DS_COMFY_PRECISION_PRESET"];
  const launchAdvanced = ["DS_COMFY_ATTENTION", "DS_COMFY_UNET_PRECISION", "DS_COMFY_TEXT_ENC_PRECISION",
    "DS_COMFY_VAE_PRECISION", "DS_COMFY_FAST", "DS_COMFY_PREVIEW_METHOD", "DS_COMFY_TROUBLESHOOT",
    "DS_COMFY_HF_ENDPOINT", "DS_COMFY_HTTP_PROXY", "DS_COMFY_EXTRA_ARGS"];
  return (
    <div className="ds-section">
      <div className="ds-cards" role="radiogroup" aria-label="ComfyUI mode">
        {MODES.map((m) => (
          <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} disabled={locked}
            className={`ds-card${mode === m.id ? " selected" : ""}`}
            onClick={() => model.setField("DS_COMFY_MODE", m.id)}>
            <strong>{t(m.title)}</strong>
            <span>{t(m.desc)}</span>
          </button>
        ))}
      </div>
      <FieldList model={model} keys={keys} />
      {mode === "remote" ? <SharedGpuToggle model={model} /> : null}
      <TestButton run={() => testComfy({
        mode,
        base_url: effective(model, "DS_COMFY_BASE_URL"),
        remote_url: effective(model, "DS_COMFY_REMOTE_URL"),
        auth_type: effective(model, "DS_COMFY_REMOTE_AUTH_TYPE"),
        auth: secretDraft(model, "DS_COMFY_REMOTE_AUTH"),
        verify_tls: effective(model, "DS_COMFY_REMOTE_VERIFY_TLS"),
      })} />
      {showLaunch && mode === "managed" ? (
        <details className="ds-launch" open={advanced}>
          <summary>{t("launchOptions")}</summary>
          <FieldList model={model} keys={advanced ? [...launchUser, ...launchAdvanced] : launchUser} />
        </details>
      ) : null}
    </div>
  );
}

export function H3Section({ model, advanced = false }: { model: ConfigModel; advanced?: boolean }) {
  const { t } = useI18n();
  const provider = String(effective(model, "DS_H3_PROVIDER") ?? "local");
  const keys = provider === "minimax"
    ? ["DS_H3_MINIMAX_API_KEY", "DS_H3_MINIMAX_BASE_URL", "DS_H3_MINIMAX_RESOLUTION"]
    : advanced ? ["DS_H3_BUILTIN_WORKFLOW"] : [];
  return (
    <div className="ds-section">
      <div className="ds-cards" role="radiogroup" aria-label="H3 provider">
        {(["local", "minimax"] as const).map((id) => (
          <button key={id} type="button" role="radio" aria-checked={provider === id}
            className={`ds-card${provider === id ? " selected" : ""}`} onClick={() => model.setField("DS_H3_PROVIDER", id)}>
            <strong>{t(id === "local" ? "h3Local" : "h3Minimax")}</strong>
          </button>
        ))}
      </div>
      <FieldList model={model} keys={keys} />
      {provider === "minimax" ? (
        <>
          <TestButton label={t("minimaxTest")} run={() => testMinimax({
            api_key: secretDraft(model, "DS_H3_MINIMAX_API_KEY"),
            base_url: effective(model, "DS_H3_MINIMAX_BASE_URL"),
          })} />
          <div className="ds-field-help">{t("h3MinimaxNote")}</div>
        </>
      ) : null}
    </div>
  );
}

export function ComfyRuntimePanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<ComfyRuntimeStatus | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => getComfyRuntime(showLog).then((s) => alive && setStatus(s)).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [showLog, refreshKey]);
  if (!status) return null;
  return (
    <section className="ds-runtime" aria-label={t("comfyRuntime")}>
      <div className="ds-doctor-head">
        <strong>{t("comfyRuntime")}</strong>
        <span className={`ds-pill ds-state-${status.managed?.state ?? (status.error ? "failed" : "ready")}`}>
          {status.mode}{status.managed ? ` · ${status.managed.state}` : ""}{status.proxy ? ` · proxy ${status.proxy.running ? "on" : "off"}` : ""}
        </span>
        {status.mode === "managed" ? (
          <>
            <button type="button" className="btn secondary sm" disabled={busy} onClick={async () => {
              setBusy(true);
              try { setStatus(await restartComfy()); } finally { setBusy(false); }
            }}>{t("restartComfy")}</button>
            <button type="button" className="btn ghost sm" onClick={() => setShowLog((v) => !v)}>
              {showLog ? t("hideLog") : t("showLog")}
            </button>
            <button type="button" className="btn ghost sm" onClick={async () => {
              const p = await getLaunchPreview();
              setPreview(p.ok ? (p.args ?? []).join(" ") + (p.warnings?.length ? `\n# ${p.warnings.join("\n# ")}` : "") : p.error ?? "");
            }}>{t("launchPreview")}</button>
          </>
        ) : null}
      </div>
      <div className="ds-kv">
        <span>URL</span><code>{status.effective_url}</code>
        {status.proxy ? (<><span>Remote</span><code>{status.proxy.remote}</code></>) : null}
        {status.models_dir ? (<><span>{t("modelsDir")}</span><code>{status.models_dir}</code></>) : null}
        {status.managed?.pid ? (<><span>PID</span><code>{status.managed.pid}</code></>) : null}
      </div>
      {status.pending_reconfigure ? <div className="ds-warn">{t("comfyDeferred")}</div> : null}
      {status.error || status.managed?.last_error ? <div className="ds-error">{status.error ?? status.managed?.last_error}</div> : null}
      {status.warnings.length ? <div className="ds-warn">{status.warnings.join("; ")}</div> : null}
      {preview ? <pre className="ds-pre">{preview}</pre> : null}
      {showLog && status.log_tail ? <pre className="ds-pre ds-log">{status.log_tail}</pre> : null}
    </section>
  );
}
