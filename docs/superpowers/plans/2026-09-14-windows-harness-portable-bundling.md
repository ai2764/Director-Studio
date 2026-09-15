# Windows Harness Portable Bundling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows x64 portable ZIP that starts Director Studio with a bundled DeepSeek Harness sidecar and requires no system Node.js, npm, or Harness dependency installation.

**Architecture:** Keep a multi-file ZIP with `DirectorStudio.exe` as the only user-facing entry point. Compile first-party Harness TypeScript while retaining production packages in their normal layout for package metadata and Koffi resolution. The frozen Python entry point owns a checksum-pinned private Node child, authenticates its capabilities before backend settings load, and stops only the process it created.

**Tech Stack:** Python 3.11+, PyInstaller, PowerShell 7, Node.js 22.23.2 win-x64, TypeScript 7, npm lockfiles, DeepSeek Harness packages, Koffi 3.2.1, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-14-windows-harness-portable-bundling-design.md`

## Global Constraints

- Support exactly `win-x64` in this iteration; Linux behavior must not change.
- Pin `node-v22.23.2-win-x64.zip` at SHA-256 `1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97`.
- Do not commit Node binaries, generated `dist`, or generated `node_modules`.
- Do not package or invoke npm, `tsx`, TypeScript, or Vitest at runtime.
- Preserve production package layout and `@koromix/koffi-win32-x64/win32_x64/koffi.node`.
- Bind to `127.0.0.1`, create a fresh 32-byte token per launch, and never persist it to `.env`.
- Spawn without a shell and pass only an allowlisted child environment.
- Harness startup failure is terminal and diagnostic; never silently fall back to Legacy.
- Store sessions and logs under the frozen `data` root, which is created at runtime and excluded from the archive.
- Commit the five existing working-tree changes separately before portable implementation.

## File Structure

- `harness/tsconfig.build.json`: portable JavaScript emit configuration.
- `harness/package.json`: deterministic `build` command.
- `backend/app/portable_harness.py`: configuration, paths, spawn, health, and owned cleanup.
- `backend/packaging/entrypoint.py`: start Harness before importing backend settings.
- `backend/app/config.py`: explicit `DS_HARNESS_MANAGED` setting.
- `packaging/windows-harness-runtime.json`: audited Node and Koffi runtime contract.
- `scripts/stage_windows_harness.py`: create the private runtime tree and manifest.
- `scripts/build-windows-portable.ps1`: assemble `Director-Studio-Windows-x64`.
- `scripts/verify-windows-portable.ps1`: verify the packaged app and lifecycle.
- `scripts/verify_bundled_harness.py`: deterministic offline turn through packaged Harness.
- `scripts/verify_portable_contents.py`: platform-aware archive policy.
- Matching `backend/tests/test_*.py`: unit and integration coverage for each boundary.

---

### Task 1: Commit the Existing Harness-Default Baseline

**Files:**
- Modify: `backend/.env.example`
- Modify: `backend/tests/test_harness_launcher.py`
- Modify: `docs/HARNESS.md`
- Modify: `scripts/harness-launcher.ps1`
- Modify: `start.ps1`

**Interfaces:**
- Consumes: `Resolve-AgentRuntime(Requested, EnvFile)`.
- Produces: Harness as the source checkout and template default; explicit Legacy still wins.

- [ ] **Step 1: Verify the already-written test and diff**

```powershell
Push-Location backend; try { py -m pytest tests/test_harness_launcher.py -q } finally { Pop-Location }
git diff --check
git diff -- backend/.env.example backend/tests/test_harness_launcher.py docs/HARNESS.md scripts/harness-launcher.ps1 start.ps1
```

Expected: launcher tests pass and the diff contains only runtime-default behavior, its assertion, and usage text.

- [ ] **Step 2: Commit the baseline**

```powershell
git add backend/.env.example backend/tests/test_harness_launcher.py docs/HARNESS.md scripts/harness-launcher.ps1 start.ps1
git commit -m "feat: make Harness the default agent runtime"
```

### Task 2: Emit a Plain-Node Harness Entrypoint

**Files:**
- Create: `harness/tsconfig.build.json`
- Modify: `harness/package.json`
- Test: `harness/src/server.test.ts`

**Interfaces:**
- Consumes: `harness/src/server.ts` and its NodeNext `.js` imports.
- Produces: `npm run build` and `harness/dist/server.js`, executable without `tsx`.

- [ ] **Step 1: Add and run a failing build-contract test**

```ts
it("declares a plain Node production build", async () => {
  const pkg = JSON.parse(await readFile(resolve("package.json"), "utf8"));
  expect(pkg.scripts.build).toBe("tsc -p tsconfig.build.json");
});
```

Run `npm --prefix harness test -- --run src/server.test.ts`.

Expected: FAIL because `scripts.build` is absent.

- [ ] **Step 2: Add the emit configuration and command**

Create `harness/tsconfig.build.json`:

```json
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "noEmit": false,
    "rootDir": "src",
    "outDir": "dist",
    "declaration": false,
    "sourceMap": false
  }
}
```

Add `"build": "tsc -p tsconfig.build.json"` to `package.json` scripts.

- [ ] **Step 3: Verify direct execution and commit**

```powershell
npm --prefix harness run build
npm --prefix harness test
git add harness/package.json harness/tsconfig.build.json harness/src/server.test.ts
git commit -m "build: emit Harness sidecar JavaScript"
```

Before committing, launch `node dist/server.js` from `harness` with a temporary token/port and require authenticated health to return service `director-studio-harness`, protocol `1`, and both `native-sessions-v1` and `context-envelope-v2` capabilities. Stop that exact process in `finally`.

### Task 3: Implement the Managed Sidecar Lifecycle

**Files:**
- Create: `backend/app/portable_harness.py`
- Create: `backend/tests/test_portable_harness.py`

**Interfaces:**
- Produces: `PortableHarnessSettings(runtime, managed)`, `BundledHarnessPaths(node, entry, root, session_root, log_path)`, `ManagedHarness(process, base_url, token, log_path)`, and `HarnessStartupError(stage, log_path, detail)`.
- Produces functions: `read_portable_harness_settings(env_file, environ)`, `bundled_harness_paths(install_root, data_root)`, `reserve_loopback_port()`, and `start_managed_harness(install_root, data_root, startup_timeout=20.0)`.

- [ ] **Step 1: Write failing configuration and path tests**

```python
def test_environment_overrides_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DS_DIRECTOR_AGENT_RUNTIME=harness" + chr(10) + "DS_HARNESS_MANAGED=true" + chr(10))
    result = read_portable_harness_settings(env_file, {"DS_DIRECTOR_AGENT_RUNTIME": "legacy"})
    assert result == PortableHarnessSettings(runtime="legacy", managed=True)

def test_bundled_paths_follow_install_and_data_roots(tmp_path):
    result = bundled_harness_paths(tmp_path, tmp_path / "data")
    assert result.node == tmp_path / "runtime" / "node" / "node.exe"
    assert result.entry == tmp_path / "harness" / "dist" / "server.js"
    assert result.session_root == tmp_path / "data" / "harness-sessions"
    assert result.log_path == tmp_path / "data" / "logs" / "harness-sidecar.log"
```

Run `Push-Location backend; try { py -m pytest tests/test_portable_harness.py -q } finally { Pop-Location }`.

Expected: FAIL because `app.portable_harness` is absent.

- [ ] **Step 2: Implement parsing and path primitives**

Environment overrides dotenv. Runtime defaults to `harness`. Managed mode defaults to true and accepts case-insensitive `true/false`, `1/0`, and `yes/no`; invalid values raise a configuration-stage `HarnessStartupError`.

Implement port selection exactly through an IPv4 socket bound to `("127.0.0.1", 0)`. Generate tokens with `secrets.token_hex(32)`.

- [ ] **Step 3: Write failing spawn, health, failure, and cleanup tests**

Use injected `popen` and `health_probe` callables. Assert:

```python
handle = start_managed_harness(
    install_root,
    data_root,
    startup_timeout=0.2,
    popen=fake_popen,
    health_probe=lambda url, token: expected_health,
)
assert captured_command == [str(paths.node), str(paths.entry)]
assert len(handle.token) == 64
assert "DS_LLM_API_KEY" not in captured_environment
assert "NODE_OPTIONS" not in captured_environment
assert captured_environment["DS_HARNESS_SESSION_ROOT"] == str(paths.session_root)
```

Separate cases cover missing Node, missing entry, early exit, readiness timeout, wrong service/protocol/capabilities, log path in the exception text, and `stop()` acting only on its captured process handle.

- [ ] **Step 4: Implement bounded authenticated lifecycle**

Spawn exactly:

```python
process = popen(
    [str(paths.node), str(paths.entry)],
    cwd=paths.root,
    env=child_environment,
    stdin=subprocess.DEVNULL,
    stdout=log_file,
    stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
```

Allowlist Windows essentials (`SystemRoot`, `WINDIR`, `SystemDrive`, `ComSpec`, `PATHEXT`, `TEMP`, `TMP`, `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, `NUMBER_OF_PROCESSORS`, `PROCESSOR_ARCHITECTURE`) plus only token, port, and session root. Poll every 100 ms. The default probe sends Bearer auth and requires `{ok: true, service: director-studio-harness, protocol: 1}` plus both required capabilities. Clean up the exact child on every failed start.

- [ ] **Step 5: Verify and commit**

```powershell
Push-Location backend; try { py -m pytest tests/test_portable_harness.py -q } finally { Pop-Location }
git add backend/app/portable_harness.py backend/tests/test_portable_harness.py
git commit -m "feat: manage bundled Harness sidecar lifecycle"
```

### Task 4: Start Harness Before Backend Settings Load

**Files:**
- Modify: `backend/packaging/entrypoint.py`
- Modify: `backend/app/config.py`
- Create: `backend/tests/test_packaged_entrypoint.py`
- Modify: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: Task 3 lifecycle interfaces.
- Produces: `Settings.harness_managed: bool = True` and late loading of `settings/create_app`.

- [ ] **Step 1: Write failing late-import and mode tests**

Inject a fake handle and `uvicorn.run`; assert the sidecar records `sidecar-ready` before `app.config.settings` observes its URL/token, and assert `handle.stop()` is called once. Add cases proving `legacy` and `DS_HARNESS_MANAGED=false` skip bundled startup.

Core assertion:

```python
assert events == [
    "sidecar-ready",
    ("http://127.0.0.1:19001", "generated-token"),
]
assert handle.stop_calls == 1
```

Run `Push-Location backend; try { py -m pytest tests/test_packaged_entrypoint.py tests/test_config.py -q } finally { Pop-Location }`.

Expected: FAIL because current module-level imports freeze settings too early.

- [ ] **Step 2: Refactor the entrypoint and add the setting**

Inside `main()` use this order:

```python
handle = None
launch = read_portable_harness_settings(runtime_paths.env_file, os.environ)
try:
    if launch.runtime == "harness" and launch.managed:
        handle = start_managed_harness(runtime_paths.install_root, runtime_paths.data_root)
        os.environ["DS_HARNESS_BASE_URL"] = handle.base_url
        os.environ["DS_HARNESS_INTERNAL_TOKEN"] = handle.token
    from app.config import settings
    from app.main import create_app
    uvicorn.run(create_app(), host=settings.host, port=settings.port, reload=False)
finally:
    if handle is not None:
        handle.stop()
```

When management is false and runtime is Harness, require an explicit non-empty token and valid loopback base URL before Uvicorn starts. Add `harness_managed: bool = True` to `Settings`.

- [ ] **Step 3: Verify and commit**

```powershell
Push-Location backend; try { py -m pytest tests/test_packaged_entrypoint.py tests/test_config.py tests/test_packaged_runtime.py -q } finally { Pop-Location }
git add backend/packaging/entrypoint.py backend/app/config.py backend/tests/test_packaged_entrypoint.py backend/tests/test_config.py
git commit -m "feat: start bundled Harness before portable backend"
```

### Task 5: Stage the Pinned Runtime Reproducibly

**Files:**
- Create: `packaging/windows-harness-runtime.json`
- Create: `scripts/stage_windows_harness.py`
- Create: `backend/tests/test_stage_windows_harness.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces CLI: `py scripts/stage_windows_harness.py --repo-root <root> --destination <package-root> [--node-archive <zip>]`.
- Produces `runtime/node/node.exe`, `runtime/node/LICENSE`, `harness/dist`, production `harness/node_modules`, `harness/package.json`, `harness/THIRD_PARTY_LICENSES.json`, and `portable-manifest.json`.

- [ ] **Step 1: Write failing config, checksum, and ZIP-safety tests**

```python
config = load_runtime_config(CONFIG_PATH)
assert config.version == "22.23.2"
assert config.archive == "node-v22.23.2-win-x64.zip"
assert config.sha256 == "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97"

with pytest.raises(StageError, match="checksum"):
    verify_sha256(bad_archive, config.sha256)
with pytest.raises(StageError, match="unsafe archive member"):
    extract_node_runtime(traversal_zip, destination, config)
```

A valid fixture contains only the expected archive root, `node.exe`, and `LICENSE`; assert no npm CLI is copied. Run the test and expect missing module/config failure.

- [ ] **Step 2: Add the exact audited runtime config**

```json
{
  "platform": "windows",
  "arch": "x64",
  "node_version": "22.23.2",
  "node_archive": "node-v22.23.2-win-x64.zip",
  "node_url": "https://nodejs.org/download/release/v22.23.2/node-v22.23.2-win-x64.zip",
  "node_sha256": "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97",
  "koffi_binary": "harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node"
}
```

- [ ] **Step 3: Implement safe Node extraction**

Download to a temporary file only when `--node-archive` is absent. In either path, verify SHA-256 first. Reject absolute/drive paths, `..`, links, and archive roots other than `node-v22.23.2-win-x64`. Copy only `node.exe` and `LICENSE`.

- [ ] **Step 4: Write failing production-tree and manifest tests**

With an injected command runner, require these commands in separate generated work directories:

```python
assert commands == [
    [npm, "ci"],
    [npm, "run", "build"],
    [npm, "ci", "--omit=dev"],
]
assert (destination / "harness" / "dist" / "server.js").is_file()
assert (destination / config.koffi_binary).is_file()
assert not (destination / "harness" / "node_modules" / "tsx").exists()
assert not (destination / "harness" / "node_modules" / "typescript").exists()
```

- [ ] **Step 5: Implement Harness staging, licenses, and deterministic manifest**

Never prune checkout `node_modules`. Copy package/lock/source to a temporary build tree, run full install/build, then create a separate runtime tree with `npm ci --omit=dev`. Fail if Koffi is absent or a forbidden dev package remains.

Collect package `LICENSE*`, `COPYING*`, and `NOTICE*` files into JSON records containing package name, version, relative source path, and text. Emit no timestamp or machine path. Manifest shape:

```json
{
  "format": 1,
  "platform": "win-x64",
  "node": {"version": "22.23.2", "archive_sha256": "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97"},
  "harness": {"version": "0.1.0", "package_lock_sha256": "0dbdeb879eda86a6da3d9cd920c9ede913666e8b4127da28b6d5dc5a18545d65"},
  "entrypoint": "harness/dist/server.js"
}
```

- [ ] **Step 6: Verify a real stage and commit**

```powershell
Push-Location backend; try { py -m pytest tests/test_stage_windows_harness.py -q } finally { Pop-Location }
py scripts/stage_windows_harness.py --repo-root . --destination "$env:TEMP/director-harness-stage-check"
git add .gitignore packaging/windows-harness-runtime.json scripts/stage_windows_harness.py backend/tests/test_stage_windows_harness.py
git commit -m "build: stage Windows Harness portable runtime"
```

Run the staged private Node against staged `server.js` without a token and require the explicit missing-token exit. Remove only the resolved, exact test directory under the system temp root.

### Task 6: Update Package Assembly and Content Policy

**Files:**
- Rename: `scripts/build-legacy-portable.ps1` to `scripts/build-windows-portable.ps1`
- Rename: `scripts/verify-legacy-portable.ps1` to `scripts/verify-windows-portable.ps1`
- Modify: `scripts/verify_portable_contents.py`
- Modify: `backend/tests/test_portable_contents_verifier.py`
- Modify: `backend/tests/test_packaged_runtime_paths.py`
- Modify: `scripts/test-portable-verifier-slow-health.ps1`

**Interfaces:**
- Consumes: Task 5 staging CLI/manifest.
- Produces: `dist/Director-Studio-Windows-x64/` and its ZIP.
- Produces: `verify_windows_harness_runtime(root)` and `verify_portable_manifest(root)`.

- [ ] **Step 1: Write failing platform-aware policy tests**

Update the fixture helper to create parents for nested required files. Assert:

```python
windows = verifier.FLAVORS["windows"]
assert windows.name == "Director-Studio-Windows-x64"
assert "runtime/node/node.exe" in verifier.required_package_files(windows)
assert "harness/dist/server.js" in verifier.required_package_files(windows)
assert "portable-manifest.json" in verifier.required_package_files(windows)
assert "runtime/node/node.exe" not in verifier.required_package_files(verifier.FLAVORS["linux"])
```

Add rejection cases for absent Koffi, present `tsx`/TypeScript/Vitest, malformed manifest, wrong platform/checksum, absolute manifest paths, and Harness content in Linux packages. Run the focused verifier tests and require failure before implementation.

- [ ] **Step 2: Implement Windows runtime policy without weakening Linux policy**

Set the Windows flavor to `Director-Studio-Windows-x64`; require Node, license, server, package metadata, license index, manifest, and exact Koffi binary. Reject these runtime roots:

```python
WINDOWS_FORBIDDEN_DEV_PACKAGES = {
    "harness/node_modules/tsx",
    "harness/node_modules/typescript",
    "harness/node_modules/vitest",
    "harness/node_modules/@vitest",
}
```

Validate all digest fields with exactly 64 lowercase hex characters. Keep durable-state and credential rejection unchanged.

- [ ] **Step 3: Rename and wire the Windows scripts**

Use `git mv`. In the build script set `$packageName = "Director-Studio-Windows-x64"` and call:

```powershell
py (Join-Path $PSScriptRoot "stage_windows_harness.py") --repo-root $repoRoot --destination $packageRoot
if ($LASTEXITCODE -ne 0) { throw "Harness runtime staging failed" }
```

Stage after normal package files and before runtime verification/archive creation. Replace the old blanket Harness rejection with the Python allowlist verifier. Add all nested runtime files to archive assertions. Update live references in tests and README; do not rewrite historical plans/specs.

- [ ] **Step 4: Verify and commit**

```powershell
Push-Location backend; try { py -m pytest tests/test_portable_contents_verifier.py tests/test_packaged_runtime_paths.py -q } finally { Pop-Location }
pwsh -NoProfile -File scripts/test-portable-verifier-slow-health.ps1
git add scripts/build-windows-portable.ps1 scripts/verify-windows-portable.ps1 scripts/verify_portable_contents.py scripts/test-portable-verifier-slow-health.ps1 backend/tests/test_portable_contents_verifier.py backend/tests/test_packaged_runtime_paths.py README.md
git commit -m "build: assemble Windows Harness portable package"
```

### Task 7: Verify a Deterministic Offline Turn and Child Ownership

**Files:**
- Create: `scripts/verify_bundled_harness.py`
- Create: `backend/tests/test_verify_bundled_harness.py`
- Modify: `scripts/build-windows-portable.ps1`
- Modify: `scripts/verify-windows-portable.ps1`

**Interfaces:**
- Produces CLI: `py scripts/verify_bundled_harness.py --package-root <path> --port <port>`.
- Produces success only after reply `portable harness smoke test complete`.

- [ ] **Step 1: Write failing protocol-driver tests**

Drive a fake NDJSON server using:

```python
HOST_RESULTS = {
    "context": {"system": "portable smoke", "state": "empty", "tools": []},
    "llm": {"content": "portable harness smoke test complete", "thinking": "", "tool_calls": []},
}
```

For each request event, POST `{ok: true, data: HOST_RESULTS[method]}` to the response URL. Assert unknown method, Harness error, missing result, child exit, and timeout fail and clean up the exact child. Run the test and expect failure because the script is absent.

- [ ] **Step 2: Implement the offline packaged protocol driver**

Start the packaged Node with no `PATH`, npm configuration, proxy setting, or provider credential. Use a temporary session root, generated token, and supplied port. Send:

```json
{
  "message": "Return the smoke-test response.",
  "history": [],
  "session_id": "portable-smoke",
  "context_window": 4096,
  "max_steps": 3
}
```

Service only `context` and `llm`, read until result/error, assert the exact reply, and always stop the process and remove the exact temporary session root.

- [ ] **Step 3: Wire readiness and ownership checks into portable verification**

Run the offline driver on a distinct port before launching the main EXE. Then require:

```powershell
$runtime = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/director/runtime?check_sidecar=true" -TimeoutSec 5
if ($runtime.runtime -ne 'harness' -or $runtime.sidecar_ready -ne $true) {
    throw 'Packaged backend did not authenticate its managed Harness sidecar'
}
```

After normal application shutdown, wait five seconds and assert no process remains with `ExecutablePath` equal to packaged `runtime/node/node.exe`. Forced cleanup is permitted only in `finally`, after the verifier has recorded failure.

- [ ] **Step 4: Verify and commit**

```powershell
Push-Location backend; try { py -m pytest tests/test_verify_bundled_harness.py -q } finally { Pop-Location }
pwsh -NoProfile -File scripts/test-portable-process-cleanup.ps1
git add scripts/verify_bundled_harness.py backend/tests/test_verify_bundled_harness.py scripts/build-windows-portable.ps1 scripts/verify-windows-portable.ps1
git commit -m "test: verify bundled Harness offline"
```

### Task 8: Document and Verify the Release

**Files:**
- Modify: `docs/HARNESS.md`
- Modify: `README.md`
- Modify: `backend/.env.example`

**Interfaces:**
- Produces: complete Windows portable usage/recovery documentation and release evidence.

- [ ] **Step 1: Document exact user behavior**

State that Windows x64 portable includes private Node and prebuilt Harness; users run only `DirectorStudio.exe`. Document explicit Legacy mode, and external mode using `DS_HARNESS_MANAGED=false` plus loopback URL/token. Document `data/harness-sessions`, `data/logs/harness-sidecar.log`, and the no-silent-fallback rule. Add commented `DS_HARNESS_MANAGED=true` beside Harness settings.

- [ ] **Step 2: Run focused verification**

```powershell
Push-Location backend; try { py -m pytest tests/test_harness_launcher.py tests/test_portable_harness.py tests/test_packaged_entrypoint.py tests/test_stage_windows_harness.py tests/test_portable_contents_verifier.py tests/test_packaged_runtime.py tests/test_packaged_runtime_paths.py tests/test_verify_bundled_harness.py -q } finally { Pop-Location }
npm --prefix harness run typecheck
npm --prefix harness test
npm --prefix harness run build
```

Expected: all selected tests pass.

- [ ] **Step 3: Run the related regression suite**

```powershell
Push-Location backend; try { py -m pytest tests/test_harness_runtime.py tests/test_harness_integration.py tests/test_harness_session_recovery.py tests/test_director_model_runtime.py tests/test_llm_provider.py tests/test_projects_api.py tests/test_portable_tools_installer.py tests/test_portable_runtime_paths.py -q } finally { Pop-Location }
```

Expected: all selected tests pass. The two previously acknowledged unrelated native-audio failures, if seen only in the full backend suite, are reported separately and do not authorize portable-code changes.

- [ ] **Step 4: Build and inspect the real package**

```powershell
pwsh -NoProfile -File scripts/build-windows-portable.ps1
Get-Item dist/Director-Studio-Windows-x64.zip | Select-Object FullName,Length
Get-Content -Raw dist/Director-Studio-Windows-x64/portable-manifest.json
git diff --check
git status --short
```

Expected: EXE, private Node, compiled server, Koffi binary, manifest, and ZIP exist; runtime endpoint says Harness ready; offline turn returns the fixed smoke reply; no packaged Node process remains; generated artifacts are ignored.

- [ ] **Step 5: Commit docs and capture final evidence**

```powershell
git add README.md docs/HARNESS.md backend/.env.example
git commit -m "docs: document bundled Windows Harness runtime"
git log --oneline -8
git status --short --branch
```

Expected: task commits appear in order and the tracked working tree is clean.

## Self-Review

- Spec coverage: Tasks 2-8 cover package shape, pinned runtime, dependency layout, managed lifecycle, Legacy/external modes, diagnostics, security, persistence, offline operation, ownership cleanup, and documentation.
- Placeholder scan: every implementation boundary, failure case, command, and expected result is concrete; no deferred implementation markers remain.
- Type consistency: lifecycle type and function names are identical in Tasks 3 and 4; staging outputs are identical in Tasks 5-7.
