# Director Studio Architecture

Extensible layout for pre-production assets, Director Agent, dual human gates, and pure H3 Ref2AV production.

## Goals

- **Add a feature without rewriting the core** — new Comfy workflow → new pipeline module + router + UI feature folder.
- **Assets are first-class** — typed library entries with IDs, not loose images.
- **Jobs are generic** — any pipeline runs through the same upload → queue → poll → save path.
- **Video is pure H3 Ref2AV** — `MiniMaxH3ReferenceToVideo` only; layout first frame is a library ref (Picture 1), never I2V `first_frame`.
- **Exclusive VRAM** — unload Ollama before Comfy image/video jobs; agent context lives on disk across swaps.

## Backend layout

```
backend/app/
  main.py                 # create_app(), mount routers
  config.py               # DS_* settings (Comfy, Ollama, VRAM policy, paths)
  api/                    # cross-cutting HTTP
    health.py             # /api/health (comfy + ollama_reachable)
    files.py
    pipelines.py
    projects.py           # Project/Shot + Director gates + H3 submit
  core/
    comfy/                # ComfyUI HTTP client
    jobs/                 # generic job store + background runner (+ VRAM hook)
    library/              # multi-kind asset library
    projects/             # Project + Shot models, disk store, state transitions
    h3/                   # prompt compose/validate, frame length helpers
    vram/                 # exclusive Ollama ↔ Comfy orchestrator + OllamaClient
    schemas.py            # JobRecord, LibraryAsset, JobStatus, HealthResponse, …
  agents/
    director/             # plan script, queue first frames, write six-section prompt
      service.py          # DirectorService orchestration
      planner.py          # LLM plan parse + ref matching
      prompts.py          # system/user prompt templates
      context_io.py       # persist/reload agent context under data/projects/
  pipelines/
    base.py               # Pipeline ABC
    registry.py           # register / get / list
    actor/                # casting workbench
    scene/                # multi-angle set design
    first_frame/          # layout first-frame image gen (library asset)
    h3_ref2va/            # pure MiniMaxH3ReferenceToVideo production
  workflows/*.api.json    # Comfy API graphs (not UI graphs)
```

## Pipeline contract

Implement `pipelines.base.Pipeline`:

| Method | Role |
|--------|------|
| `id` / `asset_kind` / `display_name` | Identity + library folder |
| `build_prompt(job, uploaded_images)` | Patch workflow JSON → Comfy prompt |
| `map_history_outputs(history)` | SaveImage / SaveVideo nodes → logical keys |
| `output_labels` | UI labels for slots |
| `save_to_library(...)` | Default copies outputs into `data/library/<kind>/` |
| `meta_defaults()` | Form defaults for the UI |

Register in package `__init__.py`:

```python
from ..registry import register_pipeline
register_pipeline(MyPipeline())
```

Wire HTTP in `api/__init__.py` via `include_router`.

## Data on disk

```
data/
  jobs/<job_id>/
    job.json          # generic JobRecord (pipeline_id + params)
    inputs/           # user uploads
    outputs/          # downloaded results by logical key
  library/
    actors/<act_id>/asset.json + images
    scenes/…
    layouts/…         # first_frame pipeline (layout_first_frame role)
    productions/…     # h3_ref2va video outputs
  projects/
    <prj_id>/
      project.json
      shots/<sht_id>.json
      agent/
        context.json  # Director context survives Ollama unload
```

## Projects, shots, and dual gates

Domain lives under `core/projects/`:

| Piece | Role |
|-------|------|
| `models.py` | `Project`, `Shot`, `ShotRef`, `PromptSections`, `ShotStatus`, `RefRole` |
| `store.py` | Disk CRUD under `data/projects/` |
| `transitions.py` | Legal status events + `assert_h3_submittable` |

**Gate 1 — layout first frame:** agent queues `first_frame` job → human approves/rejects layout. On approve, layout becomes `layout_first_frame` at **Picture 1** and status moves toward full-shot review.

**Gate 2 — full shot:** human approves ordered refs + six-section H3 prompt before queue. Submit runs `h3_ref2va` only when layout is approved, Picture 1 is layout, ≤9 image refs, and prompt validates.

Human approve/reject/edit/submit does **not** require a warm LLM (`rewrite_prompt` defaults off).

API: `/api/projects/*` (create, plan, queue first frame, approve/reject layout, approve shot, submit H3).

## Director Agent (`agents/director`)

Local agent (Ollama) that:

1. Plans shots from script text (ref matching against library kinds).
2. Queues layout first-frame jobs (persists context → `release_llm` → Comfy).
3. After layout approve (optional), rewrites six-section prompt with LLM wake + disk context reload.

Does **not** call Comfy for long runs directly; uses job runner + pipelines so artifacts match Casting/Set Design.

## VRAM orchestration (`core/vram`)

| API | Behavior |
|-----|----------|
| `release_llm()` | Unload plan model(s) via Ollama (`keep_alive=0`) |
| `ensure_llm_ready()` | Health + warm model for agent turns |
| `before_comfy_job(pipeline_id)` | Exclusive: unload LLM before Comfy submit |
| `after_comfy_job(...)` | Terminal hook; does not auto-reload LLM |

Job runner calls `before_comfy_job` for **all** Comfy pipelines under exclusive policy. Agent context is serialized under `data/projects/<id>/agent/` before every unload.

## Pipelines (current)

| Pipeline id | Role | Notes |
|-------------|------|--------|
| `actor` | Casting workbench | Auto-route by uploads |
| `scene` | Multi-angle set design | `QwenEdit2511_MultiAngle_SceneRef` |
| `first_frame` | Layout still for a shot | Library asset; **not** I2V socket |
| `h3_ref2va` | Production video | Pure `MiniMaxH3ReferenceToVideo`; max 9 refs; 15 steps / `res_multistep` / `beta` |

H3 prompt six-section order: `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`. Frame lengths: `n % 17 == 5`, range 124–362.

## Adding the next feature (e.g. Costume)

1. Drop `backend/workflows/costume_….api.json`.
2. Create `pipelines/costume/` (pipeline + workflow + schemas + router).
3. `register_pipeline(CostumePipeline())`.
4. `api.include_router(costume_router)`.
5. Frontend: `features/costume/` + nav entry in `app/navigation.ts`.

No changes to Comfy client or job runner required if the new pipeline only needs image uploads + one workflow. VRAM hook applies automatically.

## Frontend layout

```
frontend/src/
  app/App.tsx             # shell, nav, health
  app/navigation.ts       # routes
  shared/                 # reusable UI + HTTP helpers
  features/
    casting/              # actor pipeline UI
    set/                  # scene multi-angle
    library/              # asset library views
    director/             # script → plan → first-frame gate
    production/           # shot list, refs/prompt, H3 submit / rerun
```

## Scene multi-angle (Set Design)

Workflow: `QwenEdit2511_MultiAngle_SceneRef.api.json`

| Input | Role |
|-------|------|
| Scene reference image | Plate for multi-angle LoRA |
| Angle list (one line each) | Fed to CR Prompt List → batch list |
| Prepend / append | Optional style text on every angle |

One Comfy prompt runs **all angle lines** (`OUTPUT_IS_LIST`). Outputs: `angle_00`… in `data/library/scenes/`.

API: `/api/scenes/*` · pipeline id `scene`.

## Actor casting routing (workbench)

No Comfy toggles from the API. Blank 1×1 placeholders + size checks auto-route:

| Upload | Behavior |
|--------|----------|
| No actor ref | Text-to-actor |
| Headshot | Face + visible hair; body from optional body description |
| Full-body | Face + body proportions |
| No wardrobe | Keep master wardrobe |
| Wardrobe / model | Auto extract + transfer |

Graph order: **master → full-body three-view → bust three-view** (bust from sheet crop for hair consistency).

## Roadmap

| Area | Extension point | Status |
|------|-----------------|--------|
| Set (scene multi-angle) | `pipelines/scene` | **implemented** |
| Costume / Props | New `pipelines/*` | not built |
| Director Agent | `agents/director` + projects API + Director UI | **implemented** |
| H3 Ref2AV + first frame | `pipelines/h3_ref2va`, `pipelines/first_frame` | **implemented** |
| VRAM exclusive swap | `core/vram` + job runner hook | **implemented** |
| Project / Shot dual gates | `core/projects` + Production UI | **implemented** |
| Production review | `features/production` | **implemented** |

## API stability

Actor HTTP API remains under `/api/actors/*` with the same response shapes as v0.1.  
Internally jobs are generic (`pipeline_id`, `params`, `outputs` map).

Projects: `/api/projects/*` · pipelines list: `GET /api/pipelines` · health: `GET /api/health` (`details.ollama_reachable`).
