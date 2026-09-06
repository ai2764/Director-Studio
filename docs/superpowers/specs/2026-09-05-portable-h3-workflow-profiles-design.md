# Portable Custom H3 Workflows

## Summary

Director Studio Portable will let users import and activate a custom ComfyUI MiniMax H3 Ref2AV API workflow while the service is running. The custom graph is treated as an opaque implementation: Director Studio identifies only the final video output and the H3 inputs it owns, then leaves loaders, LoRAs, samplers, schedulers, latent processing, decoding, upscaling, frame interpolation, audio processing, and muxing unchanged.

Onboarding is deterministic and user-confirmed. It does not use the Director agent, a setup agent, or Ollama. The workflow is configured output-first: the user selects a terminal video output, Director Studio reverse-traverses that output's upstream graph to find compatible H3 input boundaries, and the user confirms the input mapping.

The first release supports MiniMax H3 Ref2AV workflows with Picture references and optional standalone Audio references. Other video models, I2V, first/last-frame interfaces, reference-video inputs, and paired reference-video-audio inputs are outside the contract.

## Goals

- Import a locally working ComfyUI API-format H3 workflow from the running Portable UI.
- Support arbitrary internal graph topology behind a small, stable Director Studio input/output boundary.
- Let the user identify the intended final video when a graph has multiple terminal outputs.
- Reverse-discover eligible H3 inputs only from the selected output's upstream subgraph.
- Show human-readable node names prominently and node IDs as secondary identifiers.
- Validate and test the filled graph through the existing Comfy MCP execution path.
- Use runtime artifacts reported by MCP as the authoritative output result.
- Activate, switch, and restore workflows without restarting Director Studio.
- Keep the bundled official H3 workflow immutable and always available.
- Require neither Ollama nor an LLM for workflow integration.

## Non-goals

- Supporting arbitrary video-model semantics through the H3 provider.
- Converting I2V, first-frame, last-frame, FLF, or video-reference workflows into Ref2AV.
- Supporting reference video or combined reference-video-audio inputs.
- Rewriting graph topology or tuning workflow-internal quality settings.
- Automatically installing models or ComfyUI custom nodes.
- Replacing Actor, Scene, Prop, or Layout workflows in this release.
- Editing an installed workflow in place or hot-reloading manual JSON changes.

## User workflow

The user starts ComfyUI and Director Studio. Comfy MCP continues to be launched by Director Studio from its normal configuration. Ollama is not required for this setup flow.

1. Build and successfully run the custom H3 Ref2AV graph in ComfyUI.
2. Export the graph in ComfyUI API format.
3. Open `Settings -> Workflows -> H3` in Director Studio.
4. Import the API JSON.
5. Select the intended **Final Video Output** from detected terminal video-output candidates.
6. Review H3 nodes found by reverse traversal from that output and select one if more than one is compatible.
7. Confirm the prompt, dimensions, frame count, Picture, optional Audio, and optional seed mappings.
8. Validate the filled graph against the connected ComfyUI instance.
9. Select a Picture from the Asset Library and run a 56-frame test. Standalone reference Audio is optional.
10. If the selected output returns multiple video artifacts, choose the artifact or output field to use.
11. Inspect the returned video and select **Use Workflow**.
12. Switch back to **Built-in Official H3** at any time.

The user never edits node IDs by hand. Automatic discovery may preselect an unambiguous candidate, but every custom workflow still shows the selected output and input boundary for confirmation.

## Boundary contract

The contract describes only the values Director Studio injects and the runtime artifact it consumes. It does not describe the complete ComfyUI graph.

```python
@dataclass(frozen=True)
class H3InputMapping:
    h3_node_id: str
    prompt_input: str
    width_input: str
    height_input: str
    frames_input: str
    picture_input_pattern: str
    audio_input_pattern: str | None = None
    seed_node_id: str | None = None
    seed_input: str | None = None


@dataclass(frozen=True)
class H3OutputSelection:
    node_id: str
    artifact_index: int | None = None


@dataclass(frozen=True)
class H3BoundaryMapping:
    inputs: H3InputMapping
    output: H3OutputSelection


@dataclass(frozen=True)
class ResolvedH3Workflow:
    workflow_id: str
    workflow: dict[str, Any]
    mapping: H3BoundaryMapping
    workflow_sha256: str
    source: Literal["builtin", "custom"]
```

A compatible custom workflow must satisfy these application boundaries:

- The selected output is a terminal, output-capable node and produces a video artifact during the test run.
- At least one compatible `MiniMaxH3ReferenceToVideo` node exists upstream of the selected output.
- The selected H3 node exposes prompt, width, height, frame length, and dynamic Picture inputs.
- Dynamic Picture inputs support Director Studio's ordered Picture 1 through Picture 9 semantics.
- Standalone Audio is either absent or supports ordered Audio 1 through Audio 3 semantics.
- Seed injection is optional. If no seed mapping is selected, Director Studio preserves the workflow's existing seed behavior.
- The graph does not require Director Studio to provide I2V, first-frame, last-frame, reference-video, or paired reference-video-audio semantics.

The graph may contain multiple H3 nodes, samplers, decoders, upscalers, interpolation stages, audio nodes, muxers, preview nodes, and output nodes. Nodes outside the confirmed boundary remain untouched. Director Studio injects directly into the confirmed H3 sockets; doing so may replace links into those sockets, while preserving every other input and edge.

The serialized custom-workflow record contains a schema version, workflow hash, confirmed input mapping, confirmed output selection, compatibility result, and validation metadata. Node IDs are strings. Input names must match the imported graph exactly. An optional artifact index must refer to the ordered video artifacts MCP observed for the selected output node during the successful test.

## Graph inspection

### Import validation

The importer accepts only a ComfyUI API graph: a root JSON object whose node values are objects containing a non-empty `class_type` string and an `inputs` object. Structural errors identify each offending node by title when available and by node ID. UI-format workflow exports are rejected with a concise instruction to export API format.

This strict import gate is intentional. Director Studio assumes the imported workflow can already run in the user's ComfyUI installation; it does not attempt to repair malformed JSON or infer missing node classes.

### Node presentation

Every candidate uses the following display priority:

1. Node `_meta.title` from the workflow.
2. ComfyUI `object_info` display name.
3. `class_type`.
4. `Node <id>` as the secondary identifier.

For example, the UI may show `Final Video Combine · VHS_VideoCombine · Node 214`. Node IDs are never the only visible label.

### Final video output discovery

Director Studio calculates node out-degree from graph links and identifies terminal nodes. It enriches those nodes with connected ComfyUI `/object_info` metadata, including output-node status and declared output types. Output candidates are ranked using structural and type metadata, not a hardcoded list of saver class names.

The user selects the intended **Final Video Output** before input discovery. A terminal node can be offered when it is output-capable and declares or empirically returns a video-like artifact. The test run is authoritative: if the selected node produces no video, validation does not silently substitute another node.

### Reverse input discovery

After output selection, Director Studio reverse-traverses only the selected output's upstream subgraph. It locates compatible MiniMax H3 Ref2AV nodes and validates their canonical sockets using both the imported graph and `object_info`.

- One compatible upstream H3 node may be preselected.
- Multiple compatible upstream H3 nodes require user selection.
- H3 nodes outside the selected output's upstream graph are irrelevant.
- Prompt, width, height, frame length, Picture 1-9, and optional Audio 1-3 map to the selected H3 node's canonical inputs.
- Seed candidates are restricted to the selected upstream subgraph and are optional.

There is no LLM fallback. Ambiguity is resolved through constrained, named choices in the UI.

## Output resolution

Comfy MCP runtime artifacts are the authoritative output source. Director Studio does not assume that the last node, a particular class, or a particular history field is the final video.

After a test execution:

- If the selected output node returns one video artifact, it is selected automatically.
- If it returns multiple video artifacts, the UI asks the user to select one and persists its zero-based artifact index.
- If it returns no video artifact, the test fails with the selected node's observed outputs. When MCP reports videos from other nodes, those producing nodes are shown as suggestions, but the user's selection is not changed automatically.
- Activation requires a selected output node and a successful test. An artifact index is also required when the test revealed multiple video outputs.

Runtime production jobs use the persisted output node and optional artifact index to choose among MCP-reported artifacts. A missing mapped artifact fails the job explicitly. The boundary identity used for graph validation excludes only `artifact_index`, because selecting among already observed artifacts does not change the submitted graph; test evidence separately binds the chosen index to the exact workflow, boundary, and test job.

## Storage and resolution

The bundled official workflow remains inside the PyInstaller resource tree. Custom data is stored beside the executable under the existing persistent data root:

```text
Director Studio/
|-- DirectorStudio.exe
`-- data/
    `-- workflow_profiles/
        `-- h3/
            |-- active.json
            |-- imports/
            |   `-- <temporary-import-id>/
            `-- profiles/
                `-- <workflow-id>/
                    |-- workflow.api.json
                    |-- profile.json
                    `-- validation.json
```

`profile` may remain an internal storage and API term for compatibility, but user-facing UI uses **Workflow** and **Custom H3 Workflows**.

Imported files are copied into a generated import directory and identified thereafter only by an opaque import ID. Workflow IDs are normalized server-side. Client requests cannot provide arbitrary filesystem paths.

`active.json` contains only the selected workflow ID and expected workflow hash. The active pointer is written atomically. A custom workflow is resolved only when its schema version, files, and hashes are valid; otherwise the resolver selects the built-in official workflow and surfaces a health warning.

The main runtime interfaces are:

```python
resolve_active_h3_workflow() -> ResolvedH3Workflow

fill_h3_graph(
    workflow: ResolvedH3Workflow,
    params: H3JobParams,
    uploaded_images: list[str],
    uploaded_audios: list[str],
) -> dict[str, Any]
```

The official workflow uses a built-in mapping and the same generic graph filler. With no active custom workflow, generated graphs and current Portable behavior remain equivalent to the existing release.

The release build and distributed zip contain only the official H3 workflow and built-in mapping. Build scripts must never collect persistent imports, custom workflows, active pointers, validation records, test inputs, or generated outputs. A fresh extraction always starts on **Built-in Official H3**.

## Runtime switching and reproducibility

Import, discovery, confirmation, validation, test, activation, and workflow selection all occur while Director Studio is running. Activation does not restart the backend.

Every H3 job snapshots the resolved workflow ID, workflow hash, mapping version, and filled graph when submitted. A workflow switch affects only jobs submitted afterward. Running and queued jobs continue with their captured graph.

```text
Job A submitted with Built-in Official H3
  -> Custom workflow activated
  -> Job A continues with Built-in Official H3
  -> Job B uses the custom workflow
```

Manual edits to an installed workflow invalidate its hash. The user must re-import and revalidate the changed workflow. This prevents partial writes and stale mappings from changing production behavior silently.

## Validation pipeline

Validation has five gates:

1. **Import validation** checks size limits, JSON structure, API-graph shape, generated IDs, and safe storage paths.
2. **Boundary validation** checks the confirmed output, upstream relationship, H3 sockets, Picture and Audio capacity, and optional seed mapping.
3. **Filled-graph validation** injects placeholder job boundaries and verifies graph references without imposing rules on internal topology.
4. **Comfy validation** submits the filled graph to the configured ComfyUI/MCP path. ComfyUI is authoritative for custom nodes, models, widgets, fixed inputs, and graph validity.
5. **Test execution** submits a real 56-frame job and verifies a video artifact from the confirmed output node.

Only a workflow that passes all five gates may be activated. Validation metadata records the workflow hash, confirmed boundary version, ComfyUI endpoint identity when available, result, timestamp, test job ID, and observed output selection. Activation rechecks the hash immediately before updating `active.json`.

The test runner uses the existing exclusive VRAM orchestrator. Since onboarding has no LLM phase, it does not acquire an Ollama lease. No new memory-management mechanism is introduced.

## Backend API

The internal route prefix may retain `workflow-profiles` for compatibility, while UI copy uses **Workflow**.

```text
GET  /api/workflow-profiles/h3
POST /api/workflow-profiles/h3/imports
GET  /api/workflow-profiles/h3/imports/{id}/analysis
PUT  /api/workflow-profiles/h3/imports/{id}/output
PUT  /api/workflow-profiles/h3/imports/{id}/mapping
POST /api/workflow-profiles/h3/imports/{id}/validate
POST /api/workflow-profiles/h3/imports/{id}/test
PUT  /api/workflow-profiles/h3/imports/{id}/test-output
POST /api/workflow-profiles/h3/imports/{id}/activate
POST /api/workflow-profiles/h3/select
```

`analysis` returns named terminal output candidates. After `output` is set, it also returns named upstream H3 and optional seed candidates. `test-output` persists an artifact index only from artifacts actually observed in the latest successful test for the current workflow hash and boundary identity.

There is no mapping-proposal or agent endpoint. The first release does not expose custom-workflow deletion. Selection remains reversible, and a later release may add protected deletion.

## UI

`Settings -> Workflows -> H3` opens **Custom H3 Workflows** and explains that it connects a locally working ComfyUI H3 Ref2AV API workflow to Director Studio.

The screen contains these sections:

1. **Current Workflow** shows the selected workflow, source, health, hash, and a control to restore **Built-in Official H3**.
2. **Import Workflow** accepts ComfyUI API JSON and reports structural issues with named nodes.
3. **Final Video Output** lists terminal output candidates with node title first, class second, and node ID last.
4. **H3 Inputs** lists upstream H3 candidates and the confirmed prompt, dimensions, frame count, Picture, optional Audio, and optional seed mappings.
5. **Validate & Test** shows each gate, selects a test Picture and optional standalone Audio, previews the returned video, and resolves multiple returned artifacts when necessary.
6. **Use Workflow** activates the tested workflow explicitly.

Production displays the resolved local workflow name near the provider selector. A broken custom workflow produces a visible warning and states that the built-in workflow is being used.

## Errors and fallback

Errors retain their stage and structured detail:

- `ImportError`: malformed, oversized, UI-format, or otherwise invalid API graph, with named offending nodes.
- `OutputSelectionError`: no eligible output, an invalid selection, or a selected output that returns no video.
- `InputSelectionError`: no compatible upstream H3 boundary or unresolved multiple candidates.
- `DependencyError`: the connected ComfyUI lacks a required custom node or model.
- `WorkflowValidationError`: ComfyUI rejects the filled graph.
- `TestExecutionError`: the test queues but generation fails.
- `OutputMappingError`: execution succeeds but the selected runtime video artifact is absent or ambiguous.
- `ProfileChangedError`: the workflow hash differs from the validated version.

Import, validation, and test failures never change the current workflow. If an active custom workflow is missing, damaged, or schema-incompatible at startup or submission, the resolver falls back to the built-in official workflow and records a visible warning. A failure after a job has captured a valid custom graph fails that job explicitly instead of silently regenerating with another workflow.

## Security and limits

- Accept JSON only, with conservative byte, node, edge, string, and nesting limits.
- Store imports under generated directories; reject traversal, symlinks, and caller-selected paths.
- Never evaluate node values or execute code during inspection.
- Derive choices only from parsed graph nodes, ComfyUI `object_info`, and MCP runtime artifacts.
- Accept mappings only to nodes, inputs, and artifact fields present in those trusted manifests.
- Require explicit user confirmation and activation after validation and test review.
- Do not expose shell, general filesystem, `.env`, source-tree, or project-data access.
- Do not send workflow content, paths, or mappings to an LLM.

## Testing

Automated coverage must verify:

- The built-in mapping produces a graph equivalent to current official H3 behavior.
- Changed node IDs remain compatible when semantic inputs and output metadata match.
- Internal loaders, LoRAs, sampler chains, decode, upscale, interpolation, audio, and mux settings are preserved.
- A simple `SaveVideo` graph and a multi-stage `VHS_VideoCombine` graph are discovered through graph and `object_info` metadata without a class-name allowlist.
- Multiple terminal outputs require a named user selection.
- Reverse traversal excludes H3 nodes unrelated to the selected output.
- Multiple upstream H3 nodes require a named user selection.
- Ordered Picture 1-9 and optional standalone Audio 1-3 sockets are injected correctly.
- Seed mapping is optional and an unmapped seed remains untouched.
- Reference-video and paired reference-video-audio interfaces are not offered.
- I2V, first-frame, last-frame, malformed, UI-format, oversized, and path-traversal inputs are rejected precisely.
- ComfyUI, not Director Studio topology rules, accepts or rejects internal custom nodes, models, and fixed inputs.
- A selected output with one video is automatic; multiple videos require artifact selection; no video fails without silent substitution.
- MCP validation, execution, artifact reporting, download, and output selection complete end to end without Ollama running.
- A changed hash invalidates prior validation and observed artifact choices.
- A damaged active workflow falls back to official for subsequent jobs.
- A submitted job retains its captured graph across a later workflow switch.
- PyInstaller includes the official workflow while external custom data remains writable and persists across executable upgrades.
- Release artifacts contain no imported workflow, custom active pointer, test input, validation record, or generated output from the build machine.
- The frontend uses node names as primary labels and renders import, selection, validation, activation, fallback, and running-job states.

Regression fixtures should include an API-valid multi-stage graph modeled on the tested YZ/Goldfish workflow: H3 generation, first sampler, latent split, latent upscale, concatenate, second sampler, and VHS video output. The fixture must be sanitized and self-contained; the malformed downloaded file is not shipped.

Before release, a packaged Portable build must import a separately exported compatible H3 workflow and produce a real 56-frame video through the target ComfyUI installation. The test must switch between the custom and built-in workflows without restarting Director Studio.

Artifact inspection must additionally prove that the executable and zip contain the official H3 workflow, exclude every test custom workflow, and start on the built-in workflow after clean extraction.

## Compatibility and migration

This feature has not shipped publicly, so the boundary schema may be bumped without preserving experimental import records created by earlier branch builds. Existing records with the old mandatory seed/saver mapping are either migrated deterministically when their referenced nodes remain valid or rejected with an instruction to re-import. The built-in official workflow is regenerated against the new schema and always remains available.

## Delivery order

1. Schema v2, official mapping, resolver compatibility, and safe storage.
2. Output candidate discovery using graph out-degree and ComfyUI metadata.
3. Output selection and reverse upstream H3/seed discovery.
4. Generic direct H3 input injection with optional seed.
5. MCP artifact-aware test and production output selection.
6. Revised APIs with all agent proposal paths removed.
7. **Custom H3 Workflows** UI and Production workflow indicator.
8. Unit, integration, packaging, and real Portable smoke tests.
