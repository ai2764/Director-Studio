import { Context } from "@deepseek-ai/cordis";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import JsonlPersistence from "@deepseek-ai/dsh-session-persistence-jsonl";
import Timer from "@deepseek-ai/cordis-plugin-timer";
import Agents, { type AgentHandle } from "@deepseek-ai/dsh-agent";
import AgentLoop from "@deepseek-ai/dsh-agent-loop";
import BasicCompaction from "@deepseek-ai/dsh-compaction-basic";
import Llm, {
  LlmAdapter,
  LlmError,
  CallId,
  createUserMessage,
  createAssistantMessage,
  errorChain,
  type GenerateOptions,
  type StreamChunk,
  type Message,
  type ContentBlock,
} from "@deepseek-ai/dsh-llm";
import Sessions, { SessionId } from "@deepseek-ai/dsh-session";
import Projection from "@deepseek-ai/dsh-session-projection";
import SystemPrompt from "@deepseek-ai/dsh-system-prompt";
import TokenMeter from "@deepseek-ai/dsh-token-meter";
import Tools from "@deepseek-ai/dsh-tools";
import { harnessSchema } from "./schema.js";

export interface TurnInput {
  message: string;
  history: { role: string; content: string }[];
  context_window: number;
  max_steps: number;
  session_id?: string;
  operation?: "chat" | "compact";
}
export interface CompactionResult {
  compacted: boolean;
  before_tokens: number;
  after_tokens: number;
  session_id: string;
}
export interface TurnResult { reply: string; thinking: string; compaction?: CompactionResult }
const sessionOwners = new Set<string>();
export type Host = (
  method: "context" | "llm" | "tool",
  params: Record<string, unknown>,
) => Promise<any>;
export class ProtocolError extends Error {
  constructor(
    public code: string,
    message: string,
    public retryable = false,
  ) {
    super(message);
  }
}
const textOf = (blocks: readonly ContentBlock[]) =>
  blocks
    .filter((b) => b.type === "text")
    .map((b) => (b.type === "text" ? b.text : ""))
    .join("\n");
const hostSystem = (context: any): string =>
  `${context.system}\n\nPROJECT_STATE:\n${typeof context.state === "string" ? context.state : JSON.stringify(context.state)}`;
const SUMMARY_SYSTEM = "Summarize conversation history concisely. Preserve user decisions, creative constraints, unresolved questions and confirmed tool outcomes. Do not invent successful actions. Current project facts are supplied separately by the backend.";
function isLegacyHostSnapshot(message: Message): boolean {
  if (message.role !== "user") return false;
  const source = message.source;
  return source.kind === "plugin" && source.plugin === "@deepseek-ai/dsh-system-prompt"
    && "sections" in source && Array.isArray(source.sections) && source.sections.length > 0
    && source.sections.every((section: any) => section.name === "host-state");
}
function messagesForHost(
  messages: readonly Message[],
): Record<string, unknown>[] {
  return messages.flatMap<Record<string, unknown>>((message) => {
    const results = message.content.filter((b) => b.type === "tool-result");
    if (results.length)
      return results.map((b) => ({
        role: "tool",
        tool_call_id: b.toolCallId,
        content: textOf(b.content),
      }));
    const calls = message.content.filter((b) => b.type === "tool-call");
    return [
      {
        role: message.role,
        content: textOf(message.content),
        ...(calls.length
          ? {
              tool_calls: calls.map((b) => ({
                id: b.id,
                type: "function",
                function: { name: b.name, arguments: b.arguments },
              })),
            }
          : {}),
      },
    ];
  });
}
class HostAdapter extends LlmAdapter {
  constructor(
    private host: Host,
    private window: number,
    private beforeRequest: (purpose: "turn" | "compaction") => Promise<void>,
    private maxSteps: number,
  ) {
    super();
  }
  private steps = 0;
  override providerInfo(provider: string) {
    return { id: provider, name: "Python host" };
  }
  override async listModels(provider: string) {
    return [
      {
        provider,
        id: "turn",
        name: "Python host",
        inputModalities: ["text"] as const,
      },
    ];
  }
  override async resolveModel(provider: string, model: string) {
    return {
      provider,
      id: model,
      name: model,
      inputModalities: ["text"] as const,
      context: { contextWindow: this.window },
    };
  }
  override async *stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    const purpose = options.model === "compaction" ? "compaction" : "turn";
    if (purpose === "turn" && ++this.steps > this.maxSteps)
      throw new LlmError("Harness step limit reached", "MAX_STEPS");
    let result: any;
    for (let attempt = 0; ; attempt++) {
      options.signal?.throwIfAborted();
      await this.beforeRequest(purpose);
      try {
        result = await this.host("llm", {
          messages: messagesForHost(options.messages),
          system: options.system ?? "",
          tools: options.tools ?? [],
          purpose,
          ...(options.maxTokens ? { max_output_tokens: options.maxTokens } : {}),
        });
        break;
      } catch (error) {
        if (
          error instanceof ProtocolError &&
          error.code === "CONTEXT_WINDOW_EXCEEDED"
        )
          throw new LlmError(error.message, error.code);
        if (
          !(error instanceof ProtocolError) ||
          !error.retryable ||
          attempt >= 2
        ) {
          if (error instanceof ProtocolError)
            throw new LlmError(error.message, error.code);
          throw error;
        }
      }
    }
    let index = 0;
    for (const [type, value] of [
      ["reasoning", result.thinking],
      ["text", result.content],
    ] as const) {
      if (!value) continue;
      yield { type: "block-start", index, blockType: type };
      yield {
        type: type === "text" ? "text-delta" : "reasoning-delta",
        index,
        text: String(value),
      };
      yield {
        type: "block-end",
        index: index++,
        block: { type, text: String(value) },
      };
    }
    for (const call of result.tool_calls ?? []) {
      const name = call.name ?? call.function?.name;
      const args = call.arguments ?? call.function?.arguments ?? {};
      const id = CallId(call.id ?? crypto.randomUUID());
      const argumentsText =
        typeof args === "string" ? args : JSON.stringify(args);
      yield { type: "block-start", index, blockType: "tool-call" };
      yield {
        type: "tool-call-delta",
        index,
        id,
        name,
        argumentsDelta: argumentsText,
      };
      yield {
        type: "block-end",
        index: index++,
        block: { type: "tool-call", id, name, arguments: argumentsText },
      };
    }
    const usage = result.usage;
    if (Number.isInteger(usage?.input_tokens) && Number.isInteger(usage?.output_tokens)
        && usage.input_tokens >= 0 && usage.output_tokens >= 0) {
      yield { type: "usage", usage: { inputTokens: usage.input_tokens, outputTokens: usage.output_tokens,
        ...(Number.isInteger(usage.reasoning_tokens) ? { reasoningTokens: usage.reasoning_tokens } : {}) } };
    }
    yield {
      type: "finish",
      reason: {
        kind: (result.finish_reason ?? result.done_reason) === "length"
          ? "max-tokens" : result.tool_calls?.length ? "tool-calls" : "stop",
      },
    };
  }
}

/** A short-lived runtime resumes native durable history; Python owns business IO. */
export async function runTurn(
  input: TurnInput,
  host: Host,
  signal: AbortSignal,
  options: { sessionRoot?: string } = {},
): Promise<TurnResult> {
  signal.throwIfAborted();
  const sessionRoot = resolve(options.sessionRoot ?? process.env.DS_HARNESS_SESSION_ROOT
    ?? fileURLToPath(new URL("../../.run/harness-sessions", import.meta.url)));
  const ownerKey = input.session_id ? `${sessionRoot}\0${input.session_id}` : undefined;
  if (ownerKey && sessionOwners.has(ownerKey)) throw new ProtocolError("SESSION_BUSY", "This Harness session is already active");
  if (ownerKey) sessionOwners.add(ownerKey);
  const ctx = new Context();
  let handle: AgentHandle | undefined;
  let scoped: Context | undefined;
  let disposers: (() => void)[] = [];
  let failure: unknown;
  let compactionFailure: unknown;
  let initialContext: any;
  class FailClosedCompaction extends BasicCompaction {
    private committed: Awaited<ReturnType<BasicCompaction["compactRegion"]>> | null = null;
    private regionFailed = false;
    override async summarize(...args: Parameters<BasicCompaction["summarize"]>) {
      const [history, agent, signal] = args;
      // This native hook changes only summary input, never the stored transcript.
      // Regenerable host snapshots are identified by provenance, not text matching.
      return super.summarize({
        system: SUMMARY_SYSTEM,
        tools: [],
        messages: history.messages.filter(message => !isLegacyHostSnapshot(message)),
      }, agent, signal);
    }
    override async compactRegion(...args: Parameters<BasicCompaction["compactRegion"]>) {
      try {
        this.committed = await super.compactRegion(...args);
        return this.committed;
      } catch (error) {
        this.regionFailed = true;
        throw error;
      }
    }
    override async compactIfNeeded(
      ...args: Parameters<BasicCompaction["compactIfNeeded"]>
    ) {
      compactionFailure = undefined;
      this.committed = null;
      this.regionFailed = false;
      try {
        return await super.compactIfNeeded(...args);
      } catch (error) {
        // Native bounded attempts may commit useful summaries yet remain above
        // the early-pressure threshold. That is not a failed checkpoint.
        const after = ctx.tokenMeter.measure(args[0].session).totalTokens;
        if (!args[2]?.aborted && this.committed && !this.regionFailed && after < input.context_window)
          return this.committed;
        compactionFailure = error;
        throw error;
      }
    }
  }
  const refresh = async () => {
    signal.throwIfAborted();
    const context = await host("context", {});
    initialContext ??= context;
    if (!scoped) return;
    for (const dispose of disposers.splice(0)) dispose();
    disposers.push(
      scoped.systemPrompt.variable("host_envelope", () => hostSystem(context)),
      scoped.systemPrompt.section({
        name: "host-state",
        order: 50,
        complete: true,
        text: "{{host_envelope}}",
      }),
    );
    for (const tool of context.tools ?? []) {
      const fn = tool.function;
      disposers.push(
        scoped.tools.register({
          name: fn.name,
          description: fn.description ?? "",
          parameters: harnessSchema(fn.parameters),
          output: {
            schema: {},
            render: (_args: unknown, value: unknown) => [
              { type: "text" as const, text: JSON.stringify(value) },
            ],
          },
          async execute(args, exec) {
            // Refresh immediately before selecting/executing each tool, including multiple calls in one response.
            const latest = await host("context", {});
            if (!latest.tools.some((t: any) => t.function.name === fn.name))
              throw new Error(`Tool no longer offered: ${fn.name}`);
            const result = await host("tool", {
              name: fn.name,
              arguments: args,
              call_id: String(exec.callId),
            });
            await refresh();
            return result;
          },
        }),
      );
    }
  };
  const cancel = () => handle?.agent.cancel({ kind: "user" });
  signal.addEventListener("abort", cancel, { once: true });
  try {
    await ctx.plugin(Timer);
    await ctx.plugin(Llm);
    await ctx.plugin(Sessions);
    if (input.session_id) await ctx.plugin(JsonlPersistence, { root: sessionRoot, compression: "none" });
    await ctx.plugin(Projection);
    await ctx.plugin(SystemPrompt, {
      includeHarnessIdentity: false,
      includeRuntimeContext: false,
    });
    await ctx.plugin(Tools, { mode: "native" });
    await ctx.plugin(Agents);
    await ctx.plugin(TokenMeter);
    ctx.llm.registerAdapter(
      ["host"],
      new HostAdapter(host, input.context_window, async purpose => {
        signal.throwIfAborted();
        if (purpose === "compaction") return;
        if (compactionFailure) {
          const cause = compactionFailure instanceof Error ? compactionFailure.message : String(compactionFailure);
          throw new LlmError(`History compaction failed: ${cause}. Original history is preserved.`, "COMPACTION_FAILED");
        }
        if (handle && ctx.tokenMeter.measure(handle.agent.session).totalTokens >= input.context_window)
          throw new LlmError("Current request exceeds the reserved input budget even after history compaction; reduce the current message, references or project context.", "CONTEXT_WINDOW_EXCEEDED");
      }, input.max_steps),
    );
    await ctx.plugin(FailClosedCompaction, {
      summarizationProvider: "host",
      summarizationModel: "compaction",
      compactionRetries: 1,
      maxOverflowRetries: 1,
    });
    await ctx.plugin(AgentLoop, { agents: [], maxParallelToolCalls: 1 });
    if (!ctx.get("compaction") || !ctx.get("tokenMeter"))
      throw new Error("Compaction services did not initialize");
    ctx.on("agent/error", (payload) => {
      failure = payload.error;
    });
    const sessionId = SessionId(input.session_id ?? crypto.randomUUID());
    const existing = input.session_id
      ? (await ctx.sessionPersistence.list()).some(meta => meta.id === sessionId) : false;
    const seed = existing ? [] : input.history.length ? input.history
      : input.session_id ? (await host("context", { include_history: true })).history ?? [] : [];
    if (!Array.isArray(seed) || seed.length > 10000 || seed.some(row =>
      !row || !["user", "assistant"].includes(row.role) || typeof row.content !== "string")) {
      throw new ProtocolError("INVALID_HISTORY", "Initial history is invalid or exceeds import bounds");
    }
    const agentOptions = {
      agentOptions: { provider: "host", model: "turn" },
      signal,
      setup: async (agentCtx: Context) => {
        scoped = agentCtx;
        await refresh();
      },
    };
    handle = existing
      ? await ctx.agents.resume({ ...agentOptions, resumeSessionId: sessionId })
      : await ctx.agents.create({ ...agentOptions, sessionId });
    for (const row of seed) {
      const content: ContentBlock[] = [{ type: "text", text: row.content }];
      if (row.role === "assistant") {
        handle.agent.session.append("step/start", { turn: 0, step: 0 });
        handle.agent.session.append(
          "assistant/message",
          {
            message: createAssistantMessage({
              content,
              source: { provider: "host", model: "turn" },
            }),
            turn: 0,
            step: 0,
          },
          { surfaceOp: "append" },
        );
        handle.agent.session.append("step/end", { turn: 0, step: 0 });
      } else
        handle.agent.session.append(
          "user/message",
          createUserMessage({ content, source: { kind: "user" } }),
          { surfaceOp: "append" },
        );
    }
    // Seed the backend envelope for initial pressure estimation, including the
    // known route and project/tool overhead. The stock loop replaces this seed
    // with its effective request header before making the first model call.
    handle.agent.session.append("request/header", {
      header: {
        config: { provider: "host", model: "turn" },
        system: hostSystem(initialContext),
        tools: (initialContext.tools ?? []).map((tool: any) => ({
          name: tool.function.name,
          description: tool.function.description ?? "",
          parameters: harnessSchema(tool.function.parameters),
        })),
      },
      reason: "initial",
    });
    let migration: CompactionResult | undefined;
    if (existing && handle.agent.session.deriveMessages().some(isLegacyHostSnapshot)) {
      // Migrate the old duplicate-envelope surface using the native durable
      // checkpoint API; original events and UI history remain untouched.
      // Native compactNow always retains its final surface node, even with zero
      // retention. A sourced boundary lets it include the last old snapshot too.
      const last = handle.agent.session.deriveMessages().at(-1);
      if (last?.role !== "user" || last.source.kind !== "plugin"
          || last.source.plugin !== "director-studio/context-envelope") {
        handle.agent.session.append("user/message", createUserMessage({
          content: [{ type: "text", text: "Current project facts are now supplied once in the system envelope. Earlier host-state snapshots are regenerable, not user requests." }],
          source: { kind: "plugin", plugin: "director-studio/context-envelope" },
        }), { surfaceOp: "append" });
      }
      try {
        const before = ctx.tokenMeter.measure(handle.agent.session).totalTokens;
        const result = await ctx.compaction.compactNow(handle.agent, signal);
        migration = { compacted: result !== null, before_tokens: before,
          after_tokens: ctx.tokenMeter.measure(handle.agent.session).totalTokens, session_id: sessionId };
      } catch (error) {
        signal.throwIfAborted();
        throw new ProtocolError("COMPACTION_FAILED",
          `Legacy context migration failed: ${errorChain(error)}. Original history is preserved.`);
      }
    }
    if (input.operation === "compact") {
      if (migration) return { reply: "", thinking: "", compaction: migration };
      const before = ctx.tokenMeter.measure(handle.agent.session).totalTokens;
      const result = await ctx.compaction.compactNow(handle.agent, signal);
      const after = ctx.tokenMeter.measure(handle.agent.session).totalTokens;
      await ctx.sessions.flush(handle.agent.session);
      if (after >= input.context_window) {
        const detail = result === null ? "No compactable history remains" : "The summary was saved";
        throw new ProtocolError("CONTEXT_WINDOW_EXCEEDED",
          `${detail}, but the fixed Director envelope still exceeds the service context capacity (${after} estimated tokens >= ${input.context_window}). Increase the model context window.`);
      }
      return { reply: "", thinking: "", compaction: { compacted: result !== null,
        before_tokens: before, after_tokens: after, session_id: sessionId } };
    }
    const start = handle.agent.session.seq;
    signal.throwIfAborted();
    handle.agent.followup(
      createUserMessage({
        content: [{ type: "text", text: input.message }],
        source: { kind: "user" },
      }),
    );
    await handle.agent.whenIdle();
    signal.throwIfAborted();
    const events = handle.agent.session.events.slice(start);
    const end = events.findLast((e) => e.type === "turn/end");
    if (end?.type === "turn/end" && end.data.reason.kind === "error") {
      const error = end.data.reason.error;
      if (error.code === "CONTEXT_WINDOW_EXCEEDED" && compactionFailure)
        throw new ProtocolError("COMPACTION_FAILED", `Overflow recovery failed: ${errorChain(compactionFailure)}. Original history is preserved.`);
      throw new ProtocolError(error.code ?? "HARNESS_ERROR", error.message);
    }
    if (end?.type === "turn/end" && end.data.reason.kind === "max-tokens")
      throw new ProtocolError("INCOMPLETE_TURN", "Model output was truncated; no completed result confirmed");
    if (failure) throw failure;
    const answer = events.findLast((e) => e.type === "assistant/message");
    if (answer?.type !== "assistant/message")
      throw new ProtocolError("NO_RESULT", "Harness ended without a result");
    if (answer.data.message.content.some((b) => b.type === "tool-call"))
      throw new ProtocolError(
        "INCOMPLETE_TURN",
        "Harness stopped before completing tools",
      );
    await ctx.sessions.flush(handle.agent.session);
    return {
      reply: textOf(answer.data.message.content),
      thinking: answer.data.message.content
        .filter((b) => b.type === "reasoning")
        .map((b) => b.text)
        .join(""),
    };
  } finally {
    signal.removeEventListener("abort", cancel);
    try {
      await handle?.dispose();
    } finally {
      try { await ctx.fiber.dispose(); }
      finally { if (ownerKey) sessionOwners.delete(ownerKey); }
    }
  }
}
