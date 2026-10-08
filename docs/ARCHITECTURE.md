# Director Studio architecture

Current implementation snapshot: **2026-09-27**, including execution-only managed planning.
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

- Storyboard recovery cannot call `set_script` after a failed submission in the
  same chat turn. This is a temporary tool boundary, not `Project.script_locked`:
  a fresh user turn restores ordinary authoring, and direct user edits remain
  available. It prevents rewriting the acceptance source to evade a rejection;
  it is not a general natural-language authorization classifier.
- Storyboard review separates blocking requirement/causality conflicts (`issues`)
  from advisory generation uncertainty (`warnings`). Difficulty, action density
  and uncertain motion fidelity do not by themselves invalidate a candidate.
  Warnings are retained with the saved review and tool result, and are included
  in replacement proposals before confirmation. Semantic classification remains
  the model's responsibility; there is no action-count or keyword rule.
- Each image observation permits one content reinspection and at most two
  structural/source repairs, with four total inference calls maximum. An identical
  candidate with identical issues stops early; different invalid citations do not
  collapse into one generic no-progress error. Source errors identify every failed
  field, attribute, source ID and quote. Exact original-language source validation
  remains mandatory. Failed attempts are diagnostic-only records under
  `agent/reference_review_failures/`, never accepted facts or observation cache.
  Cached successful Pictures survive a later failure.
- Missing assets, exact files, unreadable image bytes and invalid Picture packs
  produce `MATERIAL_INPUT_INVALID`; failed visual evidence review produces
  `MATERIAL_REVIEW_INVALID`. Both are upstream preflight failures, not creative
  prompt repair requests. Chat preserves that distinction and managed runs pause
  without spending a prompt retry or abandoning a continuity handoff. A new user
  turn can still edit/relink materials; the backend never chooses a replacement.
- Library deletion marks every dependent project's shots material-review-pending
  and records `material_changes.deleted_assets`. Ordinary ref IDs and authored
  prompts are retained to preserve the intended binding; successful re-review
  clears the derived marker. Existing Layout deletion still detaches its special
  Layout/Picture bindings. The delete response lists affected project/shot IDs.

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
- Dialogue source/attribution and missing language metadata are prepared before
  creative prompt generation. Source-backed inference fills only missing language
  labels, retaining the original attribution and exact words. Derived overlays
  keep their original unresolved lines, cited evidence, ordered prior requests,
  and a signature covering all metadata inputs; they are not authored revisions.
  Ambiguity returns a concrete clarification question. Metadata errors never
  enter prompt-only repair, including managed tail preparation. Later user edits
  invalidate derived evidence without locking the shot or imposing language rules.
- Picture evidence and speech bindings are validated alongside the prompt
  contract. New writer output uses dialogue placeholders; compilation
  supplies canonical dialogue. Legacy candidates remain readable. When all six
  sections can be read, repair can receive combined dialogue and Picture issues,
  rather than fixing only the first visible defect.
- Normal prompt material review checks reference suitability for the saved shot;
  it is not a second storyboard approval or a shot-authoring transaction. The
  reviewer and writer receive the same shot-local authoring request (verified
  against its user-message source), historical directing requests and current
  prompt request. The saved shot remains the execution target; the screenplay
  supplies background, not an automatic rollback of an appended/revised shot.
  Unsolicited replacement briefs have no write authority. Genuine reference
  conflicts and stale-input checks still block publication. Reference inspection,
  suitability inference and normal prompt writing/repair report phase starts and
  completion/failure durations through the existing chat progress stream.
- Managed tail-prompt refinement may update independently reviewed camera fields
  (`shot_type`, `camera_angle`, `camera_motion`, `composition`) through a
  guarded commit. It does not grant permission to silently change story,
  references, dialogue or duration. A same-event refinement receipt permits
  binding the resulting job without treating an unrelated edit as approved.
- Managed planning accepts the saved storyboard as the current authored edit.
  One inference chooses optional tail handoffs; it does not reapprove story,
  character coverage, dialogue or total film runtime, and cannot rewrite shots.
  Unsolicited creative judgments/patches in the response have no authority.
  Local H3 per-shot duration limits, known IDs, unique targets, earlier-source
  ordering and a current input snapshot remain mandatory. The backend builds the
  complete ordered shot list and publishes only a draft, under the project lock.
  Script and directing requests inform continuity choices, not a second creative
  acceptance gate. Actual generated tails are still reviewed before use, and
  existing prompt/submission checks and storyboard-authoring validation remain.
  Historical planning-refinement receipts remain readable; new planning no longer
  produces them. This does not add a new approval step or a general audit workflow.

Implementation: [dialogue preflight](../backend/app/agents/director/dialogue_preflight.py),
[prompt repair](../backend/app/agents/director/prompt_repair.py),
[tail review](../backend/app/agents/director/tail_prompt_review.py),
[managed prompt commit](../backend/app/core/managed_runs/prompt_commit.py).
Deterministic checks do not prove generated video quality.

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
for the target architecture; the implemented scope is described above.

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
Managed planning checks its input snapshot and publishes the draft under the same
process-local project lock, without writing shots or the project. Director
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
