# OpenAI-Compatible Director LLM Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the complete Director planning and chat workflow through one environment-selected Ollama, LM Studio, or generic OpenAI-compatible provider, with API-discovered models and LM Studio unload-before-Comfy lifecycle control.

**Architecture:** A process-wide active provider composes a provider-neutral inference client, a model catalog/selection layer, and a lifecycle adapter. Ollama keeps its native transport; LM Studio and generic providers share an official OpenAI SDK Chat Completions client, while only LM Studio adds native model enumeration and unloading. The VRAM orchestrator delegates model lifecycle to the active provider and bypasses local GPU ownership work for remote providers.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic Settings, `openai>=3.9,<4`, `httpx`, pytest, React/Vitest regression suite

**Spec:** `docs/superpowers/specs/2026-09-10-openai-compatible-llm-provider-design.md`

## Global Constraints

- `DS_LLM_PROVIDER` selects exactly one of `ollama`, `lm-studio`, or `openai-compatible`; the default is `ollama`.
- A model is not required in `.env`; the active provider API populates the existing dropdown.
- OpenAI-compatible inference uses `/v1/chat/completions`, not Responses API, in this delivery.
- Required visual requests never silently discard images.
- LM Studio unload failure prevents local ComfyUI execution from starting.
- Generic OpenAI-compatible providers never receive vendor-specific lifecycle calls.
- API keys must never be persisted, returned by APIs, or logged.
- Existing Ollama configuration and behavior remain backward compatible.

---

## File Structure

- `backend/app/config.py`: environment configuration for the active provider.
- `backend/app/core/vram/director_model.py`: provider-scoped runtime and persisted model selection.
- `backend/app/core/llm/provider.py`: inference, lifecycle, and active-provider protocols plus normalized result types.
- `backend/app/core/llm/openai_compatible.py`: OpenAI SDK transport, message conversion, streaming, and bounded compatibility fallbacks.
- `backend/app/core/llm/lifecycle.py`: remote no-op and LM Studio native lifecycle adapters.
- `backend/app/core/llm/ollama.py`: Ollama provider adapter implementing the expanded contracts.
- `backend/app/core/llm/factory.py`: single cached active-provider construction.
- `backend/app/core/llm/__init__.py`: public provider exports.
- `backend/app/core/vram/orchestrator.py`: provider-neutral local GPU coordination.
- `backend/app/agents/director/llm_plan_provider.py`: Director skill injection over the active inference client.
- `backend/app/api/projects.py`: route chat through the active inference client.
- `backend/app/api/director.py`: provider-aware catalog, wake, and runtime status endpoints.
- `backend/app/api/health.py`: active-provider health rather than unconditional Ollama health.
- `backend/app/scripts/wake_agent.py`: provider-neutral command-line wake path.
- `backend/.env.example`, `README.md`, `backend/requirements.txt`: configuration and dependency documentation.
- `backend/tests/test_config.py`, `test_director_model_runtime.py`, `test_llm_provider.py`, `test_openai_compatible_client.py`, `test_lmstudio_lifecycle.py`, `test_vram_orchestrator.py`, `test_projects_api.py`, `test_director_vram_api.py`: focused and regression coverage.

---

### Task 1: Provider Configuration and Provider-Scoped Model Selection

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/core/vram/director_model.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config.py`
- Test: `backend/tests/test_director_model_runtime.py`

**Interfaces:**
- Produces settings `llm_provider`, `llm_base_url`, `llm_api_key`, and `llm_timeout_sec`.
- Produces `get_director_model(provider_id: str | None = None) -> str`.
- Produces `set_director_model(model: str, *, provider_id: str | None = None, persist: bool = True) -> str`.
- Produces persisted state `{ "provider": string, "model": string }`.

- [ ] **Step 1: Write failing configuration tests**

Add tests that instantiate `Settings` with a controlled empty env and with provider variables:

```python
def test_llm_provider_defaults_to_ollama(monkeypatch, tmp_path):
    monkeypatch.delenv("DS_LLM_PROVIDER", raising=False)
    configured = Settings(_env_file=None, data_dir=tmp_path)
    assert configured.llm_provider == "ollama"
    assert configured.llm_base_url == ""
    assert configured.llm_api_key is None


def test_lmstudio_provider_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("DS_LLM_PROVIDER", "lm-studio")
    monkeypatch.setenv("DS_LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    configured = Settings(_env_file=None, data_dir=tmp_path)
    assert configured.llm_provider == "lm-studio"
    assert configured.llm_base_url == "http://127.0.0.1:1234/v1"
```

- [ ] **Step 2: Run configuration tests and verify failure**

Run: `cd backend; pytest tests/test_config.py -q`

Expected: FAIL because the new settings do not exist.

- [ ] **Step 3: Add settings and example environment values**

Add these settings while preserving `ollama_base_url`:

```python
from typing import Literal

llm_provider: Literal["ollama", "lm-studio", "openai-compatible"] = "ollama"
llm_base_url: str = ""
llm_api_key: str | None = None
llm_timeout_sec: float = 600.0
```

Document all three provider configurations in `backend/.env.example`. Do not add a committed secret value.

- [ ] **Step 4: Write failing provider-scoped persistence tests**

Replace direct string-only expectations with explicit provider cases:

```python
def test_model_selection_is_scoped_to_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "_persist_path", lambda: tmp_path / "director_model.json")
    dm.set_director_model("qwen-local", provider_id="ollama", persist=True)
    assert dm.get_director_model("ollama") == "qwen-local"
    assert dm.get_director_model("lm-studio") == ""


def test_legacy_model_file_only_applies_to_ollama(tmp_path, monkeypatch):
    path = tmp_path / "director_model.json"
    path.write_text('{"model":"legacy-qwen"}', encoding="utf-8")
    monkeypatch.setattr(dm, "_persist_path", lambda: path)
    assert dm.get_director_model("ollama") == "legacy-qwen"
    assert dm.get_director_model("openai-compatible") == ""
```

- [ ] **Step 5: Run persistence tests and verify failure**

Run: `cd backend; pytest tests/test_director_model_runtime.py -q`

Expected: FAIL because the functions do not accept `provider_id` and persistence is unscoped.

- [ ] **Step 6: Implement provider-scoped selection**

Store the runtime override as a provider/model pair, accept legacy state only for Ollama, and use `settings.director_plan_model` only as an Ollama fallback:

```python
_override: tuple[str, str] | None = None


def _active_provider(provider_id: str | None) -> str:
    return (provider_id or settings.llm_provider or "ollama").strip()


def get_director_model(provider_id: str | None = None) -> str:
    provider = _active_provider(provider_id)
    if _override and _override[0] == provider:
        return _override[1]
    persisted = _read_persisted()
    if persisted and persisted["provider"] == provider:
        return persisted["model"]
    if provider == "ollama":
        return (settings.director_plan_model or "").strip()
    return ""


def set_director_model(model: str, *, provider_id: str | None = None, persist: bool = True) -> str:
    global _override
    provider = _active_provider(provider_id)
    name = (model or "").strip()
    if not name:
        raise ValueError("model name must be non-empty")
    _override = (provider, name)
    if persist:
        _write_persisted({"provider": provider, "model": name})
    _mark_runtime_model_changed()
    return name
```

Return provider-aware `override`, `persisted`, and `source` values from `model_status(provider_id=None)` without exposing unrelated provider selections.

- [ ] **Step 7: Run focused tests**

Run: `cd backend; pytest tests/test_config.py tests/test_director_model_runtime.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```powershell
git add backend/app/config.py backend/app/core/vram/director_model.py backend/.env.example backend/tests/test_config.py backend/tests/test_director_model_runtime.py
git commit -m "feat: configure active llm provider"
```

---

### Task 2: Provider-Neutral Protocol and OpenAI-Compatible Inference Client

**Files:**
- Modify: `backend/app/core/llm/provider.py`
- Create: `backend/app/core/llm/openai_compatible.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/test_openai_compatible_client.py`

**Interfaces:**
- Produces `LLMResult`, `LLMClient`, `LLMLifecycle`, and `LLMProvider` contracts.
- Produces `UnsupportedLLMFeatureError(feature: Literal["tools", "response_format", "vision"], message: str)`.
- Produces `OpenAICompatibleClient(base_url: str, api_key: str | None, timeout: float, client: AsyncOpenAI | None = None)`.
- `OpenAICompatibleClient` implements `list_models`, `health`, `generate`, `chat`, `chat_response`, `generate_stream`, and `chat_stream` with Ollama-compatible method signatures.

- [ ] **Step 1: Write failing request-normalization tests**

Use a recording fake for `client.chat.completions.create` and assert exact SDK arguments:

```python
@pytest.mark.asyncio
async def test_chat_response_converts_images_tools_and_schema(recording_openai):
    client = OpenAICompatibleClient(
        base_url="http://127.0.0.1:1234/v1",
        api_key=None,
        timeout=10,
        client=recording_openai,
    )
    result = await client.chat_response(
        "vision-model",
        messages=[{"role": "user", "content": "inspect", "images": ["aGVsbG8="]}],
        tools=[{"type": "function", "function": {"name": "save", "parameters": {"type": "object"}}}],
        format={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        require_vision=True,
    )
    sent = recording_openai.chat.completions.calls[0]
    assert sent["messages"][0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,aGVsbG8="
    assert sent["response_format"]["type"] == "json_schema"
    assert result["tool_calls"] == [{"id": "call_1", "name": "save", "arguments": {"ok": True}}]
```

Also test `/v1/models` IDs, plain text generation, tool argument JSON parsing, provider reasoning fields, and omission of Ollama-only `num_gpu`/`keep_alive` options.

- [ ] **Step 2: Run client tests and verify failure**

Run: `cd backend; pytest tests/test_openai_compatible_client.py -q`

Expected: FAIL because the client and contracts do not exist.

- [ ] **Step 3: Define normalized protocols and errors**

Expand `provider.py` with concrete contracts:

```python
class LLMResult(TypedDict, total=False):
    content: str
    thinking: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str


class UnsupportedLLMFeatureError(RuntimeError):
    def __init__(self, feature: Literal["tools", "response_format", "vision"], message: str):
        self.feature = feature
        super().__init__(message)


class LLMClient(Protocol):
    async def list_models(self) -> list[str]:
        raise NotImplementedError
    async def health(self) -> bool:
        raise NotImplementedError
    async def chat_response(self, model: str, *, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]] | None = None, format: dict[str, Any] | str | None = None, require_vision: bool = False, **kwargs: Any) -> LLMResult:
        raise NotImplementedError
    async def generate(self, model: str, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError
    async def chat(self, model: str, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError
```

Define `LLMLifecycle` with `uses_local_gpu`, `release_failure_is_fatal`, `prepare`, `release`, and `status`; expand `LLMProvider` to expose `client` and `lifecycle` alongside catalog/selection methods.

- [ ] **Step 4: Add the official SDK dependency**

Add exactly:

```text
openai>=3.9,<4
```

to `backend/requirements.txt`.

- [ ] **Step 5: Implement common request conversion**

Create `openai_compatible.py` with focused helpers:

```python
def _image_url(value: str) -> str:
    return value if value.startswith(("data:", "http://", "https://")) else f"data:image/jpeg;base64,{value}"


def _messages(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for source in items:
        message = dict(source)
        images = list(message.pop("images", []) or [])
        if images:
            text = str(message.get("content") or "")
            message["content"] = [
                {"type": "text", "text": text},
                *({"type": "image_url", "image_url": {"url": _image_url(image)}} for image in images),
            ]
        converted.append(message)
    return converted


def _response_format(schema: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if schema is None:
        return None
    if isinstance(schema, str):
        return {"type": "json_object"} if schema == "json" else {"type": schema}
    if schema.get("type") in {"json_schema", "json_object", "text"}:
        return schema
    return {"type": "json_schema", "json_schema": {"name": "director_output", "strict": True, "schema": schema}}
```

- [ ] **Step 6: Implement non-streaming and streaming calls**

Construct `AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url.rstrip("/"), timeout=timeout, max_retries=0)`. Normalize `response.choices[0].message`, parse function arguments with `json.loads`, and read optional reasoning from `reasoning_content`, `reasoning`, or `model_extra`.

For streaming, yield only stable application events:

```python
request = {"model": model, "messages": _messages(messages), "stream": True}
async for chunk in await self._client.chat.completions.create(**request):
    delta = chunk.choices[0].delta
    thinking = _field(delta, "reasoning_content") or _field(delta, "reasoning")
    if thinking:
        yield {"kind": "think", "text": str(thinking)}
    if delta.content:
        yield {"kind": "token", "text": delta.content}
```

Classify a 400/404/422 as `UnsupportedLLMFeatureError` only when the response body explicitly names an unsupported `tools`, `response_format`/`json_schema`, or image/multimodal feature. Re-raise authentication, rate-limit, timeout, and 5xx errors unchanged.

- [ ] **Step 7: Add fallback and strict-vision tests**

Test one schema fallback, error non-fallback, and required vision behavior:

```python
@pytest.mark.asyncio
async def test_schema_rejection_retries_once_without_response_format(schema_rejecting_openai):
    client = OpenAICompatibleClient(
        base_url="http://127.0.0.1:1234/v1",
        api_key=None,
        timeout=10,
        client=schema_rejecting_openai,
    )
    result = await client.chat_response("model", messages=[{"role": "user", "content": "json"}], format={"type": "object"})
    assert len(schema_rejecting_openai.chat.completions.calls) == 2
    assert "response_format" not in schema_rejecting_openai.chat.completions.calls[1]
    assert result["content"] == '{"ok":true}'


@pytest.mark.asyncio
async def test_required_vision_rejection_is_not_retried_as_text(vision_rejecting_openai):
    client = OpenAICompatibleClient(
        base_url="http://127.0.0.1:1234/v1",
        api_key=None,
        timeout=10,
        client=vision_rejecting_openai,
    )
    with pytest.raises(UnsupportedLLMFeatureError, match="vision"):
        await client.chat_response("text-model", messages=[{"role": "user", "content": "look", "images": ["aGVsbG8="]}], require_vision=True)
    assert len(vision_rejecting_openai.chat.completions.calls) == 1
```

- [ ] **Step 8: Run focused client tests**

Run: `cd backend; pytest tests/test_openai_compatible_client.py -q`

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

```powershell
git add backend/app/core/llm/provider.py backend/app/core/llm/openai_compatible.py backend/requirements.txt backend/tests/test_openai_compatible_client.py
git commit -m "feat: add openai compatible llm client"
```

---

### Task 3: Active Provider Factory and API-Discovered Models

**Files:**
- Create: `backend/app/core/llm/lifecycle.py`
- Create: `backend/app/core/llm/factory.py`
- Modify: `backend/app/core/llm/ollama.py`
- Modify: `backend/app/core/llm/__init__.py`
- Modify: `backend/app/api/director.py`
- Modify: `backend/tests/test_llm_provider.py`
- Create: `backend/tests/test_lmstudio_lifecycle.py`

**Interfaces:**
- Produces cached `get_llm_provider() -> LLMProvider` and test-only `reset_llm_provider() -> None`.
- Produces `RemoteLifecycle`, `LMStudioLifecycle`, `OllamaLifecycle`.
- `LMStudioLifecycle.list_models() -> list[str]` filters native catalog entries to `type == "llm"`.
- `/api/director/model` reconciles the stored selection against the active catalog.

- [ ] **Step 1: Write failing factory and catalog tests**

Cover all configured provider values and one active singleton:

```python
def test_factory_builds_one_lmstudio_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "lm-studio")
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:1234/v1")
    reset_llm_provider()
    first = get_llm_provider()
    second = get_llm_provider()
    assert first is second
    assert first.provider_id == "lm-studio"
    assert first.lifecycle.uses_local_gpu is True


@pytest.mark.asyncio
async def test_stale_selection_is_replaced_by_first_catalog_model():
    provider = EmptySelectionProvider(["current-a", "current-b"], current="removed")
    result = await director.get_model(provider=provider)
    assert provider.selected == ("current-a", True)
    assert result["model"] == "current-a"
```

- [ ] **Step 2: Run provider tests and verify failure**

Run: `cd backend; pytest tests/test_llm_provider.py tests/test_lmstudio_lifecycle.py -q`

Expected: FAIL because the factory and lifecycle adapters do not exist and stale selection is not reconciled.

- [ ] **Step 3: Implement provider classes and cached factory**

Build providers around their client and lifecycle:

```python
@dataclass
class ActiveProvider:
    provider_id: str
    client: LLMClient
    lifecycle: LLMLifecycle
    catalog: Callable[[], Awaitable[list[str]]]

    async def list_models(self) -> list[str]:
        return await self.catalog()

    def model_status(self) -> dict[str, Any]:
        return model_status(self.provider_id)

    def select_model(self, model: str, *, persist: bool) -> str:
        return set_director_model(model, provider_id=self.provider_id, persist=persist)
```

The factory defaults empty LM Studio URLs to `http://127.0.0.1:1234/v1`, defaults empty generic URLs to `https://api.openai.com/v1`, and uses the existing Ollama URL for Ollama. Cache exactly one provider with `functools.lru_cache(maxsize=1)` and expose `reset_llm_provider()` to clear it in tests and application reconfiguration tests.

- [ ] **Step 4: Implement LM Studio native catalog and lifecycle HTTP helpers**

Normalize the inference URL to an origin and query the native API:

```python
def _server_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


async def list_models(self) -> list[str]:
    payload = await self._get("/api/v1/models")
    return [str(item["key"]) for item in payload.get("models", []) if item.get("type") == "llm" and item.get("key")]
```

Lifecycle unloading is implemented in Task 4; this task provides the tested native request/authentication and catalog parsing primitives.

- [ ] **Step 5: Reconcile API selection against the live catalog**

Change `get_model` so an empty or absent selection is replaced:

```python
selected = str(status.get("model") or "").strip()
if reachable and available and selected not in available:
    provider.select_model(available[0], persist=True)
    status = provider.model_status()
```

Update `DirectorModelBody` copy from Ollama-specific wording to active-provider model identifiers.

- [ ] **Step 6: Run provider/catalog tests**

Run: `cd backend; pytest tests/test_llm_provider.py tests/test_lmstudio_lifecycle.py tests/test_director_model_runtime.py -q`

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/app/core/llm backend/app/api/director.py backend/tests/test_llm_provider.py backend/tests/test_lmstudio_lifecycle.py backend/tests/test_director_model_runtime.py
git commit -m "feat: select active llm provider"
```

---

### Task 4: Provider-Neutral VRAM Orchestration and LM Studio Unload

**Files:**
- Modify: `backend/app/core/llm/lifecycle.py`
- Modify: `backend/app/core/vram/orchestrator.py`
- Modify: `backend/app/core/vram/__init__.py`
- Modify: `backend/app/core/vram/director_model.py`
- Test: `backend/tests/test_lmstudio_lifecycle.py`
- Test: `backend/tests/test_vram_orchestrator.py`

**Interfaces:**
- `LLMLifecycle.prepare(model: str, on_status: Callable | None = None) -> None`.
- `LLMLifecycle.release(models: Sequence[str]) -> None`.
- `LLMLifecycle.status(model: str) -> dict[str, Any]`.
- `VramOrchestrator(*, provider: LLMProvider | None = None, ollama: OllamaClient | None = None, comfy: ComfyFreeClient | None = None, models: Sequence[str] | None = None, policy: str = "exclusive", acquire_timeout_sec: float | None = None)` retains the `ollama=` compatibility path used by existing tests.

- [ ] **Step 1: Write failing LM Studio unload tests**

Test all loaded LLM instances and ignore unloaded/embedding entries:

```python
@pytest.mark.asyncio
async def test_lmstudio_release_unloads_every_loaded_llm_instance(mock_transport):
    lifecycle = LMStudioLifecycle("http://127.0.0.1:1234/v1", api_key="token", transport=mock_transport)
    await lifecycle.release(["selected-model"])
    assert mock_transport.unload_bodies == [
        {"instance_id": "instance-a"},
        {"instance_id": "instance-b"},
    ]


@pytest.mark.asyncio
async def test_lmstudio_unload_http_failure_is_fatal(mock_transport):
    mock_transport.fail_unload = True
    lifecycle = LMStudioLifecycle("http://127.0.0.1:1234/v1", api_key=None, transport=mock_transport)
    with pytest.raises(RuntimeError, match="unload"):
        await lifecycle.release([])
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `cd backend; pytest tests/test_lmstudio_lifecycle.py -q`

Expected: FAIL until release behavior is implemented.

- [ ] **Step 3: Implement LM Studio release and runtime status**

Enumerate native catalog `loaded_instances`, accept either `id` or `instance_id`, and post each unique ID:

```python
async def release(self, models: Sequence[str]) -> None:
    payload = await self._get("/api/v1/models")
    instance_ids = {
        str(instance.get("instance_id") or instance.get("id"))
        for model in payload.get("models", [])
        if model.get("type") == "llm"
        for instance in model.get("loaded_instances", [])
        if instance.get("instance_id") or instance.get("id")
    }
    for instance_id in sorted(instance_ids):
        await self._post("/api/v1/models/unload", {"instance_id": instance_id})
```

`prepare` verifies the native catalog is reachable and reports that the selected model will JIT load; `status` returns provider, local-GPU status, loaded instance IDs, and whether the selected model is loaded.

- [ ] **Step 4: Write failing orchestrator behavior tests**

Add one local LM Studio and one remote provider case:

```python
@pytest.mark.asyncio
async def test_comfy_does_not_start_when_local_lifecycle_release_fails():
    provider = FakeProvider(lifecycle=FailingLocalLifecycle())
    orch = VramOrchestrator(provider=provider, comfy=FakeComfy())
    with pytest.raises(RuntimeError, match="unload failed"):
        await orch.before_comfy_job("actor")
    assert orch.owner is None


@pytest.mark.asyncio
async def test_remote_llm_session_does_not_free_comfy_or_claim_gpu():
    comfy = FakeComfy()
    orch = VramOrchestrator(provider=FakeProvider(lifecycle=RemoteLifecycle()), comfy=comfy)
    async with orch.llm_session():
        await orch.ensure_llm_ready()
        assert orch.owner is None
    assert comfy.free_calls == 0
```

- [ ] **Step 5: Run orchestrator tests and verify failure**

Run: `cd backend; pytest tests/test_vram_orchestrator.py -q`

Expected: the new lifecycle cases FAIL against Ollama-only orchestration.

- [ ] **Step 6: Delegate lifecycle operations in the orchestrator**

Keep generation reservations and existing Comfy ownership bookkeeping. Replace direct model operations with provider lifecycle calls:

```python
async def release_llm(self) -> None:
    await self.provider.lifecycle.release(self.models)
    self._llm_ready = False


async def ensure_llm_ready(self, on_status=None) -> None:
    if self.provider.lifecycle.uses_local_gpu and self.owner != "llm":
        raise RuntimeError("ensure_llm_ready requires active llm_session (owner=llm)")
    model = get_director_model(self.provider.provider_id)
    await self.provider.lifecycle.prepare(model, on_status=on_status)
    self._llm_ready = True
```

For `before_comfy_job`, invoke release only for a local-GPU lifecycle. If `release_failure_is_fatal` is true, leave `owner` unset and propagate the error. Preserve Ollama's existing best-effort behavior through `OllamaLifecycle.release_failure_is_fatal = False`.

For a remote lifecycle, `llm_session` checks `fail_if_generation_pending` but does not wait on or mutate GPU ownership and does not free Comfy memory.

- [ ] **Step 7: Preserve compatibility construction and singleton wiring**

If tests pass `ollama=FakeOllama()`, wrap it in an Ollama provider internally. Normal `get_orchestrator()` must use the same cached object returned by `get_llm_provider()`. When model selection changes, invalidate `_llm_ready` without reaching into an Ollama-only field.

- [ ] **Step 8: Run lifecycle and VRAM regression tests**

Run: `cd backend; pytest tests/test_lmstudio_lifecycle.py tests/test_vram_orchestrator.py tests/test_runner_vram_hook.py tests/test_external_pipeline_runner.py -q`

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

```powershell
git add backend/app/core/llm/lifecycle.py backend/app/core/vram backend/tests/test_lmstudio_lifecycle.py backend/tests/test_vram_orchestrator.py
git commit -m "feat: manage llm lifecycle by provider"
```

---

### Task 5: Route Director Planning, Chat, Vision, Tools, and Runtime APIs Through the Active Provider

**Files:**
- Create: `backend/app/agents/director/llm_plan_provider.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/app/api/director.py`
- Modify: `backend/app/api/health.py`
- Modify: `backend/app/scripts/wake_agent.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_director_agent.py`
- Modify: `backend/tests/test_director_skill_loading.py`
- Modify: `backend/tests/test_director_vram_api.py`
- Modify: `backend/tests/test_project_chat_stream_lifecycle.py`

**Interfaces:**
- Produces `DirectorLLMPlanProvider(provider: LLMProvider | None = None)` implementing existing `PlanProvider`.
- `_make_chat_fn(on_progress=None, provider: LLMProvider | None = None)` routes all calls through `provider.client`.
- Existing HTTP response bodies remain compatible; provider/runtime fields are additive.

- [ ] **Step 1: Write failing active-provider planning and vision tests**

Replace Ollama-specific plan-provider expectations with an injected recording provider:

```python
@pytest.mark.asyncio
async def test_plan_provider_uses_active_client_and_selected_model(monkeypatch):
    provider = RecordingProvider(provider_id="lm-studio", model="qwen-local")
    plan = DirectorLLMPlanProvider(provider)
    await plan.complete("system", "user", guides=("storyboard",))
    assert provider.client.generate_calls[0][0] == "qwen-local"
    assert "Director Studio" in provider.client.generate_calls[0][1]


@pytest.mark.asyncio
async def test_plan_provider_requires_vision_from_active_client():
    provider = RecordingProvider(provider_id="openai-compatible", model="vision-model")
    plan = DirectorLLMPlanProvider(provider)
    await plan.complete_with_images("system", "inspect", images=["aGVsbG8="])
    assert provider.client.chat_calls[0]["require_vision"] is True
```

- [ ] **Step 2: Run planning tests and verify failure**

Run: `cd backend; pytest tests/test_director_agent.py tests/test_director_skill_loading.py -q`

Expected: targeted new tests FAIL because planning still constructs `OllamaClient`.

- [ ] **Step 3: Extract the active plan provider**

Move skill-wrapped completion behavior out of `api/projects.py`:

```python
class DirectorLLMPlanProvider:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or get_llm_provider()

    @property
    def model(self) -> str:
        return get_director_model(self.provider.provider_id)

    async def complete(self, system: str, user: str, *, guides: Iterable[str] = ()) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        return await self.provider.client.generate(self.model, prompt)

    async def complete_with_images(self, system: str, user: str, *, images: list[str], guides: Iterable[str] = ()) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        return await self.provider.client.chat(self.model, prompt, images=images, require_vision=True)
```

Use it from `get_director_service` and retain a temporary `OllamaPlanProvider = DirectorLLMPlanProvider` alias only if existing external imports require it.

- [ ] **Step 4: Write failing chat transport and tool fallback tests**

Inject an active provider into `_make_chat_fn` and assert no `orch.ollama` access. Add an explicit unsupported-tools case:

```python
@pytest.mark.asyncio
async def test_chat_fn_uses_active_provider_client(recording_provider, fake_orchestrator):
    chat = await projects_api._make_chat_fn(provider=recording_provider)
    await chat("system", "hello")
    assert recording_provider.client.generate_calls


@pytest.mark.asyncio
async def test_unsupported_native_tools_retry_text_protocol_once(tool_rejecting_provider, fake_orchestrator):
    chat = await projects_api._make_chat_fn(provider=tool_rejecting_provider)
    text = await chat("system", "save it", tools=[SAVE_TOOL])
    assert text == '{"tool":"save_script","params":{}}'
    assert tool_rejecting_provider.client.chat_response_calls == 1
    assert tool_rejecting_provider.client.generate_calls == 1
```

- [ ] **Step 5: Run chat tests and verify failure**

Run: `cd backend; pytest tests/test_projects_api.py tests/test_project_chat_stream_lifecycle.py -q`

Expected: targeted new tests FAIL because `_make_chat_fn` calls `orch.ollama`.

- [ ] **Step 6: Migrate `_make_chat_fn` without changing the frontend stream contract**

Resolve one provider and one selected model per call. Replace `orch.ollama.chat_response`, `generate_stream`, `chat`, and `generate` with `provider.client` equivalents. Use `UnsupportedLLMFeatureError(feature="tools")` for exactly one text-protocol retry; remove the Ollama/XML-only condition while preserving XML rejection as one recognized Ollama compatibility case.

Runtime progress labels use the provider and model:

```python
label = f"Thinking with {model} via {provider.provider_id}"
if use_images:
    label += f" · {len(use_images)} image{'s' if len(use_images) != 1 else ''}"
await _runtime(label + "…")
```

Continue returning the normalized dict for native tool loops and the same token/thinking progress event bodies consumed by the frontend.

- [ ] **Step 7: Make Director control and health APIs provider-aware**

Inject the active provider into wake/status calls. Wake uses `provider.client.generate` after lifecycle preparation. `/api/director/vram` reads `provider.lifecycle.status(model)` and retains legacy Ollama keys with neutral values for other providers. `/api/health` reports:

```json
{
  "llm_provider": "lm-studio",
  "llm_reachable": true
}
```

Update `wake_agent.py` to use `get_llm_provider().client`; remove user-facing statements that every LLM is Ollama.

- [ ] **Step 8: Run all Director and API focused tests**

Run: `cd backend; pytest tests/test_llm_provider.py tests/test_projects_api.py tests/test_director_agent.py tests/test_director_skill_loading.py tests/test_director_vram_api.py tests/test_project_chat_stream_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 9: Commit Task 5**

```powershell
git add backend/app/agents/director/llm_plan_provider.py backend/app/api/projects.py backend/app/api/director.py backend/app/api/health.py backend/app/scripts/wake_agent.py backend/tests
git commit -m "refactor: route director through active llm"
```

---

### Task 6: Documentation, Full Regression, and LM Studio Acceptance

**Files:**
- Modify: `README.md`
- Modify: `backend/.env.example`
- Modify: `docs/superpowers/plans/2026-09-10-openai-compatible-llm-provider.md` only to check completed steps during execution

**Interfaces:**
- Documents exact environment values and observable verification commands.
- Produces no new runtime interface.

- [ ] **Step 1: Document the three provider configurations**

Add a Director LLM configuration section with these executable examples:

```dotenv
# Ollama
DS_LLM_PROVIDER=ollama
DS_OLLAMA_BASE_URL=http://127.0.0.1:11434

# LM Studio
DS_LLM_PROVIDER=lm-studio
DS_LLM_BASE_URL=http://127.0.0.1:1234/v1
# DS_LLM_API_KEY=lm-studio

# OpenAI-compatible
DS_LLM_PROVIDER=openai-compatible
DS_LLM_BASE_URL=https://api.openai.com/v1
DS_LLM_API_KEY=replace-with-a-real-secret
```

Explain that model selection comes from the Director dropdown, LM Studio must have its server enabled, and local LM Studio models are unloaded before ComfyUI work and JIT-loaded on the next Director request.

- [ ] **Step 2: Install backend dependencies in the active environment**

Run: `cd backend; python -m pip install -r requirements.txt`

Expected: exit 0 with an installed `openai` version in the allowed `>=3.9,<4` range.

- [ ] **Step 3: Run the complete backend test suite**

Run: `cd backend; pytest -q`

Expected: all tests pass with zero failures.

- [ ] **Step 4: Run frontend regression tests and production build**

Run: `cd frontend; npm test -- --run`

Expected: all Vitest tests pass.

Run: `cd frontend; npm run build`

Expected: TypeScript and Vite build exit 0.

- [ ] **Step 5: Run LM Studio catalog and chat acceptance**

With LM Studio server running and the backend configured for `lm-studio`, run:

```powershell
Invoke-RestMethod http://127.0.0.1:8790/api/director/model
```

Expected: `provider` is `lm-studio`, `reachable` is true, `available` contains at least one LLM key, and `model` is the persisted choice or the first key.

Create/select a project in the existing UI and send one normal Director message. Expected: visible streaming tokens, no Ollama connection attempt, and the selected LM Studio model appears in runtime status.

- [ ] **Step 6: Run LM Studio tool, vision, and unload/JIT acceptance**

Send a Director instruction that invokes an existing non-destructive state tool, such as saving an initial script. Expected: the model requests the tool and Director Studio executes it once.

With a vision-capable model, attach one image and request a visual review. Expected: the model describes the supplied image; a text-only fallback is not reported.

Start one local reference-image generation. During the handoff, run:

```powershell
Invoke-RestMethod http://127.0.0.1:1234/api/v1/models
```

Expected: the selected LLM has no loaded instance before ComfyUI begins. After generation, send another Director message and query again; expected: LM Studio JIT loads the selected model and the response completes.

- [ ] **Step 7: Inspect final diff and secrets**

Run:

```powershell
git diff --check main...HEAD
$secretFiles = @(git grep -l -E "sk-[A-Za-z0-9_-]{20,}" -- ':!backend/.env.example' ':!docs/**')
if ($secretFiles.Count) { Write-Error "Possible secrets in tracked files: $($secretFiles -join ', ')"; exit 1 }
git status --short
```

Expected: no whitespace errors, no committed API key matches, and only intended tracked changes.

- [ ] **Step 8: Commit documentation and acceptance notes**

```powershell
git add README.md backend/.env.example docs/superpowers/plans/2026-09-10-openai-compatible-llm-provider.md
git commit -m "docs: explain director llm providers"
```

- [ ] **Step 9: Record final verification evidence**

Run:

```powershell
git status --short --branch
git log --oneline main..HEAD
```

Expected: a clean `feat/openai-compatible-llm` worktree with the design, plan, implementation, tests, and documentation commits based on `main`.
