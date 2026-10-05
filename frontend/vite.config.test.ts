import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function proxyTarget(): Promise<string> {
  const mod = await import("./vite.config");
  const config = mod.default as {
    server: { proxy: { "/api": { target: string } } };
  };
  return config.server.proxy["/api"].target;
}

it("keeps the main backend when DS_BACKEND_URL is unset", async () => {
  vi.stubEnv("DS_BACKEND_URL", "");
  expect(await proxyTarget()).toBe("http://127.0.0.1:8790");
});

it("proxies a non-default frontend to the configured backend", async () => {
  vi.stubEnv("DS_BACKEND_URL", "http://127.0.0.1:8792");
  expect(await proxyTarget()).toBe("http://127.0.0.1:8792");
});
