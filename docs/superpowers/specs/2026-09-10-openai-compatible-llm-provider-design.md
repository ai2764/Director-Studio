# OpenAI-Compatible Director LLM Provider Design

## Context

Director Studio currently exposes a small `LLMProvider` boundary for listing and selecting models, but the production inference path is still coupled to `OllamaClient`. Planning, chat, streaming, image input, tool calls, structured output, model warm-up, unloading, and VRAM status all reach Ollama-specific methods or payloads.

The first delivery must let one environment-selected provider run the complete Director workflow. LM Studio is the initial integration target. The same inference implementation must also cover OpenAI and services that expose the common OpenAI Chat Completions surface, including llama.cpp, OpenRouter, DeepSeek, vLLM, and LiteLLM.

## Goals

- Select exactly one active LLM provider from environment configuration.
- Preserve Ollama as the default with no required migration for existing users.
- Add a common OpenAI-compatible inference client based on the official OpenAI Python SDK.
- Support text chat, streaming, image input, custom tools, and schema-constrained JSON through a provider-neutral Director interface.
- Populate the existing model dropdown from the active provider API; do not require a model in `.env`.
- Keep model selection durable and scoped to the provider that supplied it.
- Let LM Studio unload its active model before local ComfyUI generation and reload it through JIT on the next Director request.
- Degrade only optional compatibility features, with explicit and bounded fallback behavior.

## Non-goals

- No provider selector or credential editor in the UI.
- No simultaneous or per-request provider routing.
- No automatic fallback from one vendor to another.
- No Responses API mode in the first delivery. Chat Completions is the common compatibility baseline.
- No vendor-specific lifecycle control for generic OpenAI-compatible services or llama.cpp in the first delivery.
- No changes to image-generation or H3 video providers.

## Configuration

`DS_LLM_PROVIDER` selects the only active provider:

```dotenv
# Existing default
DS_LLM_PROVIDER=ollama
DS_OLLAMA_BASE_URL=http://127.0.0.1:11434

# LM Studio inference plus native model lifecycle
DS_LLM_PROVIDER=lm-studio
DS_LLM_BASE_URL=http://127.0.0.1:1234/v1
DS_LLM_API_KEY=lm-studio

# OpenAI or another OpenAI-compatible endpoint
DS_LLM_PROVIDER=openai-compatible
DS_LLM_BASE_URL=https://api.openai.com/v1
DS_LLM_API_KEY=your-secret-key
```

`DS_LLM_API_KEY` is optional for unauthenticated local servers. The client supplies an internal non-secret placeholder when the SDK requires a non-empty value. A missing key against an authenticated endpoint remains an authentication error.

`DS_DIRECTOR_PLAN_MODEL` is no longer required. It remains a legacy Ollama-compatible fallback so existing deployments continue to start, but the normal model source is the active provider's catalog plus the persisted dropdown selection.

## Architecture

### Active provider

The application creates one `ActiveLLMProvider` at startup and shares it across Director APIs, services, and the VRAM orchestrator. The provider owns three independent responsibilities:

1. `LLMClient`: provider-neutral inference operations.
2. `ModelCatalog`: model discovery and selection persistence.
3. `LLMLifecycle`: local runtime health, warm/load, unload, and status behavior.

Keeping lifecycle separate from inference prevents LM Studio-specific model management from leaking into the OpenAI-compatible protocol client.

### Inference clients

The complete `LLMClient` protocol covers:

- non-streaming text completion;
- normalized message-based completion;
- token/reasoning streaming;
- image content;
- custom tools and tool-result turns;
- structured response formats.

`OllamaClient` is adapted to this protocol without changing its wire format.

`OpenAICompatibleClient` uses `AsyncOpenAI` with the configured `base_url` and only the widely supported `/v1/chat/completions` request subset. LM Studio and generic OpenAI-compatible providers share this client.

Director-facing responses use one normalized shape:

```json
{
  "content": "visible assistant text",
  "thinking": "optional reasoning text",
  "tool_calls": [
    {"id": "optional-call-id", "name": "tool_name", "arguments": {}}
  ],
  "finish_reason": "optional-provider-reason"
}
```

### Message conversion

- Existing system, user, assistant, and tool messages are converted at the client boundary.
- Raw base64 images are converted to OpenAI image content parts using data URLs.
- Tool definitions remain standard `{type: "function", function: ...}` definitions.
- Stringified tool arguments are parsed into objects before reaching the Director tool loop.
- OpenAI-compatible streaming deltas become the existing `{kind: "token"|"think", text: ...}` events, so the frontend stream contract does not change.
- Ollama schema objects are converted to OpenAI `response_format` JSON Schema objects with a stable generated schema name.

### Model catalog and persistence

The existing `/api/director/model` endpoint remains the frontend contract.

- Ollama lists `/api/tags` as it does today.
- Generic OpenAI-compatible providers list `/v1/models`.
- LM Studio uses its native `/api/v1/models` catalog so it can filter to `type=llm` and retain loaded-instance metadata. It returns model keys to the existing dropdown.
- If there is no valid persisted selection and the catalog is non-empty, the backend selects and persists the first returned LLM.
- Dropdown changes continue through `PUT /api/director/model`.

The durable file changes from an unscoped value to:

```json
{
  "provider": "lm-studio",
  "model": "openai/gpt-oss-20b"
}
```

An old `{ "model": "..." }` file is accepted only for the Ollama provider. A selection is invalid when its provider differs from the active provider or its model is absent from the current catalog. Invalid selections are replaced with the first available model. No API key or base URL is persisted in project data.

## Runtime and VRAM Coordination

`LLMLifecycle` exposes whether the provider consumes the local generation GPU and implements provider-specific runtime actions.

### Ollama lifecycle

The existing health, warm, loaded-model inspection, and unload behavior remains in place.

### LM Studio lifecycle

LM Studio inference uses the common OpenAI-compatible client. Lifecycle control uses its native REST API:

1. Query `GET /api/v1/models`.
2. Read `loaded_instances` for loaded LLMs.
3. Before ComfyUI acquires the GPU, call `POST /api/v1/models/unload` for every relevant loaded instance ID.
4. Confirm the unload calls succeeded before ComfyUI generation begins.
5. On the next Director request, allow the selected model to load through LM Studio JIT inference.

The configured `/v1` inference URL is normalized to the same server origin for `/api/v1` lifecycle calls. Authentication uses the same API token.

If LM Studio model enumeration or unloading fails, local ComfyUI execution fails closed with an actionable error instead of risking an out-of-memory run.

### Generic OpenAI-compatible lifecycle

Generic endpoints use a no-op remote lifecycle. Director requests do not unload ComfyUI, and ComfyUI requests do not call vendor-specific model endpoints. Existing project/job admission locks remain intact to prevent concurrent state mutations even when the LLM itself is remote.

### Runtime endpoints

`/api/director/wake` and `/api/director/vram` become provider-aware without removing existing response fields. Ollama-specific status fields are empty or false for other providers, while additive fields report the active provider and lifecycle kind. Waking LM Studio performs health preparation and lets the next inference trigger JIT; waking a generic remote provider performs only a connectivity check.

## Compatibility and Fallback Rules

The primary request always uses standard Chat Completions semantics.

- If a provider explicitly rejects custom tools as unsupported, retry once with Director Studio's existing text tool protocol.
- If a provider explicitly rejects JSON Schema response formatting, retry once with an explicit JSON-only instruction and retain application-side parsing and validation.
- Authentication, permission, rate-limit, timeout, and server failures do not trigger protocol fallback.
- A request marked `require_vision=true` fails visibly when image input is unsupported. Images are never silently removed from a visual review.
- Missing reasoning output is valid; the visible assistant response continues without a reasoning section.
- There is no cross-provider fallback and no automatic endpoint rewriting beyond normalizing a configured trailing slash.

Fallback attempts are bounded to one retry and recorded without logging prompts containing sensitive user material at normal log levels.

## Error Handling and Security

- An unreachable catalog returns `reachable=false`; the existing UI disables chat and labels the provider unavailable.
- An empty catalog leaves the selected model empty and displays `No models available`.
- Missing or stale model selections are reconciled against the current catalog before inference.
- Provider errors are normalized into concise actionable messages while retaining the original exception for server logs.
- API keys are read only from environment-backed settings and are never returned by APIs, persisted, or logged.
- HTTP clients use explicit connect and request timeouts and close cleanly during application shutdown.

## Testing

### Automated tests

- Provider factory selects exactly one configured provider and preserves Ollama defaults.
- Provider-scoped model persistence migrates legacy Ollama state and rejects stale or cross-provider state.
- OpenAI-compatible `/v1/models` discovery populates the existing API response.
- LM Studio catalog filtering returns LLM model keys and loaded-instance metadata.
- Text, streaming, image, tool, tool-result, and JSON Schema requests are converted correctly.
- OpenAI-compatible responses and stream deltas normalize to existing Director shapes.
- Unsupported tool and schema responses receive exactly one eligible fallback.
- Authentication and transient server failures never masquerade as unsupported features.
- Required vision failures do not fall back to text.
- LM Studio unload enumerates and unloads loaded instances before ComfyUI ownership.
- Failed LM Studio unload prevents the ComfyUI job from starting.
- Generic remote providers never invoke local model lifecycle methods.
- Existing Ollama, Director chat, planning, tool-loop, and VRAM tests remain green.

### LM Studio acceptance run

1. Configure only `DS_LLM_PROVIDER=lm-studio` and `DS_LLM_BASE_URL=http://127.0.0.1:1234/v1`.
2. Start the LM Studio server with at least one chat model available.
3. Verify the existing Director model dropdown populates without `DS_DIRECTOR_PLAN_MODEL`.
4. Select a model and verify the selection survives an application restart.
5. Complete a normal streamed Director conversation.
6. Complete a Director action that requires a tool call.
7. With a vision-capable model, upload an image and complete visual review.
8. Start a local ComfyUI generation and verify LM Studio unloads all relevant loaded instances first.
9. Send another Director message and verify LM Studio JIT reloads the selected model.

## Delivery Boundaries

Implementation will be developed from the current local `main` branch on `feat/openai-compatible-llm`. The first delivery is complete when the automated suite passes and the LM Studio acceptance run validates catalog discovery, inference, tool use, visual input, unload-before-Comfy, and JIT reload. Support for a vendor means protocol compatibility, not identical availability of every optional model capability.
