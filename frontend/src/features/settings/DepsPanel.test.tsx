// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../setup/i18n";
import type { DepsStatus } from "./depsApi";

const api = vi.hoisted(() => ({
  getDeps: vi.fn(),
  installDeps: vi.fn(),
  installWorkflow: vi.fn(),
  cancelDep: vi.fn(),
  restartComfyForDeps: vi.fn(),
}));
vi.mock("./depsApi", async (orig) => ({ ...(await orig<typeof import("./depsApi")>()), ...api }));

import { DepsPanel } from "./DepsPanel";
import { consumeSettingsTab, requestSettingsTab } from "./navigation";

function status(overrides: Partial<DepsStatus> = {}): DepsStatus {
  return {
    mode: "managed", can_install: true, comfy_reachable: true, hf_endpoint: "https://huggingface.co", hf_token_set: false,
    models_dir: "/d/models", custom_nodes_dir: "/d/comfy/custom_nodes", restart_needed: false, jobs: [],
    workflows: [{
      file: "h3_ref2va_fast4.api.json", zh: "H3 视频（4 步加速）", en: "H3 video (fast 4-step)", ready: false,
      missing_bytes: 2_000_000_000,
      items: [
        { kind: "pack", id: "ComfyUI-KJNodes", name: "ComfyUI-KJNodes", state: "installed", installable: true, license: "GPL-3.0",
          preinstalled: true, size: null, repo: "https://github.com/kijai/ComfyUI-KJNodes" },
        { kind: "pack", id: "H3-Optimizations", name: "H3 Optimizations", state: "missing", installable: true, license: null,
          unlicensed: true, size: null, repo: "https://github.com/Zironic/H3-Optimizations", path: "/d/comfy/custom_nodes/H3-Optimizations" },
        { kind: "model", id: "lora.safetensors", name: "lora.safetensors", state: "missing", installable: true,
          license: "apache-2.0", license_url: "https://hf/lic", size: 2_000_000_000, folder: "loras", page: "https://hf/repo",
          job: { kind: "model", id: "lora.safetensors", state: "downloading", done: 500_000_000, total: 2_000_000_000, error: null, source: "huggingface.co" } },
        { kind: "model", id: "vae.safetensors", name: "vae.safetensors", state: "installed", installable: true, license: "other",
          size: 600_000_000, gated: true, page: "https://hf/gated" },
      ],
    }],
    ...overrides,
  };
}

beforeEach(() => {
  setLang("zh");
  Object.values(api).forEach((fn) => fn.mockReset());
  api.installDeps.mockResolvedValue({ jobs: [] });
  api.installWorkflow.mockResolvedValue({ jobs: [] });
  api.cancelDep.mockResolvedValue({ cancelled: true });
  api.restartComfyForDeps.mockResolvedValue({ result: "applied" });
});
afterEach(cleanup);

describe("DepsPanel", () => {
  it("shows per-workflow items with status, licence tags, progress and the licence notice", async () => {
    api.getDeps.mockResolvedValue(status());
    render(<DepsPanel />);
    await screen.findByText("H3 视频（4 步加速）");
    expect(screen.getAllByText(/由你本人从原作者处/).length).toBeGreaterThan(0);
    expect(screen.getByText("⚠ 无许可证")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getByText(/需同意条款 · 去同意条款/).closest("a")?.getAttribute("href")).toBe("https://hf/gated");
    expect(screen.getByText(/缺少 · 2.0 GB/)).toBeTruthy();
    expect(screen.getByText("安装包预装")).toBeTruthy();
  });

  it("confirms before downloading and warns about unlicensed packs", async () => {
    api.getDeps.mockResolvedValue(status());
    render(<DepsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "安装" }));
    expect(api.installDeps).not.toHaveBeenCalled();
    expect(screen.getByText(/作者保留所有权利/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "我已了解，开始下载" }));
    await waitFor(() => expect(api.installDeps).toHaveBeenCalledWith([{ kind: "pack", id: "H3-Optimizations" }]));
  });

  it("completes a whole workflow and cancels a running job", async () => {
    api.getDeps.mockResolvedValue(status());
    render(<DepsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "一键补全此工作流" }));
    fireEvent.click(screen.getByRole("button", { name: "我已了解，开始下载" }));
    await waitFor(() => expect(api.installWorkflow).toHaveBeenCalledWith("h3_ref2va_fast4.api.json"));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(api.cancelDep).toHaveBeenCalledWith("model", "lora.safetensors"));
  });

  it("offers a ComfyUI restart after a node pack install", async () => {
    api.getDeps.mockResolvedValue(status({ restart_needed: true }));
    render(<DepsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "重启 ComfyUI 以加载新节点" }));
    await waitFor(() => expect(api.restartComfyForDeps).toHaveBeenCalled());
  });

  it("is status-only for a remote ComfyUI", async () => {
    setLang("en");
    api.getDeps.mockResolvedValue(status({ mode: "remote", can_install: false }));
    render(<DepsPanel />);
    await screen.findByText("H3 video (fast 4-step)");
    expect(screen.getByText(/status and links only/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Complete this workflow" })).toBeNull();
    expect(screen.getAllByText("Source").length).toBeGreaterThan(0);
  });

  it("Director LLM group downloads only the default GGUF, even with a remote ComfyUI", async () => {
    setLang("en");
    const llm = {
      file: "llm:director", zh: "导演大模型（内置 llama.cpp）", en: "Director LLM (built-in llama.cpp)", kind: "llm" as const,
      ready: false, missing_bytes: 18_973_870_528,
      items: [{ kind: "model" as const, id: "Qwen3.8-27B-Q4_K_M.gguf", name: "Qwen3.8-27B-Q4_K_M.gguf", state: "missing" as const,
        installable: true, license: "apache-2.0", community: true, size: 18_973_870_528, folder: "llm", page: "https://hf/ggml-org" }],
    };
    api.getDeps.mockResolvedValue(status({ mode: "remote", can_install: false, workflows: [...status().workflows, llm] }));
    render(<DepsPanel />);
    await screen.findByText("Director LLM (built-in llama.cpp)");
    expect(screen.queryByRole("button", { name: "Complete this workflow" })).toBeNull();
    const buttons = screen.getAllByRole("button", { name: "Download" });
    expect(buttons).toHaveLength(2); // group header + the item itself
    fireEvent.click(buttons[0]);
    fireEvent.click(screen.getByRole("button", { name: /Start download|I understand/ }));
    await waitFor(() => expect(api.installDeps).toHaveBeenCalledWith([{ kind: "model", id: "Qwen3.8-27B-Q4_K_M.gguf" }]));
    expect(api.installWorkflow).not.toHaveBeenCalled();
  });
});

describe("settings navigation", () => {
  it("remembers a tab request until the settings page consumes it", () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent<{ tab: string }>).detail.tab);
    window.addEventListener("ds:open-settings", listener);
    requestSettingsTab("deps");
    window.removeEventListener("ds:open-settings", listener);
    expect(seen).toEqual(["deps"]);
    expect(consumeSettingsTab()).toBe("deps");
    expect(consumeSettingsTab()).toBeNull();
  });
});
