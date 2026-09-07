# Linux Portable Distribution

## Status

Approved design for an Ubuntu x86_64 Portable distribution of Director Studio.

## Goal

Ship a native Linux Portable package with the same application behavior and feature scope as the current Windows Portable package. Linux users must be able to extract one archive, configure `.env`, install the private ComfyUI MCP tools, and run Director Studio locally while keeping Ollama and ComfyUI as external services.

The release artifact is:

```text
Director-Studio-Linux-x86_64.tar.gz
```

It is built on Ubuntu 22.04 and supported on Ubuntu 22.04 and 24.04 x86_64.

## Product parity

Linux and Windows use the same React frontend, FastAPI backend, API contracts, project format, job system, pipeline registry, Director agent, VRAM coordination, bundled workflows, and workflow-profile implementation. Platform-specific code is limited to packaging, tool installation, launch behavior, process verification, and release automation.

The Linux package includes the current Windows feature set:

- Director chat and local Ollama model selection.
- Asset library and Actor, Costume, Scene, and Prop generation paths.
- Optional Layout generation and reference management.
- Production queue and local ComfyUI generation.
- MiniMax official API submission when configured.
- Built-in official H3 Ref2AV workflow.
- Runtime Custom H3 workflow import, output selection, boundary confirmation, validation, test generation, activation, switching, snapshotting, and fallback.
- External durable projects, assets, jobs, outputs, and workflow profiles.

No Linux-only business behavior or reduced feature mode is introduced.

## Non-goals

The first Linux release does not provide:

- ARM64 builds.
- AppImage, Flatpak, Snap, Deb, or RPM packaging.
- Docker as the end-user distribution format.
- Bundled Ollama, ComfyUI, GPU drivers, models, custom nodes, or LoRAs.
- Automatic NVIDIA driver or CUDA installation.
- A systemd service, root installation, desktop-menu registration, or automatic updates.
- A real GPU video-generation test in GitHub Actions.
- Support declarations for distributions other than Ubuntu 22.04 and 24.04.

## Package contract

The extracted archive has this initial layout:

```text
Director-Studio-Linux-x86_64/
|-- DirectorStudio
|-- launch.sh
|-- install-tools.sh
|-- Install-Tools.py
|-- portable-tools-requirements.txt
`-- .env
```

`DirectorStudio` is a native x86_64 Linux ELF executable created by PyInstaller in one-file mode. The React production build, backend modules, five repository-owned ComfyUI workflows, and Director skill/guides are embedded in that executable.

The archive must not contain `data/`, projects, assets, jobs, generated media, test inputs, imported workflow profiles, an active custom-workflow pointer, caches, source tests, or local development paths.

At runtime, writable state is created beside the executable:

```text
Director-Studio-Linux-x86_64/
|-- data/
|   |-- projects/
|   |-- library/
|   |-- jobs/
|   `-- workflow_profiles/
`-- tools/
    `-- venv/
```

The archive preserves executable permissions on `DirectorStudio`, `launch.sh`, and `install-tools.sh`.

## Runtime and data paths

The existing frozen-runtime contract remains authoritative:

- `bundle_root` is PyInstaller's extraction/resource directory.
- `install_root` is the directory containing the executable.
- `data_root` is `<install_root>/data`.
- `.env` is `<install_root>/.env`.
- bundled workflows and frontend files are read-only resources below `bundle_root`.

No Linux code may store durable state in PyInstaller's temporary extraction directory, the current working directory, the user's home directory, or a system directory.

The application may be launched from any working directory, including an install path containing spaces.

## MCP tool installer

`Install-Tools.py` becomes platform-aware while retaining one shared implementation.

On Windows it continues to use:

```text
tools/venv/Scripts/python.exe
tools/venv/Scripts/comfy-mcp.exe
tools/venv/Scripts/comfy.exe
```

On Linux it uses:

```text
tools/venv/bin/python
tools/venv/bin/comfy-mcp
tools/venv/bin/comfy
```

The Python installer must:

1. Create a private virtual environment below the extracted package.
2. Upgrade pip inside that environment.
3. Install the pinned portable tool requirements.
4. Verify that `comfy_mcp` imports.
5. Verify that the `comfy` command starts with `--help`.
6. Write absolute MCP and Comfy CLI paths into the package `.env`.
7. Preserve every unrelated `.env` value.
8. Be idempotent when run repeatedly.

`install-tools.sh` locates `python3`, verifies Python 3.11 or newer, and invokes `Install-Tools.py` relative to the script directory. It never invokes `sudo`, never modifies the system Python environment, and reports actionable errors for a missing interpreter, missing `venv` support, failed dependency installation, or missing installed entry points.

Users remain responsible for installing and starting Ollama and ComfyUI. The installer configures only the private ComfyUI MCP/CLI tool environment.

## Launch behavior

`launch.sh` resolves its own directory and starts the sibling `DirectorStudio` executable without relying on the caller's working directory.

The script:

1. Starts Director Studio as a child process with terminal output visible.
2. Polls the configured local health endpoint with a bounded timeout.
3. Opens the UI using `xdg-open` when available.
4. Prints the UI URL when a desktop opener is unavailable.
5. Keeps the launcher attached to the application process.
6. Forwards termination by stopping the child on `INT` or `TERM`.
7. Exits non-zero if the executable exits before becoming healthy or the health timeout expires.

Running `./DirectorStudio` directly remains supported and starts the service without browser-launch orchestration.

The launcher does not start, stop, install, or supervise Ollama or ComfyUI.

## Build implementation

The existing PyInstaller spec remains the shared source of embedded resources. Platform-neutral portions of the Windows build verification should be reusable from both build paths rather than duplicated as PowerShell-only assertions.

The Linux build entry point is:

```text
scripts/build-linux-portable.sh
```

It uses strict shell error handling and performs these stages:

1. Install frontend dependencies with `npm ci`.
2. Run the frontend test suite.
3. Build the production frontend.
4. Run the backend test suite required for packaging.
5. verify PyInstaller availability.
6. Remove only validated repository-local generated build directories.
7. Build the one-file Linux executable.
8. Verify that the result is an x86_64 ELF executable.
9. Inspect embedded resources.
10. Assemble the clean package directory.
11. Run the packaged executable on an isolated port and wait for `/api/health`.
12. Verify the bundled UI and API are served.
13. Verify Custom H3 routes and the built-in official H3 fallback are present.
14. Verify forbidden user/test/generated content is absent.
15. Create the `.tar.gz` archive and a matching `.sha256` file.

Build cleanup must validate absolute targets before recursively removing them. It may remove only known `build/` and `dist/` paths below the repository checkout.

## GitHub Actions

A repository workflow builds and validates the Linux package on a standard GitHub-hosted Ubuntu runner.

Triggers:

- Pull requests run tests, build the Linux executable, and run package smoke verification.
- `workflow_dispatch` runs the same pipeline and uploads the package as a temporary artifact.
- version tags matching `v*` run the same pipeline and attach the archive plus checksum to the corresponding GitHub Release.

The workflow uses Ubuntu 22.04, Node.js matching the frontend's supported toolchain, and Python 3.13 matching current development. It installs dependencies from the repository lockfile and requirements files.

Temporary Actions artifacts use a seven-day retention period. Release publication uses GitHub's provided token with only the minimum `contents: write` permission required for the release job. Pull-request jobs remain read-only.

Third-party release-upload actions are unnecessary; the preinstalled GitHub CLI may create or update the tagged release and upload the two files.

The workflow never receives MiniMax API keys, user `.env` files, workflow imports, project data, models, or media assets.

## Validation strategy

### Unit and integration tests

- Parameterize portable installer tests for Windows `Scripts` and Linux `bin` layouts.
- Verify `.env` updates preserve unrelated settings and correctly quote paths containing spaces.
- Verify missing interpreter, missing entry point, and failed subprocess behavior.
- Keep runtime-path tests platform-neutral and assert durable state remains beside the executable.
- Test launcher behavior through controlled stub executables and health responses where practical.
- Keep all existing business, H3 profile, MCP, job, and frontend tests unchanged unless a real platform assumption is exposed.

### Package verification

- `file DirectorStudio` identifies an x86_64 ELF executable.
- The binary starts successfully on an isolated loopback port.
- `/api/health`, `/docs`, the root UI, and `/mobile` respond from the package.
- The five official workflow resources and Director guides are embedded.
- A clean extraction selects Built-in Official H3.
- Custom H3 setup endpoints are available.
- No custom profile, test asset, user data, local absolute path, or generated output is present.
- Shell scripts pass syntax checking; ShellCheck is used when available in CI.

### Real Linux acceptance test

Before announcing the Linux build as GPU-validated, run one manual acceptance pass on an Ubuntu x86_64 NVIDIA workstation:

1. Extract the release archive into a clean directory.
2. Configure ComfyUI and Ollama base URLs.
3. Run `./install-tools.sh`.
4. Run `./launch.sh`.
5. Confirm ComfyUI and Ollama health in the UI.
6. Create a project and exchange one Director message.
7. Run one asset or Layout workflow.
8. Run the built-in official local H3 workflow.
9. Import, validate, test, activate, and run one compatible Custom H3 workflow.
10. Confirm switching back to Built-in Official H3 requires no restart.

GitHub Actions proves packaging and CPU-level application behavior; it does not claim GPU/model/custom-node compatibility.

## Error handling

- Missing `python3`: print the minimum supported version and an Ubuntu installation hint.
- Missing Python `venv` support: identify the required Ubuntu package without attempting privileged installation.
- Failed pip installation: preserve pip output and exit non-zero without partially rewriting `.env`.
- Missing MCP/Comfy CLI entry points: fail before writing paths.
- Port already occupied: surface the existing application error and do not kill unrelated processes.
- Browser opener unavailable: keep the server running and print the URL.
- Health timeout or early executable exit: stop the owned child process and return non-zero.
- Unsupported architecture or operating system: fail during build or installation with an explicit supported-platform message.

## Security and privacy

- All application and generation data remains local unless the user explicitly selects the MiniMax official API provider.
- The installer writes only below the extracted package directory and its `.env`.
- No command is assembled through untrusted shell evaluation.
- Paths are passed as quoted arguments, including paths containing whitespace.
- CI artifacts contain no secrets or durable user state.
- Release automation has no write permission on pull-request jobs.

## Documentation

README installation documentation gains a Linux Portable section alongside the existing Windows section. It documents:

- supported Ubuntu versions and x86_64 architecture;
- external Ollama and ComfyUI prerequisites;
- archive extraction;
- executable permission recovery when necessary;
- `.env` configuration;
- `./install-tools.sh`;
- `./launch.sh`;
- direct executable launch;
- Custom H3 parity and limitations;
- troubleshooting for Python venv, MCP paths, occupied ports, and browser opening.

Windows instructions and filenames remain intact.

## Acceptance criteria

The Linux Portable work is complete when:

1. A GitHub-hosted Ubuntu 22.04 runner builds `Director-Studio-Linux-x86_64.tar.gz` and its SHA-256 file from a clean checkout.
2. The archive passes all automated content, startup, health, UI, API, and clean-state checks.
3. Existing backend and frontend suites pass.
4. Windows installer and packaging tests continue to pass.
5. Linux installation creates and configures only a private package-local tools environment.
6. Linux and Windows expose the same business features and persistent data contract.
7. The README contains complete Linux installation and troubleshooting instructions.
8. The artifact is clearly labeled CPU/package-verified until the real NVIDIA acceptance test is completed.
