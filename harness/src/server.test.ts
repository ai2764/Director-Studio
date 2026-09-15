import { it, expect } from "vitest";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createServer, installParentGuard } from "./server.js";

it("declares a plain Node production build", async () => {
  const pkg = JSON.parse(await readFile(resolve("package.json"), "utf8"));
  expect(pkg.scripts.build).toBe("tsc -p tsconfig.build.json");
});

it("excludes tests from the production build", async () => {
  const config = JSON.parse(
    await readFile(resolve("tsconfig.build.json"), "utf8"),
  );
  expect(config.exclude).toEqual(["src/**/*.test.ts"]);
});

it("closes the sidecar when its owning parent disappears", () => {
  let checkParent: (() => void) | undefined;
  let closed = 0;
  const server = {
    close: () => closed++,
    closeAllConnections: () => closed++,
  } as any;
  installParentGuard(
    server,
    "1234",
    (callback) => {
      checkParent = callback;
      return { unref() {} } as any;
    },
    () => false,
  );

  checkParent?.();

  expect(closed).toBe(2);
});

it("authenticates health and round-trips sequential host requests", async () => {
  const server = createServer("secret", async (_input, host) => ({
    reply: String(await host("context", {})),
    thinking: "",
  }));
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${(server.address() as any).port}`;
  const headers = {
    Authorization: "Bearer secret",
    "Content-Type": "application/json",
  };
  try {
    expect((await fetch(`${base}/health`)).status).toBe(401);
    expect(await (await fetch(`${base}/health`, { headers })).json()).toEqual({
      ok: true,
      service: "director-studio-harness",
      protocol: 1,
      capabilities: ["native-sessions-v1", "context-envelope-v2"],
    });
    const path = `${base}/turns/${randomUUID()}`;
    const response = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify({
        message: "a",
        history: [],
        context_window: 32000,
        max_steps: 2,
      }),
    });
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "",
      events: any[] = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buffer.indexOf("\n")) >= 0) {
        const event = JSON.parse(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 1);
        events.push(event);
        if (event.type === "request")
          expect(
            (
              await fetch(`${path}/responses/${event.id}`, {
                method: "POST",
                headers,
                body: JSON.stringify({ ok: true, data: "answer" }),
              })
            ).status,
          ).toBe(204);
      }
    }
    expect(events.at(-1)).toEqual({
      type: "result",
      reply: "answer",
      thinking: "",
    });
  } finally {
    await new Promise<void>((r) => server.close(() => r()));
  }
});
it.each(["delete", "disconnect"])(
  "aborts pending host work on %s",
  async (mode) => {
    let cleaned = false;
    const server = createServer("secret", async (_input, host) => {
      try {
        await host("llm", {});
        return { reply: "", thinking: "" };
      } finally {
        cleaned = true;
      }
    });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const base = `http://127.0.0.1:${(server.address() as any).port}`;
    const headers = {
      Authorization: "Bearer secret",
      "Content-Type": "application/json",
    };
    const path = `${base}/turns/${randomUUID()}`;
    try {
      const response = await fetch(path, {
        method: "POST",
        headers,
        body: JSON.stringify({
          message: "a",
          history: [],
          context_window: 32000,
          max_steps: 2,
        }),
      });
      const reader = response.body!.getReader();
      await reader.read();
      if (mode === "delete") {
        expect((await fetch(path, { method: "DELETE", headers })).status).toBe(
          204,
        );
        while (!(await reader.read()).done) {}
      } else await reader.cancel();
      await new Promise<void>((resolve, reject) => {
        const deadline = Date.now() + 1000;
        const check = () =>
          cleaned
            ? resolve()
            : Date.now() > deadline
              ? reject(new Error("pending work leaked"))
              : setTimeout(check, 10);
        check();
      });
      expect(cleaned).toBe(true);
    } finally {
      server.closeAllConnections();
      await new Promise<void>((r) => server.close(() => r()));
    }
  },
);
it("rejects invalid turns and unknown responses before starting work", async () => {
  const server = createServer("secret", async () => {
    throw new Error("must not run");
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${(server.address() as any).port}`;
  const headers = {
    Authorization: "Bearer secret",
    "Content-Type": "application/json",
  };
  try {
    expect(
      (
        await fetch(`${base}/turns/${randomUUID()}`, {
          method: "POST",
          headers,
          body: "{}",
        })
      ).status,
    ).toBe(400);
    for (const extra of [{ session_id: "../escape" }, { operation: "erase" }, { operation: "compact" }]) {
      expect((await fetch(`${base}/turns/${randomUUID()}`, { method: "POST", headers,
        body: JSON.stringify({ message: "", history: [], context_window: 32000, max_steps: 2, ...extra }) })).status).toBe(400);
    }
    expect(
      (
        await fetch(`${base}/turns/${randomUUID()}/responses/${randomUUID()}`, {
          method: "POST",
          headers,
          body: '{"ok":true,"data":{}}',
        })
      ).status,
    ).toBe(409);
  } finally {
    await new Promise<void>((r) => server.close(() => r()));
  }
});
