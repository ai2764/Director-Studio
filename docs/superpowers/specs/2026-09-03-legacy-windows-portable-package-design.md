# Legacy Windows Portable Package Design

Date: 2026-09-03 (America/Los_Angeles)
Status: Approved in chat

## Objective

Produce a reproducible Windows x64 portable release of Director Studio's
Legacy runtime at commit `06e5016`. The release must not contain or start the
DeepSeek Harness sidecar. A user should be able to unzip the archive, configure
local services, run one executable, and open the UI from the same FastAPI
origin.

## Artifact

The build produces `dist/Director-Studio-Legacy-Windows-x64.zip`. Its top-level
folder contains:

- `DirectorStudio.exe`, built with PyInstaller;
- `.env.example`, copied from the backend configuration template;
- `README.md`, including installation and custom-workflow instructions;
- an initially empty `data/` directory for projects, jobs, and the library.

The Vite production build and backend workflow JSON files are bundled as
read-only application resources. The archive excludes `harness/`, development
tests, existing user `data/`, `.env`, credentials, exports, and worktrees.

## Runtime Paths

In a source checkout, existing paths remain unchanged. In a frozen executable:

- bundled frontend and workflow resources resolve under PyInstaller's resource
  root;
- `.env` is loaded from the executable directory;
- durable `data/` resolves beside the executable, never inside PyInstaller's
  temporary extraction directory;
- the packaged server runs without Uvicorn reload and serves the frontend from
  `/` and the API from `/api` on `127.0.0.1:8790` by default;
- `DS_DIRECTOR_AGENT_RUNTIME` is forced or validated as `legacy`; no Harness
  process is required or included.

## External Dependencies

The package does not redistribute GPU models or third-party services. Users
install and start:

- Ollama and the configured Director model;
- ComfyUI;
- `comfy-cli` and `comfy-mcp`, available as commands or configured through
  `DS_COMFY_MCP_COMMAND`, `DS_COMFY_MCP_ARGS`, and
  `DS_COMFY_MCP_COMFY_BIN`;
- optionally, MiniMax H3 credentials when `DS_H3_PROVIDER=minimax`.

The README must distinguish service installation from Director Studio archive
installation and provide health URLs for Ollama, ComfyUI, and Director Studio.

## Custom Comfy Workflows

MCP configuration controls transport and process startup only. It does not
describe Director Studio's pipeline-specific node mapping.

Two supported integration paths are documented:

1. **Compatible replacement:** replace a bundled workflow JSON only when it
   preserves the exact node IDs, input names, output semantics, and required
   custom nodes expected by the corresponding Director pipeline. Rebuild the
   portable package after changing bundled JSON.
2. **New or incompatible workflow:** add a pipeline/adapter that loads the new
   API-format workflow, maps Director inputs into its nodes, declares output
   files, registers the pipeline, and then rebuilds. MCP config still only
   points to the MCP/Comfy executables and optional arguments.

The README links to `docs/ARCHITECTURE.md` and names the relevant source
locations: `backend/workflows/`, `backend/app/pipelines/`, and pipeline package
registration in `backend/app/pipelines/__init__.py`.

## Build and Verification

A PowerShell build script performs a clean frontend build, runs PyInstaller
from an explicit spec, assembles the portable folder, and creates the zip. It
must reject a dirty/missing frontend build and must never copy secrets or user
data.

Verification includes:

- backend tests covering frozen/source path resolution and Legacy-only startup;
- frontend tests and production build;
- PyInstaller build completion;
- archive-content inspection proving no `harness`, `.env`, existing `data`, or
  credential files are included;
- launching the extracted executable on an isolated port and checking
  `/api/health` plus the frontend entry page;
- `git diff --check` and a clean tracked worktree before delivery.

## Non-goals

- Building an MSI or Inno Setup installer;
- bundling Ollama, ComfyUI, models, or GPU drivers;
- packaging the Harness runtime;
- changing Comfy workflows, H3 provider behavior, or Director tool behavior;
- signing the executable or publishing a public release.
