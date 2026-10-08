# Director Studio architecture

Director Studio connects a conversational Director to project storage and
image/video generation services. For installation and everyday use, start with
the [README](../README.md). For runtime settings and troubleshooting, see
[Harness](HARNESS.md) and [context recovery](HARNESS_CONTEXT_RECOVERY.md).

## Responsibilities

| Layer | Responsibility | Source |
| --- | --- | --- |
| UI | Assets, Director chat, shots, prompts and generation jobs | [Frontend features](../frontend/src/features/) |
| Director | Shot planning, reference review, prompt writing and tool calls | [Director modules](../backend/app/agents/director/) |
| Conversation runtime | Model/tool sequencing, native history and compaction | [Harness kernel](../harness/src/kernel.ts), [Python boundary](../backend/app/agents/director/harness_runtime.py) |
| Project storage | Projects, shots, dialogue, reference bindings and transitions | [Projects](../backend/app/core/projects/) |
| Managed runs | Saved plans, job binding, pause/cancel and guarded prompt updates | [Managed runs](../backend/app/core/managed_runs/) |
| Generation | Durable job records, submission and adapter-specific recovery | [Job runner](../backend/app/core/jobs/runner.py) |
| Providers | LLM selection, local model lifecycle and GPU coordination | [LLM factory](../backend/app/core/llm/factory.py), [VRAM](../backend/app/core/vram/) |

The LLM makes creative proposals. Python validates and persists business changes;
Harness cannot write project data directly. Chat, managed runs and generation
jobs have separate lifecycles. Cancelling chat does not cancel an already
submitted generation job or undo completed edits. Unknown submission outcomes
must be inspected before requesting another attempt.

## Prompt and reference path

The Director reads a shot and its materials, reviews reference evidence, writes
a candidate prompt, validates it against current state, then saves and submits.

- Layouts are optional composition references. Generating a Layout does not
  automatically bind it to H3; the shot's selected references determine inputs.
- Pictures use saved, unique contiguous indices starting at 1, with up to nine
  images. A Layout is not required to be Picture 1.
- A prompt has six sections: `subject_definitions`, `summary`,
  `retention_analysis`, `detailed_description`, `overall_soundscape`, and
  `non_diegetic_music`. Canonical dialogue is compiled from saved shot dialogue.
- Missing files, invalid reference packs and failed visual review block prompt
  publication. Concurrent edits or changed input bytes require fresh review.
- Configuration conflicts are returned to the Director for bounded repair;
  reviewers cannot silently change authored duration or voice bindings. See
  [authoring error recovery](director-configuration-recovery.md).
- Submission needs valid materials and a complete prompt. There is no separate
  mandatory shot-approval step. Deterministic validation does not certify the
  visual or audio quality of a generated video.

Implementation: [dialogue preflight](../backend/app/agents/director/dialogue_preflight.py),
[prompt repair](../backend/app/agents/director/prompt_repair.py),
[submission checks](../backend/app/core/projects/transitions.py).

## Generation adapters

Director LLM providers include Ollama, LM Studio, llama-swap and
OpenAI-compatible endpoints. The selected model comes from the provider's
catalog. Generic remote endpoints do not imply support for local model unloading.

Local Comfy pipelines use the `comfy_mcp` adapter; the runner also supports a
direct-Comfy adapter. H3 resolves a local or MiniMax API adapter per job.
External image-generation pipelines have a separate execution path.

Registered pipelines include `actor`, `scene`, `prop`, `ref_frame`,
`qwen21_layout`, `gpt_actor`, `gpt_ref_frame`, and `h3_ref2va`; see the
[registry imports](../backend/app/pipelines/__init__.py). A Comfy
[Pipeline](../backend/app/pipelines/base.py) builds a workflow and maps outputs;
an `ExternalPipeline` implements external execution and its recovery policy.
Workflow-specific size, frame and continuation constraints belong to the
selected H3 profile. See [custom video input](custom-h3-video-input.md).

## Persistence and concurrency

Project data is stored under the application data directory:

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
  library/                         # legacy/unassigned assets
  jobs/                            # legacy/unassigned jobs
```

Canonical locations are defined in [paths.py](../backend/app/core/paths.py).
Harness history is stored separately: `.run/harness-sessions` for source
launches, `data/harness-sessions` for Windows portable. Back it up along with
project data when preserving conversations. Concurrent sidecars must use
separate session roots.

Locks and chat admission guards are process-local, designed for one backend per
data directory. Atomic file replacement is not a transaction across multiple
files. Prompt and managed-run commits check current input snapshots; mismatched
or stale state blocks publication. Run separate backends against separate data
and ports.

## Operational entry points

- [Configuration](../backend/app/config.py): `DS_*` environment settings.
- [HTTP routes](../backend/app/api/): projects, chat, jobs and health.
- `GET /api/health`: `comfy_reachable` and
  `details.llm.provider` / `details.llm.reachable`.
- `GET /api/pipelines`: pipeline discovery.
- `/api/projects/*`: project endpoints.
