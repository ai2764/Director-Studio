# Portable H3 Workflow Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a running Director Studio Portable instance safely import, analyze, test, activate, and hot-switch custom MiniMax H3 Ref2AV API workflows while every clean package still ships and starts with only the official workflow.

**Architecture:** Add an external, versioned H3 profile store under the persistent data root and make both the bundled official workflow and custom profiles implement one declarative boundary contract. Deterministic inspection and validation own correctness; a constrained Ollama proposer only ranks ambiguous candidates. Each local H3 job snapshots its resolved profile before entering the queue, while the existing Comfy MCP adapter and exclusive VRAM orchestrator remain the execution path.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest, React 19, TypeScript, Vitest, Comfy MCP, Ollama, PyInstaller, PowerShell packaging checks.

**Spec:** `docs/superpowers/specs/2026-09-05-portable-h3-workflow-profiles-design.md`

## Global Constraints

- The release executable and zip contain only the official H3 workflow and built-in profile.
- Custom profiles live only under `settings.data_dir / "workflow_profiles" / "h3"`.
- The first release accepts only pure `MiniMaxH3ReferenceToVideo`; reject I2V, `ref_frame`, and `last_frame` semantics.
- Director Studio may inject prompt, dimensions, frame length, ordered Picture 1-9, optional Audio 1-3, seed, and output prefix only.
- Preserve workflow-owned model, LoRA, sampler, scheduler, steps, denoise, guider, decoder, FPS, codec, upscale, and mux settings.
- Agent proposals are untrusted structured suggestions and can reference only candidates found by deterministic inspection.
- Activation requires all five gates: import, contract, filled graph, Comfy validation, and a successful 56-frame real test.
- Activation is atomic and affects only jobs submitted afterward; every submitted local H3 job captures its profile and hash.
- Broken custom profiles fall back to the bundled official profile for subsequent jobs and surface a warning.
- Do not add general file, shell, `.env`, source-tree, or project-data tools to the setup agent.
- Existing users with no custom active profile retain current local and MiniMax API behavior.

---

### Task 1: Profile Models, Safe Store, and Built-in Resolver

**Files:**
- Create: `backend/app/workflow_profiles/__init__.py`
- Create: `backend/app/workflow_profiles/h3/__init__.py`
- Create: `backend/app/workflow_profiles/h3/models.py`
- Create: `backend/app/workflow_profiles/h3/errors.py`
- Create: `backend/app/workflow_profiles/h3/store.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_h3_profile_store.py`

**Interfaces:**
- Produces: `H3BoundaryMapping`, `H3WorkflowProfile`, `ResolvedH3Profile`, `H3ProfileStore`, `resolve_active_h3_profile()`.
- Consumes: `settings.data_dir`, `settings.workflows_dir`, bundled `h3_ref2va.api.json`.

- [ ] **Step 1: Write failing model and store tests**

```python
def test_fresh_store_resolves_builtin_official(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    resolved = H3ProfileStore().resolve_active()
    assert resolved.profile_id == "builtin-official-h3"
    assert resolved.source == "builtin"
    assert resolved.workflow_sha256


def test_active_pointer_rejects_path_traversal(tmp_path, monkeypatch):
    store = isolated_store(tmp_path, monkeypatch)
    with pytest.raises(ProfileStorageError):
        store.select_profile("../outside")


def test_changed_custom_workflow_falls_back_to_builtin(tmp_path, monkeypatch):
    store = installed_custom_store(tmp_path, monkeypatch)
    store.workflow_path("custom").write_text("{}", encoding="utf-8")
    resolved = store.resolve_active()
    assert resolved.source == "builtin"
    assert resolved.warning.code == "profile_changed"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_store.py -q`

Expected: collection fails because `app.workflow_profiles.h3` does not exist.

- [ ] **Step 3: Implement strict profile models and errors**

```python
class H3BoundaryMapping(BaseModel):
    h3_node_id: str
    prompt_input: str
    width_input: str
    height_input: str
    frames_input: str
    picture_input_pattern: str
    audio_input_pattern: str | None = None
    seed_node_id: str
    seed_input: str
    saver_node_id: str
    output_prefix_input: str
    output_fields: tuple[str, ...] = ("videos",)


class H3WorkflowProfile(BaseModel):
    id: str
    kind: Literal["h3_ref2av"] = "h3_ref2av"
    contract_version: Literal[1] = 1
    workflow_sha256: str
    mapping: H3BoundaryMapping
    status: Literal["draft", "mapped", "validated", "tested", "active", "broken"]


@dataclass(frozen=True)
class ResolvedH3Profile:
    profile_id: str
    workflow: dict[str, Any]
    mapping: H3BoundaryMapping
    workflow_sha256: str
    source: Literal["builtin", "custom"]
    warning: ProfileWarning | None = None
```

Define typed `ProfileStorageError`, `ProfileChangedError`, and `ProfileWarning` in `errors.py`.

- [ ] **Step 4: Add the persistent profile root and store implementation**

Add to `Settings`:

```python
workflow_profiles_dir: Path = _DEFAULT_DATA_DIR / "workflow_profiles"
```

Derive it from a relocated `data_dir` in `_derive_persistent_paths`. Implement generated import IDs, normalized profile IDs (`[a-z0-9][a-z0-9-]{0,63}`), SHA-256 checks, JSON reads with UTF-8 BOM tolerance, atomic temporary-file replacement, and a built-in profile whose mapping matches the current official graph.

- [ ] **Step 5: Run focused and configuration tests**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_store.py tests/test_config.py -q`

Expected: all pass.

- [ ] **Step 6: Commit the profile foundation**

```powershell
git add backend/app/config.py backend/app/workflow_profiles backend/tests/test_h3_profile_store.py
git commit -m "add H3 workflow profile store"
```

---

### Task 2: Generic H3 Graph Filler and Per-job Profile Snapshot

**Files:**
- Modify: `backend/app/pipelines/base.py`
- Modify: `backend/app/core/jobs/runner.py`
- Modify: `backend/app/pipelines/h3_ref2va/workflow.py`
- Modify: `backend/app/pipelines/h3_ref2va/pipeline.py`
- Modify: `backend/app/pipelines/h3_ref2va/schemas.py`
- Modify: `backend/app/workflow_profiles/h3/store.py`
- Test: `backend/tests/test_h3_profile_runtime.py`
- Test: `backend/tests/test_h3_ref2va_graph.py`
- Test: `backend/tests/test_job_execution_adapters.py`

**Interfaces:**
- Consumes: `ResolvedH3Profile`, `H3BoundaryMapping`, `H3ProfileStore.resolve_active()` from Task 1.
- Produces: `fill_profile_graph(profile, job_params)`, `snapshot_profile_for_job(job)`, and the optional pipeline hook `prepare_job_submission(job)`.

- [ ] **Step 1: Write failing equivalence and hot-switch tests**

```python
def test_builtin_profile_matches_current_official_fill(tmp_path):
    profile = builtin_profile()
    actual = fill_profile_graph(profile, sample_job_params())
    assert normalize(actual) == normalize(expected_current_official_graph())


def test_changed_node_ids_fill_from_mapping():
    profile = profile_with_remapped_ids(h3="900", noise="901", saver="902")
    graph = fill_profile_graph(profile, sample_job_params())
    assert graph["900"]["inputs"]["prompt"] == SAMPLE_PROMPT
    assert graph["901"]["inputs"]["noise_seed"] == 42


async def test_queued_job_keeps_profile_selected_at_submission(
    sample_h3_job, sample_inputs, installed_profile_store
):
    job = sample_h3_job
    await start_pipeline_job(job, images=sample_inputs)
    store.select_profile("second-profile")
    snapshot = load_job_profile_snapshot(job.id)
    assert snapshot.profile_id == "first-profile"
```

- [ ] **Step 2: Run tests and confirm the old hard-coded workflow fails them**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_runtime.py tests/test_h3_ref2va_graph.py -q`

Expected: new tests fail because profile-aware filling and snapshotting are absent.

- [ ] **Step 3: Add the submission hook without changing other pipelines**

Add to `_PipelineCommon`:

```python
def prepare_job_submission(self, job: JobRecord) -> None:
    return None
```

Call it synchronously in `start_pipeline_job()` before the job is enriched, persisted, reserved, or placed in an asyncio task:

```python
pipeline = get_pipeline(job.pipeline_id)
pipeline.prepare_job_submission(job)
store.save_job(job)
```

For local H3 only, copy the resolved workflow and profile into `job_dir(job.id) / "workflow_profile"` using atomic writes, then store `h3_profile_id`, `h3_profile_sha256`, and `h3_contract_version` in `job.params`. MiniMax API jobs skip this snapshot.

- [ ] **Step 4: Replace hard-coded graph injection with the mapping contract**

Implement:

```python
def fill_profile_graph(
    profile: ResolvedH3Profile,
    job_params: dict[str, Any],
) -> dict[str, Any]:
    graph = copy.deepcopy(profile.workflow)
    binding = profile.mapping
    h3_inputs = graph[binding.h3_node_id]["inputs"]
    h3_inputs[binding.prompt_input] = job_params["prompt"]
    h3_inputs[binding.width_input] = int(job_params["width"])
    h3_inputs[binding.height_input] = int(job_params["height"])
    h3_inputs[binding.frames_input] = validate_frame_count(job_params["frames"])
    # Remove existing dynamic sockets, create LoadImage/LoadAudio nodes, then bind
    # picture_input_pattern/audio_input_pattern using zero-based {index}.
    graph[binding.seed_node_id]["inputs"][binding.seed_input] = int(job_params["seed"])
    graph[binding.saver_node_id]["inputs"][binding.output_prefix_input] = job_params["output_prefix"]
    return graph
```

Keep `_assert_pure_ref2va()` and all current prompt/Picture/Audio validation. Read the job snapshot in `build_prompt()` and `map_history_outputs()` so a later active-profile change cannot affect the job.

- [ ] **Step 5: Run graph, job runner, provider, and adapter tests**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_runtime.py tests/test_h3_ref2va_graph.py tests/test_no_i2v_on_h3_pipeline.py tests/test_job_execution_adapters.py -q`

Expected: all pass, including official-graph equivalence.

- [ ] **Step 6: Commit the generic runtime**

```powershell
git add backend/app/pipelines backend/app/core/jobs/runner.py backend/app/workflow_profiles/h3/store.py backend/tests
git commit -m "run H3 jobs from versioned workflow profiles"
```

---

### Task 3: Deterministic Workflow Inspector and Contract Validator

**Files:**
- Create: `backend/app/workflow_profiles/h3/inspector.py`
- Create: `backend/app/workflow_profiles/h3/validator.py`
- Modify: `backend/app/workflow_profiles/h3/models.py`
- Test: `backend/tests/test_h3_workflow_inspector.py`
- Test: `backend/tests/test_h3_workflow_validator.py`

**Interfaces:**
- Consumes: `H3BoundaryMapping` from Task 1 and `fill_profile_graph()` from Task 2.
- Produces: `inspect_h3_workflow(graph) -> H3WorkflowAnalysis` and `validate_h3_contract(graph, mapping) -> ValidationReport`.

- [ ] **Step 1: Write failing inspection tests for unique, ambiguous, and forbidden graphs**

```python
def test_inspector_auto_maps_unique_ref2av_graph():
    analysis = inspect_h3_workflow(unique_graph())
    assert analysis.compatibility == "auto_compatible"
    assert analysis.mapping.h3_node_id == "136"
    assert analysis.mapping.saver_node_id == "92"


def test_inspector_requires_confirmation_for_two_reachable_savers():
    analysis = inspect_h3_workflow(graph_with_preview_and_final_savers())
    assert analysis.compatibility == "needs_confirmation"
    assert {x.node_id for x in analysis.saver_candidates} == {"92", "148"}


@pytest.mark.parametrize("forbidden", ["MiniMaxH3ImageToVideo", "ref_frame", "last_frame"])
def test_inspector_rejects_non_ref2av_semantics(forbidden):
    assert inspect_h3_workflow(graph_with_forbidden(forbidden)).compatibility == "unsupported"
```

- [ ] **Step 2: Run the inspector tests and verify missing-module failures**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_inspector.py tests/test_h3_workflow_validator.py -q`

- [ ] **Step 3: Implement bounded parsing and normalized manifests**

Reject files over 8 MiB, more than 2,000 nodes, more than 10,000 graph edges, nesting deeper than 32, and string values over 64 KiB. Build an adjacency graph from `[node_id, output_index]` values. Emit only node IDs, class types, sanitized titles, input names, output reachability, candidate roles, and redacted defaults.

Redact values whose key contains `key`, `token`, `secret`, `password`, `authorization`, or `path`, and replace absolute path-shaped strings with `"<redacted-path>"`.

- [ ] **Step 4: Implement candidate discovery and exact validation**

Require one `MiniMaxH3ReferenceToVideo`; validate exact H3 input names from the proposed mapping; identify `RandomNoise` and `SaveVideo` candidates that are in the H3-to-final-output connected component. Treat multiple valid candidates as confirmation-required. Reject unmapped reachable `LoadImage`/`LoadAudio` file inputs unless marked as an explicit fixed dependency in the analysis report.

The validator must build a synthetic boundary job with one Picture, zero Audio, 864x480, 56 frames, and seed 42; call `fill_profile_graph()`; then verify every link points to an existing node and every mapped node/input still exists.

- [ ] **Step 5: Run inspector and validator tests**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_inspector.py tests/test_h3_workflow_validator.py -q`

Expected: all pass.

- [ ] **Step 6: Commit deterministic compatibility analysis**

```powershell
git add backend/app/workflow_profiles/h3 backend/tests/test_h3_workflow_*.py
git commit -m "inspect and validate custom H3 workflows"
```

---

### Task 4: Import, Mapping, Validation, Activation, and Selection API

**Files:**
- Create: `backend/app/api/h3_workflow_profiles.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/integrations/comfy_mcp.py`
- Modify: `backend/app/workflow_profiles/h3/store.py`
- Create: `backend/tests/test_h3_workflow_profiles_api.py`
- Modify: `backend/tests/test_comfy_mcp_client.py`

**Interfaces:**
- Consumes: store, inspector, validator, and graph filler from Tasks 1-3.
- Produces: the `/api/workflow-profiles/h3` HTTP interface and `ComfyMcpClient.validate_workflow(graph)`.

- [ ] **Step 1: Write failing API lifecycle tests**

```python
def test_import_analyze_validate_activate_and_select_builtin(client, sample_api_json):
    imported = client.post(
        "/api/workflow-profiles/h3/imports",
        files={"workflow": ("custom.api.json", sample_api_json, "application/json")},
    ).json()
    import_id = imported["import_id"]
    assert client.get(f"/api/workflow-profiles/h3/imports/{import_id}/analysis").status_code == 200
    assert client.post(f"/api/workflow-profiles/h3/imports/{import_id}/validate").status_code == 200
    mark_test_succeeded(import_id)
    assert client.post(f"/api/workflow-profiles/h3/imports/{import_id}/activate").status_code == 200
    assert client.post("/api/workflow-profiles/h3/select", json={"profile_id": "builtin-official-h3"}).status_code == 200


def test_activate_rejects_profile_without_successful_test(client, imported_id):
    response = client.post(f"/api/workflow-profiles/h3/imports/{imported_id}/activate")
    assert response.status_code == 409
```

- [ ] **Step 2: Run the API tests and verify 404/missing-route failures**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_profiles_api.py tests/test_comfy_mcp_client.py -q`

- [ ] **Step 3: Add a validate-only MCP operation**

Refactor the temporary API JSON creation in `ComfyMcpClient` into a private context manager and implement:

```python
async def validate_workflow(self, graph: dict[str, Any]) -> dict[str, Any]:
    with self._temporary_workflow(graph) as path:
        payload = await self.call_tool("validate_workflow", {"workflow_path": str(path)})
    if payload.get("valid") is not True:
        raise ComfyMcpError(format_validation_errors(payload))
    return payload
```

Make `submit_workflow()` call this method before `run_workflow()` so current execution behavior remains covered.

- [ ] **Step 4: Implement API models and routes**

Use multipart upload only on import; all later routes accept opaque IDs and strict Pydantic bodies. Map storage/contract/dependency/profile-change failures to stable HTTP 400/409/422 responses with `{code, message, details}`. Do not accept filesystem paths. Activation copies the validated import to a normalized installed-profile directory and atomically changes `active.json` only after a successful test record with the same hash.

- [ ] **Step 5: Run API and MCP tests**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_profiles_api.py tests/test_comfy_mcp_client.py -q`

Expected: all pass.

- [ ] **Step 6: Commit the profile HTTP API**

```powershell
git add backend/app/api backend/app/integrations/comfy_mcp.py backend/app/workflow_profiles/h3 backend/tests
git commit -m "add H3 workflow profile API"
```

---

### Task 5: Constrained Ollama Mapping Proposer

**Files:**
- Create: `backend/app/workflow_profiles/h3/agent.py`
- Create: `backend/app/workflow_profiles/h3/agent_prompt.py`
- Modify: `backend/app/api/h3_workflow_profiles.py`
- Test: `backend/tests/test_h3_mapping_agent.py`

**Interfaces:**
- Consumes: `H3WorkflowAnalysis.agent_manifest`, candidate IDs from Task 3, `get_director_model()`, `get_orchestrator().llm_session()`, and `OllamaClient.chat_response()`.
- Produces: `propose_h3_mapping(import_id) -> MappingProposal` with explanations and candidate-only selections.

- [ ] **Step 1: Write failing tests proving proposals cannot escape candidates**

```python
async def test_agent_proposal_is_filtered_to_inspector_candidates(fake_ollama):
    fake_ollama.response = {"content": json.dumps({"seed_node_id": "evil", "saver_node_id": "148"})}
    with pytest.raises(ContractError, match="not an inspected candidate"):
        await proposer.propose(analysis_with_candidates(seed=["129"], saver=["92", "148"]))


async def test_auto_compatible_analysis_skips_ollama(fake_ollama):
    proposal = await proposer.propose(auto_compatible_analysis())
    assert proposal.mapping == auto_compatible_analysis().mapping
    assert fake_ollama.calls == []
```

- [ ] **Step 2: Run tests and verify the proposer is missing**

Run: `Set-Location backend; py -m pytest tests/test_h3_mapping_agent.py -q`

- [ ] **Step 3: Implement the compact prompt and strict response schema**

The system prompt must state that the agent may select only listed IDs, cannot change the workflow, and must return one JSON object matching `MappingProposal`. Send the redacted manifest and candidate lists, not the full workflow. Use the currently selected Director model; return a clear configuration error when no Ollama model is selected.

- [ ] **Step 4: Use the existing VRAM LLM session**

```python
async with get_orchestrator().llm_session(on_status=None):
    response = await get_orchestrator().ollama.chat_response(
        get_director_model(),
        messages=messages,
        format=MappingProposal.model_json_schema(),
        options={"temperature": 0},
    )
```

Parse JSON after stripping any thinking wrapper, validate with Pydantic, and reject every ID/input not present in deterministic candidates. Store a proposal separately from the accepted mapping; never activate from this endpoint.

- [ ] **Step 5: Run mapping-agent and VRAM tests**

Run: `Set-Location backend; py -m pytest tests/test_h3_mapping_agent.py tests/test_vram_orchestrator.py -q`

Expected: all pass.

- [ ] **Step 6: Commit the bounded setup agent**

```powershell
git add backend/app/workflow_profiles/h3/agent.py backend/app/workflow_profiles/h3/agent_prompt.py backend/app/api/h3_workflow_profiles.py backend/tests/test_h3_mapping_agent.py
git commit -m "add local H3 mapping proposer"
```

---

### Task 6: Test-run Integration and Activation Gate

**Files:**
- Modify: `backend/app/api/h3_workflow_profiles.py`
- Modify: `backend/app/workflow_profiles/h3/store.py`
- Modify: `backend/app/pipelines/h3_ref2va/pipeline.py`
- Create: `backend/tests/test_h3_profile_test_run.py`

**Interfaces:**
- Consumes: existing `create_job()`, `start_pipeline_job()`, Asset Library file resolution, profile snapshot hook, and Comfy MCP execution.
- Produces: `POST /api/workflow-profiles/h3/imports/{id}/test` and durable same-hash test results used by activation.

- [ ] **Step 1: Write failing test-run and activation-gate tests**

```python
async def test_test_run_creates_56_frame_local_h3_job(client, actor_picture):
    response = client.post(
        f"/api/workflow-profiles/h3/imports/{import_id}/test",
        json={"picture_asset_id": actor_picture.id, "audio_asset_id": None},
    )
    assert response.status_code == 202
    job = load_job(response.json()["job_id"])
    assert job.params["frames"] == 56
    assert job.params["h3_profile_import_id"] == import_id


def test_test_result_for_old_hash_cannot_activate(store):
    store.record_test_success(import_id, workflow_sha256="old")
    mutate_import_workflow(import_id)
    with pytest.raises(ProfileChangedError):
        store.activate_import(import_id)
```

- [ ] **Step 2: Run the focused tests and confirm failures**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_test_run.py -q`

- [ ] **Step 3: Implement isolated import-profile test jobs**

Resolve one Picture asset and optional Voice asset through existing safe library APIs. Create an H3 local job with 864x480, 56 frames, seed 42, and a fixed neutral prompt that binds `<Picture 1>` and requests a slow camera push with normal ambient audio. Mark the job as a setup test and snapshot the import profile rather than the currently active profile. Do not add the output to the Asset Library or any shot.

- [ ] **Step 4: Persist terminal test results and enforce activation**

On job completion, expose its normal job URL to the Setup UI. Record `tested` only when the job succeeded, the mapped video was downloaded, and the workflow hash still matches. Activation rechecks contract version, workflow hash, Comfy validation result, and successful test job ID.

- [ ] **Step 5: Run test-run, adapter, and VRAM tests**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_test_run.py tests/test_job_execution_adapters.py tests/test_vram_orchestrator.py -q`

Expected: all pass.

- [ ] **Step 6: Commit real test-run gating**

```powershell
git add backend/app/api/h3_workflow_profiles.py backend/app/workflow_profiles/h3/store.py backend/app/pipelines/h3_ref2va/pipeline.py backend/tests/test_h3_profile_test_run.py
git commit -m "gate H3 profile activation on test video"
```

---

### Task 7: Workflow Setup UI and Production Profile Indicator

**Files:**
- Create: `frontend/src/features/settings/WorkflowSettingsPage.tsx`
- Create: `frontend/src/features/settings/H3WorkflowSetup.tsx`
- Create: `frontend/src/features/settings/H3WorkflowSetup.test.tsx`
- Create: `frontend/src/features/settings/types.ts`
- Modify: `frontend/src/shared/api/client.ts`
- Modify: `frontend/src/shared/api/types.ts`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/app/App.test.tsx`
- Modify: `frontend/src/app/navigation.ts`
- Modify: `frontend/src/app/navigation.test.ts`
- Modify: `frontend/src/features/production/ProductionPage.tsx`
- Modify: `frontend/src/features/production/ProductionPage.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 4-6 profile API responses.
- Produces: desktop `Settings -> Workflows -> H3`, import/mapping/validation/test/activate UX, fallback warnings, and Production profile label.

- [ ] **Step 1: Write failing UI state tests**

```tsx
it("imports an API workflow and shows auto-compatible bindings", async () => {
  render(<H3WorkflowSetup />);
  await user.upload(screen.getByLabelText("Import H3 API workflow"), apiFile);
  expect(await screen.findByText("Auto-compatible")).toBeTruthy();
  expect(screen.getByText("MiniMaxH3ReferenceToVideo")).toBeTruthy();
});

it("keeps Activate disabled until the same workflow hash is tested", async () => {
  render(<H3WorkflowSetup />);
  expect(await screen.findByRole("button", { name: "Activate profile" })).toBeDisabled();
});

it("shows the resolved custom workflow in Production", async () => {
  render(<ProductionPage active />);
  expect(await screen.findByText("Workflow: My H3 Quality Profile")).toBeTruthy();
});
```

- [ ] **Step 2: Run targeted frontend tests and verify missing components**

Run: `Set-Location frontend; npm test -- H3WorkflowSetup App ProductionPage navigation`

- [ ] **Step 3: Add typed API clients**

Implement `fetchH3Profiles`, `importH3Workflow`, `fetchH3ImportAnalysis`, `proposeH3Mapping`, `saveH3Mapping`, `validateH3Import`, `testH3Import`, `activateH3Import`, and `selectH3Profile`. Use `FormData` only for the import call and `parseError()` for structured error messages.

- [ ] **Step 4: Implement the desktop Settings workflow**

Add a Settings button outside the numbered project workflow tabs; mobile configuration is out of scope. Render Active Profile, Workflow Analysis, Boundary Mapping, and Validation & Test cards. Use candidate dropdowns only for ambiguous fields, display agent explanations as advisory text, select one Picture and optional Voice asset using current library data, poll the test job, preview the returned video, and enable Activate only on matching `tested` status.

- [ ] **Step 5: Add health/fallback and Production labels**

Render `Local · ComfyUI — {profile.display_name}` in Production. When the resolver reports a warning, show `Using Built-in Official H3` and the custom-profile failure reason without blocking official generation.

- [ ] **Step 6: Run frontend tests and production build**

Run: `Set-Location frontend; npm test`

Expected: all tests pass.

Run: `npm run build`

Expected: TypeScript and Vite complete successfully.

- [ ] **Step 7: Commit the Setup UI**

```powershell
git add frontend/src
git commit -m "add H3 workflow setup interface"
```

---

### Task 8: Documentation, Packaging Isolation, and Release Verification

**Files:**
- Modify: `README.md`
- Modify: `backend/packaging/director-studio-legacy.spec`
- Modify: `scripts/test-packaged-workflow-assets.ps1`
- Create: `scripts/test-packaged-h3-profiles.ps1`
- Modify: `backend/tests/test_packaged_runtime_paths.py`
- Modify: `backend/tests/test_portable_runtime_paths.py`

**Interfaces:**
- Consumes: all runtime and UI behavior from Tasks 1-7.
- Produces: public installation instructions and automated proof that release artifacts include only the official H3 workflow/profile.

- [ ] **Step 1: Write failing packaging checks**

Extend archive inspection to assert:

```powershell
Assert-ArchiveContains "workflows/h3_ref2va.api.json"
Assert-ArchiveDoesNotContain "workflow_profiles/h3/imports"
Assert-ArchiveDoesNotContain "workflow_profiles/h3/profiles"
Assert-ArchiveDoesNotContain "active.json"
```

Add a clean-extraction test that launches path resolution with an empty external data root and asserts `builtin-official-h3` is active.

- [ ] **Step 2: Run packaging-focused tests and verify the new checks fail before wiring**

Run: `Set-Location backend; py -m pytest tests/test_packaged_runtime_paths.py tests/test_portable_runtime_paths.py -q`

- [ ] **Step 3: Keep custom data outside the PyInstaller collection**

The spec must continue to collect `backend/workflows` for the official workflow. Do not add `data`, `workflow_profiles_dir`, imports, job outputs, or test fixtures to `Analysis.datas`. Make `scripts/test-packaged-h3-profiles.ps1` inspect both the executable archive and final zip.

- [ ] **Step 4: Document the user workflow and support boundary**

Update README with:

```text
Start ComfyUI and Ollama -> run DirectorStudio.exe ->
Settings -> Workflows -> H3 -> Import API JSON -> Validate -> Test -> Activate
```

State that only `MiniMaxH3ReferenceToVideo` workflows are supported, custom nodes/models remain the user's ComfyUI responsibility, runtime switching does not affect queued/running jobs, and every clean Portable starts with the official workflow.

- [ ] **Step 5: Run complete automated verification**

Run: `Set-Location backend; py -m pytest -q`

Expected: zero failures.

Run: `Set-Location ../frontend; npm test`

Expected: zero failures.

Run: `npm run build`

Expected: successful TypeScript/Vite build.

- [ ] **Step 6: Build and inspect the Portable artifact**

Run: `Set-Location ..; pwsh -File scripts/build-legacy-portable.ps1`

Expected: `DirectorStudio.exe` and zip are created.

Run: `pwsh -File scripts/test-packaged-workflow-assets.ps1 -ExecutablePath build/pyinstaller-dist/DirectorStudio.exe`

Expected: official workflow assets are present.

Run: `pwsh -File scripts/test-packaged-h3-profiles.ps1 -ExecutablePath build/pyinstaller-dist/DirectorStudio.exe -ZipPath dist/Director-Studio-Legacy-Windows-x64.zip`

Expected: official H3 is present and every custom-profile/data assertion is absent.

- [ ] **Step 7: Perform the real runtime smoke test**

In a clean extracted directory, start ComfyUI and Ollama, run `DirectorStudio.exe`, confirm the active profile is `Built-in Official H3`, import a separately exported compatible Ref2AV API workflow, resolve any mapping choice, validate it, generate a 56-frame test video, activate it, submit one new Production job, switch back to official without restarting, and confirm the next submitted job records `builtin-official-h3`.

- [ ] **Step 8: Commit docs and packaging protections**

```powershell
git add README.md backend/packaging scripts backend/tests
git commit -m "document and verify portable H3 profiles"
```

---

### Task 9: Final Regression Review and Push-ready State

**Files:**
- Review only: all files changed in Tasks 1-8.

**Interfaces:**
- Consumes: completed implementation and all verification evidence.
- Produces: a clean, reviewed branch ready for user testing and explicit push authorization.

- [ ] **Step 1: Review the branch against the design spec**

Run: `git diff origin/main...HEAD --stat`

Run: `git diff origin/main...HEAD --check`

Inspect each spec section against code and tests. Confirm no custom workflow, local absolute path, API key, `.env`, job output, project data, or generated test video is tracked.

- [ ] **Step 2: Run focused H3 verification once more**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_store.py tests/test_h3_profile_runtime.py tests/test_h3_workflow_inspector.py tests/test_h3_workflow_validator.py tests/test_h3_workflow_profiles_api.py tests/test_h3_mapping_agent.py tests/test_h3_profile_test_run.py tests/test_h3_ref2va_graph.py tests/test_no_i2v_on_h3_pipeline.py -q`

Expected: zero failures.

- [ ] **Step 3: Verify repository hygiene**

Run: `git status --short`

Expected: no output.

Run: `git log --format="%h %ae %s" origin/main..HEAD`

Expected: every new commit uses `aibox2764@gmail.com`.

- [ ] **Step 4: Present the artifact and runtime test handoff**

Report the branch/commit range, test totals, Portable artifact path, profile import steps, known first-release limits, and whether the real 56-frame test completed. Do not push unless the user has explicitly requested it for this repository and branch.
