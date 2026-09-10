# Director Studio

Local-first pre-production workspace for planning shots, managing reusable visual and voice assets, writing MiniMax H3 Ref2AV prompts, and generating media through ComfyUI.

Director Studio runs the planning Agent through one configured Ollama, LM Studio, or OpenAI-compatible provider. Image and local video workflows run in ComfyUI through ComfyUI MCP; H3 video can alternatively be submitted to the official MiniMax API.

Core features include a typed asset library, actor and set workflows, conversational shot planning, editable Picture and Audio references, optional Layout studies, six-section H3 prompts, local/cloud video submission, durable jobs, and exclusive local-LLM/ComfyUI VRAM coordination.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the source layout and extension points.

## Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI · pluggable pipelines · ComfyUI MCP · provider-neutral Director LLM |
| Frontend | Vite + React · feature folders |
| Execution | ComfyUI through MCP (actor / scene / prop / Layout / local H3) · MiniMax H3 official API |
| Planning LLM | Ollama · LM Studio · OpenAI-compatible Chat Completions |

## Windows portable installation

The portable package runs Director Studio locally as one `DirectorStudio.exe`. The UI and backend are included; the selected LLM server and ComfyUI remain external services. The included installer creates a private Python environment for `comfy-cli` and `comfy-mcp`.

### 1. Install local prerequisites

- Windows 10 22H2 or newer, x64.
- One Director LLM provider. Ollama remains the default. For [Ollama for Windows](https://ollama.com/download/windows), install any compatible local model, for example:

  ```powershell
  ollama pull <model-name>
  ```

  LM Studio and other OpenAI-compatible servers are configured below instead. Director reads the active provider's model catalog; choose the model in the Director dropdown.

- [ComfyUI Desktop for Windows](https://docs.comfy.org/installation/desktop/windows), running at `http://127.0.0.1:8188`.
- Python 3.10 or newer for the external Comfy command-line tools. Director Studio itself does not require a separate Python installation.

Extract the complete zip to a writable folder such as `C:\DirectorStudio`; do not copy only the executable. The release archive intentionally contains no user data. Director Studio creates `data` beside the executable on first launch; after that, keep it with the other extracted files and back it up before upgrades.

```powershell
Set-Location C:\DirectorStudio
.\Install-Tools.cmd
```

The installer creates `tools\venv`, installs the pinned `comfy-cli` and `comfy-mcp` dependencies, verifies their command entry points, and updates the included `.env` with their absolute executable paths. Users who already have a compatible MCP installation may skip this installer and configure those paths manually. Internet access to PyPI is required during installation.

### 2. Configure Director Studio

```powershell
Set-Location C:\DirectorStudio
notepad .env
```

At minimum, confirm the local service URLs. When run, the installer writes its absolute MCP command paths automatically and replaces any prior values for those two path settings. Choose exactly one LLM configuration:

```dotenv
DS_COMFY_BASE_URL=http://127.0.0.1:8188

# Ollama (default)
DS_LLM_PROVIDER=ollama
DS_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

For LM Studio, enable its local API server first. The model itself is selected from the Director dropdown, so it does not need to be named in `.env`:

```dotenv
DS_LLM_PROVIDER=lm-studio
DS_LLM_BASE_URL=http://127.0.0.1:1234/v1
# DS_LLM_API_KEY=lm-studio
```

For OpenAI or another service exposing the common OpenAI Chat Completions and Models endpoints (including llama.cpp, vLLM, LiteLLM, OpenRouter, and DeepSeek-compatible gateways):

```dotenv
DS_LLM_PROVIDER=openai-compatible
DS_LLM_BASE_URL=https://api.openai.com/v1
DS_LLM_API_KEY=replace-with-a-real-secret
```

An API key is optional for unauthenticated local servers. LM Studio model instances are unloaded before local ComfyUI generation and are loaded again by LM Studio on the next Director request. Remote providers do not participate in local GPU ownership.

To use the official MiniMax API instead of local H3 generation, add your key and select the provider:

```dotenv
DS_H3_PROVIDER=minimax
DS_H3_MINIMAX_API_KEY=your-secret-key
```

Director Studio coordinates local generation with Ollama or LM Studio through its built-in exclusive GPU lock. VRAM policy, queue timeout, and LLM residency use internal defaults and require no user configuration.

Do not publish `.env`; it may contain provider credentials. Projects and generated application state are stored in the adjacent `data` folder. Back up that folder before replacing or upgrading the package.

### 3. Start

Start the configured LLM server and ComfyUI first, then run:

```powershell
.\DirectorStudio.exe
```

- UI: http://127.0.0.1:8790
- API documentation: http://127.0.0.1:8790/docs
- Health check: http://127.0.0.1:8790/api/health

If the MCP process cannot start, verify both configured executable paths. You can run `comfy --help` to check the Comfy CLI; do not use `comfy-mcp --help`, because that entry point starts the stdio server. If a workflow fails, load the same workflow in ComfyUI and confirm its custom nodes and models are installed.

### Connect a custom H3 workflow

Every clean Portable starts with **Built-in Official H3**. First make sure your custom H3 Ref2AV workflow already runs successfully in the same local ComfyUI. Then connect it at runtime:

```text
Settings -> Workflows -> H3 -> Import Workflow
-> Final Video Output -> H3 Inputs -> Validate & Test -> Use Workflow
```

Director Studio treats everything inside the selected path as an opaque ComfyUI graph. It first lists terminal video nodes; after you choose the final output, it searches backward and asks you to confirm the upstream `MiniMaxH3ReferenceToVideo` node and optional seed node. Node titles are shown before class names and IDs. The application only injects prompt, width, height, frame count, Picture 1–9, optional standalone Audio 1–3, and an optional seed. Internal models, samplers, LoRAs, upscalers, frame interpolation, and muxing stay exactly as the workflow defines them.

The 56-frame test retains videos only from the final output node you selected. If that node emits several videos, preview them and choose one; this selection does not rerun ComfyUI. Reference-video inputs are not supported. Ollama is not used for importing, mapping, validating, or testing a custom workflow—the setup is deterministic and uses ComfyUI metadata plus your confirmations.

Imported workflow JSON and its setup metadata stay under the external `data/workflow_profiles` directory and are never embedded in a release executable or zip. Any custom nodes, models, LoRAs, and other dependencies referenced by an imported graph remain the user's ComfyUI responsibility. If a custom workflow becomes unavailable or invalid, Director Studio falls back to **Built-in Official H3**.

Workflow changes apply only to jobs submitted after the switch. Queued and running jobs keep the immutable workflow snapshot captured when they were submitted. You can switch back to **Built-in Official H3** without restarting, and doing so does not alter work already in flight.

## How the local components fit together

```text
Browser UI
    ↕
Director Studio (React + FastAPI)
    ├─ Director Agent ↔ active LLM provider
    ├─ Jobs → ComfyUI MCP → ComfyUI
    ├─ Optional H3 jobs → MiniMax Official API
    └─ Projects, assets, prompts, and outputs → local data/
```

Director Studio owns project state, workflow adapters, the job queue, and GPU coordination. ComfyUI MCP is the workflow transport layer: it submits completed API-format graphs to ComfyUI, monitors execution, and retrieves outputs. The LLM provider and ComfyUI remain separate services.

## Bundled workflows

Director Studio currently uses five ComfyUI workflow graphs:

| Purpose | Workflow file | Notes |
|---|---|---|
| Actor assets | `qwen_actor_asset_workbench.api.json` | Character master and three-view outputs |
| Scene assets | `QwenEdit2511_MultiAngle_SceneRef.api.json` | Multi-angle scene generation |
| Prop assets | `qwen_prop_master.api.json` | Prop master generation |
| Layout reference | `ref_frame_layout.api.json` | Optional shot-composition Picture reference |
| Local H3 video | `h3_ref2va.api.json` | API branch of the official Comfy-Org H3 Ref2AV template |

The workflow JSON files are bundled with the application, but their model files and custom-node dependencies must also be available in the user's ComfyUI installation.

## Custom ComfyUI workflows

**MCP configuration alone is sufficient only when changing how Director Studio connects to or starts the same compatible ComfyUI/MCP service.** It does not describe the workflow graph and cannot adapt changed node IDs, inputs, or outputs.

Bundled pipelines load API-format workflow JSON from `backend/workflows/`, patch specific input and control nodes in `backend/app/pipelines/<pipeline>/workflow.py`, submit through MCP, and map known output nodes back into Director Studio. Because the current portable build is a one-file executable, bundled workflow files are read-only package resources and cannot be overridden beside `DirectorStudio.exe`.

For a local H3 Ref2AV replacement in either Source or Portable, use **Settings -> Workflows -> H3** and complete Final Video Output -> H3 Inputs -> Validate & Test -> Use Workflow. Director Studio discovers the supported boundary instead of requiring official node IDs. The custom graph must contain an upstream `MiniMaxH3ReferenceToVideo` node and a terminal node that produces the final video.

For a graph replacement in another pipeline that preserves the pipeline's exact node-ID/input/output contract:

1. Export the workflow in the API format expected by the existing pipeline.
2. Replace the matching JSON file under `backend/workflows/`.
3. Run that pipeline's tests and rebuild the portable package.

For a new or incompatible graph:

1. Add its API-format JSON under `backend/workflows/`.
2. Add or update the adapter under `backend/app/pipelines/<pipeline>/`: schemas/router, prompt and node injection, job submission, and output mapping.
3. Register the pipeline from its package `__init__.py` using `register_pipeline(...)`, and ensure the package is imported by application startup.
4. Add tests for graph validation, prompt injection, and output mapping.
5. Rebuild with `pwsh -File scripts/build-legacy-portable.ps1`.

Non-H3 custom-workflow overrides remain source-only in the current release. Portable supports H3 Ref2AV workflow import through Settings, while the packaged official workflow remains a read-only fallback. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the pipeline contract and extension points.

## Replacing bundled generation workflows

This section covers source-level replacement for Actor Assets and Layout Reference Frame, plus the H3 boundary contract used by runtime profile import. Scene generation is outside this guide.

### What MCP does—and what the pipeline adapter does

Comfy MCP validates the completed API workflow, submits it to ComfyUI, monitors the run, and downloads its outputs. ComfyUI schedules and executes the graph. The Director Studio pipeline adapter still has to translate application-level values into workflow boundary nodes and translate output nodes back into UI slots:

```text
Director Studio job
  -> pipeline adapter injects prompt, uploads, seed, dimensions, and options
  -> Comfy MCP validates and submits the completed API graph
  -> ComfyUI executes the graph
  -> Comfy MCP fetches files
  -> pipeline adapter maps saver nodes to Director Studio output keys
```

The adapter does **not** reproduce every internal ComfyUI connection. It maps the inputs and final outputs that Director Studio uses, plus a small number of required control nodes. Changing only internal model, LoRA, or processing nodes needs no Python change when the boundary contract remains identical. Changing a mapped node ID, input name, output saver, or required control path requires an adapter update.

Node IDs below are the top-level object keys in an API-format workflow JSON. They are not node titles.

### General replacement procedure

1. Work from a source checkout for Actor/Layout replacement. For H3 in Portable, use the Settings workflow above; a JSON file dropped beside `DirectorStudio.exe` is never treated as an override.
2. Back up the current JSON under `backend/workflows/`.
3. Build and successfully queue the replacement graph in ComfyUI, then export it in **API format**, not only the editable UI-format workflow.
4. Identify the Director Studio feature being replaced and use the matching mapping table below.
5. Make a worksheet with four columns: application value, old node and input, new node and input, and whether its data type is unchanged.
6. Replace the existing JSON while keeping its filename. If the filename changes, also update `WORKFLOW_FILENAME` in the matching adapter.
7. If every mapped node ID and input/output field is unchanged, no Python mapping change is required. Otherwise update the constants, graph injection code, and output mapping described below.
8. Run the focused tests, then run the complete backend suite.
9. Rebuild the portable package. The rebuilt executable is the first version that contains the replacement workflow.

Do not register a new pipeline when replacing an existing workflow. `register_pipeline(...)` is needed only when introducing a new Director Studio capability with a new pipeline ID.

### Actor asset workflow

- Workflow: `backend/workflows/qwen_actor_asset_workbench.api.json`
- Adapter: `backend/app/pipelines/actor/workflow.py`
- Builder: `build_actor_prompt()`
- Output mapper: `map_history_outputs()`

Current input and control boundary:

| Director Studio value or behavior | Node | API input / behavior |
|---|---:|---|
| Actor description | `58` | `inputs.value` |
| Legacy body and hair text | `59`, `60` | `inputs.value`; currently cleared because description is authoritative |
| Negative prompt | `11` | `inputs.text` |
| Actor reference upload | `15` | `inputs.image`; blank 1x1 placeholder means no upload |
| Wardrobe reference upload | `23` | `inputs.image`; blank 1x1 placeholder means no upload |
| Actor-reference master prompt | `63` | `inputs.value` |
| Wardrobe extraction prompt | `50` | `inputs.prompt` |
| Wardrobe transfer | `24` | `inputs.prompt`; `image3` is wired to actor reference node `15` |
| Seed | `13`, `20`, `28`, `44`, `54` | `inputs.seed` on every active sampling stage |
| Full-body three-view prompt | `66` | `inputs.value` |

Current output mapping:

| Save node | Director Studio output key | UI meaning |
|---:|---|---|
| `57` | `wardrobe_ref` | Extracted wardrobe reference |
| `31` | `master` | Actor master image |
| `39` | `bust_threeview` | Bust three-view crop |
| `46` | `fullbody_threeview` | Full-body three-view sheet |
| `48` | `asset_sheet` | Combined actor asset sheet |

The current adapter also rewires the master and multipanel path: node `16` consumes actor image `15`; node `40` consumes master `30`, actor reference `15`, and prompt `67`; node `32` crops decoded sheet `45`; saver `39` consumes crop `32`; saver `46` consumes sheet `45`; and node `47` combines the crop and sheet. If the replacement graph does not preserve this structure, update `_use_workbench_multipanel_threeview()` as well as the constants and output map. Merely changing `SAVE_NODES` is not sufficient for a structurally different actor graph.

Focused checks:

```powershell
Set-Location backend
py -m pytest tests/test_actor_hair_policy.py tests/test_job_execution_adapters.py -q
```

### Layout reference-frame workflow

- Workflow: `backend/workflows/ref_frame_layout.api.json`
- Adapter: `backend/app/pipelines/ref_frame/workflow.py`
- Builder: `build_layout_prompt()` / `fill_layout_graph()`
- Output mapper: `map_history_outputs()`

Current input, control, and output boundary:

| Director Studio value or behavior | Node | API input / behavior |
|---|---:|---|
| Positive layout prompt | `10` | `inputs.prompt`; CLIP is rewired to node `2` |
| Negative prompt | `14` | `inputs.prompt` |
| Reference uploads, in order | `7`, `8`, `9` | Dynamic `LoadImage.inputs.image`; attached to node `10` as `image1..3` |
| Sampling parameters and seed | `11` | `steps`, `cfg`, `sampler_name`, `scheduler`, `seed`, `denoise`, conditioning, model, and latent inputs |
| Model sampling shift | `5` | `inputs.shift` |
| Landscape/portrait canvas | `16` with references; `6` without references | Scene `ImageScale` or `EmptyLatentImage`; fixed at `1728x960` or `960x1728` |
| Lightning LoRA | `17` | Rebuilt by the adapter and connected to sampler `11` |
| Final image saver | `13` | `inputs.filename_prefix`; its last `outputs.images` item maps to `layout` |

For reference-image runs, the adapter constructs the full-resolution reference-latent path using nodes `20`, `21`, `30..32`, `40..42`, and `50..52`, depending on reference count. A replacement graph that preserves nodes `2`, `5`, `6`, `7..17`, and the expected sockets can usually retain the current adapter. A graph using a different conditioning or latent strategy requires changes inside `fill_layout_graph()`; updating only `NODE_DESCRIPTION` and `NODE_SAVE` will not be enough.

Focused checks:

```powershell
Set-Location backend
py -m pytest tests/test_ref_frame_pipeline.py -q
```

### H3 Ref2AV video workflow

- Workflow: `backend/workflows/h3_ref2va.api.json`
- Adapter: `backend/app/pipelines/h3_ref2va/workflow.py`
- Builder: `build_ref2va_prompt()` / `fill_ref2va_graph()`
- Output mapper: `map_history_outputs()`

The public build contains only the API-format execution branch of Comfy-Org's official `video_minimax_h3_r2v.json` template. The primary H3 node is discovered by `class_type = MiniMaxH3ReferenceToVideo`; its node ID may change without changing a constant.

Minimal application boundary:

| Director Studio value | Workflow boundary |
|---|---|
| Six-section H3 prompt | Unique `MiniMaxH3ReferenceToVideo` → `inputs.prompt` |
| Output size | Same H3 node → `inputs.width`, `inputs.height` |
| Duration | Same H3 node → validated frame count in `inputs.length` |
| Uploaded images | Dynamic `LoadImage` nodes → `ref_images.ref_image_0..8` |
| Uploaded reference audio | Dynamic `LoadAudio` nodes → `ref_audios.ref_audio_0..2` |
| Seed | Unique `RandomNoise` → `inputs.noise_seed` |
| Output directory | Unique `SaveVideo` → `inputs.filename_prefix` |
| UI result | Official saver node `92` → `video` |

Everything else comes from the workflow JSON. The adapter does not overwrite the model, LoRA, sampler, scheduler, steps, denoise, guider, decode, mux, FPS, format, or codec. In the checked-in official graph, node `127` loads the official Ref2AV model, node `123` selects `res_multistep`, node `124` contains the 20-step `simple` schedule, and node `92` saves the single final video.

The adapter requires exactly one `MiniMaxH3ReferenceToVideo`, one `RandomNoise`, and one `SaveVideo`. It rejects `MiniMaxH3ImageToVideo`, `ref_frame`, and `last_frame`. The official local workflow generates synchronized audio as part of H3 Ref2AV, but it does not preserve a supplied source track exactly and produces only the `video` output.

Because sampling settings remain inside the JSON, updating the official template's internal quality settings does not require a Python change as long as the three unique boundary node classes and H3 input names remain compatible. If the official saver node ID changes, update `NODE_SAVE` so completed history maps deterministically to `video`.

Focused checks:

```powershell
Set-Location backend
py -m pytest tests/test_h3_ref2va_graph.py tests/test_no_i2v_on_h3_pipeline.py -q
```

### Validate and rebuild after any replacement

Run the entire backend suite after the focused checks:

```powershell
Set-Location backend
py -m pytest -q
Set-Location ..
pwsh -File scripts/build-legacy-portable.ps1
```

Before distributing the result, extract the new zip, configure its `.env`, start ComfyUI and Ollama, and run one real job for every workflow you replaced. For an imported H3 profile, use the Settings Test step before activation, then submit a new Production job. Unit tests verify the graph contract and mapping; only a real ComfyUI run proves that all custom nodes, model files, tensor shapes, and output formats are compatible on the target installation.

## Development setup

```powershell
# Backend (from backend/)
python -m pip install -r requirements.txt
Copy-Item .env.example .env  # optional: customize local service settings
python -m uvicorn app.main:app --host 127.0.0.1 --port 8790 --reload

# Frontend (from frontend/)
npm install
npm run dev
```

- UI: http://127.0.0.1:5173  
- API: http://127.0.0.1:8790/docs  
- ComfyUI: http://127.0.0.1:8188 (local workflows are submitted through the installed Comfy MCP server)
- Ollama (Director): http://127.0.0.1:11434  

> Note: default API port is **8790** (frontend Vite proxy points here).

## Director & Production

| Flow | What happens |
|------|----------------|
| **Director** | Paste script → plan shots (Ollama) → optionally generate a **Layout reference** (Comfy) → write the H3 prompt |
| **Production** | Approve full shot package (refs + six-section prompt) → Gate 2 → submit pure **H3 Ref2AV** |

Rules locked for v1:

- Video mode is **pure H3 Reference-to-AV** only (`MiniMaxH3ReferenceToVideo`). No I2V first/last frame sockets.
- A Layout is an optional composition Picture reference, not an I2V `first_frame` and not a guaranteed opening frame.
- Picture references use their actual saved order. A shot becomes ready for H3 when its production prompt is complete; a Layout is not required.
- **VRAM exclusive:** Ollama unloads before Comfy Layout, asset, and local H3 jobs; Agent context reloads from disk when the LLM must think again.

API: `/api/projects/*` · pipelines: `GET /api/pipelines` · health: `GET /api/health`

## Layout (extension points)

```
backend/app/
  core/           # jobs, library, comfy, projects, h3, vram
  agents/         # Director planning, casting, reference selection, prompts
  pipelines/      # actor, scene, prop, ref_frame, h3_ref2va
  api/            # health, files, pipelines, projects
frontend/src/
  app/            # shell + nav
  shared/         # components, api client
  features/       # casting, set, library, director, production
docs/ARCHITECTURE.md
```

## Actor casting (current)

**Auto-route by uploads** (no mode toggles):

- No actor image → text-to-actor  
- Headshot → face/hair; body from optional body text  
- Full-body → face + proportions  
- Wardrobe image → extract + transfer; omit → keep outfit  

Order: master → full-body three-view → bust three-view → sheet  

API: `/api/actors/*` · `GET /api/pipelines`

## Env

| Variable | Default | Purpose |
|----------|---------|---------|
| `DS_COMFY_BASE_URL` | `http://127.0.0.1:8188` | ComfyUI |
| `DS_H3_PROVIDER` | `local` | `local` = ComfyUI MCP; `minimax` = official cloud API |
| `DS_COMFY_MCP_COMMAND` | `comfy-mcp` | ComfyUI MCP executable; Portable installer writes its absolute path |
| `DS_COMFY_MCP_ARGS` | empty | Optional extra command-line arguments passed to the MCP server process |
| `DS_COMFY_MCP_COMFY_BIN` | `comfy` | comfy-cli executable used by the MCP server |
| `DS_PORT` | `8790` | API port |
| `DS_LLM_PROVIDER` | `ollama` | Active Director provider: `ollama`, `lm-studio`, or `openai-compatible` |
| `DS_LLM_BASE_URL` | provider default | `/v1` base URL for LM Studio or an OpenAI-compatible server |
| `DS_LLM_API_KEY` | empty | Optional credential for the active OpenAI-compatible endpoint |
| `DS_LLM_TIMEOUT_SEC` | `600` | LLM request timeout in seconds |
| `DS_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama for Director |
| `DS_H3_MINIMAX_API_KEY` | empty | MiniMax API credential when `DS_H3_PROVIDER=minimax` |
| `DS_H3_MINIMAX_MODEL` | `MiniMax-H3` | MiniMax H3 API model |
| `DS_H3_MINIMAX_RESOLUTION` | `768P` | Requested MiniMax API output resolution |

The Director model is not required in the environment. Director Studio discovers the active provider's catalog, selects the first available model when no prior choice exists, and persists subsequent model-picker selections with their provider under `data/director_model.json`. If the provider returns no models, the selection remains empty.

For source development, set values in `backend/.env` (prefix `DS_`). In the portable package, use the `.env` beside `DirectorStudio.exe`.

## Build the Windows portable package

From a source checkout with Node.js, npm, Python, and PowerShell available:

```powershell
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/requirements-build.txt
pwsh -File scripts/build-legacy-portable.ps1
```

The build runs the frontend and focused packaged-runtime tests, creates the one-file executable, launches it on an isolated port, checks the health endpoint and bundled UI, then writes the archive and reports its SHA-256 in the terminal. It also verifies that the executable and zip contain the official H3 workflow but no imported profiles, active pointer, user data, projects, jobs, outputs, or tests. The current script and archive retain their existing `legacy` filename for build compatibility; the packaged application itself is Director Studio:

- `dist/Director-Studio-Legacy-Windows-x64.zip`
