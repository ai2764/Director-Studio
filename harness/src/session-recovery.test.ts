import { afterEach, expect, it, vi } from "vitest";
import { PersistenceCoordinator } from "@deepseek-ai/dsh-session-persistence";
import { Context } from "@deepseek-ai/cordis";
import Sessions, { SessionId } from "@deepseek-ai/dsh-session";
import JsonlPersistence from "@deepseek-ai/dsh-session-persistence-jsonl";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { mkdtemp, rm, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { runTurn, type Host } from "./kernel.js";

const roots: string[] = [];
afterEach(async () => { await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true }))); });
async function config() {
  const sessionRoot = await mkdtemp(join(tmpdir(), "director-native-session-"));
  roots.push(sessionRoot);
  return { sessionRoot };
}
const input = { message: "Continue", history: [], context_window: 32000, max_steps: 3, session_id: "project-a" };
const history = Array.from({ length: 60 }, (_, index) => ({ role: index % 2 ? "assistant" : "user", content: `Archive ${index}: ` + "resolved detail ".repeat(60) }));

it.each(["chat", "compact"] as const)("migrates old snapshots once during %s, preserving user text and the original log", async (operation) => {
  const runtime = await config();
  const ctx = new Context();
  await ctx.plugin(Sessions);
  await ctx.plugin(JsonlPersistence, { root: runtime.sessionRoot, compression: "none" });
  const session = ctx.sessions.create(SessionId(input.session_id));
  session.append("user/message", createUserMessage({
    content: [{ type: "text", text: "Keep BLUE. User quotation: Current runtime context. KEEP_THIS_USER_TEXT" }],
    source: { kind: "user" },
  }), { surfaceOp: "append" });
  const snapshot = "OLD_REGENERABLE_STATE ".repeat(6000);
  session.append("user/message", createUserMessage({
    content: [{ type: "text", text: snapshot }],
    source: { kind: "plugin", plugin: "@deepseek-ai/dsh-system-prompt", form: "snapshot",
      sections: [{ name: "host-state", text: snapshot }] },
  }), { surfaceOp: "append" });
  await ctx.sessions.flush(session);
  await ctx.fiber.dispose();
  let summaries = 0;
  const outcome = await runTurn({ ...input, operation }, async (method, params) => {
    if (method === "context") return { system: "CURRENT_DIRECTOR_INSTRUCTIONS", state: {}, tools: [] };
    expect(method).toBe("llm");
    expect(JSON.stringify(params.messages)).not.toContain("OLD_REGENERABLE_STATE");
    if (params.purpose === "compaction") {
      summaries++;
      expect(JSON.stringify(params.messages)).toContain("KEEP_THIS_USER_TEXT");
      expect(params.system).not.toContain("CURRENT_DIRECTOR_INSTRUCTIONS");
      expect(params.tools).toEqual([]);
      return { content: "Keep BLUE; the user's quoted text is KEEP_THIS_USER_TEXT." };
    }
    expect(JSON.stringify(params.messages)).toContain("Keep BLUE");
    return { content: "BLUE" };
  }, new AbortController().signal, runtime);
  expect(summaries).toBe(1);
  if (operation === "compact") {
    expect(outcome.compaction?.compacted).toBe(true);
    expect(outcome.compaction!.before_tokens).toBeGreaterThan(outcome.compaction!.after_tokens);
  }
  const files = await readdir(runtime.sessionRoot, { recursive: true });
  const log = await readFile(join(runtime.sessionRoot, files.find(path => path.endsWith("session.jsonl"))!), "utf8");
  expect(log).toContain(snapshot);
  expect(log).toContain('"compaction/end"');
});

it("reuses a durable native summary after runtime disposal without reimporting history", async () => {
  const runtime = await config();
  let summaries = 0;
  const host: Host = async (method, params) => {
    if (method === "context") return { system: "fixture", state: {}, tools: [] };
    expect(method).toBe("llm");
    if (params.purpose === "compaction") { summaries++; return { content: "Confirmed decision: use BLUE." }; }
    return { content: "Finished first turn." };
  };
  await runTurn({ ...input, history, context_window: 16000 }, host, new AbortController().signal, runtime);
  expect(summaries).toBe(1);
  await runTurn({ ...input, history: [{ role: "user", content: "STALE_SEED_MUST_NOT_RETURN" }] }, async (method, params) => {
    if (method === "context") return { system: "fresh project state", state: {}, tools: [] };
    expect(params.purpose).toBe("turn");
    expect(JSON.stringify(params.messages)).toContain("Confirmed decision: use BLUE.");
    expect(JSON.stringify(params.messages)).toContain("Finished first turn.");
    expect(JSON.stringify(params.messages)).not.toContain("STALE_SEED_MUST_NOT_RETURN");
    return { content: "Resumed." };
  }, new AbortController().signal, runtime);
});

it("manually compacts below automatic threshold without a user turn or tool execution", async () => {
  const runtime = await config();
  let calls = 0;
  const result = await runTurn({ ...input, history, operation: "compact" }, async (method, params) => {
    if (method === "context") return { system: "fixture", state: {}, tools: [] };
    calls++;
    expect(method).toBe("llm");
    expect(params.purpose).toBe("compaction");
    expect(params.max_output_tokens).toBeGreaterThan(0);
    return { content: "Confirmed BLUE; prior chatter resolved." };
  }, new AbortController().signal, runtime);
  expect(calls).toBe(1);
  expect(result.compaction?.compacted).toBe(true);
  expect(result.compaction!.after_tokens).toBeLessThan(result.compaction!.before_tokens);
  await runTurn({ ...input, message: "next" }, async (method, params) => {
    if (method === "context") return { system: "fixture", state: {}, tools: [] };
    expect(JSON.stringify(params.messages)).not.toContain('"Continue"');
    expect(JSON.stringify(params.messages)).toContain("Confirmed BLUE");
    return { content: "done" };
  }, new AbortController().signal, runtime);
});

it("does not report manual compaction as usable when the fixed envelope exceeds capacity", async () => {
  const runtime = await config();
  const oversizedEnvelope = "fixed director instruction ".repeat(500);
  await expect(runTurn({ ...input, history, operation: "compact", context_window: 1024 }, async (method, params) => {
    if (method === "context") return { system: oversizedEnvelope, state: {}, tools: [] };
    expect(params.purpose).toBe("compaction");
    return { content: "Confirmed BLUE." };
  }, new AbortController().signal, runtime)).rejects.toMatchObject({
    code: "CONTEXT_WINDOW_EXCEEDED",
    message: expect.stringMatching(/summary was saved.*context capacity/i),
  });
});

it("preserves original durable history after a failed manual summary", async () => {
  const runtime = await config();
  await expect(runTurn({ ...input, history, operation: "compact" }, async method => {
    if (method === "context") return { system: "fixture", state: {}, tools: [] };
    return { content: "partial", done_reason: "length" };
  }, new AbortController().signal, runtime)).rejects.toThrow();
  await runTurn(input, async (method, params) => {
    if (method === "context") return { system: "fixture", state: {}, tools: [] };
    expect(JSON.stringify(params.messages)).toContain("Archive 0:");
    return { content: "done" };
  }, new AbortController().signal, runtime);
});

it("rejects concurrent access to the same durable session until disposal", async () => {
  const runtime = await config();
  let enter!: () => void, release!: () => void;
  const entered = new Promise<void>(resolve => enter = resolve);
  const gate = new Promise<void>(resolve => release = resolve);
  const first = runTurn(input, async method => {
    if (method === "context") return { system: "fixture", state: {}, tools: [] };
    enter(); await gate; return { content: "done" };
  }, new AbortController().signal, runtime);
  await entered;
  try {
    await expect(runTurn(input, async () => { throw new Error("must not reach host"); }, new AbortController().signal, runtime)).rejects.toMatchObject({ code: "SESSION_BUSY" });
  } finally { release(); await first; }
});

it("never executes tool calls from a length-truncated response", async () => {
  let calls = 0;
  await expect(runTurn(input, async method => {
    if (method === "context") return { system: "fixture", state: {}, tools: [{ type: "function", function: { name: "change", parameters: { type: "object", properties: {} } } }] };
    if (method === "tool") { calls++; return { ok: true }; }
    return { content: "partial", finish_reason: "length", tool_calls: [{ id: "call-1", name: "change", arguments: {} }] };
  }, new AbortController().signal, await config())).rejects.toMatchObject({ code: "INCOMPLETE_TURN" });
  expect(calls).toBe(0);
});

it("records actual provider usage in the native durable log", async () => {
  const runtime = await config();
  await runTurn(input, async method => method === "context" ? { system: "fixture", state: {}, tools: [] }
    : { content: "done", usage: { input_tokens: 123, output_tokens: 7 } }, new AbortController().signal, runtime);
  const files = await readdir(runtime.sessionRoot, { recursive: true });
  const log = files.find(path => path.endsWith("session.jsonl"));
  expect(log).toBeDefined();
  const events = (await readFile(join(runtime.sessionRoot, log!), "utf8")).trim().split("\n").map(line => JSON.parse(line));
  expect(events.some(event => event.type === "assistant/message" && event.data.usage?.inputTokens === 123 && event.data.usage?.outputTokens === 7)).toBe(true);
});

it("can resume after a truncated partial tool argument without replaying it", async () => {
  const runtime = await config();
  const context = { system: "fixture", state: {}, tools: [{ type: "function", function: { name: "change", parameters: { type: "object", properties: {} } } }] };
  await expect(runTurn(input, async method => method === "context" ? context : {
    content: "I was interrupted", finish_reason: "length", tool_calls: [{ id: "partial", name: "change", arguments: '{"value":' }],
  }, new AbortController().signal, runtime)).rejects.toMatchObject({ code: "INCOMPLETE_TURN" });
  await runTurn({ ...input, message: "Continue safely" }, async (method, params) => {
    if (method === "context") return context;
    expect(method).toBe("llm");
    expect((params.messages as any[]).some(message => message.tool_calls?.length)).toBe(false);
    expect(JSON.stringify(params.messages)).toContain("I was interrupted");
    return { content: "done" };
  }, new AbortController().signal, runtime);
});

it("does not report success when the native durable write fails", async () => {
  const runtime = await config();
  const write = vi.spyOn(PersistenceCoordinator.prototype as any, "appendLiveBatch")
    .mockRejectedValue(new Error("INJECTED_DISK_WRITE_FAILURE"));
  try {
    await expect(runTurn(input, async method => method === "context"
      ? { system: "fixture", state: {}, tools: [] } : { content: "done" }, new AbortController().signal, runtime))
      .rejects.toThrow("INJECTED_DISK_WRITE_FAILURE");
  } finally { write.mockRestore(); }
});

it("requests bootstrap history only once, never on durable resume", async () => {
  const runtime = await config();
  let seeds = 0;
  const host: Host = async (method, params) => {
    if (method === "context") {
      if (params.include_history) { seeds++; return { history: [{ role: "user", content: "Bootstrap BLUE" }] }; }
      return { system: "fixture", state: {}, tools: [] };
    }
    expect(JSON.stringify(params.messages)).toContain("Bootstrap BLUE");
    return { content: "done" };
  };
  await runTurn(input, host, new AbortController().signal, runtime);
  await runTurn(input, host, new AbortController().signal, runtime);
  expect(seeds).toBe(1);
});

it("restores the saved summary in a fresh Node process", async () => {
  const runtime = await config();
  const first = `import { runTurn } from './src/kernel.ts';
    await runTurn(${JSON.stringify({ ...input, history, operation: "compact" })}, async (method) => method === 'context'
      ? {system:'fixture',state:{},tools:[]} : {content:'Persisted choice BLUE'}, new AbortController().signal, ${JSON.stringify(runtime)});`;
  execFileSync(process.execPath, ["--import", "tsx", "--input-type=module"], { input: first, cwd: process.cwd(), windowsHide: true, timeout: 15000 });
  const second = `import { runTurn } from './src/kernel.ts';
    const result = await runTurn(${JSON.stringify(input)}, async (method, params) => {
      if (method === 'context') { if (params.include_history) throw Error('must resume'); return {system:'fixture',state:{},tools:[]}; }
      if (!JSON.stringify(params.messages).includes('Persisted choice BLUE')) throw Error('summary lost');
      return {content:'restored'};
    }, new AbortController().signal, ${JSON.stringify(runtime)}); console.log(JSON.stringify(result));`;
  expect(execFileSync(process.execPath, ["--import", "tsx", "--input-type=module"],
    { input: second, cwd: process.cwd(), windowsHide: true, encoding: "utf8", timeout: 15000 })).toContain('"reply":"restored"');
});
