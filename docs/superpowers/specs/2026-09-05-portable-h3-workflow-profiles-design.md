# Portable H3 Workflow Profiles

## Summary

Director Studio Portable will let users import and activate a custom ComfyUI MiniMax H3 Ref2AV API workflow while the service is running. Users will not edit source code, Python adapters, or mapping files. A constrained local setup agent may explain and rank ambiguous mappings, but deterministic code owns parsing, validation, activation, and rollback.

The first release supports only workflows whose semantic boundary remains MiniMax H3 Ref2AV. Internal model loaders, LoRAs, samplers, schedulers, decode, upscale, audio, and mux chains may differ. Other video models, I2V workflows, and first/last-frame workflows are outside this interface.

## Goals

- Import a ComfyUI API-format H3 workflow from the running Portable UI.
- Discover or confirm the inputs Director Studio must inject for every job.
- Preserve all compatible workflow-internal quality settings.
- Validate the graph before it can become active.
- Test the workflow through the existing Comfy MCP execution path.
- Activate or roll back profiles without restarting Director Studio.
- Keep the bundled official H3 workflow immutable and always available.
- Give the local Ollama agent no general filesystem or source-editing access.

## Non-goals

- Supporting arbitrary ComfyUI video workflows under the H3 pipeline.
- Converting I2V, first-frame, last-frame, FLF, or other model semantics into Ref2AV.
- Letting the agent rewrite internal graph topology or tune quality settings.
- Automatically installing models or ComfyUI custom nodes.
- Replacing Actor, Scene, Prop, or Layout workflows in the first release.
- Supporting direct hot reload when a user edits an active JSON file manually.

## User workflow

The user first starts ComfyUI, Ollama, and `DirectorStudio.exe`. Comfy MCP continues to be launched by Director Studio from `.env` configuration.

1. Build and successfully run the custom H3 Ref2AV graph in ComfyUI.
2. Export it in ComfyUI API format.
3. Open `Settings -> Workflows -> H3` in Director Studio.
4. Import the API JSON.
5. Review deterministic analysis and any mapping recommendation from the local agent.
6. Resolve ambiguous seed or saver candidates when requested.
7. Validate the filled graph through Comfy MCP.
8. Select a Picture from the Asset Library and run a short 56-frame test. Reference Audio is optional.
9. Inspect the returned video and activate the profile.
10. Switch back to `Built-in Official H3` at any time.

An automatically compatible workflow should require no node-ID editing. A workflow with ambiguous but valid candidates presents structured choices. An unsupported workflow explains which contract requirement is missing.

## H3 boundary contract

The contract describes application-level semantics, not the complete ComfyUI graph.

```python
@dataclass(frozen=True)
class H3BoundaryMapping:
    h3_node_id: str
    prompt_input: str
    width_input: str
    height_input: str
    frames_input: str
    picture_input_pattern: str
    audio_input_pattern: str | None
    seed_node_id: str
    seed_input: str
    saver_node_id: str
    output_prefix_input: str
    output_fields: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedH3Profile:
    profile_id: str
    workflow: dict[str, Any]
    mapping: H3BoundaryMapping
    workflow_sha256: str
    source: Literal["builtin", "custom"]
```

A compatible profile must satisfy all of the following:

- It contains exactly one `MiniMaxH3ReferenceToVideo` boundary node.
- The boundary exposes prompt, width, height, frame length, and dynamic Picture inputs.
- Dynamic Picture inputs support Director Studio's ordered Picture 1 through Picture 9 semantics.
- Reference Audio is either absent or supports ordered Audio 1 through Audio 3 semantics.
- A seed input can be identified on the final generation path.
- A final reachable video saver and its Comfy history media field can be identified.
- The graph contains no `MiniMaxH3ImageToVideo`, `ref_frame`, or `last_frame` boundary.
- Every remaining reachable file input is either mapped or explicitly reported as a fixed workflow dependency.

The runtime injects only prompt, dimensions, frame length, uploaded Picture and Audio references, seed, and output prefix. It does not overwrite model, LoRA, sampler, scheduler, steps, denoise, guider, decoder, FPS, format, codec, upscale, or mux settings.

The serialized profile contains a schema version, workflow hash, mapping, compatibility result, and validation metadata. Node IDs are stored as strings. Input names and output fields must match the imported API graph exactly.

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
                `-- <profile-id>/
                    |-- workflow.api.json
                    |-- profile.json
                    `-- validation.json
```

Imported files are copied into a generated import directory and identified thereafter only by an opaque import ID. Profile IDs are normalized server-side. Client and agent requests cannot provide arbitrary filesystem paths.

`active.json` contains only the selected profile ID and expected workflow hash. The active pointer is written atomically. A custom profile is resolved only when its schema version, files, and hashes are valid; otherwise the resolver selects the built-in official profile and surfaces a health warning.

The main runtime interfaces are:

```python
resolve_active_h3_profile() -> ResolvedH3Profile

fill_h3_graph(
    profile: ResolvedH3Profile,
    params: H3JobParams,
    uploaded_images: list[str],
    uploaded_audios: list[str],
) -> dict[str, Any]
```

The official workflow receives a built-in profile and uses the same generic graph filler as custom profiles. With no custom active profile, generated graphs and current Portable behavior remain equivalent to the existing release.

The release build and distributed zip contain only the official H3 workflow and its built-in profile. Build scripts must never collect `data/workflow_profiles`, imports, active pointers, validation records, or any locally installed custom workflow. A fresh extraction therefore always starts on `Built-in Official H3`. A user retains custom profiles only by retaining or migrating that installation's external `data` directory.

## Runtime switching and job reproducibility

Import, analysis, validation, test, activation, and profile selection all occur while Director Studio is running. Activation does not restart the backend.

Every H3 job snapshots the resolved profile ID, workflow hash, mapping version, and filled graph when the job is submitted. A profile activation affects only jobs submitted afterward. Running and already queued jobs continue with their captured graph. A profile is never swapped during execution.

```text
Job A submitted with Official H3
  -> Custom H3 activated
  -> Job A continues with Official H3
  -> Job B uses Custom H3
```

Manual edits to an installed workflow invalidate its hash and do not hot reload. The user must re-import and revalidate the changed workflow. This prevents partial file writes and stale mappings from silently changing production behavior.

## Inspection and agent responsibilities

Deterministic inspection runs before any LLM request. It parses the API JSON and creates a compact manifest containing node ID, class type, title, input names, output types, graph reachability, and relevant non-sensitive defaults. It does not send uploaded media, binary data, absolute user paths, secrets, or the complete UI workflow state to Ollama.

Deterministic rules first locate the unique H3 node and exact required input names, then trace the active graph to seed and final saver candidates. Candidate results have one of three states:

- `auto_compatible`: every required boundary has one valid candidate.
- `needs_confirmation`: the graph is compatible but a boundary has multiple candidates.
- `unsupported`: a required Ref2AV semantic boundary is missing or forbidden semantics are present.

The Ollama setup agent may explain candidates, rank ambiguous mappings, and produce a mapping proposal using IDs present in the manifest. It cannot create arbitrary values, alter graph topology, activate a profile, or bypass validation. Its proposal is parsed into a strict schema and rechecked against the original graph.

The setup agent is separate from the Director production conversation so that workflow configuration does not consume or contaminate story and shot-planning context.

The setup agent receives only these bounded operations:

- `inspect_h3_workflow(import_id)`
- `propose_h3_mapping(import_id)`
- `validate_h3_profile(import_id)`
- `test_h3_profile(import_id, test_inputs)`
- `activate_h3_profile(import_id)`
- `list_h3_profiles()`
- `select_h3_profile(profile_id)`

Activation remains an explicit user action in the Workflow Setup UI. Generic file read, file write, shell, `.env`, source-tree, and project-data tools are not exposed.

## Validation pipeline

Validation has five gates:

1. **Import validation** checks size limits, JSON structure, API-format shape, generated IDs, and safe storage paths.
2. **Contract validation** checks exact node classes, input names, dynamic socket patterns, graph reachability, forbidden semantics, and output mapping.
3. **Filled-graph validation** injects placeholder job boundaries and rechecks all references without a GPU run.
4. **Comfy validation** sends the filled graph through the configured Comfy MCP validation operation so missing nodes, invalid widgets, and available model choices can be reported.
5. **Test execution** submits a 56-frame real job through the existing job system and verifies that the mapped video output is downloaded.

Only a profile that passes all five gates may be activated. Validation metadata records the workflow hash, ComfyUI endpoint identity when available, result, timestamp, and test job ID. Activation rechecks the hash immediately before updating `active.json`.

The test runner uses the existing exclusive VRAM orchestrator. Ollama can analyze the workflow first; before Comfy execution, the normal Comfy job path releases Ollama and acquires the GPU lease. No second memory-management mechanism is introduced.

## Backend API

```text
GET  /api/workflow-profiles/h3
POST /api/workflow-profiles/h3/imports
GET  /api/workflow-profiles/h3/imports/{id}/analysis
POST /api/workflow-profiles/h3/imports/{id}/propose-mapping
PUT  /api/workflow-profiles/h3/imports/{id}/mapping
POST /api/workflow-profiles/h3/imports/{id}/validate
POST /api/workflow-profiles/h3/imports/{id}/test
POST /api/workflow-profiles/h3/imports/{id}/activate
POST /api/workflow-profiles/h3/select
```

The first release does not expose profile deletion. Inactive profiles may remain installed, and selection is reversible. A later version may add deletion with separate confirmation and protection for the active profile.

## UI

`Settings -> Workflows -> H3` contains four sections:

1. **Active Profile** shows the selected profile, source, workflow hash, validation time, health, and a control to restore the built-in official profile.
2. **Workflow Analysis** shows the H3 boundary, Picture and Audio support, seed, saver, required node/model summary, and compatibility state.
3. **Boundary Mapping** shows each Director Studio value beside its mapped node and input. Unique values are fixed; ambiguous values are chosen from constrained candidate lists with an agent explanation.
4. **Validation & Test** shows each validation gate, selects a test Picture and optional Audio from the library, previews the returned test video, and enables activation only after success.

Production displays the resolved local workflow name near the provider selector. A broken custom profile produces a visible warning and shows that the built-in profile is being used.

## Errors and fallback

Errors retain their stage and structured detail:

- `ImportError`: malformed, oversized, or non-API workflow.
- `ContractError`: missing or invalid Director Studio boundary.
- `DependencyError`: ComfyUI custom node or model is unavailable.
- `WorkflowValidationError`: the filled graph is rejected.
- `TestExecutionError`: the test queues but generation fails.
- `OutputMappingError`: execution succeeds but the mapped final video is absent.
- `ProfileChangedError`: a file hash differs from the validated version.

Import and test failures never change the active profile. If an active custom profile is missing, damaged, or schema-incompatible at startup or submission time, the resolver falls back to the built-in official profile and records a visible warning. A failure after a job has captured a valid profile fails that job explicitly rather than silently regenerating it with a different workflow.

## Security and limits

- Accept JSON only, with conservative byte, node, edge, string, and nesting limits.
- Store imports under generated directories; reject traversal, symlinks, and caller-selected paths.
- Never evaluate node values or execute code during inspection.
- Redact absolute paths and values whose keys indicate secrets before building the Ollama manifest.
- Permit proposed mappings only to nodes and input names already in the parsed manifest.
- Require explicit user activation after validation and test output review.
- Do not grant the setup agent shell or general filesystem access.

## Testing

Automated coverage must verify:

- The built-in profile produces a graph equivalent to current official H3 behavior.
- Changed node IDs remain compatible when semantic inputs and output types match.
- Internal model, LoRA, sampler, scheduler, decode, and mux changes are preserved.
- Multiple seed or saver candidates require confirmation.
- Ordered Picture 1-9 and optional Audio 1-3 sockets are injected correctly.
- I2V, first-frame, last-frame, malformed, oversized, and path-traversal inputs are rejected.
- Reachable fixed local inputs are reported rather than silently accepted.
- A changed workflow hash invalidates prior validation.
- A damaged active profile falls back to the official profile for subsequent jobs.
- A submitted job retains its captured graph across a later profile switch.
- Mock Comfy MCP validation, execution, output download, and output mapping complete end to end.
- PyInstaller includes the official profile while external profiles remain writable and persist across executable upgrades.
- Release artifacts contain no custom workflow, active custom-profile pointer, test input, validation record, or generated output from the build machine.
- The frontend renders all compatibility, validation, activation, fallback, and running-job states.

Before release, a packaged Portable build must import a separately exported compatible H3 workflow and produce a real 56-frame video through the target ComfyUI installation. The test must also switch back to the official profile without restarting Director Studio.

Artifact inspection must additionally prove that the packaged executable and zip contain the official H3 workflow, do not contain the test custom workflow, and start with the official profile when extracted into a clean directory.

## Delivery order

1. Profile schemas, safe storage, built-in profile, and resolver.
2. Generic graph filler and current-official equivalence tests.
3. Deterministic workflow inspector and contract validator.
4. Import, mapping, validation, activation, and selection APIs.
5. Setup-agent tools and bounded Ollama context.
6. Workflow Setup UI and Production profile indicator.
7. Mock MCP integration, packaging tests, and real Portable smoke test.
