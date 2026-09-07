# Linux Portable Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, verify, document, and publish a native Ubuntu x86_64 Portable package with the same Director Studio business features and external-data contract as the Windows Portable package.

**Architecture:** Keep the React/FastAPI application and PyInstaller resource graph shared. Add a platform-aware private MCP installer, Linux launch/build/runtime-verification scripts, a cross-platform package-policy verifier, and an Ubuntu GitHub Actions pipeline that produces a tarball and checksum. Platform code never forks Director, Assets, Production, H3, Custom H3, job, or persistence behavior.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, pytest, React 19, TypeScript 5.8, Vitest, Bash, PyInstaller 6.22.2, GitHub Actions, Ubuntu 22.04, tar/gzip, SHA-256.

**Spec:** `docs/superpowers/specs/2026-09-06-linux-portable-design.md`

## Global Constraints

- Supported Linux targets are Ubuntu 22.04 and 24.04 on x86_64 only.
- Build the release binary on Ubuntu 22.04; do not cross-compile it from Windows.
- Linux and Windows must use the same frontend, backend, APIs, workflows, project format, job format, and Custom H3 runtime.
- Ollama and ComfyUI remain separately installed external services.
- The Portable installer creates only `<install-root>/tools/venv` and updates only `<install-root>/.env`; it never uses `sudo`.
- Durable application state stays below `<install-root>/data`; no durable state may use PyInstaller's extraction directory.
- The release archive contains the official five workflows and Director guides but no imported profiles, projects, jobs, outputs, tests, secrets, or generated media.
- A clean extraction defaults to Built-in Official H3.
- GitHub Actions performs CPU/package verification only; a real NVIDIA H3 run remains a separately reported manual acceptance test.
- ARM64, AppImage, package-manager formats, bundled services, systemd, desktop registration, and automatic updates are out of scope.

## Existing preflight commit

Commit `5617302` corrects `test_mobile_entrypoint.py` to patch `settings.frontend_dist`, the setting actually consumed by `create_app()`. The test was previously dependent on a leftover `frontend/dist` directory. Do not repeat or revert that change.

---

### Task 1: Make the private MCP installer platform-aware

**Files:**
- Modify: `Install-Tools.py`
- Create: `install-tools.sh`
- Modify: `backend/tests/test_portable_tools_installer.py`

**Interfaces:**
- Produces: `ToolPaths(python: Path, mcp: Path, comfy: Path)`.
- Produces: `resolve_tool_paths(install_root: Path, platform_name: str | None = None) -> ToolPaths`.
- Preserves: `install_dependencies(install_root: Path, requirements: Path) -> tuple[Path, Path]`.
- Produces: end-user command `./install-tools.sh`.

- [ ] **Step 1: Add failing Linux path and idempotent configuration tests**

Add a shared module loader and these cases to `backend/tests/test_portable_tools_installer.py`:

```python
def _load_installer():
    spec = importlib.util.spec_from_file_location("portable_tools_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)
    return installer


def test_linux_tool_paths_use_private_bin_directory(tmp_path: Path) -> None:
    installer = _load_installer()

    paths = installer.resolve_tool_paths(tmp_path, platform_name="posix")

    assert paths.python == tmp_path / "tools" / "venv" / "bin" / "python"
    assert paths.mcp == tmp_path / "tools" / "venv" / "bin" / "comfy-mcp"
    assert paths.comfy == tmp_path / "tools" / "venv" / "bin" / "comfy"


def test_windows_tool_paths_keep_scripts_executables(tmp_path: Path) -> None:
    installer = _load_installer()

    paths = installer.resolve_tool_paths(tmp_path, platform_name="nt")

    scripts = tmp_path / "tools" / "venv" / "Scripts"
    assert paths.python == scripts / "python.exe"
    assert paths.mcp == scripts / "comfy-mcp.exe"
    assert paths.comfy == scripts / "comfy.exe"


def test_linux_paths_survive_dotenv_parsing(tmp_path: Path) -> None:
    installer = _load_installer()
    env_file = tmp_path / ".env"
    mcp = tmp_path / "Director Studio" / "tools" / "venv" / "bin" / "comfy-mcp"
    comfy = tmp_path / "Director Studio" / "tools" / "venv" / "bin" / "comfy"

    installer.configure_env(
        env_file=env_file,
        template=TEMPLATE,
        mcp_command=mcp,
        comfy_command=comfy,
    )
    first = env_file.read_bytes()
    installer.configure_env(
        env_file=env_file,
        template=TEMPLATE,
        mcp_command=mcp,
        comfy_command=comfy,
    )

    configured = dotenv_values(env_file)
    assert configured["DS_COMFY_MCP_COMMAND"] == str(mcp)
    assert configured["DS_COMFY_MCP_COMFY_BIN"] == str(comfy)
    assert env_file.read_bytes() == first
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```text
cd backend
python -m pytest tests/test_portable_tools_installer.py -q
```

Expected: the new tests fail because `ToolPaths` and `resolve_tool_paths` do not exist and the implementation is hard-coded to Windows `Scripts/*.exe`.

- [ ] **Step 3: Implement platform-specific private-venv paths**

Add this platform boundary to `Install-Tools.py` and use it from `install_dependencies`:

```python
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ToolPaths:
    python: Path
    mcp: Path
    comfy: Path


def resolve_tool_paths(
    install_root: Path,
    platform_name: str | None = None,
) -> ToolPaths:
    selected = platform_name or os.name
    venv_root = install_root / "tools" / "venv"
    if selected == "nt":
        commands = venv_root / "Scripts"
        return ToolPaths(
            python=commands / "python.exe",
            mcp=commands / "comfy-mcp.exe",
            comfy=commands / "comfy.exe",
        )
    if selected == "posix":
        commands = venv_root / "bin"
        return ToolPaths(
            python=commands / "python",
            mcp=commands / "comfy-mcp",
            comfy=commands / "comfy",
        )
    raise RuntimeError(f"Unsupported operating system: {selected}")
```

`install_dependencies` must create the venv when `paths.python` is absent, install through that interpreter, verify both command files, run `python -c "import comfy_mcp"`, run `comfy --help`, and return `(paths.mcp, paths.comfy)`. It must continue avoiding `comfy-mcp --help`, because that entry point starts the stdio server.

- [ ] **Step 4: Add the Linux shell wrapper**

Create `install-tools.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python_bin="${DS_PYTHON_EXE:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python 3.11 or newer was not found. Install python3 and python3-venv, then run this script again." >&2
  exit 1
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Director Studio tools require Python 3.11 or newer." >&2
  exit 1
fi

exec "$python_bin" "$root_dir/Install-Tools.py"
```

Add a POSIX-only behavior test that runs the wrapper with `DS_PYTHON_EXE` pointing to a fake executable that records its arguments and exits 17. Assert that the wrapper returns 17 and invokes the sibling `Install-Tools.py`. Mark only this wrapper-process test with `pytest.mark.skipif(os.name == "nt", reason="requires POSIX process semantics")`; the Python path/configuration tests must run on both platforms.

- [ ] **Step 5: Run installer tests and the full backend suite**

Run:

```text
cd backend
python -m pytest tests/test_portable_tools_installer.py -q
python -m pytest -q
```

Expected: installer tests pass; full backend reports 811 or more passing tests with no failures.

- [ ] **Step 6: Commit the installer unit**

```text
git update-index --add --chmod=+x install-tools.sh
git add Install-Tools.py install-tools.sh backend/tests/test_portable_tools_installer.py
git commit -m "feat add Linux portable tools installer"
```

---

### Task 2: Add an attached Linux launcher with browser opening

**Files:**
- Create: `launch.sh`
- Create: `backend/tests/test_linux_portable_launcher.py`

**Interfaces:**
- Consumes: sibling executable `DirectorStudio` and optional `.env` containing `DS_PORT`.
- Consumes: process environment overrides `DS_PORT` and `DS_STARTUP_TIMEOUT_SEC`.
- Produces: foreground launcher exit status and an opened or printed UI URL.

- [ ] **Step 1: Write POSIX launcher behavior tests**

Create tests that are skipped on Windows and use a temporary package containing:

- a fake `DirectorStudio` shell program that runs `python3 -m http.server "$DS_PORT" --bind 127.0.0.1`;
- a fake `xdg-open` earlier on `PATH` that writes its single URL argument to a capture file;
- an `.env` containing `DS_PORT=<allocated-port>`;
- the repository `launch.sh`.

The main assertion is:

```python
assert _wait_for_text(open_capture) == f"http://127.0.0.1:{port}"
launcher.send_signal(signal.SIGTERM)
assert launcher.wait(timeout=10) == 143
assert not _can_connect(port)
```

Add a second fake executable that immediately exits 23 and assert the launcher exits non-zero without invoking `xdg-open`.

- [ ] **Step 2: Run the launcher tests and verify RED on Linux**

Run on a POSIX host:

```text
cd backend
python -m pytest tests/test_linux_portable_launcher.py -q
```

Expected: failure because `launch.sh` does not exist.

- [ ] **Step 3: Implement `launch.sh`**

The script must:

```bash
#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
executable="$root_dir/DirectorStudio"
if [[ ! -x "$executable" ]]; then
  echo "DirectorStudio is missing or is not executable: $executable" >&2
  exit 1
fi

port="${DS_PORT:-}"
if [[ -z "$port" && -f "$root_dir/.env" ]]; then
  port="$(sed -nE 's/^[[:space:]]*DS_PORT[[:space:]]*=[[:space:]]*"?([0-9]+)"?[[:space:]]*$/\1/p' "$root_dir/.env" | tail -n 1)"
fi
port="${port:-8790}"
timeout_sec="${DS_STARTUP_TIMEOUT_SEC:-60}"
url="http://127.0.0.1:$port"

child_pid=""
stop_child() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
}
trap 'stop_child; exit 143' INT TERM

(cd "$root_dir" && exec "$executable") &
child_pid=$!
deadline=$((SECONDS + timeout_sec))
while (( SECONDS < deadline )); do
  if ! kill -0 "$child_pid" 2>/dev/null; then
    wait "$child_pid"
    exit $?
  fi
  if curl --fail --silent --show-error "$url/api/health" >/dev/null 2>&1; then
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$url" >/dev/null 2>&1 || echo "Open $url in your browser."
    else
      echo "Open $url in your browser."
    fi
    wait "$child_pid"
    exit $?
  fi
  sleep 0.25
done

echo "Director Studio did not become healthy within $timeout_sec seconds." >&2
stop_child
exit 1
```

Use the executable's exit code for early-exit behavior. Preserve the explicit 143 exit for launcher termination.

- [ ] **Step 4: Verify launcher behavior and shell syntax on Linux**

Run:

```text
bash -n launch.sh
shellcheck launch.sh
cd backend
python -m pytest tests/test_linux_portable_launcher.py -q
```

Expected: all commands pass.

- [ ] **Step 5: Commit the launcher unit**

```text
git update-index --add --chmod=+x launch.sh
git add launch.sh backend/tests/test_linux_portable_launcher.py
git commit -m "feat add Linux portable launcher"
```

---

### Task 3: Centralize portable archive and embedded-resource policy

**Files:**
- Create: `scripts/verify_portable_contents.py`
- Create: `backend/tests/test_portable_contents_verifier.py`
- Modify: `scripts/build-legacy-portable.ps1`

**Interfaces:**
- Produces: `PackageFlavor(name: str, executable: str, wrappers: tuple[str, ...], archive_kind: Literal["zip", "tar"])`.
- Produces: `parse_pyinstaller_listing(text: str) -> set[str]`.
- Produces: `verify_embedded_entries(entries: set[str]) -> None`.
- Produces: `verify_package_tree(root: Path, flavor: PackageFlavor) -> None`.
- Produces: `verify_archive(archive: Path, package_root: Path, flavor: PackageFlavor) -> None`.
- Produces CLI: `python scripts/verify_portable_contents.py --platform windows|linux --package-root PATH --executable PATH --archive PATH`.

- [ ] **Step 1: Write failing pure-policy tests**

Cover these literal cases in `test_portable_contents_verifier.py`:

```python
def test_listing_parser_normalizes_windows_and_posix_entries():
    text = "\n".join([
        " 1, 2, 3, 1, 'b', 'workflows\\h3_ref2va.api.json'",
        "app/agents/director/DIRECTOR_SKILL.md",
    ])
    assert verifier.parse_pyinstaller_listing(text) == {
        "workflows/h3_ref2va.api.json",
        "app/agents/director/DIRECTOR_SKILL.md",
    }


def test_embedded_policy_rejects_imported_profile_state():
    entries = set(verifier.REQUIRED_EMBEDDED)
    entries.add("data/workflow_profiles/h3/profiles/custom/profile.json")
    with pytest.raises(ValueError, match="workflow_profiles"):
        verifier.verify_embedded_entries(entries)


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_clean_package_requires_platform_files(tmp_path: Path, platform: str):
    flavor = verifier.FLAVORS[platform]
    package = tmp_path / flavor.name
    package.mkdir()
    for name in verifier.required_package_files(flavor):
        (package / name).write_text("fixture", encoding="utf-8")
    verifier.verify_package_tree(package, flavor)


def test_package_tree_rejects_active_secret(tmp_path: Path):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    (package / ".env").write_text("DS_H3_MINIMAX_API_KEY=secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="active secret"):
        verifier.verify_package_tree(package, flavor)
```

Also test forbidden directory names (`data`, `projects`, `jobs`, `outputs`, `tests`, `workflow_profiles`), forbidden credential-like filenames/extensions, exactly one archive executable, top-level package containment, archive executable byte equality, tar symlink rejection, and both zip/tar member normalization.

- [ ] **Step 2: Run policy tests and verify RED**

Run:

```text
cd backend
python -m pytest tests/test_portable_contents_verifier.py -q
```

Expected: import failure because `scripts/verify_portable_contents.py` does not exist.

- [ ] **Step 3: Implement the cross-platform policy module**

Use exact immutable flavor and embedded-resource declarations:

```python
@dataclass(frozen=True)
class PackageFlavor:
    name: str
    executable: str
    wrappers: tuple[str, ...]
    archive_kind: Literal["zip", "tar"]


FLAVORS = {
    "windows": PackageFlavor(
        "Director-Studio-Legacy-Windows-x64",
        "DirectorStudio.exe",
        ("Install-Tools.cmd",),
        "zip",
    ),
    "linux": PackageFlavor(
        "Director-Studio-Linux-x86_64",
        "DirectorStudio",
        ("install-tools.sh", "launch.sh"),
        "tar",
    ),
}

REQUIRED_EMBEDDED = {
    "workflows/qwen_actor_asset_workbench.api.json",
    "workflows/qwen_prop_master.api.json",
    "workflows/QwenEdit2511_MultiAngle_SceneRef.api.json",
    "workflows/ref_frame_layout.api.json",
    "workflows/h3_ref2va.api.json",
    "app/agents/director/DIRECTOR_SKILL.md",
    "app/agents/director/guides/script-planning.md",
    "app/agents/director/guides/storyboard-validation.md",
    "app/agents/director/guides/reference-strategy.md",
    "app/agents/director/guides/reference-frame-generation.md",
    "app/agents/director/guides/visual-qc.md",
    "app/agents/director/guides/h3-prompt-writing.md",
    "app/agents/director/guides/video-qc.md",
}
```

`required_package_files` returns the executable, flavor wrappers, `Install-Tools.py`, `portable-tools-requirements.txt`, `.env`, and `README.md`.

Implement `parse_pyinstaller_listing` by normalizing backslashes, accepting the archive viewer's final quoted field or a single bare path, and discarding diagnostic lines. `verify_embedded_entries` requires every literal above and rejects path segments representing durable/test state without rejecting the Python source package named `app/workflow_profiles`.

Implement zip inspection with `zipfile.ZipFile(archive)` and tar inspection with `tarfile.open(archive, "r:gz")`. Reject absolute members, `..` traversal, links, devices, and members outside the expected top-level package. Stream-hash the archived executable and compare it with the built executable.

Run `PyInstaller.utils.cliutils.archive_viewer` through `sys.executable -m` in the CLI, parse stdout, and fail with a concise message plus non-zero status.

- [ ] **Step 4: Use the shared verifier from the Windows build**

After Windows zip creation, add:

```powershell
py (Join-Path $PSScriptRoot "verify_portable_contents.py") `
    --platform windows `
    --package-root $packageRoot `
    --executable $builtExe `
    --archive $zipPath
if ($LASTEXITCODE -ne 0) { throw "Cross-platform package policy verification failed" }
```

Keep the existing Windows-specific runtime/process verifier. Existing PowerShell embedded checks may remain during this release; they provide independent compatibility coverage while the shared verifier becomes authoritative for both package formats.

- [ ] **Step 5: Run verifier, packaging, and full backend tests**

Run:

```text
cd backend
python -m pytest tests/test_portable_contents_verifier.py tests/test_packaged_runtime_paths.py tests/test_portable_runtime_paths.py -q
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the package-policy unit**

```text
git add scripts/verify_portable_contents.py scripts/build-legacy-portable.ps1 backend/tests/test_portable_contents_verifier.py
git commit -m "build centralize portable package policy"
```

---

### Task 4: Add Linux packaged-runtime verification

**Files:**
- Create: `scripts/verify_linux_portable.py`
- Create: `backend/tests/test_linux_portable_verifier.py`

**Interfaces:**
- Produces: `wait_for_health(process: subprocess.Popen[bytes], url: str, timeout_sec: float) -> dict[str, Any]`.
- Produces: `verify_runtime(package_root: Path, port: int, timeout_sec: float) -> dict[str, Any]`.
- Produces CLI: `python scripts/verify_linux_portable.py --package-root PATH --port PORT --timeout-sec 60`.

- [ ] **Step 1: Write failing process and HTTP contract tests**

Use a fake executable Python script in a temporary package. It must serve:

- `/api/health` with `{"ok": true}`;
- `/api/workflow-profiles/h3` with active profile `builtin-official-h3` and source `builtin`;
- `/`, `/mobile`, and `/docs` with HTTP 200.

Tests assert that `verify_runtime`:

```python
result = verifier.verify_runtime(package, port, timeout_sec=10)
assert result["health"]["ok"] is True
assert result["active_h3"] == "builtin-official-h3"
assert result["frontend_status"] == 200
assert result["mobile_status"] == 200
assert result["docs_status"] == 200
assert not _process_with_executable(package / "DirectorStudio")
assert not (package / "data").exists()
```

The fake creates `data/` only in its working directory. This proves the verifier runs a copied package below system temp and leaves the source package unchanged. Add early-exit and health-timeout tests and assert the owned process group is gone afterward.

- [ ] **Step 2: Run verifier tests and verify RED on Linux**

Run:

```text
cd backend
python -m pytest tests/test_linux_portable_verifier.py -q
```

Expected: import failure because the verifier does not exist.

- [ ] **Step 3: Implement safe Linux runtime verification**

The verifier must:

```python
with tempfile.TemporaryDirectory(prefix="director-studio-linux-verify-") as temp:
    runtime_root = Path(temp) / "package"
    shutil.copytree(package_root, runtime_root)
    executable = runtime_root / "DirectorStudio"
    env = os.environ.copy()
    env.update({"DS_HOST": "127.0.0.1", "DS_PORT": str(port)})
    process = subprocess.Popen(
        [str(executable)],
        cwd=runtime_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
```

Poll with `urllib.request.urlopen` at 250 ms intervals. Treat process exit as immediate failure and include decoded trailing stdout/stderr. After health succeeds, fetch the root, `/mobile`, `/docs`, and `/api/workflow-profiles/h3`; require the official built-in active profile.

In `finally`, send `SIGTERM` to `os.getpgid(process.pid)`, wait up to five seconds, then send `SIGKILL` only to that owned process group if necessary. Never identify or stop processes by port or name.

- [ ] **Step 4: Run focused verifier tests**

Run:

```text
cd backend
python -m pytest tests/test_linux_portable_verifier.py -q
```

Expected: all tests pass on Linux; the module imports without side effects on Windows.

- [ ] **Step 5: Commit the runtime-verifier unit**

```text
git add scripts/verify_linux_portable.py backend/tests/test_linux_portable_verifier.py
git commit -m "build verify Linux portable runtime"
```

---

### Task 5: Build the native Ubuntu package

**Files:**
- Create: `scripts/build-linux-portable.sh`
- Create: `backend/tests/test_linux_build_safety.py`
- Modify: `backend/packaging/director-studio-legacy.spec`

**Interfaces:**
- Consumes: Tasks 1-4 scripts and shared PyInstaller spec.
- Produces: `dist/Director-Studio-Linux-x86_64/`.
- Produces: `dist/Director-Studio-Linux-x86_64.tar.gz`.
- Produces: `dist/Director-Studio-Linux-x86_64.tar.gz.sha256`.

- [ ] **Step 1: Write failing build-safety tests**

Load shell helpers in a subprocess with `DS_BUILD_TEST_MODE=1`. Assert:

- non-Linux `uname -s` is rejected;
- non-x86_64 `uname -m` is rejected;
- the cleanup helper rejects `/`, the checkout root, and paths outside the checkout;
- package filenames equal the interface above;
- the copied Linux wrappers are required and executable.

The production script must stop after defining functions when `DS_BUILD_TEST_MODE=1`, allowing tests to call `validate_generated_path` without running npm or PyInstaller.

- [ ] **Step 2: Run build-safety tests and verify RED**

Run on Linux:

```text
cd backend
python -m pytest tests/test_linux_build_safety.py -q
```

Expected: failure because `scripts/build-linux-portable.sh` does not exist.

- [ ] **Step 3: Keep the PyInstaller spec platform-neutral**

Do not duplicate the spec. Retain `name="DirectorStudio"`, shared datas, shared hidden imports, and external durable-state comments. Add no Windows-only binaries. If PyInstaller emits a Linux warning for `upx=True`, set:

```python
upx=False
```

for both platforms rather than branching the resource graph; package correctness takes priority over executable compression.

- [ ] **Step 4: Implement the Linux build script**

Start with:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
frontend_root="$repo_root/frontend"
backend_root="$repo_root/backend"
build_root="$repo_root/build"
pyinstaller_root="$build_root/pyinstaller"
pyinstaller_dist="$build_root/pyinstaller-dist"
dist_root="$repo_root/dist"
package_name="Director-Studio-Linux-x86_64"
package_root="$dist_root/$package_name"
archive="$dist_root/$package_name.tar.gz"
checksum="$archive.sha256"
verification_port="${DS_LINUX_VERIFICATION_PORT:-18791}"
```

Require `uname -s == Linux` and `uname -m == x86_64`. `validate_generated_path` must resolve the candidate and require its parent to remain below the resolved repository root, while rejecting the repository root itself. Only validated `build_root` and `dist_root` may be removed recursively.

Execute, with explicit exit-on-failure behavior:

```text
npm ci
npm test -- --run
npm run build
python -m pytest -q
python -m PyInstaller --clean --noconfirm --workpath "$pyinstaller_root" --distpath "$pyinstaller_dist" "$backend_root/packaging/director-studio-legacy.spec"
file build/pyinstaller-dist/DirectorStudio
```

Require `file` output to contain both `ELF 64-bit` and `x86-64`.

Copy `DirectorStudio`, `launch.sh`, `install-tools.sh`, `Install-Tools.py`, `portable-tools-requirements.txt`, `backend/.env.example` as `.env`, and `README.md` into the package. Apply mode 0755 to the binary and shell scripts and 0644 to configuration/documentation files.

Run:

```text
python scripts/verify_linux_portable.py --package-root "$package_root" --port "$verification_port" --timeout-sec 60
tar -C "$dist_root" -czf "$archive" "$package_name"
python scripts/verify_portable_contents.py --platform linux --package-root "$package_root" --executable "$package_root/DirectorStudio" --archive "$archive"
(cd "$dist_root" && sha256sum "$package_name.tar.gz" > "$package_name.tar.gz.sha256")
```

Print a final JSON object containing absolute package, archive, checksum, SHA-256, and byte size.

- [ ] **Step 5: Run shell and focused safety verification**

Run on Linux:

```text
bash -n scripts/build-linux-portable.sh
shellcheck scripts/build-linux-portable.sh install-tools.sh launch.sh
cd backend
python -m pytest tests/test_linux_build_safety.py tests/test_portable_contents_verifier.py tests/test_linux_portable_verifier.py -q
```

Expected: all checks pass.

- [ ] **Step 6: Build and verify a real Linux package**

Run on Ubuntu 22.04 x86_64 with build dependencies installed:

```text
./scripts/build-linux-portable.sh
```

Expected: the executable starts, all content/runtime checks pass, and the archive plus checksum exist under `dist/`.

- [ ] **Step 7: Commit the Linux build unit**

```text
git update-index --add --chmod=+x scripts/build-linux-portable.sh
git add scripts/build-linux-portable.sh backend/tests/test_linux_build_safety.py backend/packaging/director-studio-legacy.spec
git commit -m "build add Ubuntu portable package"
```

---

### Task 6: Automate Linux builds and releases with GitHub Actions

**Files:**
- Create: `.github/workflows/linux-portable.yml`

**Interfaces:**
- Consumes: `scripts/build-linux-portable.sh`.
- Produces: seven-day Actions artifact `director-studio-linux-x86_64` on manual and tag runs.
- Produces: release assets `Director-Studio-Linux-x86_64.tar.gz` and `.sha256` for `v*` tags.

- [ ] **Step 1: Create the workflow with read-only build permissions**

Use this job boundary:

```yaml
name: Linux Portable

on:
  pull_request:
  workflow_dispatch:
  push:
    tags:
      - "v*"

jobs:
  build:
    runs-on: ubuntu-22.04
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install system checks
        run: sudo apt-get update && sudo apt-get install --yes shellcheck
      - name: Install Python dependencies
        run: python -m pip install -r backend/requirements.txt -r backend/requirements-build.txt
      - name: Build and verify Linux Portable
        run: ./scripts/build-linux-portable.sh
      - name: Upload Linux Portable
        if: github.event_name != 'pull_request'
        uses: actions/upload-artifact@v4
        with:
          name: director-studio-linux-x86_64
          retention-days: 7
          if-no-files-found: error
          path: |
            dist/Director-Studio-Linux-x86_64.tar.gz
            dist/Director-Studio-Linux-x86_64.tar.gz.sha256
```

- [ ] **Step 2: Add the tag-only release job**

Use a separate job so pull-request execution never receives write permission:

```yaml
  release:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: build
    runs-on: ubuntu-22.04
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: director-studio-linux-x86_64
          path: dist
      - name: Publish release assets
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release view "$GITHUB_REF_NAME" >/dev/null 2>&1 || gh release create "$GITHUB_REF_NAME" --generate-notes
          gh release upload "$GITHUB_REF_NAME" \
            dist/Director-Studio-Linux-x86_64.tar.gz \
            dist/Director-Studio-Linux-x86_64.tar.gz.sha256 \
            --clobber
```

- [ ] **Step 3: Validate workflow syntax and permissions**

Run a YAML parser locally and inspect the rendered workflow:

```text
python -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('.github/workflows/linux-portable.yml').read_text())"
```

Expected: parse succeeds; only the tag-gated `release` job has `contents: write`; no secrets other than `github.token` are referenced.

- [ ] **Step 4: Commit CI automation**

```text
git add .github/workflows/linux-portable.yml
git commit -m "ci build Linux portable releases"
```

---

### Task 7: Document Linux installation and parity

**Files:**
- Modify: `README.md`
- Modify: `backend/.env.example`

**Interfaces:**
- Documents: `./install-tools.sh`, `./launch.sh`, direct execution, supported platforms, external dependencies, Custom H3 parity, and CPU-versus-GPU verification status.

- [ ] **Step 1: Generalize the environment-template comment**

Change the opening comment from Windows-specific wording to:

```text
# Local service configuration. Source checkouts copy this file to backend/.env;
# Portable builds publish it as .env beside the DirectorStudio executable.
```

Do not change any environment default or add required model/VRAM configuration.

- [ ] **Step 2: Add the Linux Portable README section**

Place it after Windows installation and before shared Custom H3 guidance. It must state:

```text
Supported: Ubuntu 22.04 or 24.04, x86_64.
External services: Ollama and ComfyUI.

tar -xzf Director-Studio-Linux-x86_64.tar.gz
cd Director-Studio-Linux-x86_64
chmod +x DirectorStudio install-tools.sh launch.sh
```

Then instruct the user to edit `.env`, run `./install-tools.sh`, start Ollama and ComfyUI, and run `./launch.sh`. Document `./DirectorStudio` as the no-browser-launch alternative.

Explain that Actor, Costume, Scene, Prop, Layout, official H3, MiniMax API, and runtime Custom H3 behavior match Windows. Reuse the existing Custom H3 section rather than duplicating its workflow-mapping instructions.

Troubleshooting must include:

- `python3` 3.11+ and Ubuntu's `python3-venv` package;
- `curl` and `xdg-open` behavior;
- occupied port 8790;
- executable permission recovery after non-tar transfer;
- ComfyUI/Ollama base URL checks;
- custom nodes/models remaining the user's ComfyUI responsibility;
- GitHub Actions artifact being CPU/package-verified until the manual NVIDIA checklist is completed.

- [ ] **Step 3: Run documentation-sensitive tests and scans**

Run:

```text
cd backend
python -m pytest tests/test_portable_tools_installer.py tests/test_packaged_runtime_paths.py tests/test_portable_runtime_paths.py -q
cd ..
if git grep -n "Windows portable build publishes" -- backend/.env.example; then exit 1; fi
git diff --check
```

Expected: tests pass; the obsolete Windows-only comment search returns no matches; diff check passes.

- [ ] **Step 4: Commit Linux documentation**

```text
git add README.md backend/.env.example
git commit -m "docs add Linux portable installation"
```

---

### Task 8: Complete local and hosted release verification

**Files:**
- Modify only if a verification failure identifies a defect in an earlier task; use a new failing regression test before each correction.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified branch, successful GitHub Actions run, downloadable Linux archive/checksum, and an explicit GPU-validation status.

- [ ] **Step 1: Run all local platform-neutral tests**

Run:

```text
cd backend
python -m pytest -q
cd ../frontend
npm test -- --run
npm run build
cd ..
git diff --check
```

Expected: backend has at least 811 passing tests and no failures; frontend has at least 188 passing tests; production build and diff check succeed.

- [ ] **Step 2: Rebuild the Windows Portable regression artifact**

On Windows run:

```text
pwsh -NoProfile -File scripts/build-legacy-portable.ps1
```

Expected: Windows tests, PyInstaller build, package startup, embedded workflow/guide checks, shared package-policy checks, zip creation, and H3 isolation checks pass.

- [ ] **Step 3: Push the feature branch and observe the Ubuntu build**

Push `codex/linux-portable`, open a PR against `main`, and wait for the `Linux Portable / build` check. Do not claim Linux packaging success from Windows tests alone.

- [ ] **Step 4: Trigger and download the manual artifact**

Run the workflow with `workflow_dispatch`, download `director-studio-linux-x86_64`, and verify:

```text
sha256sum --check Director-Studio-Linux-x86_64.tar.gz.sha256
tar -tzf Director-Studio-Linux-x86_64.tar.gz
```

Expected: checksum passes; every member is under one `Director-Studio-Linux-x86_64/` directory; required files are present; forbidden data is absent.

- [ ] **Step 5: Record the true acceptance status**

If no Ubuntu NVIDIA workstation has completed the ten-step manual acceptance test from the spec, release notes must say:

```text
Linux package and CPU-level runtime verified on Ubuntu 22.04 GitHub Actions. Local NVIDIA/ComfyUI workflow generation still requires community hardware validation.
```

Only replace that sentence with GPU-validated wording after recording the Ubuntu version, GPU, ComfyUI version, built-in H3 result, and Custom H3 result.

- [ ] **Step 6: Commit any verification-only metadata and request review**

If no tracked metadata changes are needed, do not create an empty commit. Request code review with the test totals, Windows artifact result, GitHub Actions URL, archive SHA-256, and GPU-validation status.
