# <img src="frontend/public/director-agent-shot-board.png" alt="Director robot" width="56" height="56"> Director Studio

An agentic filmmaking workspace for **MiniMax H3 Ref2AV**. Talk to a Director Agent, prepare reusable assets, plan shots, and generate images and video through ComfyUI.

Director Studio connects your LLM to generation tools and keeps projects, assets, prompts, and results on your machine. **Director Studio itself does not require a GPU**; hardware requirements come from the LLM and generation models you choose. With local services and the required tools/models installed, the production process can run offline. Cloud LLMs and the official MiniMax API are optional.

### What you can do

- **Direct through conversation:** plan and revise shots, choose references, write H3 prompts, and submit generation jobs.
- **Prepare reusable assets:** actors, outfits, scenes, props, portraits, and voice references.
- **Generate optional Layouts:** use Qwen Image 2.1 with text alone or up to three image references.
- **Make music videos:** upload the song, import externally prepared lyrics/timing, listen to segments, and discuss individual sections with the Agent.
- **Continue a shot from video:** use a completed earlier take or an external clip as independent video context. This local H3 feature is experimental and disabled by default.
- **Use your own H3 workflow:** import variations in Settings while preserving their internal model and sampling choices.

### Get started

[Windows Portable](#windows-portable-installation) · [Linux Portable](#linux-portable-installation) · [macOS Portable](#macos-portable-installation) · [Run from source](#run-from-source)

Download published packages from [Releases](https://github.com/ai2764/Director-Studio/releases). For newer preview builds, use the **Artifacts** section of a successful [Windows](https://github.com/ai2764/Director-Studio/actions/workflows/windows-portable.yml), [Linux](https://github.com/ai2764/Director-Studio/actions/workflows/linux-portable.yml), or [macOS](https://github.com/ai2764/Director-Studio/actions/workflows/macos-portable.yml) build. Preview downloads require a GitHub account and expire after seven days. This README describes `main`; a tagged release may contain an earlier feature set.

Director supports Ollama, LM Studio, llama-swap, and OpenAI-compatible providers. The [Harness runtime](docs/HARNESS.md) is selected by the example configuration and portable defaults; source users can also choose Legacy. Portable packages include a private Node runtime and compiled Harness sidecar. Conversation compaction helps manage history, while the model server determines the available context capacity.

For implementation details, see [Architecture](docs/ARCHITECTURE.md), [context recovery](docs/HARNESS_CONTEXT_RECOVERY.md), and [Director configuration recovery](docs/director-configuration-recovery.md).

## Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI · pluggable pipelines · ComfyUI MCP · provider-neutral Director LLM |
| Frontend | Vite + React · feature folders |
| Execution | ComfyUI through MCP (actor / scene / prop / Layout / local H3) · MiniMax H3 official API |
| Planning LLM | Ollama · LM Studio · llama-swap · OpenAI-compatible Chat Completions |

## Platform support

| Platform | Portable package | Source install | Support level |
|----------|------------------|----------------|---------------|
| Windows 10/11 x64 | Yes | Yes | Officially supported and tested |
| Ubuntu 22.04/24.04 x86_64 | Yes | Yes | Officially supported and tested |
| Other Linux distributions | Not published | Likely compatible | Best effort; not covered by CI |
| macOS 15 or newer, Apple Silicon / Intel | Yes (separate builds) | Yes | Tested on macOS 15 VMs; see [verification scope](#macos-verification-scope) |

Windows, Ubuntu, and macOS use the same application code and project format. Only the platform entry points, private tool-environment paths, process handling, and package format differ:

The macOS port adds packaging, launch/install scripts, and platform-specific verification. It uses the existing React frontend and FastAPI backend without changes to application business logic. ComfyUI, model servers, and GPU workflow compatibility are separate from the application package.

| Platform | Install tools | Start Portable | Release artifact |
|----------|---------------|----------------|------------------|
| Windows | Automatic on first launch | `DirectorStudio.exe` | `Director-Studio-Windows-x64.zip` |
| Ubuntu | `./install-tools.sh` | `./launch.sh` | `Director-Studio-Linux-x86_64.tar.gz` |
| macOS Apple Silicon | `Install-Tools.command` | `Launch.command` | `Director-Studio-macOS-arm64.tar.gz` |
| macOS Intel | `Install-Tools.command` | `Launch.command` | `Director-Studio-macOS-x86_64.tar.gz` |

An officially supported platform is exercised by its own CI build and packaged-runtime checks. “Best effort” means the source may run there, but releases are not built or verified for that platform.

## Windows portable installation

For the short instructions included in the ZIP, see
[Windows portable instructions](packaging/windows-portable-readme.md). This
section provides the additional configuration and troubleshooting detail for
source readers.

The portable package is started through one `DirectorStudio.exe`. The UI,
backend, private Node.js runtime, compiled Harness sidecar, private Python
runtime, and its locked Python installer are included. On the first launch,
Director Studio automatically downloads the pinned `comfy-mcp` and `comfy-cli`
packages into `data\tools\comfy`; later launches reuse that private copy. Do not
install Node.js, Python, npm packages, or MCP tools yourself. The selected LLM
server and ComfyUI remain external services.

### 1. Install local prerequisites

- Windows 10 22H2 or newer, x64.
- One Director LLM provider. Ollama remains the default. For [Ollama for Windows](https://ollama.com/download/windows), install any compatible local model, for example:

  ```powershell
  ollama pull <model-name>
  ```

  LM Studio, llama-swap and other OpenAI-compatible servers are configured below instead. Director reads the active provider's model catalog; choose the model in the Director dropdown.

- A compatible ComfyUI installation with the selected workflow's models and custom nodes. The usual local endpoint is `http://127.0.0.1:8188`; a remote ComfyUI server is also supported. [ComfyUI Desktop](https://docs.comfy.org/installation/desktop/windows) is one installation option.

Extract the complete zip to a writable folder such as `C:\DirectorStudio`; do not copy only the executable. The release archive intentionally contains no user data. Director Studio creates `data` beside the executable on first launch; after that, keep it with the other extracted files and back it up before upgrades.

The first launch requires internet access to Python Package Index (PyPI). It
verifies every downloaded wheel against the package's lock file before making
the private tool environment active. Director Studio does not scan for or reuse
a system `comfy-mcp`, `comfy-cli`, or Python installation. `Install-Tools.cmd`
is not included in the Windows package and is no longer required.

### 2. Configure Director Studio

```powershell
Set-Location C:\DirectorStudio
notepad .env
```

At minimum, confirm the local service URLs. Leave the MCP command settings
commented to use the managed private runtime. An explicit process environment
or uncommented `.env` MCP command skips automatic setup and uses that override.

#### Director LLM providers

`DS_LLM_PROVIDER` selects exactly one active Director provider. Do not set a model name in `.env`: Director Studio reads the provider's model catalog and exposes it in the Director model picker.

| Provider | `DS_LLM_PROVIDER` | Model catalog | Local unload behavior |
|----------|-------------------|---------------|-----------------------|
| Ollama | `ollama` | Ollama API | Unloads before local ComfyUI jobs |
| LM Studio | `lm-studio` | OpenAI-compatible `/v1/models` | Uses LM Studio's native unload endpoint |
| llama-swap | `llama-swap` | Proxy model catalog | Unloads proxy models before local ComfyUI jobs; failed release stops GPU handoff |
| OpenAI, llama.cpp, or another compatible service | `openai-compatible` | OpenAI-compatible `/v1/models` | No unload request is assumed |

Choose one of these configurations. Ollama is the default:

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
```

For a dedicated local llama-swap proxy:

On Windows, `.\start-llama-swap.ps1` starts the locally installed proxy at
`127.0.0.1:11435` and writes its PID and logs to `.run/`. Override the
installation paths with `-ExePath` and `-ConfigPath` if needed; use
`-ValidateOnly` to check the config without starting the service.
With the local `llama-swap` provider configured below, Windows `start.ps1`
starts or reuses the proxy, then continues starting the Harness, backend and
frontend. A healthy existing proxy is skipped without stopping app startup.

```dotenv
DS_LLM_PROVIDER=llama-swap
DS_LLM_BASE_URL=http://127.0.0.1:11435/v1
```

Use the dedicated `llama-swap` provider when Director should coordinate its local
GPU lifecycle; selecting generic `openai-compatible` does not enable that behavior.

For the OpenAI API:

```dotenv
DS_LLM_PROVIDER=openai-compatible
DS_LLM_BASE_URL=https://api.openai.com/v1
DS_LLM_API_KEY=replace-with-your-api-key
```

For a local llama.cpp server exposing the OpenAI-compatible API:

```dotenv
DS_LLM_PROVIDER=openai-compatible
DS_LLM_BASE_URL=http://127.0.0.1:8080/v1
```

The same `openai-compatible` setting works with vLLM, LiteLLM, OpenRouter, DeepSeek-compatible gateways, and most third-party services that implement Chat Completions plus Models. Replace the base URL with the provider's documented `/v1` endpoint and set `DS_LLM_API_KEY` only when that endpoint requires authentication.

LM Studio model instances are unloaded before local ComfyUI generation and loaded again by LM Studio on the next Director request. Remote providers do not participate in local GPU ownership.

To enable the official MiniMax API alongside local H3 generation, add your key. Production and JSON Production then offer a per-run **Local · ComfyUI** / **MiniMax · Official API** selector; `DS_H3_PROVIDER` only sets its initial choice:

```dotenv
DS_H3_MINIMAX_API_KEY=your-secret-key
# Optional: make MiniMax the initial selector value.
DS_H3_PROVIDER=minimax
```

Director Studio coordinates local generation with Ollama, LM Studio or llama-swap through its built-in exclusive GPU lock. VRAM policy, queue timeout, and LLM residency use internal defaults and require no user configuration.

Do not publish `.env`; it may contain provider credentials. Projects and generated application state are stored in the adjacent `data` folder. Back up that folder before replacing or upgrading the package.

Keep unauthenticated Ollama, LM Studio, llama-swap, llama.cpp, and ComfyUI endpoints bound to `127.0.0.1`. To open Director Studio itself to the LAN, set `DS_HOST=0.0.0.0`, allow the selected `DS_PORT` through the host firewall, and use only a trusted private network. This does not add authentication to Director Studio or to the upstream model servers.

### 3. Start

Start the configured LLM server and ComfyUI first, then run:

```powershell
.\DirectorStudio.exe
```

- UI: http://127.0.0.1:8790
- API documentation: http://127.0.0.1:8790/docs
- Health check: http://127.0.0.1:8790/api/health

If first-launch tool setup fails, keep the extracted package in a writable
folder, confirm PyPI is reachable, and inspect `data\logs\comfy-bootstrap.log`.
An interrupted or failed update leaves the last valid private tool environment
untouched; launch again after fixing the network problem. If you explicitly
override the MCP commands, verify those configured paths. Do not run
`comfy-mcp --help`, because that entry point starts the stdio server. If a
workflow fails, load the same workflow in ComfyUI and confirm its custom nodes
and models are installed.

## macOS portable installation

Choose `arm64` for Apple Silicon (M-series chips) or `x86_64` for Intel, on macOS 15 or newer. Both packages include the native executable, frontend, private Node runtime, and native Harness sidecar. A separate Python installation is only needed for the Comfy command-line tools. Ollama, LM Studio, and ComfyUI remain external services.

### 1. Download and extract

Download the matching `.tar.gz` and `.sha256` files from a [release](https://github.com/ai2764/Director-Studio/releases) that includes Mac assets. For builds not yet released, open a successful [macOS Portable workflow run](https://github.com/ai2764/Director-Studio/actions/workflows/macos-portable.yml) and download `director-studio-macos-arm64` or `director-studio-macos-x86_64` from its **Artifacts** section. Unzip that artifact to obtain the package and checksum; CI artifacts are retained for seven days.

With both files in the same directory, verify the checksum before extracting. For Apple Silicon (replace `arm64` with `x86_64` for Intel):

```bash
shasum -a 256 -c Director-Studio-macOS-arm64.tar.gz.sha256
tar -xzf Director-Studio-macOS-arm64.tar.gz
cd Director-Studio-macOS-arm64
```

You can also extract the archive in Finder. Keep the complete extracted folder in a writable location, for example `~/Applications/Director-Studio-macOS-arm64`; `data/` will be created beside `DirectorStudio`. Back up `data/` and `.env` before upgrading.

### 2. Install external tools

Install [Homebrew](https://brew.sh/) if needed, then run `brew install ffmpeg`. This supplies both `ffmpeg` and `ffprobe` for audio references and video frame extraction.

For ComfyUI integration, install Python 3.11 or newer (`brew install python`), then double-click `Install-Tools.command`. This creates `tools/venv` and writes its MCP executable paths to the package's `.env`. The same tools connect to either a local or remote ComfyUI server. If you already have compatible MCP tools, configure their paths instead. The installer is not needed when using only Director chat and the official MiniMax API.

**Intel prerequisite:** before running the Comfy tools installer, run `brew install rust pkg-config openssl@3`; Homebrew's Xcode Command Line Tools must also be installed. The current Intel dependency installation builds `cryptography` from source. If OpenSSL is not detected, run `OPENSSL_DIR="$(brew --prefix openssl@3)" ./install-tools.sh` in Terminal. These build tools are not needed to run the included executable. See the [cryptography installation guide](https://cryptography.io/en/latest/installation/).

The launch and install scripts include the standard Apple Silicon and Intel Homebrew paths. For Python installed elsewhere, run `DS_PYTHON_EXE=/absolute/path/to/python3 ./install-tools.sh`.

### 3. Configure services

Edit the package's `.env` using the shared [Director LLM provider settings](#director-llm-providers). In Terminal, `open -e .env` opens it in TextEdit; in Finder, press **Command-Shift-.** to show hidden files. Set `DS_COMFY_BASE_URL` to your ComfyUI server when using it. Keep credentials in `.env`, and preserve that file with `data/` across upgrades.

Local generation requires ComfyUI workflows, custom nodes, and models compatible with your Mac hardware. Configure a compatible remote ComfyUI server or the official MiniMax API when a workflow needs hardware or nodes unavailable on your Mac.

### 4. Start Director Studio

Start your configured services, then double-click `Launch.command`. It opens `http://127.0.0.1:8790` after the backend is healthy. Keep its Terminal window open while using Director Studio; press **Control-C** there to stop it. You can also run `./launch.sh` from the package directory, or `./DirectorStudio` to start without automatically opening a browser.

Current builds have an ad-hoc signature and are not Apple Developer ID signed or notarized. If macOS blocks the downloaded launcher or executable, attempt to open it, then allow that specific item in **System Settings → Privacy & Security → Open Anyway**. Allow only a package you trust. If executable permissions were lost while transferring the extracted files, run `chmod +x DirectorStudio Launch.command Install-Tools.command launch.sh install-tools.sh` in the package directory.

### macOS verification scope

The [macOS workflow](https://github.com/ai2764/Director-Studio/actions/workflows/macos-portable.yml) builds and tests both architectures in separate macOS 15 virtual machines. CI verifies:

- The frontend and backend automated test suites, plus shell syntax, launcher behavior, and package safety checks.
- The native private Node runtime, production-only Harness dependencies, native Koffi binary, and an offline deterministic Harness turn.
- The executable's CPU architecture and ad-hoc signature, archive contents, and launch permissions.
- Startup of the executable from the extracted archive, `/api/health`, `/`, `/mobile`, `/docs`, and the built-in H3 profile.

These checks do not cover Finder double-click behavior, first-download Gatekeeper prompts, or real generation through ComfyUI, a local GPU, or a model provider. The CI machines have no configured ComfyUI or LLM server; a healthy Director Studio process does not mean those external services are reachable. Desktop installation and real generation still require manual acceptance on the target setup. Newer macOS versions are not currently covered by the CI matrix.

### Build the Mac package

On a Mac matching the desired architecture, with Node.js 22 and Python 3.13 (Intel also needs the Rust/OpenSSL build tools described above):

```bash
brew install ffmpeg
python3.13 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install -r backend/requirements.txt -r backend/requirements-build.txt
python scripts/build_macos_portable.py
```

The builder runs frontend/backend tests, freezes the application, verifies its architecture and signature, checks archive contents, and starts the extracted package to check health, web pages, and the built-in H3 profile. Outputs are `dist/Director-Studio-macOS-<arch>.tar.gz` and its `.sha256` checksum. Build staging is temporary and does not remove existing release folders or user data.

The **macOS Portable** GitHub Actions workflow builds both architectures on native macOS 15 runners. Run it manually from Actions, or use a `v*` tag to attach verified packages to a release. Windows and Linux cannot generate this PyInstaller Mac executable directly.

## Linux portable installation

Supported: Ubuntu 22.04 or 24.04, x86_64. The package includes its private Node runtime and native Harness sidecar. Ollama and ComfyUI remain external services and must be installed and running separately.

Install the required host tools. Ubuntu's `ffmpeg` package provides both `ffmpeg` and `ffprobe`, which Director Studio uses for voice references and video tail-frame extraction:

```bash
sudo apt-get update
sudo apt-get install --yes curl ffmpeg python3-venv
```

Extract the complete archive into a writable directory:

```bash
tar -xzf Director-Studio-Linux-x86_64.tar.gz
cd Director-Studio-Linux-x86_64
chmod +x DirectorStudio install-tools.sh launch.sh
```

Edit `.env` and confirm the Ollama and ComfyUI base URLs. Then install the private Comfy command-line environment:

```bash
./install-tools.sh
```

Start Ollama and ComfyUI, then launch Director Studio:

```bash
./launch.sh
```

The launcher waits for the health endpoint and opens the UI with `xdg-open` when available. Run `./DirectorStudio` instead when you do not want it to open a browser.

Linux has the same Actor, Costume, Scene, Prop, Layout, official H3, MiniMax API, and runtime Custom H3 behavior as Windows. Follow the shared Custom H3 instructions below; imported workflows and generated state remain in the adjacent `data` directory.

Troubleshooting:

- The tools installer requires Python 3.11 or newer and Ubuntu's `python3-venv` package.
- `launch.sh` uses `curl` for readiness. If `xdg-open` is unavailable or cannot open a browser, it prints the local URL for you to open manually.
- Port 8790 is the default. Stop the process using it or set a different `DS_PORT` in `.env`.
- If executable permissions were lost during a non-tar transfer, rerun `chmod +x DirectorStudio install-tools.sh launch.sh`.
- Verify `DS_COMFY_BASE_URL` and `DS_OLLAMA_BASE_URL` when either external service cannot be reached.
- Custom nodes, models, LoRAs, and other workflow dependencies remain your responsibility in ComfyUI.
- The GitHub Actions artifact is CPU- and package-verified. GPU generation is not considered verified until the manual NVIDIA checklist has been completed on supported hardware.

## Connect a custom H3 workflow

The packaged default is currently the Turbo 8 Ref2AV graph, displayed as **Built-in H3 Turbo 8 (temporary test)**. It is derived from the Comfy-Org template with additional LoRA and optimization nodes. First make sure your replacement H3 Ref2AV workflow runs successfully in ComfyUI, then connect it at runtime:

```text
Settings -> Workflows -> H3 -> Import Workflow
-> Final Video Output -> H3 Inputs -> Validate & Test -> Use Workflow
```

Director Studio discovers the selected graph's input/output boundary. Choose a final video output, then confirm the upstream `MiniMaxH3ReferenceToVideo` node, optional seed node, and optional source-video file input. Node titles are shown before class names and IDs. Director Studio injects the prompt, width, height, frame count, Picture 1–9, optional Audio 1–3, seed when mapped, and source video when mapped. Internal model, LoRA, sampler, scheduler, steps, and encoding settings remain owned by the uploaded workflow. Continuation graphs also undergo sampling-path validation and delivery-length adjustment.

The 56-frame test retains videos only from the final output node you selected. If that node emits several videos, preview them and choose one; this selection does not rerun ComfyUI. For a mapped video input, supply a sample clip using **Video for test**. A workflow without a video input remains usable for independent shots. Importing, mapping, validating, and testing do not call the Director LLM.

Imported workflow JSON and setup metadata stay under the external `data/workflow_profiles` directory and are not embedded in a release executable or zip. Install the imported graph's custom nodes, models, and LoRAs in ComfyUI. If a custom profile becomes unavailable or invalid, Director Studio falls back to the packaged built-in graph; continuation still requires a supported source and graph.

Workflow changes apply only to jobs submitted after the switch. Queued and running jobs keep the immutable workflow snapshot captured when they were submitted. You can switch back to the built-in H3 graph without restarting, and doing so does not alter work already in flight.

For video-input mapping, supported Motion Context paths, required nodes/models, and matching API/visual JSON examples, see [Custom H3 video input](docs/custom-h3-video-input.md).

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

JSON Production Picture and Audio selections are uploaded immediately into the current project under `data/projects/<project-id>/json-production/assets/`. Refreshing the page or opening the same project from another browser restores matching slots automatically. No browser storage, additional dependency, or environment setting is required. If a JSON replacement changes a slot's shot ID, type, index, role, or label, the old file is not reused for that changed slot.

## Bundled workflows

The main bundled generation graphs are:

| Purpose | Workflow file | Notes |
|---|---|---|
| Actor assets | `qwen_actor_asset_workbench.api.json` | Character master and three-view outputs |
| Scene assets | `QwenEdit2511_MultiAngle_SceneRef.api.json` | Multi-angle scene generation |
| Prop assets | `qwen_prop_master.api.json` | Prop master generation |
| Director Layout | `qwen_image_21_layout.api.json` | Qwen Image 2.1 text-to-image or reference-to-image composition |
| Legacy Layout pipeline | `ref_frame_layout.api.json` | Earlier reference-frame adapter; distinct from the current Director Layout route |
| Local H3 video | `h3_ref2va.api.json` | Comfy-Org-derived Ref2AV graph with Turbo 8 sampling and optimization nodes |

The [Turbo 4-step continuation example](docs/custom-h3-video-input.md#downloadable-turbo-4-step-continuation-example) is available as both API and editable visual JSON. Importing it does not replace the packaged default automatically.

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
5. Rebuild with `pwsh -File scripts/build-windows-portable.ps1`.

Non-H3 graph replacement requires source adapter changes and a rebuild. Portable supports H3 Ref2AV workflow import through Settings, while the packaged built-in graph remains a read-only fallback. See [Architecture](docs/ARCHITECTURE.md) for pipeline contracts and extension points.

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

Director's local Layout generation now uses **Qwen Image 2.1**. A Layout can be generated from text alone; image references are optional.

- Workflow: `backend/workflows/qwen_image_21_layout.api.json`
- Adapter: `backend/app/pipelines/qwen21_layout/workflow.py`
- Pipeline: `qwen21_layout`

| Director Studio value | Qwen Image 2.1 boundary |
|---|---|
| Composition prompt / negative prompt | `10`, `TextEncodeQwenImage21` → `prompt` / `negative_prompt` |
| Optional references | Dynamic `LoadImage` nodes → `images.image_1..3` on node `10` |
| Seed and sampling controls | `12`, `KSampler` |
| Canvas | `15`, `EmptySD3LatentImage`; 1536×864 or 864×1536 |
| Final image | `14`, `SaveImage` → `layout` |

Focused checks: `python -m pytest tests/test_qwen21_layout_pipeline.py -q` from `backend`.

The earlier `ref_frame` pipeline is still available separately. The following mapping describes that legacy adapter, not the current Director Layout route:

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

The current built-in graph is derived from Comfy-Org's `video_minimax_h3_r2v.json` template and uses Turbo 8 sampling. It includes additional LoRA, sigma-shift, memory-optimization, and sparse-attention nodes; it is not an unchanged copy of the upstream template. Use runtime profile import to choose another variation. The primary H3 node is discovered by `class_type = MiniMaxH3ReferenceToVideo`.

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
| UI result | Built-in saver node `92` → `video` |

Model and sampling choices come from the workflow JSON. The adapter does not overwrite the model, LoRA, sampler, scheduler, steps, denoise, guider, FPS, format, or codec. In the packaged graph, node `127` loads the Ref2AV model, nodes `131`–`134` apply Turbo LoRA and optimization, node `123` selects `euler`, node `124` uses an 8-step `simple` schedule, and node `92` saves the final video. Active video continuation additionally applies the supported context and delivery-trim path.

The built-in adapter requires one `MiniMaxH3ReferenceToVideo`, one `RandomNoise`, and one `SaveVideo`. It rejects I2V `ref_frame` and `last_frame` inputs. Video continuation uses an independent source-video input instead. Local H3 generates synchronized audio, but reference audio does not guarantee exact reproduction of a source track. The built-in graph returns the `video` output.

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
pwsh -File scripts/build-windows-portable.ps1
```

Before distributing the result, extract the new zip, configure its `.env`, start ComfyUI and Ollama, and run one real job for every workflow you replaced. For an imported H3 profile, use the Settings Test step before activation, then submit a new Production job. Unit tests verify the graph contract and mapping; only a real ComfyUI run proves that all custom nodes, model files, tensor shapes, and output formats are compatible on the target installation.

## Run from source

Source development runs two Director Studio processes: the FastAPI backend on port `8790` and the Vite frontend on port `5173`. The selected Director LLM service and ComfyUI are separate processes and must already be running.

### Prerequisites

- [Git](https://git-scm.com/downloads).
- [Python 3.11 or newer](https://www.python.org/downloads/) with `venv` and `pip`.
- [Node.js 22](https://nodejs.org/en/download/archive/v22) and npm. Node 22 is the version exercised by CI.
- [FFmpeg and FFprobe](https://ffmpeg.org/download.html) available on `PATH`.
- One running Director LLM provider: Ollama, LM Studio, llama-swap, or an OpenAI-compatible endpoint.
- A running ComfyUI instance for image generation and local H3 video. ComfyUI is not required when only testing Director chat against a remote LLM.

Clone the repository, or skip this step if the source tree is already present:

```text
git clone https://github.com/ai2764/Director-Studio.git
cd Director-Studio
```

### Windows 10/11

Install Git, Python, Node.js, and FFmpeg using the links above or a trusted package manager. Confirm that each command is available in a new PowerShell window:

```powershell
git --version
py -3 --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

Create an isolated backend environment and install its dependencies:

```powershell
Set-Location backend
py -3 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Choose one `DS_LLM_PROVIDER` configuration from [Director LLM providers](#director-llm-providers). Keep `DS_HOST=127.0.0.1` for normal local use and confirm that `DS_COMFY_BASE_URL` points to the running ComfyUI instance.

Start the backend from the `backend` directory while the virtual environment remains active:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8790 --reload
```

Open a second PowerShell window at the repository root and start the frontend:

```powershell
Set-Location frontend
npm ci
npm run dev
```

### Ubuntu Linux

Ubuntu 24.04 provides a suitable Python version directly. On Ubuntu 22.04, install Python 3.11 or newer using a trusted package source or version manager before continuing. Install the remaining system dependencies and use the official [Node.js 22 downloads](https://nodejs.org/en/download/archive/v22) if the configured Ubuntu repository provides an older Node release:

```bash
sudo apt-get update
sudo apt-get install --yes git ffmpeg python3 python3-pip python3-venv

python3 --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

Create the backend environment and configure it:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
${EDITOR:-nano} .env
```

Choose one `DS_LLM_PROVIDER` configuration from [Director LLM providers](#director-llm-providers), then start the backend from the `backend` directory:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8790 --reload
```

Open a second terminal at the repository root and start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

### macOS

Use macOS 15 or newer and install the shared prerequisites. The following Homebrew setup matches the Python and Node versions used in CI:

```bash
brew install python@3.13 node@22 ffmpeg
export PATH="$(brew --prefix node@22)/bin:$PATH"
```

On Intel, also install `rust`, `pkg-config`, and `openssl@3` as described in [Install external tools](#2-install-external-tools). From the repository root, create the backend environment:

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
${EDITOR:-nano} .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8790 --reload
```

Select your provider in `backend/.env` before starting Uvicorn. In a second terminal at the repository root, start the frontend:

```bash
export PATH="$(brew --prefix node@22)/bin:$PATH"
cd frontend
npm ci
npm run dev
```

### Open the application

- UI: http://127.0.0.1:5173
- API documentation: http://127.0.0.1:8790/docs
- Health check: http://127.0.0.1:8790/api/health
- Default ComfyUI endpoint: http://127.0.0.1:8188
- Default Ollama endpoint: http://127.0.0.1:11434

The frontend Vite server proxies `/api` requests to the backend on port `8790`. If the model picker is empty, verify the active provider and its `/v1/models` or Ollama model-list endpoint from the backend machine. If generation cannot start, verify ComfyUI and the `comfy-mcp` command inside the activated Python environment.

For temporary LAN testing, set `DS_HOST=0.0.0.0`, start Uvicorn with `--host 0.0.0.0`, and run `npm run dev -- --host 0.0.0.0`. Allow ports `5173` and `8790` through the firewall only on a trusted private network. The development servers do not add authentication.

### Run checks

Backend, from `backend` with the virtual environment active:

```text
python -m pytest tests
```

Frontend, from `frontend`:

```text
npm test
npm run build
```

## Director & Production

| Flow | What happens |
|------|----------------|
| **Director** | Paste script → plan shots (selected LLM provider) → optionally generate a **Layout reference** (Comfy) → write the H3 prompt |
| **Production** | Review refs + six-section prompt → validate current inputs → submit **H3 Ref2AV** locally or through the official API; no mandatory separate approval gate |

Current production contract:

- Director production uses **H3 Reference-to-AV**. Layout and continuity stills are Picture references, not I2V first/last frame sockets. Execution uses the selected local workflow or official API.
- A Layout is an optional composition Picture reference, not an I2V `first_frame` and not a guaranteed opening frame.
- Picture references use their actual saved order. A shot becomes ready for H3 when its production prompt is complete; a Layout is not required.
- **VRAM exclusive:** The local Ollama, LM Studio or llama-swap model is released before Comfy Layout, asset, and local H3 jobs. Durable history and project state are separate from GPU residency.

### Music videos

Create an MV project, then open **Assets → Music** to upload the song and import lyrics/timing. Prepare transcription or segmentation externally; Director Studio does not require Whisper. Paste timestamped lyrics, JSON, CSV, or other readable text into the single import entry, review the normalized segments, then save them.

The song player appears above Director and Shots. Listen to the full song or loop a segment, browse neighboring lyrics, and choose the section to discuss with the Agent. A shot can cover multiple lyric segments. The original song remains the timing authority; each shot stores its song interval and whether that interval is used as an H3 Audio reference. An instrumental shot can omit that reference without losing its place in the song.

### Experimental video continuation

Set `DS_VIDEO_CONTEXT_ENABLED=true` in the source or portable `.env`, then restart Director Studio. The experiment is off by default and supports **local H3** only.

- Ask Director to continue a completed earlier shot, or configure a source video for the target shot. Sources can come from nonadjacent earlier shots or an uploaded clip; unrelated intermediate shots can remain independent.
- An explicitly selected take stays pinned. Agent configuration can instead follow the source shot's current take. Source changes invalidate the saved continuation prompt so it must be updated before resubmission.
- Previous-shot continuation inherits the selected source video's resolution. The source is submitted separately from Picture and Audio references, with its actual bytes and provenance captured for the job.
- The receiving graph decodes the source video, encodes its tail into Motion Context, samples the next shot, and trims the inherited overlap. Direct latent-tensor exchange between arbitrary workflows is not supported.

Custom continuation graphs require the certified Ref2AV + Motion Context path at 24 fps. Video windows are 5, 22, 39, or 56 frames; uploaded custom variations keep their configured window. See [Custom H3 video input](docs/custom-h3-video-input.md) for mapping, dependencies, delivery constraints, and API/visual examples. Model output still needs visual and audio review; context does not guarantee a seamless result.

API: `/api/projects/*` · pipelines: `GET /api/pipelines` · health: `GET /api/health`

## Layout (extension points)

```
backend/app/
  core/           # jobs, library, comfy, projects, h3, vram
  agents/         # Director planning, casting, reference selection, prompts
  pipelines/      # actor, scene, prop, qwen21_layout, ref_frame, h3_ref2va
  api/            # health, files, pipelines, projects
frontend/src/
  app/            # shell + nav
  shared/         # components, api client
  features/       # assets, director (including shots), music, production
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
| `DS_QWEN_IMAGE_21_COMFY_BASE_URL` | `DS_COMFY_BASE_URL` | Optional separate ComfyUI instance with Qwen Image 2.1 nodes and models for Layout generation; leave empty to use the H3 instance |
| `DS_VIDEO_CONTEXT_ENABLED` | `false` | Enable experimental local H3 shot continuation; restart after changing |
| `DS_DIRECTOR_AGENT_RUNTIME` | `harness` in the example `.env` and portable defaults | Director loop; bare source settings default to `legacy` when this value is absent |
| `DS_H3_PROVIDER` | `local` | Initial H3 provider shown in Production and JSON Production; each run can override it |
| `DS_COMFY_MCP_COMMAND` | `comfy-mcp` | ComfyUI MCP executable; Windows portable defaults to its private Python module |
| `DS_COMFY_MCP_ARGS` | empty | Optional extra command-line arguments passed to the MCP server process |
| `DS_COMFY_MCP_COMFY_BIN` | `comfy` | comfy-cli executable used by the MCP server |
| `DS_HOST` | `127.0.0.1` | API bind address; use `0.0.0.0` only for an explicitly trusted LAN |
| `DS_PORT` | `8790` | API port |
| `DS_LLM_PROVIDER` | `ollama` | Active Director provider: `ollama`, `lm-studio`, `llama-swap`, or `openai-compatible` |
| `DS_LLM_BASE_URL` | provider default | `/v1` base URL for LM Studio, llama-swap or an OpenAI-compatible server |
| `DS_LLM_API_KEY` | empty | Optional credential for the active OpenAI-compatible endpoint |
| `DS_LLM_TIMEOUT_SEC` | `600` | LLM request timeout in seconds |
| `DS_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama for Director |
| `DS_H3_MINIMAX_API_KEY` | empty | MiniMax API credential; enables the official API option in H3 provider selectors |
| `DS_H3_MINIMAX_MODEL` | `MiniMax-H3` | MiniMax H3 API model |
| `DS_H3_MINIMAX_RESOLUTION` | `768P` | Requested MiniMax API output resolution |

The Director model is not required in the environment. Director Studio discovers the active provider's catalog, selects the first available model when no prior choice exists, and persists subsequent model-picker selections with their provider under `data/director_model.json`. If the provider returns no models, the selection remains empty. The catalog endpoint must be reachable from the Director Studio backend, not only from the browser.

For source development, set values in `backend/.env` (prefix `DS_`). In a portable package, use the `.env` beside `DirectorStudio.exe` on Windows or `DirectorStudio` on macOS/Linux. Both files are ignored by Git; keep real credentials out of README, issue reports, screenshots, and committed example files.

## Build the Windows portable package

From a source checkout with Node.js, npm, Python, and PowerShell available:

```powershell
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/requirements-build.txt
pwsh -File scripts/build-windows-portable.ps1
```

The build runs the frontend and focused packaged-runtime tests, compiles
Harness, installs only its locked production dependencies, verifies and stages
the pinned Windows x64 Node and Python runtimes plus a checksum-pinned `pip`
bootstrap, and creates the executable. It runs an offline deterministic Harness
turn without system Node on `PATH`, exercises a real first-launch installation
of the locked Comfy MCP/CLI wheels into isolated package data, launches the
packaged application on isolated ports, checks authenticated Harness readiness
and the bundled UI, confirms child cleanup, then writes the archive and reports
its SHA-256. It also verifies that the executable and zip contain the packaged
H3 graph but no Comfy MCP/CLI packages, obsolete Windows installer, imported
profiles, active pointer, user data, projects, jobs, outputs, compiled tests, or
Harness development dependencies:

- `dist/Director-Studio-Windows-x64.zip`
- `dist/Director-Studio-Windows-x64.zip.sha256`

The [Windows Portable workflow](https://github.com/ai2764/Director-Studio/actions/workflows/windows-portable.yml) builds and verifies the x64 package on Windows Server 2022 for pull requests and manual runs. A `v*` tag publishes the verified ZIP and checksum to the matching GitHub release.

## Build the Linux portable package

On Ubuntu 22.04 or 24.04 x86_64, install Node.js, npm, Python 3.11 or newer, PowerShell, FFmpeg, and ShellCheck, then run:

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m pip install -r backend/requirements-build.txt
./scripts/build-linux-portable.sh
```

The Linux build stages the pinned native Node runtime and production-only Harness dependencies, runs an offline Harness turn, then runs the application and packaged-content checks with Linux-specific launcher, process-cleanup, executable-permission, and archive-safety verification. It produces:

- `dist/Director-Studio-Linux-x86_64.tar.gz`
- `dist/Director-Studio-Linux-x86_64.tar.gz.sha256`
