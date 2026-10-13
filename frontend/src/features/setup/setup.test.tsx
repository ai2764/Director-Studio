// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigResponse, DoctorReport } from "./types";

const config: ConfigResponse = {
  revision: "rev1",
  env_file: "/x/.env",
  env_exists: true,
  restart_pending: ["DS_DIRECTOR_AGENT_RUNTIME"],
  comfy_runtime: { mode: "managed", effective_url: "http://127.0.0.1:1", configured_url: "", pending_reconfigure: false, warnings: [], error: null },
  schema: {
    groups: { llm: { zh: "导演大模型", en: "Director LLM" }, comfy: { zh: "ComfyUI 连接", en: "ComfyUI" }, h3: { zh: "H3 视频", en: "H3" }, system: { zh: "系统", en: "System" } },
    fields: [
      { key: "DS_LLM_PROVIDER", group: "llm", tier: "user", apply: "hot", type: "enum", secret: false, options: ["ollama", "openai-compatible"], label: { zh: "大模型服务", en: "LLM provider" }, help: { zh: "", en: "" } },
      { key: "DS_LLM_BASE_URL", group: "llm", tier: "user", apply: "hot", type: "url", secret: false, label: { zh: "地址", en: "URL" }, help: { zh: "", en: "" } },
      { key: "DS_OLLAMA_BASE_URL", group: "llm", tier: "user", apply: "hot", type: "url", secret: false, label: { zh: "Ollama 地址", en: "Ollama" }, help: { zh: "", en: "" } },
      { key: "DS_LLM_API_KEY", group: "llm", tier: "user", apply: "hot", type: "str", secret: true, label: { zh: "API Key", en: "API key" }, help: { zh: "", en: "" } },
      { key: "DS_LLM_EXTRA_HEADERS", group: "llm", tier: "user", apply: "hot", type: "str", secret: true, label: { zh: "额外请求头", en: "Headers" }, help: { zh: "", en: "" } },
      { key: "DS_DIRECTOR_AGENT_RUNTIME", group: "agent", tier: "user", apply: "restart", type: "enum", secret: false, options: ["harness", "legacy"], label: { zh: "运行时", en: "Runtime" }, help: { zh: "", en: "" } },
      { key: "DS_PORT", group: "system", tier: "advanced", apply: "restart", type: "int", secret: false, label: { zh: "端口", en: "Port" }, help: { zh: "", en: "" } },
    ],
  },
  fields: [
    { key: "DS_LLM_PROVIDER", source: "file", locked: false, pending_restart: false, value: "openai-compatible", default: "ollama" },
    { key: "DS_LLM_BASE_URL", source: "file", locked: false, pending_restart: false, value: "http://h/v1", default: "" },
    { key: "DS_OLLAMA_BASE_URL", source: "default", locked: false, pending_restart: false, value: "http://127.0.0.1:11434", default: "" },
    { key: "DS_LLM_API_KEY", source: "file", locked: false, pending_restart: false, secret: { set: true, hint: "…AB12" } },
    { key: "DS_LLM_EXTRA_HEADERS", source: "default", locked: false, pending_restart: false, secret: { set: false } },
    { key: "DS_DIRECTOR_AGENT_RUNTIME", source: "env", locked: true, pending_restart: true, value: "harness", default: "legacy" },
    { key: "DS_PORT", source: "env", locked: true, pending_restart: false, value: 8890, default: 8790 },
  ],
};

const doctor: DoctorReport = {
  generated_at: "", models_dir: "/m", comfy: { mode: "managed", url: "", reachable: true },
  summary: { ok: 1, warn: 0, fail: 1, skip: 0 },
  checks: [
    { id: "tool.ffmpeg", category: "system", status: "ok", title: { zh: "ffmpeg 可用", en: "ffmpeg available" } },
    { id: "workflow.x", category: "workflow", status: "fail", title: { zh: "场景：缺少依赖", en: "Scenes: missing" },
      fix: { zh: "放入模型", en: "place models" },
      missing_models: [{ file: "vae.safetensors", folder: "vae", loader: "VAELoader", expected_path: "/m/vae/vae.safetensors" }],
      missing_nodes: [{ class_type: "CR Prompt List", pack: "ds_nodes", fix: { zh: "复制 ds_nodes", en: "copy ds_nodes" } },
        { class_type: "H3MemoryOptimization", pack: "H3-Optimizations", fix: { zh: "手动安装", en: "install manually" },
          repo: "https://github.com/Zironic/H3-Optimizations", license: null, bundled: false,
          install_dir: "/data/comfy/custom_nodes" }] },
  ],
};

const ollamaModels = {
  base_url: "http://127.0.0.1:40123", reachable: true, version: "0.40.1", error: null,
  runtime: { mode: "bundled", bundled_active: true, effective_url: "http://127.0.0.1:40123", configured_url: "", binary: "/r/ollama", available: true, models_dir: "/d/ollama/models", version: "0.40.1", error: null },
  models: [{ name: "qwen-tiny:latest", size: 397_000_000, parameter_size: "494M", quantization: "Q4_K_M", loaded: false }],
  suggested: [{ name: "qwen3:8b", size: "5.2 GB", note: "tools" }],
};

const api = vi.hoisted(() => ({
  getConfig: vi.fn(),
  putConfig: vi.fn(),
  getDoctor: vi.fn(),
  getSetupState: vi.fn(),
  completeSetup: vi.fn(),
  getComfyRuntime: vi.fn(),
  restartComfy: vi.fn(),
  getLaunchPreview: vi.fn(),
  getSystemInfo: vi.fn(),
  testLlm: vi.fn(),
  testComfy: vi.fn(),
  testMinimax: vi.fn(),
  selectDirectorModel: vi.fn(),
  testLlmCapabilities: vi.fn(),
  restartApp: vi.fn(),
  waitForRestart: vi.fn(),
  listOllamaModels: vi.fn(),
  pullOllamaModel: vi.fn(),
  listOllamaPulls: vi.fn(),
  cancelOllamaPull: vi.fn(),
  deleteOllamaModel: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("./api", () => api);
vi.mock("../settings/H3WorkflowSetup", () => ({ H3WorkflowSetup: () => <div>H3 setup</div> }));

import { SettingsPage } from "../settings/SettingsPage";
import { SetupWizard } from "./SetupWizard";
import { SetupGate } from "./SetupGate";
import { setLang } from "./i18n";

beforeEach(() => {
  setLang("zh");
  api.getConfig.mockResolvedValue(config);
  api.getDoctor.mockResolvedValue(doctor);
  api.getComfyRuntime.mockResolvedValue(config.comfy_runtime);
  api.getSystemInfo.mockResolvedValue({ version: "0.2.0" });
  api.putConfig.mockResolvedValue({ revision: "rev2", applied: ["DS_LLM_BASE_URL"], restart_required: [], comfy: null, warnings: [] });
  api.completeSetup.mockResolvedValue({ completed: true });
  api.testLlm.mockResolvedValue({ ok: true, models: ["m1", "m2"], latency_ms: 5 });
  api.getSetupState.mockResolvedValue({ completed: true, legacy_env_detected: false, admin_allowed: true, supervisor_available: true, boot_id: "boot1" });
  api.listOllamaModels.mockResolvedValue(ollamaModels);
  api.listOllamaPulls.mockResolvedValue({ jobs: [] });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsPage", () => {
  it("renders tabs, masks secrets and locks env keys", async () => {
    render(<SettingsPage />);
    expect(await screen.findByText("ffmpeg 可用")).toBeTruthy();
    expect(screen.getByText("/m/vae/vae.safetensors")).toBeTruthy();
    expect(screen.getByText("CR Prompt List")).toBeTruthy();
    const packLink = screen.getByRole("link", { name: "H3-Optimizations" }) as HTMLAnchorElement;
    expect(packLink.href).toBe("https://github.com/Zironic/H3-Optimizations");
    expect(screen.getByText("无许可证·未内置")).toBeTruthy();
    expect(screen.getByText("/data/comfy/custom_nodes")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "大模型" }));
    expect(await screen.findByText(/已保存 …AB12/)).toBeTruthy();
    expect(document.body.innerHTML).not.toContain("sk-");
    const runtime = screen.getByLabelText("DS_DIRECTOR_AGENT_RUNTIME") as HTMLSelectElement;
    expect(runtime.disabled).toBe(true);
    expect(screen.getByText(/DS_DIRECTOR_AGENT_RUNTIME/, { selector: "code" })).toBeTruthy();
  });

  it("saves only changed fields with the revision", async () => {
    render(<SettingsPage initialTab="llm" />);
    const input = (await screen.findByLabelText("DS_LLM_BASE_URL")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "http://new/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));
    await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith("rev1", { DS_LLM_BASE_URL: "http://new/v1" }));
  });

  it("switches language to English", async () => {
    render(<SettingsPage initialTab="system" />);
    const select = (await screen.findByLabelText("界面语言")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "en" } });
    expect(await screen.findByRole("tab", { name: "Advanced" })).toBeTruthy();
  });
});

describe("SetupWizard", () => {
  it("doctor links missing models/nodes to the workflow dependency downloader", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: "x", json: async () => ({}) }));
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "缺少模型/节点？去下载" }));
    expect(screen.getByRole("tab", { name: "工作流依赖" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText(/由你本人从原作者处/)).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("wizard doctor step offers the downloader", async () => {
    const onOpenDeps = vi.fn();
    render(<SetupWizard onDone={vi.fn()} onOpenDeps={onOpenDeps} initialStep={3} />);
    fireEvent.click(await screen.findByRole("button", { name: "缺少模型/节点？去下载" }));
    expect(onOpenDeps).toHaveBeenCalled();
  });

  it("walks through the five steps and completes", async () => {
    const onDone = vi.fn();
    render(<SetupWizard onDone={onDone} />);
    expect(await screen.findByText("快速自检")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));
    await waitFor(() => expect(api.testLlm).toHaveBeenCalled());
    expect(api.testLlm.mock.calls[0][0].api_key).toEqual({ secret: "keep" });
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(await screen.findByRole("radiogroup", { name: "ComfyUI mode" })).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: /远程 ComfyUI/ }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(await screen.findByText("应用当前配置并体检")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect((await screen.findAllByText("DS_COMFY_MODE")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(api.putConfig).toHaveBeenCalledWith("rev1", { DS_COMFY_MODE: "remote" });
    expect(api.completeSetup).toHaveBeenCalled();
  });
});

describe("batch 2: LLM sources, Ollama models, capabilities, restart", () => {
  const ollamaConfig = (): ConfigResponse => ({
    ...config,
    schema: {
      ...config.schema,
      fields: [
        ...config.schema.fields.map((f) => (f.key === "DS_LLM_PROVIDER" ? { ...f, options: ["ollama", "lm-studio", "llama-swap", "openai-compatible"] } : f)),
        { key: "DS_OLLAMA_MODE", group: "llm", tier: "user", apply: "hot", type: "enum", secret: false, options: ["system", "bundled"], label: { zh: "Ollama", en: "Ollama" }, help: { zh: "", en: "" } },
      ],
    },
    fields: [
      ...config.fields.map((f) => (f.key === "DS_LLM_PROVIDER" ? { ...f, value: "ollama" } : f)),
      { key: "DS_OLLAMA_MODE", source: "file", locked: false, pending_restart: false, value: "bundled", default: "system" },
    ],
  });

  const llamaConfig = (mode: string): ConfigResponse => ({
    ...config,
    schema: {
      ...config.schema,
      fields: [
        ...config.schema.fields.map((f) => (f.key === "DS_LLM_PROVIDER" ? { ...f, options: ["ollama", "lm-studio", "llama-swap", "openai-compatible"] } : f)),
        { key: "DS_OLLAMA_MODE", group: "llm", tier: "user", apply: "hot", type: "enum", secret: false, options: ["system", "bundled"], label: { zh: "Ollama", en: "Ollama" }, help: { zh: "", en: "" } },
        { key: "DS_LLAMA_SWAP_MODE", group: "llm", tier: "user", apply: "hot", type: "enum", secret: false, options: ["bundled", "remote"], label: { zh: "llama-swap", en: "llama-swap" }, help: { zh: "", en: "" } },
      ],
    },
    fields: [
      ...config.fields.map((f) => (f.key === "DS_LLM_PROVIDER" ? { ...f, value: "llama-swap" } : f)),
      { key: "DS_OLLAMA_MODE", source: "default", locked: false, pending_restart: false, value: "system", default: "system" },
      { key: "DS_LLAMA_SWAP_MODE", source: "file", locked: false, pending_restart: false, value: mode, default: "remote" },
    ],
  });

  it("defaults to the built-in llama.cpp card first and switches provider + modes", async () => {
    api.getConfig.mockResolvedValue(llamaConfig("bundled"));
    render(<SettingsPage initialTab="llm" />);
    const group = await screen.findByRole("radiogroup", { name: "LLM source" });
    const radios = Array.from(group.querySelectorAll("[role=radio]"));
    expect(radios).toHaveLength(5);
    expect(radios[0].textContent).toMatch(/内置 llama.cpp（llama-swap）/);
    expect(radios[0].getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/导演大模型/)).toBeTruthy(); // where to get GGUF models
    fireEvent.click(screen.getByRole("radio", { name: /外部 Ollama/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));
    await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith("rev1", { DS_LLM_PROVIDER: "ollama", DS_OLLAMA_MODE: "system" }));
  });

  it("my llama-swap card selects remote mode", async () => {
    api.getConfig.mockResolvedValue(llamaConfig("bundled"));
    render(<SettingsPage initialTab="llm" />);
    fireEvent.click(await screen.findByRole("radio", { name: /我的 llama-swap/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));
    await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith("rev1", { DS_LLM_PROVIDER: "llama-swap", DS_LLAMA_SWAP_MODE: "remote" }));
  });

  it("lists bundled Ollama models, pulls with progress and runs the capability test", async () => {
    api.getConfig.mockResolvedValue(ollamaConfig());
    api.pullOllamaModel.mockResolvedValue({ id: "j1", name: "qwen3:8b", state: "running", status: "pulling", total: 100, completed: 40, percent: 40, error: null });
    api.testLlmCapabilities.mockResolvedValue({ ok: true, model: "qwen-tiny:latest", tools: { supported: true, detail: "called ds_probe", latency_ms: 12 },
      vision: { supported: false, detail: "no image input", latency_ms: 3 }, declared: ["completion", "tools"], warnings: ["no_vision"] });
    render(<SettingsPage initialTab="llm" />);
    expect(await screen.findByText("qwen-tiny:latest")).toBeTruthy();
    expect(screen.getByText("/d/ollama/models")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("模型名，例如 qwen3:8b"), { target: { value: "qwen3:8b" } });
    fireEvent.click(screen.getByRole("button", { name: "下载" }));
    expect(await screen.findByLabelText("qwen3:8b progress")).toBeTruthy();
    expect(api.pullOllamaModel).toHaveBeenCalledWith("qwen3:8b");
    fireEvent.click(screen.getByRole("button", { name: "检测工具调用 / 图像理解" }));
    expect(await screen.findByText(/可用于 Harness 模式/)).toBeTruthy();
    expect(api.testLlmCapabilities.mock.calls[0][0].model).toBe("qwen-tiny:latest");
  });

  it("restart button in the system tab restarts through the launcher", async () => {
    api.restartApp.mockResolvedValue({ restarting: true, boot_id: "boot1" });
    api.waitForRestart.mockReturnValue(new Promise(() => undefined));
    render(<SettingsPage initialTab="system" />);
    // one in the restart-pending banner, one in the system tab
    const buttons = await screen.findAllByRole("button", { name: "立即重启" });
    expect(buttons).toHaveLength(2);
    expect(buttons[0].closest(".ds-banner-restart")).toBeTruthy();
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(api.restartApp).toHaveBeenCalled());
    expect(api.waitForRestart).toHaveBeenCalledWith("boot1");
    expect(await screen.findByText(/正在重启/)).toBeTruthy();
  });

  it("falls back to a manual hint without a supervisor", async () => {
    api.getSetupState.mockResolvedValue({ completed: true, legacy_env_detected: false, admin_allowed: true, supervisor_available: false });
    render(<SettingsPage initialTab="system" />);
    expect(await screen.findAllByText(/重新运行 director-studio/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "立即重启" })).toBeNull();
  });
});

describe("batch 3: skip persists, serving port", () => {
  it("skipping the first-run wizard marks setup as skipped", async () => {
    api.getSetupState.mockResolvedValue({ completed: false, legacy_env_detected: false, admin_allowed: true, supervisor_available: true });
    api.completeSetup.mockResolvedValue({ completed: true, skipped: true, legacy_env_detected: false, admin_allowed: true, supervisor_available: true });
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "跳过向导" }));
    await waitFor(() => expect(api.completeSetup).toHaveBeenCalledWith(true));
    expect(screen.queryByText("快速自检")).toBeNull();
  });

  it("does not reopen once completed (or skipped)", async () => {
    api.getSetupState.mockResolvedValue({ completed: true, skipped: true, legacy_env_detected: false, admin_allowed: true, supervisor_available: true });
    render(<SetupGate />);
    await waitFor(() => expect(api.getSetupState).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "跳过向导" })).toBeNull();
  });

  it("system tab shows the real serving address and the CLI override note", async () => {
    api.getSystemInfo.mockResolvedValue({ version: "0.2.0", serving_port: 18890, serving_url: "http://127.0.0.1:18890" });
    render(<SettingsPage initialTab="system" />);
    expect(await screen.findByText("http://127.0.0.1:18890")).toBeTruthy();
    expect(screen.getByText(/端口 18890/)).toBeTruthy();
  });
});
