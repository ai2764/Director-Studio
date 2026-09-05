# DeepSeek Harness local Agent runtime experiment

## Status

Approved in design review on 2026-09-02. Implementation is split into a vertical feasibility spike and a hardened experimental runtime. Legacy remains the default until the evaluation gates in this document pass.

## Goal

Integrate DeepSeek Harness as an alternative Director Agent runtime and compare it with Director Studio's existing Python chat orchestrator under controlled conditions. Harness owns session history, context compaction, the LLM/tool loop, tool-result feedback, cancellation, resume, busy/thinking state, and runtime telemetry. Director Studio remains authoritative for every film-production domain object and operation.

The first comparison uses the same local Ollama model on both paths:

`orcarouter/Qwen3.8-27B-Uncensored:latest`

The experiment must isolate runtime behavior from model behavior. It does not use a DeepSeek cloud model or any other cloud Agent model.

## Scope boundaries

Harness is responsible only for the Agent runtime. It must not reimplement or directly own:

- projects, shots, references, assets, prompts, layouts, or human approval gates;
- ComfyUI or the installed Comfy MCP integration;
- the official local H3 workflow;
- the MiniMax official H3 API provider;
- the generation job runner;
- the VRAM orchestrator;
- Director Studio's local multimodal image-analysis implementation.

The existing H3 provider choice remains unchanged. Local Comfy MCP and the MiniMax API provider stay available behind the existing FastAPI domain layer.

## Current system

The legacy Director chat path combines prompt and project-context assembly, a bounded Ollama tool loop, dynamic tool schemas, tool execution, tool-result feedback, completion-claim correction, and progress events in Python. FastAPI separately owns durable project chat history, the process-local active-session registry, cancellation endpoints, and SSE delivery. The current VRAM orchestrator serializes Ollama and Comfy ownership and releases the LLM on cancellation or generation handoff.

The Harness experiment replaces only the legacy Agent-loop responsibilities. Existing domain services and tool handlers remain the implementation behind the new bridge.

## Considered approaches

### Node sidecar with a Director HTTP bridge — selected

A local Node process runs Harness with a Director Cordis tool plugin. FastAPI remains the external gateway. Harness calls loopback-only FastAPI endpoints for model inference, project context, vision, and Director tools.

This preserves Harness's native sessions, compaction, tool registry, loop, cancellation, and event stream while keeping Python domain and VRAM authority intact.

### Node sidecar with a Director MCP adapter

An MCP server would expose Director tools to Harness through a standard discovery and execution protocol. This is a reasonable later portability layer, but it adds MCP lifecycle, dynamic-schema, authentication, and error-mapping work to the first experiment. It would make it harder to attribute failures specifically to Harness.

### FastAPI with the Harness Python SDK

This reduces the visible language boundary but still launches a packaged runtime subprocess. The official packaged Python runtime is not the preferred Windows integration surface, and custom tool and cancellation wiring is less direct than a native Node composition. This approach is not selected.

## Architecture

```text
React Director UI
        |
        | existing project chat and session APIs
        v
FastAPI runtime router
   |                         \
   | legacy                   \ harness
   v                           v
Python chat orchestrator     Node Harness sidecar
                               |  |  |
             model requests ---+  |  +--- Harness session/event store
             Director tools ------+
                               |
                               v
                     FastAPI internal bridges
                       |                 |
                       | model           | domain/vision tools
                       v                 v
                 VRAM orchestrator   existing DirectorService,
                       |             tool handlers, job runner,
                       v             Comfy MCP, H3 providers
                 local Ollama
```

The sidecar listens only on loopback. `start.ps1` starts it, checks its health, and starts the existing application services. FastAPI continues to expose the public chat API; the browser never calls the sidecar directly.

`DS_DIRECTOR_AGENT_RUNTIME=legacy|harness` selects the runtime. The configuration default is `legacy`. The application must never switch runtimes silently in the middle of a turn or treat an unavailable Harness sidecar as a reason to fall back.

The Harness dependency is pinned to an exact version in the sidecar lockfile during the Phase 1 feasibility work. Harness is a developer-preview dependency, so upgrades are explicit implementation tasks rather than floating installs.

## Authority model

Harness's append-only session event log is the only authoritative Agent interaction history for Harness sessions. It owns user and assistant messages, turn and step boundaries, tool calls and results, compaction events, cancellation, and resume state. Reusing a Harness session ID resumes that durable conversation.

FastAPI remains the only authority for project, shot, reference, asset, prompt, job, H3, and VRAM state. Harness summaries and transcripts cannot overwrite domain data. At the start of a turn and after resume or compaction, the runtime refreshes current project state through FastAPI.

FastAPI persists the mapping from a Director project to its Harness session ID. Existing legacy chat history is imported once when the first Harness session is created, with its imported origin recorded. After that cutover, `chat.jsonl` is a UI compatibility projection for the Harness path, not an Agent-context source. The projection is rebuildable from Harness events.

Legacy and Harness histories remain logically distinct after the one-time import. Automated A/B evaluation uses cloned project fixtures and independent sessions so one runtime cannot mutate the other runtime's input state.

## LLM and VRAM boundary

Harness must not call Ollama directly. A direct call would bypass Director Studio's existing serialization between Ollama and Comfy.

FastAPI exposes a loopback-only, internally authenticated, OpenAI-compatible LLM gateway. Harness configures this gateway as its local model provider. For each model request, the gateway:

1. enters the existing `VramOrchestrator.llm_session()` context;
2. releases Comfy models and ensures the selected Ollama model is ready;
3. translates messages, tool schemas, generation options, and cancellation into the existing Ollama client;
4. streams a provider-compatible response to Harness;
5. releases the VRAM owner in `finally` on success, error, disconnect, or cancellation.

This is a transport adapter, not a second VRAM policy. It does not alter the existing orchestrator's ownership rules. Between Harness model calls, a Director tool may queue local generation through the existing job path, which continues to acquire Comfy ownership normally.

The model gateway accepts only the configured local model and an internal token. It is not a general public inference endpoint.

## Vision boundary

Director Studio's existing local multimodal path remains in FastAPI. Harness receives a Director vision tool whose executor calls that path and returns structured observations. Harness does not own image bytes, select a vision model, or coordinate visual-model VRAM.

Legacy and Harness evaluation cases must use the same visual inputs and the same FastAPI visual-analysis implementation. Tool and telemetry records may store image identifiers and captions, but not image binary payloads.

## Tool bridge

The Director Cordis plugin obtains the allowed tool schemas for the current project and turn from FastAPI. Harness presents only those tools to the model. Dynamic schema snapshots are logged in the Harness request header so a run is reconstructable.

Each execution request includes:

- `project_id`;
- `harness_session_id`;
- `turn_id` and `tool_call_id`;
- tool name and JSON arguments;
- an idempotency key derived from session and tool-call identity;
- an opaque `state_version` observed when the call was made. FastAPI derives it from the current persisted project and Shot state; it does not add a new revision field to the Project domain model.

FastAPI verifies the internal token, project/session association, offered-tool allowlist, arguments, and revision constraints before delegating to the existing Python tool executor. The bridge returns a stable envelope with:

- `ok`;
- structured `data` or a structured `error`;
- affected project object identifiers;
- the observed and resulting state versions;
- a durable `commit_receipt` only when a state-changing operation succeeds.

An idempotency ledger stores the result envelope for state-changing calls. Replaying the same idempotency key returns the original result and cannot repeat a domain side effect.

## Turn and event flow

1. The existing FastAPI chat endpoint checks project availability and reserves the active chat session.
2. FastAPI resolves or creates the project's Harness session and sends the user message, attachment identifiers, and current state version to the sidecar.
3. Harness records the user message, assembles the current system prompt and tool schemas, and requests the local model through the FastAPI LLM gateway.
4. Harness dispatches any tool call through the Director plugin and appends the canonical tool result to its session log.
5. The loop continues until Harness produces a final response, reaches a controlled failure, or is cancelled.
6. Sidecar notifications are translated by FastAPI into the existing `status`, `runtime`, `think`, `token`, `tool`, `result`, and `error` SSE shapes.
7. The final assistant message and presentation metadata are projected into the existing UI history format.
8. FastAPI clears the active session in terminal cleanup without deleting the durable Harness session.

A dropped browser SSE connection does not cancel the turn. Explicit cancellation calls the existing project cancel endpoint, which forwards cancellation to Harness and any in-flight LLM gateway or Director tool request.

## Completion integrity

Prompt instructions prohibit completion claims before a successful tool result. Enforcement does not rely on the prompt alone.

Every turn maintains a success ledger populated only from valid `commit_receipt` values. The sidecar final-response guard examines state-change completion claims. A claim that work was saved, created, changed, queued, approved, or completed is allowed only when the current turn has a matching successful receipt. Otherwise the claim is suppressed and the user receives an authoritative statement that no project change was confirmed.

Tool failures, validation errors, and blocked domain transitions are model-visible results. The Agent must explain the actual failure or ask one concrete question; it cannot turn a failed result into a success narrative.

## Cancellation and recovery

Cancellation propagates from FastAPI to Harness and then through the active model or tool request's abort signal. All model-gateway VRAM contexts release in `finally`. A domain mutation that committed before cancellation remains committed and is discoverable through its receipt and the current project state.

On restart or interrupted-turn recovery, Harness loads the same session log, closes an unbalanced turn as interrupted according to its persistence contract, and refreshes project state before further action. Unknown-result mutations are not blindly repeated. If a prior tool call has an idempotency key, the bridge is queried with that key to recover its durable result.

FastAPI startup reconciles project-to-session mappings with the sidecar. An unreachable sidecar produces an explicit Harness-unavailable status. It does not start a legacy continuation of the same turn.

## Error and retry policy

- Sidecar unavailable: return `503 harness_unavailable`; never silently fall back mid-turn.
- Transient LLM gateway failure: Harness may retry at most twice under its bounded provider retry policy.
- Invalid tool arguments: return structured validation details; retrying identical arguments is rejected, and the model must correct them.
- Stale state version: refresh project state and permit at most one corrected retry.
- Domain rejection or missing critical reference: return a normal failed tool result and require a grounded explanation or concrete question.
- Lost response after a committed tool: replay the stored result for the same idempotency key.
- Cancellation: report a cancellation terminal state, not a generic inference failure.
- Sidecar crash: preserve completed session events and mark incomplete work interrupted; do not fabricate an assistant result.

## Observability

Both runtimes emit a common local telemetry schema with:

- runtime, model, project fixture, session, turn, step, and tool-call IDs;
- tool offered, selected, and argument-validation outcomes;
- retries, error categories, and terminal results;
- project state version before and after a tool call;
- receipt creation and idempotent replay;
- completion-claim guard decisions;
- cancellation request and settlement times;
- compaction boundaries and token counts;
- critical project, shot, reference, and state-version facts retained after compaction.

Telemetry excludes image bytes, API keys, internal authentication tokens, and full private file contents. The common schema is the data source for the A/B report.

## Evaluation design

Legacy and Harness runs use the same model, system instructions, generation settings, project snapshot, visual-analysis implementation, and offered domain tools. Each runtime receives an independent clone of the input fixture. Repeated runs record nondeterminism rather than allowing one run to mutate another run's baseline.

The report measures:

- correct tool selection rate;
- valid argument rate;
- recovery rate after a failed tool call;
- false completion-claim rate;
- cancellation settlement and VRAM-release behavior;
- successful resume behavior;
- retention of project, shot, reference, and state-version facts after compaction.

Required scenarios include:

1. Plan a complete shot list from a script.
2. Add, remove, and replace references after planning.
3. Bind an exact scene asset and `file_key` without reinterpreting filename tokens.
4. Detect missing critical character, scene, prop, costume, or continuity references and ask one concrete question.
5. Rewrite an H3 prompt after reference changes using current grounded state.
6. Correct invalid tool arguments and retry successfully.
7. Refuse to claim completion after a failed tool result.
8. Cancel during thinking, model streaming, and tool execution, then resume.
9. Force context compaction and verify critical state-version retention and refresh.
10. Alternate Agent and local generation work without leaking the VRAM owner.

Safety gates are absolute: false completion claims must be zero in the acceptance suite; cancellation must release the VRAM owner; tools must not bypass FastAPI validation; idempotent replay must not duplicate side effects; and Harness failure must not silently continue on legacy.

The experiment succeeds when the integration is measurable, reversible, and safe. Harness is not required to outperform legacy in every metric. A negative or neutral result is valid if the evidence identifies the runtime trade-offs reliably.

## Delivery phases

### Implementation isolation

Implementation may run in a dedicated Git worktree created from the approved spec commit. The worktree uses a descendant `codex/` implementation branch and is integrated back into the Harness spike branch only after its phase checks pass. The original checkout's existing stashes are never applied, dropped, or rewritten, and the untracked `.codex-remote-attachments/` and `exports/` directories remain outside every commit.

### Phase 1 — vertical feasibility spike

Estimated effort: 4–6 engineering days.

Deliver:

- pinned Node Harness sidecar running natively on Windows;
- health and lifecycle integration in `start.ps1`;
- FastAPI runtime flag and sidecar client;
- loopback LLM gateway through the existing VRAM orchestrator;
- durable Harness session creation and resume;
- existing SSE event compatibility;
- three to five representative Director tools covering read, validation failure, successful mutation, and a generation handoff;
- cancellation and VRAM-release integration tests;
- a go/no-go report for the hardened experiment.

Phase 1 stops without expanding the integration if Harness cannot reliably use the local model, propagate cancellation, preserve sessions, or coexist with the current VRAM policy.

### Phase 2 — hardened experimental runtime

Estimated additional effort: 8–13 engineering days.

Deliver:

- complete dynamic Director tool catalog;
- local vision tool;
- one-time legacy history import and Harness-derived UI projection;
- compaction configuration and state-refresh hooks;
- idempotency ledger and completion-integrity guard;
- restart reconciliation and structured failures;
- common telemetry and the full local A/B evaluation suite.

Legacy remains the default after Phase 2 until the evaluation report is reviewed separately.

## Test strategy

Unit tests cover schema conversion, event mapping, session mapping, internal authentication, idempotency, receipt enforcement, completion-claim guarding, and telemetry aggregation.

Contract tests cover the Harness-to-gateway model protocol, Director tool bridge envelopes, cancellation propagation, dynamic tool allowlists, and stable SSE projection.

Integration tests run FastAPI with temporary project storage and a controlled sidecar or fake model. They cover successful and failed tools, duplicate calls, cancellation, sidecar interruption, resume, compaction, stale state versions, and VRAM owner cleanup.

Live local A/B tests run the selected Ollama model against cloned fixtures. Expensive H3 generation is not required to validate Agent orchestration; H3 submission boundaries may use controlled provider fakes while preserving the real FastAPI validation and job-queue path.

## Non-goals

- Rewriting the backend in TypeScript.
- Moving Director domain logic into Harness.
- Replacing Comfy MCP or embedding it in the Agent runtime.
- Changing local or cloud H3 provider behavior.
- Rewriting the VRAM orchestrator or generation job runner.
- Adding a cloud Agent model or cloud fallback.
- Redesigning the Director chat UI.
- Making Harness the default runtime as part of this implementation.
