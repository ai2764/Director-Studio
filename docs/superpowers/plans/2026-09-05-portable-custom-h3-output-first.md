# Portable Custom H3 Output-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the experimental agent-driven H3 profile mapper with a deterministic, output-first custom H3 workflow setup that treats the ComfyUI graph internals as opaque.

**Architecture:** Import validation parses an API graph, graph analysis ranks terminal output nodes using topology plus live ComfyUI `object_info`, and reverse traversal finds H3/optional seed candidates upstream of the user-selected output. A schema-v2 boundary directly injects Director Studio inputs into the confirmed H3 node; Comfy MCP history and downloaded artifacts resolve the selected output node and optional artifact index. The official bundled workflow uses the same runtime contract and remains the default fallback.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, httpx, pytest, React 19, TypeScript 5.8, Vitest, ComfyUI HTTP API, Comfy MCP, PyInstaller, PowerShell.

## Global Constraints

- Custom workflow setup must work without Ollama and must expose no LLM proposal endpoint.
- Only MiniMax H3 Ref2AV Picture 1-9 and optional standalone Audio 1-3 inputs are supported.
- Reference video, paired reference-video-audio, I2V, first-frame, and last-frame semantics are excluded.
- Workflow-internal nodes and settings remain unchanged except links/defaults on confirmed H3 inputs and an optional confirmed seed input.
- Node labels use `_meta.title`, then ComfyUI display name, then `class_type`; `Node <id>` is secondary.
- The built-in official H3 workflow is immutable, always available, and the clean-install default.
- All persistent paths remain generated and contained below `data/workflow_profiles/h3`.
- Every behavior change follows red-green-refactor and each task ends with its focused tests passing.

---

### Task 1: Introduce the schema-v2 input and output boundary

**Files:**
- Modify: `backend/app/workflow_profiles/h3/models.py`
- Modify: `backend/app/workflow_profiles/h3/store.py`
- Modify: `backend/tests/test_h3_profile_store.py`
- Modify: `backend/tests/test_h3_profile_runtime.py`

**Interfaces:**
- Produces: `H3InputMapping`, `H3OutputSelection`, nested `H3BoundaryMapping`, and `H3WorkflowProfile.contract_version == 2`.
- Produces: `H3ProfileStore.boundary_sha256(mapping) -> str`, which excludes only `output.artifact_index`.
- Consumes: existing safe import/profile storage and official workflow resources.

- [ ] **Step 1: Write failing schema tests**

Add tests that construct this exact boundary and reject legacy mandatory saver fields:

```python
mapping = H3BoundaryMapping(
    inputs=H3InputMapping(
        h3_node_id="136",
        prompt_input="prompt",
        width_input="width",
        height_input="height",
        frames_input="length",
        picture_input_pattern="ref_images.ref_image_{index}",
        audio_input_pattern="ref_audios.ref_audio_{index}",
        seed_node_id=None,
        seed_input=None,
    ),
    output=H3OutputSelection(node_id="92", artifact_index=None),
)
assert H3WorkflowProfile(..., mapping=mapping).contract_version == 2
```

Assert that `seed_node_id` and `seed_input` must both be set or both be `None`, `artifact_index` rejects negative values, and `boundary_sha256()` is unchanged when only `artifact_index` changes.

- [ ] **Step 2: Run the schema tests and verify RED**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_store.py tests/test_h3_profile_runtime.py -q`

Expected: failures because the nested v2 models and `boundary_sha256` do not exist.

- [ ] **Step 3: Implement the v2 models and official mapping**

Define strict models with these fields:

```python
class H3InputMapping(_StrictModel):
    h3_node_id: StrictStr = Field(min_length=1)
    prompt_input: StrictStr = Field(min_length=1)
    width_input: StrictStr = Field(min_length=1)
    height_input: StrictStr = Field(min_length=1)
    frames_input: StrictStr = Field(min_length=1)
    picture_input_pattern: StrictStr = Field(min_length=1)
    audio_input_pattern: StrictStr | None = None
    seed_node_id: StrictStr | None = None
    seed_input: StrictStr | None = None

class H3OutputSelection(_StrictModel):
    node_id: StrictStr = Field(min_length=1)
    artifact_index: int | None = Field(default=None, ge=0)

class H3BoundaryMapping(_StrictModel):
    inputs: H3InputMapping
    output: H3OutputSelection
```

Use a model validator for the seed pair. Rebuild `_OFFICIAL_MAPPING` with output node `92`. Bump persisted validation, test, and profile records to contract version 2. Compute `boundary_sha256` from canonical JSON after replacing `artifact_index` with `None`; keep the full mapping in profile metadata.

Because this branch has not shipped, reject schema-v1 experimental records with the existing structured re-import warning instead of maintaining a broad migration layer.

- [ ] **Step 4: Run schema/store tests and verify GREEN**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_store.py tests/test_h3_profile_runtime.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/workflow_profiles/h3/models.py backend/app/workflow_profiles/h3/store.py backend/tests/test_h3_profile_store.py backend/tests/test_h3_profile_runtime.py
git commit -m "refactor h3 workflow boundary schema"
```

---

### Task 2: Discover named terminal outputs and reverse upstream inputs

**Files:**
- Modify: `backend/app/core/comfy/client.py`
- Rewrite: `backend/app/workflow_profiles/h3/inspector.py`
- Modify: `backend/app/workflow_profiles/h3/models.py`
- Modify: `backend/tests/test_h3_workflow_inspector.py`
- Create: `backend/tests/test_comfy_http_client.py`
- Create: `backend/tests/fixtures/h3_multistage_vhs.api.json`

**Interfaces:**
- Produces: `ComfyClient.get_object_info() -> dict[str, Any]`.
- Produces: `inspect_h3_workflow(graph, object_info=None, output_node_id=None) -> H3WorkflowAnalysis`.
- Produces: named `output_candidates`, `h3_candidates`, and `seed_candidates` containing `node_id`, `class_type`, `title`, `object_display_name`, and display label data.
- Consumes: schema-v2 models from Task 1.

- [ ] **Step 1: Add sanitized multi-stage and topology tests**

Build the fixture as an API-valid graph with this topology:

```text
MiniMaxH3ReferenceToVideo -> sampler -> latent split -> latent upscale
-> concatenate -> second sampler -> VHS_VideoCombine
```

Add an unrelated H3 branch and a second terminal preview. Test that:

```python
analysis = inspect_h3_workflow(graph, object_info=object_info)
assert [item.node_id for item in analysis.output_candidates] == ["214", "300"]

selected = inspect_h3_workflow(
    graph, object_info=object_info, output_node_id="214"
)
assert [item.node_id for item in selected.h3_candidates] == ["265"]
assert selected.h3_candidates[0].display_name == "H3 Main Generator"
```

Also test malformed node errors are emitted once per offending node and include `_meta.title` plus node ID, terminal detection uses graph out-degree, `output_node`/declared video types rank candidates without a class allowlist, and no output selection yields no upstream mapping.

- [ ] **Step 2: Run inspector tests and verify RED**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_inspector.py -q`

Expected: failures because analysis still requires one H3 and hardcodes `SaveVideo`/`RandomNoise`.

- [ ] **Step 3: Implement topology analysis and live metadata lookup**

Keep the existing size/depth/path-safety checks. Replace descendant-based saver discovery with:

```python
terminal_ids = {
    node_id for node_id, targets in outgoing.items() if not targets
}

def ancestors(start: str, incoming: Mapping[str, set[str]]) -> set[str]:
    visited, pending = set(), deque([start])
    while pending:
        node_id = pending.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(sorted(incoming.get(node_id, ())))
    return visited
```

Rank terminal candidates with graph `_meta`, `object_info[class_type]["output_node"]`, declared `output` types, and sanitized display names. Do not test saver class names. After `output_node_id` is supplied, filter H3 and optional seed candidates to its ancestor set. Preserve raw workflow values only in the stored graph; response manifests contain identifiers, names, types, and redacted fixed dependency summaries.

Add a bounded `GET /object_info` method to the existing Comfy HTTP client with the existing timeout/error conventions.

- [ ] **Step 4: Run inspector and Comfy client tests and verify GREEN**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_inspector.py tests/test_comfy_http_client.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/core/comfy/client.py backend/app/workflow_profiles/h3/inspector.py backend/app/workflow_profiles/h3/models.py backend/tests/test_h3_workflow_inspector.py backend/tests/test_comfy_http_client.py backend/tests/fixtures/h3_multistage_vhs.api.json
git commit -m "feat discover H3 workflow boundaries output first"
```

---

### Task 3: Validate and fill opaque graphs with optional seed injection

**Files:**
- Rewrite: `backend/app/workflow_profiles/h3/validator.py`
- Modify: `backend/app/pipelines/h3_ref2va/workflow.py`
- Modify: `backend/tests/test_h3_workflow_validator.py`
- Modify: `backend/tests/test_h3_ref2va_graph.py`
- Modify: `backend/tests/test_h3_profile_runtime.py`

**Interfaces:**
- Produces: `validate_h3_contract(graph, mapping, object_info=None) -> ValidationReport`.
- Produces: `fill_profile_graph(profile, job_params) -> dict[str, Any]` using `mapping.inputs` only.
- Produces: `map_history_output_candidates(history, profile) -> tuple[ComfyImageRef, ...]` and `map_history_outputs(...)` selecting the configured artifact index.
- Consumes: output/upstream analysis and schema v2.

- [ ] **Step 1: Write failing opaque-graph contract tests**

Add tests proving:

```python
report = validate_h3_contract(multistage_graph, mapping_without_seed, object_info)
assert report.valid is True

filled = fill_profile_graph(profile, params)
assert filled["265"]["inputs"]["prompt"] == params["prompt"]
assert filled["265"]["inputs"]["ref_images.ref_image_0"][0] in filled
assert filled["129"]["inputs"]["noise_seed"] == original_seed
assert filled["214"] == multistage_graph["214"]
```

Cover multiple samplers/decoders/muxers, multiple unrelated H3 nodes, selected H3 not upstream of output, missing canonical input, optional Audio, removal of stale Picture/Audio sockets only on the selected H3 node, and explicit rejection of reference-video/I2V/first/last-frame boundary mappings.

For history, assert one video is automatic, multiple videos are returned in stable MCP URL order, a valid `artifact_index` selects one, and an out-of-range index returns no mapped production output.

- [ ] **Step 2: Run validator/runtime tests and verify RED**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_validator.py tests/test_h3_ref2va_graph.py tests/test_h3_profile_runtime.py -q`

Expected: failures from mandatory RandomNoise/SaveVideo assumptions and flattened fields.

- [ ] **Step 3: Implement boundary-only validation and filling**

Validate the selected output exists and is terminal, the selected H3 is in its reverse ancestor set, canonical sockets exist, dynamic patterns contain one `{index}`, and an optional seed pair points to an upstream input. Do not reject internal fixed file nodes or constrain intermediate topology; the later Comfy validation owns those failures.

Change the filler to use `binding = profile.mapping.inputs`. Inject seed only when both optional seed fields are set. Remove output-prefix injection from the custom boundary. Keep generation of unique `LoadImage`/`LoadAudio` nodes and removal of stale dynamic Picture/Audio/video-reference sockets on the selected H3 node.

Map history from `profile.mapping.output.node_id`, filter video extensions, preserve URL order, and apply `artifact_index` only for final production mapping.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_validator.py tests/test_h3_ref2va_graph.py tests/test_h3_profile_runtime.py tests/test_no_i2v_on_h3_pipeline.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/workflow_profiles/h3/validator.py backend/app/pipelines/h3_ref2va/workflow.py backend/tests/test_h3_workflow_validator.py backend/tests/test_h3_ref2va_graph.py backend/tests/test_h3_profile_runtime.py
git commit -m "feat fill opaque H3 workflows through confirmed boundary"
```

---

### Task 4: Replace agent proposal APIs with explicit output and input confirmation

**Files:**
- Modify: `backend/app/api/h3_workflow_profiles.py`
- Modify: `backend/app/workflow_profiles/h3/store.py`
- Delete: `backend/app/workflow_profiles/h3/agent.py`
- Delete: `backend/app/workflow_profiles/h3/agent_prompt.py`
- Delete: `backend/tests/test_h3_mapping_agent.py`
- Modify: `backend/tests/test_h3_workflow_profiles_api.py`

**Interfaces:**
- Produces: `PUT /imports/{id}/output` with `{ "node_id": "214" }`.
- Produces: `PUT /imports/{id}/mapping` accepting the nested confirmed boundary.
- Removes: `POST /imports/{id}/propose-mapping` and all setup-agent/Ollama code.
- Consumes: `inspect_h3_workflow(..., output_node_id=...)` and schema-v2 storage.

- [ ] **Step 1: Write failing route and lifecycle tests**

Test this sequence:

```python
created = client.post("/api/workflow-profiles/h3/imports", files=files).json()
analysis = client.get(f".../{created['import_id']}/analysis").json()
assert analysis["output_candidates"][0]["display_name"] == "Final Video Combine"

selected = client.put(
    f".../{created['import_id']}/output", json={"node_id": "214"}
).json()
assert selected["h3_candidates"][0]["node_id"] == "265"

assert client.post(f".../{created['import_id']}/propose-mapping").status_code == 404
```

Also assert output changes invalidate mapping/validation/test evidence; mapping changes invalidate validation/test evidence; object-info failure still returns topology candidates with a visible warning; and every invalid selection returns a structured error naming the node.

- [ ] **Step 2: Run API tests and verify RED**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_profiles_api.py -q`

Expected: failures because `/output` is absent and `/propose-mapping` still exists.

- [ ] **Step 3: Implement explicit selection routes and remove the agent**

Store the selected output under the import directory before mapping. `analysis` loads that selection, obtains best-effort `object_info`, and returns upstream candidates. Saving a mapping verifies every node/input against the current analysis before writing it.

Delete the agent modules, route, imports, response types, Ollama calls, and agent-only manifest. Preserve opaque IDs, path containment, hash checks, durable lifecycle restoration, and official fallback.

- [ ] **Step 4: Run API/store tests and verify GREEN**

Run: `Set-Location backend; py -m pytest tests/test_h3_workflow_profiles_api.py tests/test_h3_profile_store.py tests/test_config.py -q`

Expected: all selected tests pass and no test imports the removed agent modules.

- [ ] **Step 5: Commit**

```powershell
git add -A backend/app/api/h3_workflow_profiles.py backend/app/workflow_profiles/h3 backend/tests/test_h3_workflow_profiles_api.py backend/tests/test_h3_mapping_agent.py
git commit -m "refactor remove agent from custom H3 setup"
```

---

### Task 5: Make test evidence and MCP artifacts output-node aware

**Files:**
- Modify: `backend/app/core/jobs/execution_adapters/comfy_mcp.py`
- Modify: `backend/app/pipelines/h3_ref2va/pipeline.py`
- Modify: `backend/app/workflow_profiles/h3/store.py`
- Modify: `backend/app/api/h3_workflow_profiles.py`
- Modify: `backend/tests/test_job_execution_adapters.py`
- Modify: `backend/tests/test_h3_profile_test_run.py`

**Interfaces:**
- Produces: test jobs that retain all video artifacts reported for the confirmed output node.
- Produces: `PUT /imports/{id}/test-output` with `{ "artifact_index": 1 }`.
- Produces: activation evidence binding workflow hash, boundary hash, test job ID, observed output-node URLs, and selected index.
- Consumes: MCP `outputs_by_node` URL ordering and downloaded-file URL identity.

- [ ] **Step 1: Write failing MCP artifact tests**

Use a fake MCP result with selected node `214` returning two MP4 URLs and another node returning an MP4. Assert the test job saves only node 214's two candidates, exposes both previews, rejects index 2, accepts index 1, and activation installs index 1 without re-running generation.

Add a production assertion:

```python
profile.mapping.output.artifact_index = 1
mapped = pipeline.map_history_outputs(history, job=job)
assert mapped["video"].filename == "second.mp4"
```

Also prove zero videos from node 214 fails even when another node produced a video, and changing workflow/boundary identity makes prior evidence unusable.

- [ ] **Step 2: Run test-run and adapter tests and verify RED**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_test_run.py tests/test_job_execution_adapters.py -q`

Expected: failures because current execution saves exactly one mapped video and has no artifact-selection evidence.

- [ ] **Step 3: Implement artifact-aware collection and evidence**

For H3 setup-test jobs, return stable logical keys `video_candidate_0`, `video_candidate_1`, and store their source references in job metadata. Normal production jobs continue to emit only logical key `video` after applying the persisted index.

`test-output` verifies the referenced job is succeeded, belongs to the import, matches workflow and boundary hashes, and contains the requested candidate index. Save the choice in test evidence. Boundary hashing excludes only artifact index, so choosing among already observed outputs does not invalidate graph validation; all other changes do.

Activation requires an artifact index only when the observed selected node produced more than one video. One video sets index 0 automatically. No video never records successful test evidence.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `Set-Location backend; py -m pytest tests/test_h3_profile_test_run.py tests/test_job_execution_adapters.py tests/test_h3_workflow_profiles_api.py tests/test_vram_orchestrator.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/core/jobs/execution_adapters/comfy_mcp.py backend/app/pipelines/h3_ref2va/pipeline.py backend/app/workflow_profiles/h3/store.py backend/app/api/h3_workflow_profiles.py backend/tests/test_job_execution_adapters.py backend/tests/test_h3_profile_test_run.py
git commit -m "feat resolve custom H3 video artifacts through MCP"
```

---

### Task 6: Replace Profile/Agent UI with Custom H3 Workflows

**Files:**
- Rewrite: `frontend/src/features/settings/types.ts`
- Modify: `frontend/src/shared/api/client.ts`
- Rewrite: `frontend/src/features/settings/H3WorkflowSetup.tsx`
- Modify: `frontend/src/features/settings/H3WorkflowSetup.test.tsx`
- Modify: `frontend/src/features/settings/WorkflowSettingsPage.tsx`
- Modify: `frontend/src/shared/styles.css`

**Interfaces:**
- Produces: frontend clients `selectH3Output`, `saveH3Mapping`, and `selectH3TestOutput`.
- Removes: `H3Proposal`, `proposeH3Mapping`, “Suggest mapping”, “Active profile”, and user-facing “profile” copy.
- Consumes: backend analysis, lifecycle, test artifact candidates, and activation APIs from Tasks 4-5.

- [ ] **Step 1: Write failing UI tests for the complete user flow**

Test that the page renders:

```text
Custom H3 Workflows
Current Workflow
Import Workflow
Final Video Output
H3 Inputs
Validate & Test
Use Workflow
```

Mock two named terminal outputs and verify options show `Final Video Combine · VHS_VideoCombine · Node 214`. Select it and return two named upstream H3 candidates; verify user confirmation is required. Assert seed can be `Workflow default`, Audio is labeled standalone reference Audio, no “agent”, “suggest”, or user-facing “profile” appears, and a two-video test shows previews plus an explicit artifact choice before activation.

- [ ] **Step 2: Run UI tests and verify RED**

Run: `Set-Location frontend; npm test -- H3WorkflowSetup`

Expected: failures because the current screen is profile/agent/saver driven.

- [ ] **Step 3: Implement the output-first settings flow**

Use a linear state machine:

```text
imported -> output selected -> inputs confirmed -> validated
-> test running -> artifact confirmed -> activatable
```

Render candidate names first with class and node ID secondary. Disable later sections until their prerequisite is confirmed. Keep current reload/poll recovery, project asset selection, error focus, and official fallback behavior. Use `Voice for test (optional standalone Audio)` only when the selected H3 node supports Audio.

Remove proposal state, proposal requests, saver/output-prefix fields, and all agent copy. Change every visible “profile” to “workflow”; internal API paths and persisted keys may retain the old term.

- [ ] **Step 4: Run frontend tests and build and verify GREEN**

Run: `Set-Location frontend; npm test -- H3WorkflowSetup App ProductionPage navigation; npm run build`

Expected: tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/features/settings/types.ts frontend/src/shared/api/client.ts frontend/src/features/settings/H3WorkflowSetup.tsx frontend/src/features/settings/H3WorkflowSetup.test.tsx frontend/src/features/settings/WorkflowSettingsPage.tsx frontend/src/shared/styles.css
git commit -m "feat add output-first custom H3 workflow setup UI"
```

---

### Task 7: Regression, documentation, packaging, and real workflow smoke test

**Files:**
- Modify: `README.md`
- Modify: `backend/tests/test_packaged_runtime_paths.py`
- Modify: `backend/tests/test_portable_runtime_paths.py`
- Modify: `scripts/verify-legacy-portable.ps1`
- Modify: `scripts/build-legacy-portable.ps1`

**Interfaces:**
- Consumes: the complete schema-v2 backend and UI.
- Produces: a verified Portable zip containing only the official H3 workflow and clean external data directories.

- [ ] **Step 1: Write failing packaging assertions**

Assert the packaged runtime includes the official H3 API graph and schema-v2 built-in mapping, excludes `data/workflow_profiles`, imported JSON, test jobs, generated videos, `agent.py`, and `agent_prompt.py`, and starts with the official workflow after extraction into a new directory.

- [ ] **Step 2: Run packaging-focused tests and verify RED where expectations changed**

Run: `Set-Location backend; py -m pytest tests/test_packaged_runtime_paths.py tests/test_portable_runtime_paths.py -q`

Expected: any schema-v1 or removed-agent artifact assertions fail until packaging is updated.

- [ ] **Step 3: Update README and portable verification**

Document the runtime flow exactly:

```text
Settings -> Workflows -> H3 -> Import Workflow
-> Final Video Output -> H3 Inputs -> Validate & Test -> Use Workflow
```

State that users must first run their API graph in local ComfyUI; internal nodes are opaque to Director Studio; Picture 1-9 and optional standalone Audio 1-3 are supported; reference video is not supported; no Ollama is required for workflow setup; the official workflow remains the fallback.

Update build/verification scripts only where required to enforce the new exclusions and official schema-v2 resource.

- [ ] **Step 4: Run the full automated suite**

Run: `Set-Location backend; py -m pytest -q`

Expected: all backend tests pass.

Run: `Set-Location ../frontend; npm test; npm run build`

Expected: all frontend tests pass and production build succeeds.

- [ ] **Step 5: Build and inspect Portable**

Run: `Set-Location ..; powershell -ExecutionPolicy Bypass -File scripts/build-legacy-portable.ps1`

Run the repository's portable verification script against the generated staging directory and zip. Confirm by archive listing that no custom workflow, imported mapping, test media, local path, or active custom pointer is present.

- [ ] **Step 6: Run real ComfyUI smoke tests**

With the target local ComfyUI and MCP running:

1. Import the previously validated community standard Ref2AV API graph.
2. Select its named final video output and upstream H3 node.
3. Run the 56-frame test and activate it.
4. Generate one production video.
5. Import the sanitized multi-stage VHS workflow when its local dependencies are available, select `Final Video Combine`, and verify output-first discovery reaches the intended H3 node.
6. Switch to **Built-in Official H3** without restarting and submit another job.

Record job IDs, frame count, resolution, duration, elapsed time, selected output node, and artifact index. Do not place the workflows or generated media in the release archive.

- [ ] **Step 7: Commit**

```powershell
git add README.md backend/tests/test_packaged_runtime_paths.py backend/tests/test_portable_runtime_paths.py scripts/verify-legacy-portable.ps1 scripts/build-legacy-portable.ps1
git commit -m "docs verify portable custom H3 workflows"
```

- [ ] **Step 8: Final verification and branch review**

Run: `git diff --check; git status --short; git log --format="%h %an <%ae> %s" main..HEAD`

Expected: no whitespace errors, no uncommitted source changes, and every new commit uses `ai2764 <aibox2764@gmail.com>`.

Inspect: `git diff --stat main...HEAD` and `git diff --name-only main...HEAD`.

Expected: only intended source, tests, docs, and packaging files are changed; no generated data, video, build directory, temporary workflow, or machine-local path is tracked.
