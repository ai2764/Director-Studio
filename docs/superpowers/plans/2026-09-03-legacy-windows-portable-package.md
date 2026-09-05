# Legacy Windows Portable Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a Legacy-only Windows x64 portable zip containing Director Studio's backend executable, production frontend, workflows, configuration template, persistent data directory, and installation documentation.

**Architecture:** A small runtime-path module separates bundled read-only resources from durable files beside the executable. A PyInstaller one-file entrypoint starts the existing FastAPI app without reload; the app serves the bundled Vite build. A PowerShell builder assembles and verifies a portable folder before creating the zip.

**Tech Stack:** Python 3.13, FastAPI, Uvicorn, PyInstaller 6.x, PowerShell 7, Node 22, Vite 6, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-03-legacy-windows-portable-package-design.md`

## Global Constraints

- Package commit `06e5016` plus packaging/documentation commits only; do not merge the Harness Phase 2 worktree.
- Do not bundle or start `harness/`.
- Do not copy `.env`, credentials, user `data/`, exports, or worktrees into the artifact.
- Bundled frontend and workflows are read-only resources; `.env` and `data/` persist beside `DirectorStudio.exe`.
- Ollama, ComfyUI, `comfy-cli`, and `comfy-mcp` remain external dependencies.
- The packaged server binds to `127.0.0.1` by default and runs without Uvicorn reload.

---

### Task 1: Frozen runtime paths and packaged entrypoint

**Files:**
- Create: `backend/app/runtime_paths.py`
- Create: `backend/packaging/entrypoint.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_packaged_runtime.py`

**Interfaces:**
- Produces: `RuntimePaths(bundle_root: Path, install_root: Path, data_root: Path, env_file: Path, frozen: bool)`.
- Produces: `resolve_runtime_paths() -> RuntimePaths` using `sys._MEIPASS` and `sys.executable` only when frozen.
- Consumes: `settings.project_root`, `settings.data_dir`, and `settings.frontend_dist` in `create_app()`.

- [ ] **Step 1: Write failing pure path-resolution tests**

```python
def test_frozen_paths_keep_data_beside_executable(tmp_path):
    result = resolve_runtime_paths(
        frozen=True,
        bundle_root=tmp_path / "bundle",
        executable=tmp_path / "install" / "DirectorStudio.exe",
    )
    assert result.data_root == tmp_path / "install" / "data"
    assert result.env_file == tmp_path / "install" / ".env"
    assert result.bundle_root == tmp_path / "bundle"


def test_source_paths_preserve_repository_layout(tmp_path):
    result = resolve_runtime_paths(frozen=False, source_root=tmp_path)
    assert result.data_root == tmp_path / "data"
    assert result.bundle_root == tmp_path
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; py -m pytest tests/test_packaged_runtime.py -q`

Expected: FAIL because `app.runtime_paths` does not exist.

- [ ] **Step 3: Implement path resolution and wire settings**

`runtime_paths.py` must accept explicit arguments for tests, default to the live interpreter, and never use PyInstaller's extraction directory for durable state. Construct settings with `Settings(_env_file=runtime_paths.env_file)`. Add `frontend_dist` as a configurable path defaulting to `<bundle_root>/frontend/dist`; keep workflows at `<bundle_root>/backend/workflows`.

- [ ] **Step 4: Add packaged entrypoint and no-reload behavior**

```python
from app.config import settings
from app.main import create_app
import uvicorn


def main() -> None:
    uvicorn.run(create_app(), host=settings.host, port=settings.port, reload=False)
```

`app.main.run()` must also select `reload=False` in a frozen process while preserving source-development reload.

- [ ] **Step 5: Verify focused and backend regression tests**

Run: `cd backend; py -m pytest tests/test_packaged_runtime.py tests/test_projects_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/runtime_paths.py backend/packaging/entrypoint.py backend/app/config.py backend/app/main.py backend/tests/test_packaged_runtime.py
git commit -m "feat: support frozen legacy runtime paths"
```

### Task 2: Reproducible Legacy portable builder

**Files:**
- Create: `backend/packaging/director-studio-legacy.spec`
- Create: `backend/requirements-build.txt`
- Create: `scripts/build-legacy-portable.ps1`
- Create: `scripts/verify-legacy-portable.ps1`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `dist/Director-Studio-Legacy-Windows-x64/DirectorStudio.exe`.
- Produces: `dist/Director-Studio-Legacy-Windows-x64.zip`.
- Verification script accepts `-PackageRoot`, `-Port`, and `-StartupTimeoutSec`.

- [ ] **Step 1: Write the verification script first**

The script must reject a package missing `DirectorStudio.exe`, `.env.example`, `README.md`, or `data/`; recursively reject paths containing `harness`, `.env`, `projects`, `jobs`, or credential-like filenames; launch the executable with `DS_PORT=<isolated port>`; poll `/api/health`; request `/`; and always stop the child process.

- [ ] **Step 2: Run verification against an empty directory and verify RED**

Run: `pwsh -File scripts/verify-legacy-portable.ps1 -PackageRoot $env:TEMP\missing-director-package`

Expected: non-zero exit identifying the missing executable.

- [ ] **Step 3: Add the PyInstaller spec**

The spec analyzes `backend/packaging/entrypoint.py`, sets `pathex` to `backend`, bundles `frontend/dist` as `frontend/dist`, bundles `backend/workflows` as `backend/workflows`, collects required backend pipeline modules, names the console executable `DirectorStudio`, and contains no Harness data or imports.

- [ ] **Step 4: Add the build dependency pin and builder**

`requirements-build.txt` pins the tested PyInstaller version. The builder runs `npm ci`, `npm test -- --run`, and `npm run build` in `frontend`; runs the focused backend tests; invokes PyInstaller with clean `build/` and staging directories; copies `.env.example` and `README.md`; creates empty `data/`; runs the verification script; and archives only the staging folder.

- [ ] **Step 5: Exclude generated build output**

Add `/build/`, `/dist/`, and generated PyInstaller metadata to `.gitignore` without ignoring source packaging files.

- [ ] **Step 6: Build and verify the artifact**

Run: `pwsh -File scripts/build-legacy-portable.ps1`

Expected: verification passes and `dist/Director-Studio-Legacy-Windows-x64.zip` exists.

- [ ] **Step 7: Commit**

```powershell
git add .gitignore backend/packaging/director-studio-legacy.spec backend/requirements-build.txt scripts/build-legacy-portable.ps1 scripts/verify-legacy-portable.ps1
git commit -m "build: package legacy Windows portable release"
```

### Task 3: Installation and custom workflow documentation

**Files:**
- Modify: `README.md`
- Test: `scripts/verify-legacy-portable.ps1`

**Interfaces:**
- Documents: archive installation, external services, environment variables, startup/health checks, upgrades/backups, compatible workflow replacement, and new pipeline integration.

- [ ] **Step 1: Update README installation steps**

Document Windows x64 prerequisites, archive extraction, why the exe must remain beside `data/`, copying `.env.example` to `.env`, Ollama model installation, ComfyUI startup, `comfy-cli`/`comfy-mcp` command availability, `DirectorStudio.exe` startup, UI/API URLs, and firewall/loopback expectations.

- [ ] **Step 2: Document custom Comfy workflow integration**

State explicitly that MCP configuration controls only the Comfy/MCP process connection. Document the compatible JSON-replacement contract and the new/incompatible workflow pipeline path through `backend/workflows/`, `backend/app/pipelines/<name>/`, pipeline registration, input-node mapping, output declarations, tests, and rebuild.

- [ ] **Step 3: Rebuild so the packaged README is current**

Run: `pwsh -File scripts/build-legacy-portable.ps1`

Expected: archive verification passes and the extracted README contains both installation and custom workflow sections.

- [ ] **Step 4: Run final regression and artifact checks**

Run:

```powershell
Set-Location backend
py -m pytest -q
Set-Location ..\frontend
npm test -- --run
npm run build
Set-Location ..
git diff --check
git status --short
```

Expected: all suites pass; only intended README change remains before commit; generated artifacts are ignored.

- [ ] **Step 5: Commit**

```powershell
git add README.md
git commit -m "docs: add Legacy portable installation guide"
```

- [ ] **Step 6: Record delivery evidence**

Report the final commit, zip absolute path, archive SHA-256, archive size, backend/frontend pass counts, isolated startup health result, and any external dependencies not included in the package.
