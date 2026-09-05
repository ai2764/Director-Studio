# DeepSeek Harness Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that Director Studio can run its project Director agent in a durable, cancellable, fully local DeepSeek Harness sidecar while preserving the existing FastAPI/UI contract, VRAM arbitration, and both H3 providers.

**Architecture:** FastAPI remains the public gateway and the sole owner of Director domain state, jobs, H3, and GPU arbitration. A loopback-only Node sidecar embeds the minimal Harness kernel, persists Harness sessions as JSONL, calls a FastAPI-hosted LLM gateway through a custom `LlmAdapter`, and exposes only four Director tools fetched from a scoped internal tool bridge. `DS_DIRECTOR_AGENT_RUNTIME=legacy|harness` selects the runtime at turn admission; the default is `legacy`, and a Harness failure is returned explicitly without switching runtimes mid-turn.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, httpx, Ollama SDK, pytest; Node.js 22, TypeScript, Vitest, DeepSeek Harness/Cordis packages pinned to their tested release-candidate versions.

**Spec:** `docs/superpowers/specs/2026-09-02-deepseek-harness-agent-runtime-design.md`

## Global Constraints

- Phase 1 only: deliver the vertical feasibility spike; do not implement the Phase 2 migration, compaction, full tool inventory, completion-claim guard, or production telemetry rollout.
- Agent inference remains local and uses `orcarouter/Qwen3.8-27B-Uncensored:latest` through the existing Ollama instance.
- Harness must never call Ollama directly; every model call traverses FastAPI's `VramOrchestrator.llm_session()` and `ensure_llm_ready()`.
- H3 local Comfy MCP and MiniMax official API paths remain unchanged.
- Bind the sidecar and internal FastAPI endpoints to loopback only; require a shared bearer token on every internal call.
- Preserve the existing `/api/projects/{project_id}/chat`, `/chat/stream`, `/chat/session`, `/chat/session/cancel`, history, and SSE payload shapes.
- `DS_DIRECTOR_AGENT_RUNTIME` defaults to `legacy`; never silently fall back from Harness to legacy after a turn is admitted.
- One project may have at most one admitted Director turn. Cancellation must reach the sidecar fetch, Harness Agent, FastAPI LLM request, and Ollama request.
- The state fingerprint is an opaque SHA-256 of canonical persisted project and ordered shot state; do not add a revision field to `Project`.
- Preserve `.codex-remote-attachments/`, `exports/`, and every existing stash. At execution time, invoke `superpowers:using-git-worktrees` and create a descendant `codex/` implementation branch/worktree.
- Pin exact Node dependency versions in `package-lock.json`; do not use alpha Harness releases.

---

## File Structure

### Backend files

- Create `backend/app/agents/director/harness_contracts.py`: typed sidecar/internal-wire models and event names shared by the FastAPI adapter and internal routes.
- Create `backend/app/agents/director/state_version.py`: canonical project/shot snapshot and opaque SHA-256 fingerprint.
- Create `backend/app/agents/director/harness_sessions.py`: durable project-to-Harness-session mapping under the project's `agent/` directory.
- Create `backend/app/agents/director/harness_tools.py`: Phase 1 tool allowlist, dynamic schemas, state-version validation, and delegation into the existing Python tool executor.
- Create `backend/app/agents/director/harness_client.py`: loopback HTTP client for sidecar turn streaming, status, and cancellation.
- Create `backend/app/agents/director/runtime.py`: admission-time `legacy|harness` selection and a common final-result projection.
- Create `backend/app/api/harness_internal.py`: bearer-protected context, tool, and OpenAI-compatible LLM gateway endpoints.
- Modify `backend/app/config.py`: runtime, sidecar, token, model, timeout, and Harness session-root settings.
- Modify `backend/app/api/__init__.py`: mount the internal router.
- Modify `backend/app/api/projects.py`: delegate non-stream, stream, status, and cancel operations through `runtime.py` while retaining request/response persistence and SSE framing.
- Modify `backend/app/core/vram/ollama_client.py`: add abort-aware streamed chat with tool-call deltas and usage normalization.
- Test in `backend/tests/test_harness_state_version.py`, `test_harness_sessions.py`, `test_harness_tools.py`, `test_harness_internal_llm.py`, `test_harness_client.py`, and `test_project_harness_runtime.py`.

### Sidecar files

- Create `harness/package.json`, `package-lock.json`, `tsconfig.json`, and `vitest.config.ts`: isolated Node workspace with exact versions and repeatable scripts.
- Create `harness/src/config.ts`: validated loopback URLs, token, session root, and model route.
- Create `harness/src/director-api.ts`: abort-aware client for context, tool, and LLM gateway calls.
- Create `harness/src/llm-adapter.ts`: Harness `LlmAdapter` translating OpenAI SSE chunks to Harness `StreamChunk` events.
- Create `harness/src/director-tools.ts`: per-Agent raw Harness tool definitions generated from FastAPI schemas.
- Create `harness/src/kernel.ts`: minimal Cordis/Harness composition, JSONL persistence, Agent create/resume, event observation, and cancellation.
- Create `harness/src/server.ts`: loopback HTTP health/turn/status/cancel transport emitting NDJSON events.
- Test in matching `harness/src/*.test.ts` files with fake FastAPI endpoints and a temporary session root.

### Operations and evidence

- Modify `start.ps1` and `kill.ps1`: start, health-check, record, and terminate `harness.pid` on port `8791` without changing frontend/backend defaults.
- Create `backend/tests/test_harness_vertical_slice.py`: process-level fake-model vertical slice covering resume, tool mutation, SSE, cancellation, and VRAM release.
- Create `docs/superpowers/reports/2026-09-02-deepseek-harness-phase-1.md`: measured go/no-go evidence and explicit Phase 2 blockers.

---

### Task 1: Backend contracts, configuration, and state fingerprint

**Files:**
- Create: `backend/app/agents/director/harness_contracts.py`
- Create: `backend/app/agents/director/state_version.py`
- Create: `backend/app/agents/director/harness_sessions.py`
- Modify: `backend/app/config.py:69-84`
- Test: `backend/tests/test_harness_state_version.py`
- Test: `backend/tests/test_harness_sessions.py`

**Interfaces:**
- Produces: `HarnessRuntime = Literal["legacy", "harness"]`; `HarnessEvent(type, text, data, code)`; `HarnessTurnResult(reply, actions, images, thinking, steps)`; `HarnessSessionBinding(project_id, session_id, created_at)`; `get_or_create_harness_session(project_id) -> HarnessSessionBinding`; `project_state_snapshot(project_id) -> dict[str, Any]`; `project_state_version(project_id) -> str`.
- Produces settings: `director_agent_runtime`, `harness_base_url`, `harness_internal_token`, `harness_model`, `harness_timeout_sec`, `harness_sessions_dir`.

- [ ] **Step 1: Write fingerprint and settings tests**

```python
def test_state_version_is_stable_and_changes_with_persisted_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "projects_dir", tmp_path)
    project = create_project("Harness", "INT. ROOM - DAY")
    first = project_state_version(project.id)
    assert first == project_state_version(project.id)
    shot = Shot(id="sht_1", project_id=project.id, title="One", index=1)
    save_shot(shot)
    project = project.model_copy(update={"shot_ids": [shot.id]})
    save_project(project)
    assert project_state_version(project.id) != first

def test_harness_defaults_are_local_and_opt_in():
    assert settings.director_agent_runtime == "legacy"
    assert settings.harness_base_url == "http://127.0.0.1:8791"
    assert settings.harness_model == "orcarouter/Qwen3.8-27B-Uncensored:latest"

def test_session_binding_is_durable_and_stable(project):
    first = get_or_create_harness_session(project.id)
    second = get_or_create_harness_session(project.id)
    assert second == first
    assert first.session_id.startswith("director-")
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run: `cd backend; python -m pytest tests/test_harness_state_version.py tests/test_harness_sessions.py -q`

Expected: FAIL importing `app.agents.director.state_version`.

- [ ] **Step 3: Implement canonical state and wire models**

```python
def project_state_snapshot(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")
    return {
        "project": project.model_dump(mode="json"),
        "shots": [shot.model_dump(mode="json") for shot in list_shots(project_id)],
    }

def project_state_version(project_id: str) -> str:
    payload = json.dumps(
        project_state_snapshot(project_id),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

Add to `Settings` with Pydantic validation that runtime is exactly `legacy` or `harness`, URLs resolve to a loopback hostname, and Harness mode requires a non-empty `DS_HARNESS_INTERNAL_TOKEN`.

Persist `HarnessSessionBinding` atomically at `ensure_project_tree(project_id) / "agent" / "harness-session.json"`. Generate the id once as `director-` plus 24 random UUID hex characters; validate that a loaded binding belongs to the requested project. This mapping, rather than re-deriving an id in Node, is the source of session identity.

- [ ] **Step 4: Run tests and type/import smoke checks**

Run: `cd backend; python -m pytest tests/test_harness_state_version.py tests/test_harness_sessions.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/config.py backend/app/agents/director/harness_contracts.py backend/app/agents/director/state_version.py backend/app/agents/director/harness_sessions.py backend/tests/test_harness_state_version.py backend/tests/test_harness_sessions.py
git commit -m "feat: define harness runtime contracts"
```

### Task 2: FastAPI-owned Director tool bridge

**Files:**
- Create: `backend/app/agents/director/harness_tools.py`
- Test: `backend/tests/test_harness_tools.py`

**Interfaces:**
- Consumes: `project_state_version(project_id)` and existing `director_tool_schemas`, `_run_tools`, `DirectorService`.
- Produces: `PHASE1_TOOL_NAMES = {"get_status", "set_script", "save_storyboard", "queue_ref_frame"}`; `get_harness_context(project_id, session_id, turn_id, message) -> HarnessContext`; `execute_harness_tool(request, svc, on_progress=None) -> HarnessToolResponse`.

- [ ] **Step 1: Write tests for dynamic schemas, stale writes, validation failure, and mutation**

```python
def test_locked_project_does_not_offer_set_script(project):
    project.script_locked = True
    save_project(project)
    names = {
        item.name for item in get_harness_context(
            project.id, "director-session", "turn-1", "revise it"
        ).tools
    }
    assert names == {"get_status", "save_storyboard", "queue_ref_frame"}

@pytest.mark.asyncio
async def test_mutation_rejects_stale_state(project, service):
    request = HarnessToolRequest(
        project_id=project.id, tool_name="set_script",
        harness_session_id="director-session", turn_id="turn-1",
        arguments={"script": "NEW"}, state_version="stale",
        tool_call_id="call_1", idempotency_key="director-session:call_1",
    )
    with pytest.raises(HarnessStateConflict):
        await execute_harness_tool(request, service)
```

Also assert invalid `set_script` arguments return a structured `ok=False` result, a successful write returns the new fingerprint and action list, and `get_status` does not mutate the fingerprint.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend; python -m pytest tests/test_harness_tools.py -q`

Expected: FAIL importing `harness_tools`.

- [ ] **Step 3: Implement the Phase 1 adapter over existing domain code**

```python
PHASE1_TOOL_NAMES = frozenset({"get_status", "set_script", "save_storyboard", "queue_ref_frame"})
MUTATING_TOOL_NAMES = PHASE1_TOOL_NAMES - {"get_status"}

def get_harness_context(
    project_id: str, session_id: str, turn_id: str, message: str
) -> HarnessContext:
    project = load_project(project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")
    schemas = director_tool_schemas(project, current_message=message)
    selected = [s for s in schemas if s["function"]["name"] in PHASE1_TOOL_NAMES]
    return HarnessContext(
        project_id=project_id,
        state_version=project_state_version(project_id),
        state=project_state_snapshot(project_id),
        tools=[HarnessToolSchema.from_openai(s) for s in selected],
    )
```

For execution, validate the persisted project/session association plus `turn_id`, `tool_call_id`, and derived idempotency-key shape; compare the supplied fingerprint before mutations; verify the requested name is currently offered; then call existing `_run_tools` with one `{name, args}` item. Convert `notes`, `actions`, `result_payloads`, touched shots, and generated `ChatImage` values into `HarnessToolResponse`; return the resulting fingerprint so the scoped Node tool set updates its state before the next call. Phase 1 validates identities but deliberately defers the durable replay ledger and receipts to Phase 2. Never duplicate domain mutations in this module.

- [ ] **Step 4: Run bridge and legacy regression tests**

Run: `cd backend; python -m pytest tests/test_harness_tools.py tests/test_director_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/agents/director/harness_tools.py backend/tests/test_harness_tools.py
git commit -m "feat: expose scoped director tools to harness"
```

### Task 3: VRAM-gated internal LLM and tool HTTP API

**Files:**
- Create: `backend/app/api/harness_internal.py`
- Modify: `backend/app/api/__init__.py:1-28`
- Modify: `backend/app/core/vram/ollama_client.py:89-165,286-430`
- Test: `backend/tests/test_harness_internal_llm.py`

**Interfaces:**
- Produces internal routes: `GET /api/internal/harness/projects/{project_id}/context`; `POST /api/internal/harness/projects/{project_id}/tools/{tool_name}`; `POST /api/internal/harness/llm/chat/completions`.
- Produces: `OllamaClient.chat_response_stream(model, *, messages, tools=None, signal=None) -> AsyncIterator[dict[str, Any]]` with `text`, `thinking`, `tool_call`, `usage`, and `finish` events.

- [ ] **Step 1: Write auth, streaming, cancellation, and cleanup tests**

```python
def test_internal_api_rejects_missing_token(client):
    response = client.get("/api/internal/harness/projects/prj_1/context")
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_llm_gateway_releases_vram_when_stream_is_cancelled(monkeypatch):
    orchestrator = RecordingOrchestrator(hanging_stream=True)
    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: orchestrator)
    response = await call_stream_then_disconnect()
    assert response.status_code == 200
    assert orchestrator.entered == 1
    assert orchestrator.exited == 1
```

Cover a tool-call delta, normal `[DONE]`, unsupported non-local model rejection, missing project, stale-state HTTP 409, and cancellation of the downstream Ollama stream.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend; python -m pytest tests/test_harness_internal_llm.py -q`

Expected: FAIL because the internal router does not exist.

- [ ] **Step 3: Implement bearer/loopback protection and domain routes**

Use one dependency that checks `request.client.host` against `127.0.0.1`, `::1`, or `localhost`, then compares `Authorization: Bearer <token>` with `secrets.compare_digest`. Context and tool routes call Task 2 directly; map `HarnessStateConflict` to HTTP 409 with `{code:"STALE_STATE", state_version:<current>}`.

- [ ] **Step 4: Implement the OpenAI-compatible SSE gateway inside the VRAM lease**

```python
async def event_stream():
    async with orch.llm_session(
        release_on_exit=not settings.llm_keep_loaded,
        fail_if_generation_pending=True,
    ):
        await orch.ensure_llm_ready()
        async for event in orch.ollama.chat_response_stream(
            settings.harness_model,
            messages=body.messages,
            tools=body.tools,
        ):
            yield encode_openai_chunk(body.model, event)
    yield "data: [DONE]\n\n"
```

Put the async context manager around the generator body so disconnect/abort runs `__aexit__`. Normalize Ollama tool call objects into OpenAI `delta.tool_calls[].function.{name,arguments}` and never expose the Ollama base URL to the sidecar.

- [ ] **Step 5: Run focused and VRAM regression tests**

Run: `cd backend; python -m pytest tests/test_harness_internal_llm.py tests/test_vram_orchestrator.py tests/test_ollama_strict_vision.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/__init__.py backend/app/api/harness_internal.py backend/app/core/vram/ollama_client.py backend/tests/test_harness_internal_llm.py
git commit -m "feat: add vram-gated harness gateway"
```

### Task 4: Minimal local Harness sidecar kernel

**Files:**
- Create: `harness/package.json`
- Create: `harness/package-lock.json`
- Create: `harness/tsconfig.json`
- Create: `harness/vitest.config.ts`
- Create: `harness/src/config.ts`
- Create: `harness/src/director-api.ts`
- Create: `harness/src/llm-adapter.ts`
- Create: `harness/src/director-tools.ts`
- Create: `harness/src/kernel.ts`
- Create: `harness/src/server.ts`
- Test: `harness/src/config.test.ts`, `director-api.test.ts`, `llm-adapter.test.ts`, `director-tools.test.ts`, `kernel.test.ts`, `server.test.ts`

**Interfaces:**
- Consumes: Task 3 internal routes and bearer token.
- Produces: `createKernel(config) -> HarnessKernel`; `runTurn(input, onEvent, signal) -> HarnessTurnResult`; `status(projectId)`; `cancel(projectId)`; loopback routes `GET /health`, `POST /v1/projects/:id/turns`, `GET /v1/projects/:id/session`, `POST /v1/projects/:id/session/cancel`.

- [ ] **Step 1: Create the Node manifest with exact dependencies**

Declare Node `>=22` and scripts `test`, `typecheck`, `dev`, `start`. Pin (no caret/tilde) `@deepseek-ai/cordis@4.0.2`, `@deepseek-ai/dsh-llm@0.0.1-rc.1`, `dsh-session`, `dsh-system-prompt`, `dsh-tools`, `dsh-session-projection`, `dsh-session-persistence-jsonl`, and `dsh-token-meter` at `0.0.1-rc.1`, `@deepseek-ai/dsh-agent@0.1.0-rc.6`, `@deepseek-ai/dsh-agent-loop@0.1.0-rc.6`, `@deepseek-ai/cordis-plugin-timer@1.1.4`, `tsx@4.23.13`, `typescript@7.0.2`, and `vitest@4.1.11`. Run `npm install` inside `harness/` to generate the lockfile.

- [ ] **Step 2: Write failing config, API, adapter, and tool tests**

```ts
it('rejects a non-loopback backend URL', () => {
  expect(() => loadConfig({ DS_BACKEND_URL: 'http://192.168.1.4:8790' }))
    .toThrow(/loopback/)
})

it('forwards abort to the FastAPI model stream', async () => {
  const controller = new AbortController()
  const pending = collect(adapter.stream(options(controller.signal)))
  controller.abort()
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
})
```

Assert OpenAI text/thinking/tool-call deltas become legal Harness block sequences with `usage` before `finish`; schemas are scoped to one project Agent; and tool execution always forwards `project_id`, `harness_session_id`, `turn_id`, `state_version`, `tool_call_id`, derived `idempotency_key`, and `exec.signal`. After a successful mutation, assert the next call uses the returned state version.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd harness; npm test -- --run`

Expected: FAIL because implementation modules are absent.

- [ ] **Step 4: Implement the FastAPI client and custom `LlmAdapter`**

```ts
export class DirectorLlmAdapter extends LlmAdapter {
  constructor(private readonly api: DirectorApi) { super() }
  override async *stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    const parser = new OpenAiSseTranslator()
    for await (const chunk of this.api.streamModel(toOpenAiRequest(options), options.signal)) {
      for (const event of parser.accept(chunk)) yield event
    }
    for (const event of parser.finish()) yield event
  }
}
```

The adapter advertises one provider `director-local` and one text model from `DS_HARNESS_MODEL`, honors `options.signal`, preserves raw JSON tool arguments, and throws on unsupported image/stop options rather than dropping them. Register an `agent/request-error` policy that retries only transient gateway failures, with at most two retries and no retry for cancellation, authentication, model mismatch, or protocol errors.

- [ ] **Step 5: Implement raw scoped Director tools**

Create Harness `ToolDefinition` objects directly from FastAPI's supported JSON-schema subset. Each definition returns the structured FastAPI result as canonical JSON and renders it as a compact text block. Register definitions inside `ctx.agents.create({ setup(agentCtx) { ... } })`, not globally, so the current dynamic inventory belongs only to that project Agent.

- [ ] **Step 6: Compose the minimal Harness kernel and durable sessions**

Mount only timer, LLM, session, system prompt, tools, Agent, projection, JSONL persistence, token meter, and Agent loop services. Do not mount sandbox, terminal, filesystem, web, MCP, skills, or subagent packages. Use the FastAPI-provided persisted `harness_session_id`; call `ctx.agents.resume()` when the JSONL exists and `create()` otherwise. Record the turn's tool-schema snapshot and current state version as durable request metadata. Observe committed assistant text, thought, tool lifecycle, and Agent status events; flush the session before emitting the terminal `result` event. On `STALE_STATE`, refresh context and permit exactly one corrected tool retry for that call; a second conflict becomes the model-visible failed result.

- [ ] **Step 7: Implement loopback NDJSON server and cancellation**

`POST /turns` accepts `{message}` and writes one JSON object per line for `status`, `think`, `token`, `tool`, `result`, or `error`. Keep an `AbortController` per project; reject a second active turn with 409; `cancel` aborts the controller and calls `agent.cancel({kind:'user'})`; shutdown drains active handles and `ctx.fiber.dispose()`.

- [ ] **Step 8: Run Node verification**

Run: `cd harness; npm test -- --run; npm run typecheck`

Expected: all tests PASS and TypeScript exits 0.

- [ ] **Step 9: Commit**

```powershell
git add harness
git commit -m "feat: embed minimal deepseek harness sidecar"
```

### Task 5: FastAPI runtime routing and public API compatibility

**Files:**
- Create: `backend/app/agents/director/harness_client.py`
- Create: `backend/app/agents/director/runtime.py`
- Modify: `backend/app/api/projects.py:541-1120`
- Test: `backend/tests/test_harness_client.py`
- Test: `backend/tests/test_project_harness_runtime.py`

**Interfaces:**
- Consumes: sidecar NDJSON API and `HarnessTurnResult`.
- Produces: `DirectorAgentRuntime.run_turn(...) -> AsyncIterator[HarnessEvent]`; `status(project_id) -> ChatSessionStatus`; `cancel(project_id) -> ChatSessionStatus`; `get_director_runtime() -> DirectorAgentRuntime`.

- [ ] **Step 1: Write client protocol tests**

Use `httpx.MockTransport` to assert bearer auth, NDJSON fragmentation across byte chunks, explicit mapping of 409/503 errors, timeout behavior, and caller cancellation closing the response stream.

- [ ] **Step 2: Write public compatibility tests**

```python
@pytest.mark.asyncio
async def test_harness_stream_keeps_existing_sse_shape(project, monkeypatch):
    monkeypatch.setattr(settings, "director_agent_runtime", "harness")
    monkeypatch.setattr(runtime, "get_harness_client", lambda: FakeHarnessClient())
    response = await projects_api.project_chat_stream_endpoint(
        project.id, ChatBody(message="status"), svc=service,
    )
    events = [chunk async for chunk in response.body_iterator]
    assert any('"type": "token"' in event for event in events)
    assert any('"type": "result"' in event for event in events)
```

Also prove default legacy calls the existing `handle_chat`, Harness unavailability returns an error without legacy fallback, a final result is projected to the existing `ChatResponse`, user/assistant history is written once, and image-upload turns remain legacy with an explicit 409 `HARNESS_VISION_PHASE2` in Harness mode.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd backend; python -m pytest tests/test_harness_client.py tests/test_project_harness_runtime.py -q`

Expected: FAIL importing the runtime/client.

- [ ] **Step 4: Implement the sidecar client and runtime selector**

The selector snapshots `settings.director_agent_runtime` before admission. The Harness branch consumes NDJSON and yields common events; on `result`, load the authoritative current project/shots and build `ChatResponse`. The legacy branch invokes the existing functions unchanged. Do not catch a Harness transport exception and invoke legacy.

- [ ] **Step 5: Refactor project routes through the common runtime**

Keep upload validation, history persistence, `director_chat_sessions` admission, background-task behavior after SSE disconnect, and SSE formatting in `projects.py`. Replace only the runner body, status lookup, and cancellation delegate. Append the assistant history row only after a terminal successful result.

- [ ] **Step 6: Run compatibility and lifecycle tests**

Run: `cd backend; python -m pytest tests/test_harness_client.py tests/test_project_harness_runtime.py tests/test_project_chat_stream_lifecycle.py tests/test_chat_sessions.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/agents/director/harness_client.py backend/app/agents/director/runtime.py backend/app/api/projects.py backend/tests/test_harness_client.py backend/tests/test_project_harness_runtime.py
git commit -m "feat: route director turns through harness"
```

### Task 6: Windows lifecycle scripts

**Files:**
- Modify: `start.ps1:1-180`
- Modify: `kill.ps1:1-92`
- Test: `backend/tests/test_harness_scripts.py`

**Interfaces:**
- Produces parameters `-HarnessPort 8791` and `-NoHarness`; `.run/harness.pid`, `.run/harness.out.log`, `.run/harness.err.log`; process cleanup on normal kill and `-Force`.

- [ ] **Step 1: Write static lifecycle contract tests**

Parse both scripts as text and assert the sidecar port parameter, Node lookup, `npm run start`, PID/log names, health URL, cleanup call, and port fallback all exist. Assert `-BackendOnly` still starts Harness unless `-NoHarness`, while `-FrontendOnly` does not.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd backend; python -m pytest tests/test_harness_scripts.py -q`

Expected: FAIL because scripts know only backend/frontend.

- [ ] **Step 3: Add sidecar startup and readiness**

Start Harness before backend only after checking ports `8791` and `8790`. Pass `DS_BACKEND_URL`, `DS_HARNESS_PORT`, `DS_HARNESS_INTERNAL_TOKEN`, `DS_HARNESS_MODEL`, and `DS_HARNESS_SESSIONS_DIR` through `Start-DetachedProcess`. After backend starts, poll both `/api/health` and sidecar `/health` with a bounded timeout; on failure stop only PIDs created by this invocation.

- [ ] **Step 4: Add sidecar shutdown**

Call `Stop-FromPidFile "harness"`; add `HarnessPort` to listener cleanup and fallback detection. Preserve current `PortsOnly` and `Force` semantics.

- [ ] **Step 5: Run static and PowerShell parse checks**

Run: `cd backend; python -m pytest tests/test_harness_scripts.py -q`

Run: `powershell -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw .\start.ps1)); [void][scriptblock]::Create((Get-Content -Raw .\kill.ps1))"`

Expected: tests PASS and parse command exits 0.

- [ ] **Step 6: Commit**

```powershell
git add start.ps1 kill.ps1 backend/tests/test_harness_scripts.py
git commit -m "feat: manage harness sidecar lifecycle"
```

### Task 7: Vertical-slice proof and go/no-go report

**Files:**
- Create: `backend/tests/test_harness_vertical_slice.py`
- Create: `docs/superpowers/reports/2026-09-02-deepseek-harness-phase-1.md`

**Interfaces:**
- Consumes: complete Phase 1 system.
- Produces: repeatable spike evidence and a decision record; no new runtime API.

- [ ] **Step 1: Write a deterministic process-level test fixture**

Start FastAPI on an ephemeral loopback port with a scripted fake `OllamaClient`, start the compiled sidecar on another ephemeral port with a temporary Harness session root, create a real project, and select `director_agent_runtime=harness`. The scripted model performs `get_status`, an invalid `set_script`, a valid `set_script`, and then returns final text.

- [ ] **Step 2: Assert the vertical slice**

Verify public SSE ordering, one real project mutation, no duplicated mutation, persisted Harness JSONL, public chat history projection, sidecar restart/resume with the same stable session, second-turn context continuity, explicit cancel of a hanging model call, and `llm_session.__aexit__` after both success and cancellation. Add a separate fake `queue_ref_frame` delegation assertion that returns its job/action payload without changing H3 provider configuration.

- [ ] **Step 3: Run the vertical test and fix only Phase 1 defects**

Run: `cd backend; python -m pytest tests/test_harness_vertical_slice.py -q -s`

Expected: PASS. If a failure requires Phase 2 functionality, record it as a blocker rather than broadening this spike.

- [ ] **Step 4: Run the complete verification matrix**

Run: `cd harness; npm test -- --run; npm run typecheck`

Run: `cd backend; python -m pytest -q`

Run: `git diff --check`

Expected: all commands exit 0.

- [ ] **Step 5: Perform one real local smoke test**

With Ollama already containing `orcarouter/Qwen3.8-27B-Uncensored:latest`, set a temporary `DS_HARNESS_INTERNAL_TOKEN`, run `./start.ps1 -BackendOnly`, submit one status/read turn and one harmless project-name/script test project mutation, cancel one long turn, then run `./kill.ps1`. Confirm `.run/harness.pid` is removed, both ports are free, the Harness JSONL is present, and Ollama/Comfy VRAM transitions match the existing exclusivity policy.

- [ ] **Step 6: Write the go/no-go report with measured results**

The report must record exact commit, Windows/Node/Python/Harness versions, commands, pass counts, first-token and total latency for legacy vs Harness on the same model/prompt, resume result, cancel latency, peak VRAM observation, and local Phase 1 counters for offered/selected tools, argument validation, retry count, terminal category, and before/after state versions. Record known limitations and one decision: `GO_PHASE_2`, `NO_GO`, or `GO_WITH_BLOCKERS`. List each blocker with owner and proposed Phase 2 task.

- [ ] **Step 7: Commit evidence**

```powershell
git add backend/tests/test_harness_vertical_slice.py docs/superpowers/reports/2026-09-02-deepseek-harness-phase-1.md
git commit -m "test: prove harness vertical slice"
```

---

## Phase 1 Acceptance Gate

- Legacy remains the default and its full test suite passes.
- Harness handles two turns for one project across a sidecar restart using the same durable session.
- The public UI API and SSE schema remain unchanged.
- At least one read, one validation failure, one mutation, and one generation handoff traverse Harness → FastAPI → existing Python domain code.
- Harness model traffic traverses the FastAPI VRAM lease; no sidecar request reaches Ollama directly.
- Explicit cancellation reaches the Agent and Ollama stream and releases the VRAM lease.
- No Harness failure triggers mid-turn legacy fallback.
- H3 local/MiniMax provider selection and code paths are untouched.
- `start.ps1`/`kill.ps1` manage the sidecar on Windows without leaving a listener or PID file.
- The report contains enough evidence to make a Phase 2 go/no-go decision.
