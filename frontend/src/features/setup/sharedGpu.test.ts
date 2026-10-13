import { describe, expect, it } from "vitest";
import { sharedGpuAuto } from "./Sections";
import type { ConfigResponse } from "./types";

const model = (values: Record<string, unknown>) => ({
  config: { fields: Object.entries(values).map(([key, value]) => ({ key, value })) } as unknown as ConfigResponse,
  draft: {},
});

describe("sharedGpuAuto (DS_VRAM_SHARED_GPU=auto)", () => {
  it("local ComfyUI shares the GPU", () => {
    expect(sharedGpuAuto(model({ DS_COMFY_MODE: "managed" }))).toBe(true);
  });
  it("two loopback forwards are treated as one GPU", () => {
    expect(sharedGpuAuto(model({ DS_COMFY_MODE: "remote", DS_COMFY_REMOTE_URL: "http://127.0.0.1:18188",
      DS_LLM_PROVIDER: "llama-swap", DS_LLM_BASE_URL: "http://127.0.0.1:21435/v1" }))).toBe(true);
  });
  it("same tailnet host is shared, different hosts are not", () => {
    const base = { DS_COMFY_MODE: "remote", DS_COMFY_REMOTE_URL: "http://100.1.2.3:8188", DS_LLM_PROVIDER: "llama-swap" };
    expect(sharedGpuAuto(model({ ...base, DS_LLM_BASE_URL: "http://100.1.2.3:11435/v1" }))).toBe(true);
    expect(sharedGpuAuto(model({ ...base, DS_LLM_BASE_URL: "http://100.9.9.9:11435/v1" }))).toBe(false);
    expect(sharedGpuAuto(model({ ...base, DS_LLM_BASE_URL: "" }))).toBe(false); // local default URL
  });
});
