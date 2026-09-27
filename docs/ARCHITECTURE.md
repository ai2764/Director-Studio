# Director Studio architecture

Current implementation snapshot: **2026-09-26**, including managed planning recovery.
This describes shipped code on this branch, not the full proposed agent refactor.
For setup use the [README](../README.md); for runtime configuration use
[Harness](HARNESS.md). Dated audits, plans and test reports are historical evidence,
not an automatically updated list of current capabilities.

## Ownership: creative decisions versus execution guarantees

The LLM plans shots, chooses references and continuity, writes cinematic direction,
and proposes repairs. Python owns project data, validation, submission authority,
job execution and managed-run coordination. Harness owns the conversational loop,
native session history and compaction; it does not write business data directly.

| Layer | Current responsibility | Main source |
|---|---|---|
| UI | Script/chat, shot materials, prompts, jobs and managed-run controls | [frontend features](../frontend/src/features/) |
| Director | Planning, tool dispatch, reference review, normal/tail prompt writing | [Director modules](../backend/app/agents/director/) |
| Conversation runtime | Harness by default in the Windows source launcher; explicit Legacy alternative | [Harness kernel](../harness/src/kernel.ts), [Python boundary](../backend/app/agents/director/harness_runtime.py) |
| Domain state | Projects, shots, dialogue, Layout selection and transitions | [projects](../backend/app/core/projects/) |
| Managed run | Saved plan/steps, continuation, job binding, pause/cancel and guarded prompt commits | [managed runs](../backend/app/core/managed_runs/) |
| Generation | Durable job records and adapter-specific submission/recovery | [job runner](../backend/app/core/jobs/runner.py) |
| Inference/GPU | Provider selection, local model lifecycle and exclusive GPU coordination | [LLM factory](../backend/app/core/llm/factory.py), [VRAM](../backend/app/core/vram/) |

These are distinct lifecycles, not one universal state machine:

- A chat turn can call several tools sequentially. Cancelling chat does not
  automatically cancel an already submitted generation job.
- A managed run advances its own persisted plan and binds jobs to specific steps.
  It is not a durable queue for arbitrary multi-shot chat edits.
- A generation job has its own status and provider identity. Restart/recovery
  behavior belongs to its execution adapter; unknown external effects must not
  be treated as permission to resubmit.

Business authority is still spread across Director service/tool handlers,
project transitions, job synchronization and managed-run code. The broader
state/mutation unification remains planned, not completed.

## Prompt and reference path

A typical path is: read current shot/materials → review evidence → generate a
candidate → validate/repair → save against current state → submit a job.

- Layouts are **optional** composition references, not mandatory opening frames.
  A generated Layout candidate is not active merely because `layout_asset_id`
  exists: active H3 inputs are the shot's bound references.
- Picture numbers follow saved `picture_index`, with unique contiguous indices
  starting at 1 and at most 9 images. Layout is **not forced to Picture 1**.
- Submission requires references and a complete six-section prompt, but no
  separate mandatory “approve shot” gate. Existing review/approval actions remain.
  See [submission checks](../backend/app/core/projects/transitions.py).
- The six sections are `subject_definitions`, `summary`, `retention_analysis`,
  `detailed_description`, `overall_soundscape`, and `non_diegetic_music`.
- Dialogue source/attribution and Picture evidence are validated alongside the
  prompt contract. New writer output uses dialogue placeholders; compilation
  supplies canonical dialogue. Legacy candidates remain readable. When all six
  sections can be read, repair can receive combined dialogue and Picture issues,
  rather than fixing only the first visible defect.
- Managed tail-prompt refinement may update independently reviewed camera fields
  (`shot_type`, `camera_angle`, `camera_motion`, `composition`) through a
  guarded commit. It does not grant permission to silently change story,
  references, dialogue or duration. A same-event refinement receipt permits
  binding the resulting job without treating an unrelated edit as approved.
- Planning conflicts get at most one camera/continuity repair proposal and one
  separate review. False claims can be retracted without rewriting shots. Only
  implicated shots' four camera fields may change; saved revision requests and
  script-current confirmed decisions are included as evidence. Accepted changes
  invalidate old prompts/video bindings and are recorded in the draft's recovery
  history. Live generation or a concurrent edit prevents publication. Explicit
  unresolved requirements still stop the run; there is no keyword-based camera rule.

Implementation: [dialogue preflight](../backend/app/agents/director/dialogue_preflight.py),
[prompt repair](../backend/app/agents/director/prompt_repair.py),
[tail review](../backend/app/agents/director/tail_prompt_review.py),
[managed prompt commit](../backend/app/core/managed_runs/prompt_commit.py).
The [prompt/refinement verification report](superpowers/reports/2026-09-26-prompt-contract-managed-refinement.md)
records test scope and limitations; deterministic checks do not prove generated
video quality.

## Context and history

Harness creates a short-lived runtime handle per request but resumes a stable
native JSONL session. Python owns the UI transcript and domain state; native
Harness history owns the replay/compaction surface. Initial import of Python
history is not repeated on every resumed turn. Manual compaction uses the same
admission guard as chat and does not retry a business action.

Python supplies current instructions, focused project evidence and authoritative
tool schemas. Summary calls omit Director business tools and project payload.
Context estimates account for reserved output and an image allowance, but are
not exact tokenizer guarantees. See [context recovery](HARNESS_CONTEXT_RECOVERY.md)
and the [sidecar implementation](../harness/IMPLEMENTATION.md).

The task-context pilot implements **P0/P1A only**:

- `DS_DIRECTOR_TASK_CONTEXT_MODE=off` and an empty project allowlist are defaults.
- `shadow` measures the alternative view without changing model input.
- `pilot` exposes versioned `overview` / `shot_prompt` views and read-only source
  lookup to the same agent; it does not create another agent or expand authority.
- Missing evidence may return `CONTEXT_REQUIRED`; stale evidence blocks saving.
- P1B–P4 are not implemented. Real-model quality/latency gains are not established.

See the [refactor design](superpowers/specs/2026-09-26-agentic-state-refactor-design.md)
for the target and the [pilot report](superpowers/reports/2026-09-26-task-context-pilot-verification.md)
for what was actually verified.

## Providers and execution adapters

The active Director LLM provider can be `ollama`, `lm-studio`, `llama-swap`,
or `openai-compatible`. The selected model comes from that provider's catalog.
Local lifecycle adapters coordinate release with the GPU owner; a generic
OpenAI-compatible endpoint does not imply support for local model unloading.
Model unloading and history persistence are separate concerns: there is no
requirement to serialize a fresh agent context before every unload.

Generation is not tied to one Comfy node:

- Built-in local Comfy pipelines use the `comfy_mcp` adapter. The runner also
  contains a direct-Comfy adapter.
- H3 resolves a local or official MiniMax API adapter per job.
- External pipelines, such as GPT-backed assets, have a separate execution path
  and do not use the local GPU owner lock.

Registered pipeline modules include `actor`, `scene`, `prop`, `ref_frame`,
`gpt_actor`, `gpt_ref_frame`, and `h3_ref2va`; see the
[registry imports](../backend/app/pipelines/__init__.py). “Props not built” and
the old `first_frame` pipeline path no longer describe this implementation.

For extensions, use [Pipeline / ExternalPipeline](../backend/app/pipelines/base.py)
and the [adapter registry](../backend/app/core/jobs/execution.py):

- A Comfy `Pipeline` builds a workflow prompt and maps history outputs.
- An `ExternalPipeline` implements `run_external` and declares its recovery policy.
- Shared hooks cover submission preparation, output postprocessing, defaults and
  library saving. Register the pipeline, then add its router/UI where needed.
- Workflow-specific frame/size constraints belong to the selected H3 workflow
  contract, not a hardcoded architecture-wide range.

## Persistence and concurrency

Project-owned assets and jobs live beneath their project. Legacy/unassigned
global pools remain readable. The canonical paths are defined in
[paths.py](../backend/app/core/paths.py).

```text
data/
  projects/<project_id>/
    project.json
    shots/<shot_id>.json
    library/<kind>/<asset_id>/
    jobs/<job_id>/job.json + inputs/ + outputs/
    managed_runs/<run_id>.json
    agent/
    json-production/
  library/                         # legacy/unassigned pool
  jobs/                            # legacy/unassigned pool
```

Harness sessions live separately: source development defaults to
`.run/harness-sessions`; Windows portable uses `data/harness-sessions`.
Back them up with project data when preserving conversational continuity, and
do not share one session root between concurrent sidecars.

Locks/admission guards are process-local for the single-backend deployment.
Atomic file replacement is not a cross-file database transaction. In particular,
managed prompt commit writes the shot and run separately, attempts rollback on
run-write failure, and fails closed on a mismatched state after interruption.
Its narrow refinement receipt is not a universal durable operation ledger.
Planning repair uses the same process-local lock for camera writes and draft
publication, rolling back completed shot writes if draft saving fails. Director
context writes share this lock and use atomic replacement. Superseded H3 terminal
events cannot restore obsolete video bindings; this does not add cross-process
locking or multi-file crash atomicity.

## Source map and operational entry points

- [app/config.py](../backend/app/config.py): `DS_*` settings.
- [app/api](../backend/app/api/): HTTP routes; projects, chat, jobs and health.
- [agents/director](../backend/app/agents/director/): LLM-facing orchestration,
  context, source inspection and tool handlers.
- [core](../backend/app/core/): domain models, storage, jobs, providers and GPU.
- [pipelines](../backend/app/pipelines/): workflow/provider capabilities.
- [frontend/src/features](../frontend/src/features/): Director, Production,
  JSON Production, assets, voice and workflow settings.

`GET /api/health` reports `comfy_reachable` and
`details.llm.provider` / `details.llm.reachable`, not the old
`details.ollama_reachable`. Pipeline discovery is `GET /api/pipelines`;
project endpoints are under `/api/projects/*`.

For historical context, the [2026-09-12 Harness audit](HARNESS_CAPABILITY_AUDIT.md)
captures gaps at that date. Consult current runtime docs before treating those
findings as unresolved work.
