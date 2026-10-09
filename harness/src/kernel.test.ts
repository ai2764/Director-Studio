import { describe, it, expect } from "vitest";
import { runTurn, ProtocolError, type Host } from "./kernel.js";

const input = {
  message: "hello",
  history: [],
  context_window: 32000,
  max_steps: 3,
};
const tools = [
  {
    type: "function",
    function: {
      name: "change",
      description: "change fixture",
      parameters: {
        type: "object",
        properties: { value: { type: "string" } },
        required: ["value"],
      },
    },
  },
];
describe("real Harness kernel", () => {
  it("preserves the summary cause when overflow recovery cannot commit", async () => {
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools: [] };
      if (params.purpose === "compaction") throw new ProtocolError("SUMMARY_ERROR", "REAL_SUMMARY_CAUSE");
      throw new ProtocolError("CONTEXT_WINDOW_EXCEEDED", "provider overflow");
    };
    await expect(runTurn({ ...input, history: [{ role: "user", content: "prior ".repeat(1000) },
      { role: "assistant", content: "last" }] }, host, new AbortController().signal))
      .rejects.toMatchObject({ code: "COMPACTION_FAILED", message: expect.stringContaining("REAL_SUMMARY_CAUSE") });
  });
  it("continues from durable summaries above the soft threshold but within input capacity", async () => {
    const purposes: string[] = [];
    let summaries = 0;
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "policy ".repeat(3600), state: {}, tools: [] };
      purposes.push(String(params.purpose));
      if (params.purpose === "compaction") return { content: ++summaries === 1 ? "remember BLUE. ".repeat(150) : "BLUE." };
      return { content: "BLUE" };
    };
    const history = Array.from({ length: 30 }, () => ({ role: "user", content: "old record ".repeat(100) }));
    expect((await runTurn({ ...input, history, context_window: 8000 }, host, new AbortController().signal)).reply).toBe("BLUE");
    expect(purposes).toEqual(["compaction", "compaction", "turn"]);
  });

  it("rejects an irreducible current envelope before sending it to the provider", async () => {
    let models = 0;
    const host: Host = async method => {
      if (method === "context") return { system: "policy ".repeat(5000), state: {}, tools: [] };
      models++;
      return { content: "must not be sent" };
    };
    await expect(runTurn({ ...input, context_window: 8000 }, host, new AbortController().signal))
      .rejects.toMatchObject({ code: "CONTEXT_WINDOW_EXCEEDED" });
    expect(models).toBe(0);
  });
  it("meters one literal host envelope and refreshes it after tools without history snapshots", async () => {
    let version = 1;
    let models = 0;
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "Director {{literal}}", state: JSON.stringify({ version }), tools };
      if (method === "tool") { version = 2; return { ok: true }; }
      models++;
      expect(params.system).toBe('Director {{literal}}\n\nPROJECT_STATE:\n{"version":' + version + '}');
      expect(JSON.stringify(params.messages)).not.toContain("Current runtime context.");
      expect(JSON.stringify(params.messages)).not.toContain("PROJECT_STATE");
      expect(params.tools).toEqual(expect.arrayContaining([expect.objectContaining({ name: "change" })]));
      return models === 1 ? { tool_calls: [{ id: "change-1", name: "change", arguments: { value: "next" } }] } : { content: "done" };
    };
    expect((await runTurn(input, host, new AbortController().signal)).reply).toBe("done");
    expect(models).toBe(2);
  });
  it.each([false, true])("compacts seeded history before the first model turn (large envelope: %s)", async (largeEnvelope) => {
    const purposes: string[] = [];
    const history = Array.from({ length: largeEnvelope ? 82 : 162 }, (_, i) => ({
      role: i % 2 ? "assistant" : "user",
      content: i === 0 ? "Preserve the title suffix BLUE." : `Archive ${i}: ` + "resolved production detail ".repeat(30),
    }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: largeEnvelope ? "current project detail ".repeat(2500) : {}, tools: [] };
      purposes.push(String(params.purpose));
      if (params.purpose === "compaction") {
        expect(JSON.stringify(params.messages)).toContain("Preserve the title suffix BLUE.");
        return { content: "The confirmed title suffix is BLUE. Production chatter resolved." };
      }
      expect(purposes[0]).toBe("compaction");
      expect(JSON.stringify(params.messages)).toContain("confirmed title suffix is BLUE");
      expect(params.messages).toEqual(expect.arrayContaining([{ role: "user", content: "Rename the target" }]));
      return { content: "done" };
    };
    expect(await runTurn({ ...input, history, message: "Rename the target", context_window: 32768 }, host, new AbortController().signal)).toEqual({ reply: "done", thinking: "" });
    expect(purposes).toEqual(["compaction", "turn"]);
  });

  it.each(["truncated", "error", "not-smaller"])("stops before business inference after a %s initial summary", async (failure) => {
    const purposes: string[] = [];
    const history = Array.from({ length: 162 }, () => ({ role: "user", content: "historical detail ".repeat(50) }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools: [] };
      purposes.push(String(params.purpose));
      if (params.purpose === "compaction") {
        if (failure === "error") throw new ProtocolError("UNAVAILABLE", "summary failed");
        return failure === "truncated"
          ? { content: "partial summary", done_reason: "length" }
          : { content: "not compressed ".repeat(10000) };
      }
      return { content: "should not proceed with uncompressed history" };
    };
    await expect(runTurn({ ...input, history, context_window: 32768 }, host, new AbortController().signal)).rejects.toMatchObject({
      code: "COMPACTION_FAILED",
      message: expect.stringMatching(failure === "error" ? /summary failed/ : failure === "not-smaller" ? /not smaller/ : /max-tokens|truncat/),
    });
    expect(purposes).toEqual(["compaction"]);
  });
  it("continues when proactive compaction cannot shrink history that still fits the hard budget", async () => {
    const purposes: string[] = [];
    const history = Array.from({ length: 162 }, () => ({ role: "user", content: "historical detail ".repeat(50) }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools: [] };
      purposes.push(String(params.purpose));
      return params.purpose === "compaction"
        ? { content: "not compressed ".repeat(10000) }
        : { content: "completed within the hard budget" };
    };
    const result = await runTurn({ ...input, history, context_window: 40000 }, host, new AbortController().signal);
    expect(result.reply).toBe("completed within the hard budget");
    expect(purposes).toEqual(["compaction", "turn"]);
  });

  it("does not repeatedly summarize the same nonshrinking history after small tool results", async () => {
    let summaries = 0, models = 0, changes = 0;
    const history = Array.from({ length: 162 }, () => ({ role: "user", content: "historical detail ".repeat(50) }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools };
      if (method === "tool") { changes++; return { ok: true }; }
      if (params.purpose === "compaction") {
        summaries++;
        return { content: "not compressed ".repeat(10000) };
      }
      models++;
      expect(JSON.stringify(params.messages)).toContain("historical detail");
      return models < 3 ? { tool_calls: [{ id: `change-${models}`, name: "change", arguments: { value: "next" } }] }
        : { content: "completed without repeated summaries" };
    };
    const result = await runTurn({ ...input, history, context_window: 40000 }, host, new AbortController().signal);
    expect(result.reply).toBe("completed without repeated summaries");
    expect(changes).toBe(2);
    expect(summaries).toBe(1);
  });

  it("tries compaction again when added history changes the compactable span", async () => {
    let summaries = 0, models = 0;
    const history = Array.from({ length: 162 }, (_, index) => ({ role: "user",
      content: index === 0 ? "Keep BLUE. " + "historical detail ".repeat(50) : "historical detail ".repeat(50) }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools };
      if (method === "tool") return { ok: true, evidence: "New confirmed detail. ".repeat(300) };
      if (params.purpose === "compaction") {
        summaries++;
        expect(JSON.stringify(params.messages)).toContain("Keep BLUE");
        return { content: summaries === 1 ? "not compressed ".repeat(10000) : "Confirmed BLUE." };
      }
      models++;
      if (models === 1) return { tool_calls: [{ id: "change-1", name: "change", arguments: { value: "next" } }] };
      expect(JSON.stringify(params.messages)).toContain("Confirmed BLUE");
      expect(JSON.stringify(params.messages)).toContain("New confirmed detail");
      return { content: "done" };
    };
    expect((await runTurn({ ...input, history, context_window: 40000 }, host, new AbortController().signal)).reply).toBe("done");
    expect(summaries).toBe(2);
  });

  it("still recovers provider-confirmed overflow after a nonshrinking proactive summary", async () => {
    let summaries = 0, models = 0;
    const history = Array.from({ length: 162 }, () => ({ role: "user", content: "historical detail ".repeat(50) }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools: [] };
      if (params.purpose === "compaction") {
        summaries++;
        return { content: summaries === 1 ? "not compressed ".repeat(10000) : "Saved confirmed decisions." };
      }
      if (++models === 1) throw new ProtocolError("CONTEXT_WINDOW_EXCEEDED", "provider-confirmed overflow");
      expect(JSON.stringify(params.messages)).toContain("Saved confirmed decisions");
      return { content: "recovered" };
    };
    expect((await runTurn({ ...input, history, context_window: 40000 }, host, new AbortController().signal)).reply).toBe("recovered");
    expect(summaries).toBe(2);
    expect(models).toBe(2);
  });

  it("fails closed if overflow compaction still cannot shrink the history", async () => {
    let summaries = 0, models = 0;
    const history = Array.from({ length: 162 }, () => ({ role: "user", content: "historical detail ".repeat(50) }));
    const host: Host = async (method, params) => {
      if (method === "context") return { system: "fixture", state: {}, tools: [] };
      if (params.purpose === "compaction") { summaries++; return { content: "not compressed ".repeat(20000) }; }
      models++;
      throw new ProtocolError("CONTEXT_WINDOW_EXCEEDED", "provider-confirmed overflow");
    };
    await expect(runTurn({ ...input, history, context_window: 40000 }, host, new AbortController().signal))
      .rejects.toMatchObject({ code: "COMPACTION_FAILED", message: expect.stringContaining("not smaller") });
    expect(summaries).toBe(2);
    expect(models).toBe(1);
  });

  it.each(["finish_reason", "done_reason"])("reports truncated %s output as incomplete", async (field) => {
    const host: Host = async (method) => method === "context"
      ? { system: "fixture", state: {}, tools: [] }
      : { content: "unfinished", [field]: "length" };
    await expect(runTurn(input, host, new AbortController().signal)).rejects.toMatchObject({ code: "INCOMPLETE_TURN" });
  });
  it("returns a final answer and consumes canonical history", async () => {
    const host: Host = async (method, params) => {
      if (method === "context")
        return { system: "fixture", state: { version: 1 }, tools: [] };
      expect(params.messages).toEqual(
        expect.arrayContaining([{ role: "assistant", content: "previous" }]),
      );
      return { content: "done", thinking: "considered", tool_calls: [] };
    };
    expect(
      await runTurn(
        { ...input, history: [{ role: "assistant", content: "previous" }] },
        host,
        new AbortController().signal,
      ),
    ).toEqual({ reply: "done", thinking: "considered" });
  });
  it("feeds tool failure back for argument repair without replay", async () => {
    let calls = 0,
      models = 0,
      contexts = 0;
    const host: Host = async (method, params) => {
      if (method === "context") {
        contexts++;
        return { system: "fixture", state: { version: calls }, tools };
      }
      if (method === "tool") {
        calls++;
        return calls === 1 ? { error: "invalid value" } : { ok: true };
      }
      models++;
      if (models === 3) {
        expect(JSON.stringify(params.messages)).toContain("invalid value");
        return { content: "fixed" };
      }
      return {
        content: "",
        tool_calls: [
          {
            id: `call${models}`,
            name: "change",
            arguments: { value: models === 1 ? "bad" : "good" },
          },
        ],
      };
    };
    expect(
      (await runTurn(input, host, new AbortController().signal)).reply,
    ).toBe("fixed");
    expect(calls).toBe(2);
    expect(contexts).toBeGreaterThanOrEqual(5);
  });
  it("concludes the turn after a terminal host tool without another model call", async () => {
    let models = 0;
    const host: Host = async (method) => {
      if (method === "context") return { system: "fixture", state: {}, tools };
      if (method === "tool") return {
        ok: true,
        concludes_turn: true,
        reply: "Started local H3 video; waiting for the Job event.",
      };
      models++;
      if (models > 1) throw new Error("terminal tool must prevent a second model call");
      return {
        content: "",
        tool_calls: [{
          id: "start-1",
          name: "change",
          arguments: { value: "start" },
        }],
      };
    };

    expect((await runTurn(input, host, new AbortController().signal)).reply)
      .toBe("Started local H3 video; waiting for the Job event.");
    expect(models).toBe(1);
  });
  it("bounds steps", async () => {
    let calls = 0;
    const host: Host = async (method) =>
      method === "context"
        ? { system: "", state: {}, tools }
        : method === "tool"
          ? {}
          : {
              tool_calls: [
                {
                  id: `c${++calls}`,
                  name: "change",
                  arguments: { value: "a" },
                },
              ],
            };
    await expect(
      runTurn({ ...input, max_steps: 2 }, host, new AbortController().signal),
    ).rejects.toMatchObject({ code: "MAX_STEPS" });
    expect(calls).toBe(2);
  });
  it("retries transient model failures at most twice", async () => {
    let calls = 0;
    const host: Host = async (method) => {
      if (method === "context") return { system: "", state: {}, tools: [] };
      calls++;
      throw new ProtocolError("UNAVAILABLE", "temporary", true);
    };
    await expect(
      runTurn(input, host, new AbortController().signal),
    ).rejects.toMatchObject({ code: "UNAVAILABLE" });
    expect(calls).toBe(3);
  });
  it("cancels a pending model call", async () => {
    const abort = new AbortController();
    const host: Host = async (method) => {
      if (method === "context") return { system: "", state: {}, tools: [] };
      abort.abort();
      throw abort.signal.reason;
    };
    await expect(runTurn(input, host, abort.signal)).rejects.toBeDefined();
  });
  it("uses stock overflow compaction and retries the same authoritative envelope", async () => {
    const order: string[] = [];
    const host: Host = async (method, params) => {
      order.push(method === "llm" ? String(params.purpose) : method);
      if (method === "context")
        return {
          system: "fresh project",
          state: { version: order.length },
          tools: [],
        };
      if (params.purpose === "turn" && !order.includes("compaction"))
        throw new ProtocolError("CONTEXT_WINDOW_EXCEEDED", "fixture overflow");
      return {
        content:
          params.purpose === "compaction"
            ? "Prior discussion summarized."
            : "done",
      };
    };
    const history = Array.from({ length: 20 }, (_, i) => ({
      role: i % 2 ? "assistant" : "user",
      content: `Message ${i}: ` + "historical detail ".repeat(120),
    }));
    expect(
      (
        await runTurn(
          { ...input, history, context_window: 32768 },
          host,
          new AbortController().signal,
        )
      ).reply,
    ).toBe("done");
    expect(order).toContain("compaction");
    // Compaction changes history, not business state. No late context injection.
    expect(order.at(-1)).toBe("turn");
    expect(order.filter((event) => event === "turn" || event === "compaction"))
      .toEqual(["turn", "compaction", "turn"]);
  });
});
