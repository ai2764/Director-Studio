# Harness capability audit — 2026-09-12

## Scope and evidence

Audited the installed DeepSeek packages pinned to `0.1.1-rc.2`, not just current upstream documentation. Also downloaded (without installing or executing package scripts) the exact-version JSONL persistence, LLM retry and tool-result pruner packages to inspect their published implementations. Upstream master documentation is supplementary; it can differ from the pinned release.

Usage diagnostics were committed before this audit in `5fe8acd`.

Fresh verification covered 59 targeted backend tests, 40 frontend tests, and
the frontend build. These are fixture-backed integration/unit checks, not a
measured live-Qwen recovery success rate. This audit changes no runtime behavior
or dependencies.

## Main conclusion

Keep Director business authority in Python, but let Harness own durable agent execution history, compaction and generic request recovery. The current per-HTTP-turn disposable session discarded much of the benefit of adopting Harness. Do not build a second summary store, transcript-repair algorithm, generic retry loop or context-meter policy.

A short-lived agent handle is not itself a problem: it can resume a stable persisted session. The problem is creating a new random session and importing plain chat rows every time.

## Native capabilities and integration status

| Capability | Native implementation | Current integration | Direction |
|---|---|---|---|
| Automatic history compaction | compaction-basic, token-meter; balanced tool boundaries, shrink validation, retained recent tail | Mounted; extra compaction retries set to 0 | Reuse, configure and test; do not write another summarizer |
| Confirmed context-overflow recovery | compaction-basic; retries only after replacement progress, bounded separately | Mounted with maxOverflowRetries=1; depends on Python recognizing provider overflow | Improve error mapping; preserve the progress requirement |
| Manual compression before retry | compaction.compactNow on an idle agent; transaction and cancellation handling | No host/API/UI entry | Expose this native operation after stable sessions exist |
| Durable summary and tool history | session events plus a persistence backend; agents.resume | Persistence interface exists in dependency tree, but no backend is mounted; fresh UUID per request | Add exact-version JSONL backend and stable project/conversation-to-session mapping |
| Crash history repair | persistence coordinator closes interrupted records, distinguishes TOOL_NOT_STARTED from TOOL_OUTCOME_UNKNOWN | Disposable session loses this history | Reuse; reconcile actual business effects before retrying uncertain mutations |
| Oversized tool-result pruning | compaction-tool-result-pruner; retains head/tail, original full result stays in log | Not mounted | Evaluate for large tool outputs, with targeted correctness tests |
| Transient model request retry | llm-retry; finite policy, exponential backoff/jitter, cancellation and retry events | Custom immediate retry loop in HostAdapter, up to two additional attempts | Replace generic loop after wiring native failure codes and adapter retry policy |
| Context and usage projections | tokenUsage, contextPressure, contextBreakdown, projectedTokens | Meter and projections mounted, but actual usage never emitted by HostAdapter | Forward provider usage and expose native projections; keep final-provider-call diagnostics where useful |
| Model request options | maxTokens, reasoningEffort, purpose and exact model metadata | Host boundary forwards messages and purpose only; actual model is hidden behind host/turn | Preserve supported options and truthful route metadata |
| Output-token exhaustion | AgentLoop records assistant completion and ends with max-tokens | Mapped to INCOMPLETE_TURN | Not repaired by overflow compaction or ordinary request-error retry; needs a bounded application policy |
| Tools and agent loop | Native parsing, tool dispatch, errors returned to model, serial/parallel control | Already using stock loop and serial tools | Keep; Python remains authoritative for domain validation |
| Multimodal content | LLM content blocks support images | Harness adapter advertises text-only and strips to text/tool records; Python attaches images afterward | Preserve safe attachment references/metadata or account for hidden payload; never silently claim complete token visibility |

## Concrete integration gaps

### 1. Session identity and persistence

`harness/src/kernel.ts:191` documents a disposable runtime; around line 293 it creates a random session ID. Only reply and thinking return to Python, and the handle/context are disposed at the end. The next request reseeds Python user/assistant text instead of resuming Harness's event log.

Mounting persistence alone is insufficient. The host needs stable session mapping, first-import-only behavior, resume handling, per-session exclusion, and explicit reset/model-change semantics. Python project/shot state remains authoritative and is refreshed on resume; a persisted transcript must not become a second project database.

Use a dedicated backend-controlled persistence root. Do not allow multiple
sidecar instances to write the same live session. JSONL has first-party Windows
handling; runtime/encoding compatibility must be tested on the actual Node
version. The default zstd physical encoding is separate from semantic context
compaction.

### 2. Model envelope and token accounting are not aligned

HostAdapter forwards only `messages` and `purpose`. Python replaces system/tools with business context, injects Director skill text, and attaches images. Therefore Harness's recorded request is not the exact final provider request.

The newly added Python usage telemetry reaches the UI, but HostAdapter emits no `usage` chunk to Harness. Its native meter cannot use the real provider anchor. Forwarding usage alone does not fix a mismatched request envelope.

The host also drops `options.maxTokens`; compaction-basic sets it explicitly, but Ollama currently uses the global Director num_predict. Generic host/turn and host/compaction labels hide the actual model route, and context_window is currently derived from Director settings even when another provider is selected.

Fix the adapter contract before relying on stock projections as the complete truth. Native usage uses disjoint uncached/cache-read/cache-write buckets; avoid double-counting when converting provider totals. Unknown counts must stay unknown. Native heuristic counting is still approximate, particularly for Chinese and JSON; it is not an exact tokenizer.

### 3. Native retry is useful, but not a cure for output truncation

The published retry plugin handles failed model requests through agent/request-error and a captured provider retry policy. Our TRANSIENT_LLM catch-all must be translated into supported error categories, or explicitly included in a bounded policy. Installing the plugin while leaving the inner retry loop would multiply attempts and obscure telemetry.

In the pinned AgentLoop implementation, max-tokens is handled after assistant/message is recorded and returns a terminal turn reason; it does not enter agent/request-error. Do not relabel output exhaustion as context overflow. A continuation should inspect the partial output and completed tools, change supported conditions where justified, and have a finite recovery budget.

Additional review finding: our adapter checks for tool_calls before checking finish_reason=length. A response containing both tool calls and a length finish can therefore be labeled tool-calls and bypass native max-tokens handling. Add a regression test and define safe precedence before implementing recovery. This was identified by source review, not reproduced against a live provider.

### 4. Existing safety wrapper needs reassessment, not blind removal

FailClosedCompaction stops our request if automatic compaction throws; stock pressure handling otherwise warns and continues. That is a deliberate host policy. However, its sticky failure state can also prevent the native recovery path that allows retry after durable pruning progress even if later summarization fails. Reevaluate this interaction when mounting the pruner.

Keep Python's project freshness checks, schema/domain validation, explicit layout-generation intent, mutation deduplication/receipts, and GPU/Comfy orchestration. Native transcript recovery can describe an unknown tool outcome; it cannot establish that a Comfy job or project mutation happened exactly once.

## Suggested order, not an implementation authorization

1. Repair the adapter contract: supported output/reasoning controls, exact route/capacity, usage, failure codes, finish precedence, and final-envelope alignment.
2. Introduce stable sessions plus the stock persistence backend; migrate existing text history once, then resume instead of reseeding.
3. Expose native compactNow and compaction/usage/retry events to the existing UI; retain provider-boundary debug observations as a separate view.
4. Replace the custom transient retry loop with bounded native retry. Evaluate stock tool-result pruning without assuming that head/tail trimming preserves every important business fact.
5. Add only the missing output-truncation continuation policy, with completed-tool reconciliation and condition changes. Do not implement unbounded always-mode recovery.

Acceptance checks should cover summary reuse across turns/restart, no duplicate seeded history, manual compression retaining the latest request, real provider usage reaching the meter, separate truncation/overflow behavior, compressed-history continuation, and no replay of uncertain mutations. No numerical reliability improvement can be claimed before live long-context/multi-shot Qwen tests.

## Sources

Installed package code and README files under `harness/node_modules/@deepseek-ai/` are the primary pinned-version evidence, especially compaction-basic, compaction, agent-loop, token-meter and session-persistence.

Supplementary official references:
- https://github.com/deepseek-ai/deepseek-harness
- https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/llm/llm-retry/README.md
- https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/compaction/compaction-tool-result-pruner/README.md
- https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/session/session-persistence-jsonl/README.md
