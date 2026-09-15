# Windows Comfy MCP Portable Bundling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Windows x64 portable archive that runs the existing Comfy MCP execution adapter without system Python, pip, `comfy-mcp`, or `comfy`.

**Architecture:** Stage a pinned official embeddable CPython under `runtime/python`, install an exact hashed Windows wheel set into its `Lib/site-packages`, and generate a relocatable `comfy.exe` launcher adjacent to `python.exe`. The packaged entry point supplies private runtime defaults before settings import; the existing `ComfyMcpClient` continues to own the demand-started stdio server.

**Tech Stack:** Python 3.13 Windows embeddable runtime, pip/uv lock generation, comfy-mcp 0.10.0, comfy-cli 1.20.0, FastAPI/Pydantic Settings, PowerShell packaging, pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-windows-comfy-mcp-portable-bundling-design.md`

## Global Constraints

- Windows x64 only; Linux packaging is unchanged.
- ComfyUI remains external at `DS_COMFY_BASE_URL`.
- A fresh portable archive must not require system Python, pip, `comfy-mcp`, `comfy`, or `Install-Tools.cmd`.
- Explicit process environment and uncommented `.env` MCP settings override bundled defaults.
- Bundle `comfy-mcp==0.10.0` and `comfy-cli==1.20.0`; lock every transitive wheel by exact version and SHA-256.
- Use only binary Windows CPython 3.13 wheels; reject source distributions and foreign native wheels.
- Preserve current workflows and the official-H3-only packaging boundary.
- Include CPython, comfy-mcp, comfy-cli, and dependency license records.
- Treat produced binaries as local evaluation artifacts until redistribution licensing is approved.

---

### Task 1: Stage a relocatable private Python tool runtime

**Files:**
- Create: `packaging/windows-comfy-runtime.json`
- Create: `packaging/windows-comfy-requirements.lock`
- Create: `scripts/stage_windows_comfy.py`
- Create: `backend/tests/test_stage_windows_comfy.py`

**Interfaces:**
- Consumes: repository root, package destination, optional predownloaded CPython ZIP and wheelhouse.
- Produces: `RuntimeConfig`, `load_runtime_config()`, `extract_python_runtime()`, `stage_python_packages()`, `write_comfy_launcher()`, and `stage_windows_comfy()`.

- [ ] **Step 1: Write failing manifest and safe-extraction tests**

Test that `load_runtime_config()` accepts only `windows/x64`, Python `3.13.14`, archive `python-3.13.14-embed-amd64.zip`, URL `https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip`, SHA-256 `90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907`, and exact package versions. Create a fixture ZIP containing `python.exe`, `python313.dll`, `python313.zip`, `python313._pth`, and `LICENSE.txt`; assert traversal, missing files, and checksum mismatch fail with `StageError`.

- [ ] **Step 2: Run the extraction tests and observe the missing-module failure**

Run from `backend`:

```powershell
py -m pytest -p no:cacheprovider tests/test_stage_windows_comfy.py -q
```

Expected: collection fails because `scripts/stage_windows_comfy.py` does not exist.

- [ ] **Step 3: Implement config loading and safe CPython extraction**

Use a frozen config dataclass and the same `PurePosixPath`/symlink/path-traversal policy as `stage_windows_harness.py`. Patch `python313._pth` deterministically so it contains:

```text
python313.zip
.
Lib/site-packages
import site
```

Copy the CPython license to `THIRD_PARTY_LICENSES/python.txt`.

- [ ] **Step 4: Write failing wheel, launcher, and license tests**

Build a fixture wheelhouse with minimal `.whl` ZIPs and assert staging:

```python
assert (runtime / "Lib/site-packages/comfy_mcp/__init__.py").is_file()
assert (runtime / "Lib/site-packages/comfy_cli/__init__.py").is_file()
assert (runtime / "comfy.exe").is_file()
assert read_launcher_shebang(runtime / "comfy.exe") == "#!python.exe"
assert (licenses / "comfy-mcp.txt").is_file()
assert (licenses / "comfy-cli.txt").is_file()
```

Also assert an sdist, an unlocked wheel, a duplicate distribution, missing license data, or wrong platform tag is rejected.

- [ ] **Step 5: Generate and commit the exact hashed Windows lock**

Resolve for CPython 3.13/win_amd64 with uv and emit hashes:

```powershell
py -m uv pip compile portable-tools-requirements.txt --python-version 3.13 --python-platform x86_64-pc-windows-msvc --generate-hashes --output-file packaging/windows-comfy-requirements.lock
```

Change the two direct requirements to exact versions before compiling:

```text
comfy-cli==1.20.0
comfy-mcp==0.10.0
```

- [ ] **Step 6: Implement locked wheel download/install and the relocatable launcher**

Use `pip download --require-hashes --only-binary=:all:` into a temporary wheelhouse, then install into `Lib/site-packages` with `pip install --no-index --no-compile --target`. Generate the distlib Windows console launcher for `comfy_cli.__main__:main` with executable `python.exe`, place it beside the private interpreter, and reject `__pycache__`, `.pyc`, tests, pip, setuptools, wheel, uv, and build caches in the staged tree.

- [ ] **Step 7: Run tests green and commit**

```powershell
py -m pytest -p no:cacheprovider tests/test_stage_windows_comfy.py -q
git add packaging/windows-comfy-runtime.json packaging/windows-comfy-requirements.lock scripts/stage_windows_comfy.py backend/tests/test_stage_windows_comfy.py portable-tools-requirements.txt
git commit -m "build: stage Windows Comfy MCP runtime"
```

---

### Task 2: Select bundled MCP commands before packaged settings load

**Files:**
- Create: `backend/app/portable_comfy.py`
- Create: `backend/tests/test_portable_comfy.py`
- Modify: `backend/packaging/entrypoint.py`
- Modify: `backend/tests/test_packaged_entrypoint.py`

**Interfaces:**
- Consumes: install root, portable `.env`, process environment.
- Produces: `PortableComfyCommands` and `apply_portable_comfy_defaults(install_root, env_file, environ) -> dict[str, object]`.

- [ ] **Step 1: Write failing resolution and precedence tests**

Assert a complete private runtime produces these environment defaults:

```python
{
    "DS_COMFY_MCP_COMMAND": str(root / "runtime/python/python.exe"),
    "DS_COMFY_MCP_ARGS": "-m comfy_mcp.server",
    "DS_COMFY_MCP_COMFY_BIN": str(root / "runtime/python/comfy.exe"),
}
```

Assert process values win over `.env`, uncommented `.env` values win over bundled defaults, partial bundled files raise `PortableComfyError`, and a source checkout with no runtime leaves settings unchanged.

- [ ] **Step 2: Run focused tests red**

```powershell
py -m pytest -p no:cacheprovider tests/test_portable_comfy.py tests/test_packaged_entrypoint.py -q
```

Expected: failure because the resolver and entrypoint call do not exist.

- [ ] **Step 3: Implement the resolver and packaged entrypoint integration**

Parse only the three MCP keys from `.env`; do not import `app.config`. Apply defaults immediately after `runtime_paths` loads and before `from app.config import settings`. Restore temporary environment values in `finally`, alongside the existing Harness variables.

- [ ] **Step 4: Run tests green and commit**

```powershell
py -m pytest -p no:cacheprovider tests/test_portable_comfy.py tests/test_packaged_entrypoint.py tests/test_config.py -q
git add backend/app/portable_comfy.py backend/packaging/entrypoint.py backend/tests/test_portable_comfy.py backend/tests/test_packaged_entrypoint.py
git commit -m "feat: use bundled Comfy MCP in Windows portable"
```

---

### Task 3: Verify the real bundled MCP/CLI boundary offline

**Files:**
- Create: `scripts/verify_bundled_comfy.py`
- Create: `backend/tests/test_verify_bundled_comfy.py`

**Interfaces:**
- Consumes: a staged package root.
- Produces: a zero-exit smoke verifier with concise stage-specific failures.

- [ ] **Step 1: Write failing fake-runtime verifier tests**

Use executable fixture scripts to prove the verifier constructs a sanitized environment, calls the private interpreter and launcher, performs MCP initialize/list-tools over stdio, checks expected `validate_workflow`, `run_workflow`, `get_job`, and `fetch_outputs` tools, closes the protocol, and rejects a surviving child.

- [ ] **Step 2: Run verifier tests red**

```powershell
py -m pytest -p no:cacheprovider tests/test_verify_bundled_comfy.py -q
```

Expected: missing verifier module.

- [ ] **Step 3: Implement the verifier using the production MCP session**

Set `PATH` to only the private runtime and Windows system directory, clear `PYTHONHOME`, `PYTHONPATH`, and MCP overrides, set `COMFY_BIN` to the private launcher, and run:

```text
python.exe -c "import comfy_mcp, comfy_cli"
comfy.exe --version
python.exe -m comfy_mcp.server
```

Use the same MCP protocol/client code as `ComfyMcpSession`; do not contact ComfyUI or queue a workflow. Capture bounded stderr and always terminate the owned process.

- [ ] **Step 4: Run tests green and commit**

```powershell
py -m pytest -p no:cacheprovider tests/test_verify_bundled_comfy.py tests/test_comfy_mcp_client.py -q
git add scripts/verify_bundled_comfy.py backend/tests/test_verify_bundled_comfy.py
git commit -m "test: verify bundled Comfy MCP offline"
```

---

### Task 4: Integrate the MCP runtime into Windows build and package policy

**Files:**
- Modify: `scripts/build-windows-portable.ps1`
- Modify: `scripts/verify-windows-portable.ps1`
- Modify: `scripts/verify_portable_contents.py`
- Modify: `backend/tests/test_portable_contents_verifier.py`
- Modify: `backend/tests/test_packaged_runtime_paths.py`
- Modify: `scripts/test-portable-verifier-slow-health.ps1`
- Modify: `scripts/stage_windows_harness.py`
- Modify: `backend/tests/test_stage_windows_harness.py`

**Interfaces:**
- Consumes: staged private Python runtime and its license/lock metadata.
- Produces: one Windows ZIP whose deterministic manifest describes Node, Harness, Python, comfy-mcp, and comfy-cli.

- [ ] **Step 1: Write failing package-tree and archive policy tests**

Require `runtime/python/python.exe`, `python313.dll`, `python313.zip`, `python313._pth`, `comfy.exe`, both import roots, their `.dist-info` metadata/licenses, and `THIRD_PARTY_LICENSES`. Reject pip/build tools, tests, credentials, absolute paths, and an incomplete runtime. Keep Linux rejecting all `runtime/python` content.

- [ ] **Step 2: Run packaging policy tests red**

```powershell
py -m pytest -p no:cacheprovider tests/test_portable_contents_verifier.py tests/test_packaged_runtime_paths.py tests/test_stage_windows_harness.py -q
```

- [ ] **Step 3: Extend the deterministic portable manifest**

Have `stage_windows_harness.write_portable_manifest()` read the Comfy runtime
config and lock digest. Emit Python version `3.13.14` with the exact archive
SHA-256 from Task 1, comfy-mcp version `0.10.0` with the SHA-256 of
`packaging/windows-comfy-requirements.lock`, and comfy-cli version `1.20.0`.

Keep format version explicit and reject absolute values.

- [ ] **Step 4: Wire staging and verification into the PowerShell build**

Add optional `-PythonArchive` and `-Wheelhouse` build inputs. Stage Comfy after Harness, run `verify_bundled_comfy.py` before starting the packaged backend, require runtime/license entries in the folder and ZIP, and keep `Install-Tools.cmd` only as a repair path.

- [ ] **Step 5: Run packaging tests green and commit**

```powershell
py -m pytest -p no:cacheprovider tests/test_portable_contents_verifier.py tests/test_packaged_runtime_paths.py tests/test_stage_windows_harness.py -q
pwsh -NoProfile -File scripts/test-portable-verifier-slow-health.ps1
git add scripts/build-windows-portable.ps1 scripts/verify-windows-portable.ps1 scripts/verify_portable_contents.py scripts/stage_windows_harness.py backend/tests/test_portable_contents_verifier.py backend/tests/test_packaged_runtime_paths.py backend/tests/test_stage_windows_harness.py scripts/test-portable-verifier-slow-health.ps1
git commit -m "build: bundle Comfy MCP in Windows portable"
```

---

### Task 5: Document, build, and verify the local artifact

**Files:**
- Modify: `README.md`
- Modify: `docs/HARNESS.md`
- Modify: `backend/.env.example`

**Interfaces:**
- Consumes: the completed package and verifier commands.
- Produces: operator documentation plus a locally verified ZIP hash and size.

- [ ] **Step 1: Update portable documentation**

State that Windows portable bundles Harness, Node, Python, Comfy MCP, and Comfy CLI; users start only ComfyUI, their LLM server, and `DirectorStudio.exe`. Document `Install-Tools.cmd` as an override/repair action and preserve explicit external MCP settings.

- [ ] **Step 2: Run focused and complete tests**

```powershell
py -m pytest -p no:cacheprovider backend/tests/test_stage_windows_comfy.py backend/tests/test_portable_comfy.py backend/tests/test_verify_bundled_comfy.py backend/tests/test_portable_contents_verifier.py backend/tests/test_packaged_runtime_paths.py -q
py -m pytest -p no:cacheprovider -q
npm test --prefix frontend
npm run build --prefix frontend
npm test --prefix harness
npm run build --prefix harness
```

- [ ] **Step 3: Build the real Windows package**

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows-portable.ps1
```

Expected: private Python import, `comfy.exe --version`, MCP initialize/list-tools, bundled Harness offline turn, packaged backend health, process cleanup, H3 isolation, package policy, ZIP creation, and SHA-256 all pass.

- [ ] **Step 4: Re-run post-build evidence checks**

```powershell
py scripts/verify_bundled_comfy.py --package-root dist/Director-Studio-Windows-x64
py scripts/verify_bundled_harness.py --package-root dist/Director-Studio-Windows-x64 --port 18793
py scripts/verify_portable_contents.py --platform windows --package-root dist/Director-Studio-Windows-x64 --executable build/pyinstaller-dist/DirectorStudio.exe --archive dist/Director-Studio-Windows-x64.zip
git diff --check
git status --short
```

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md docs/HARNESS.md backend/.env.example
git commit -m "docs: explain bundled Windows Comfy MCP runtime"
```

Report the artifact path, byte size, SHA-256, test counts, license publication gate, and any remaining requirement that ComfyUI/LLM servers run externally.
