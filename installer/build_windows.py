#!/usr/bin/env python3
"""Manifest-driven builder for the Director Studio Windows x64 installer.

    python installer/build_windows.py lock  --variant cu128          # (re)generate Windows uv locks
    python installer/build_windows.py stage --variant cu128 [--out DIR]  # staging tree (runs on Linux too)
    python installer/build_windows.py check --root DIR               # static validation of a staging tree
    python installer/build_windows.py build --variant cu128          # stage + check + ISCC (Windows / CI)

Same layout and same pinned components as the Linux package (installer/build_linux.py),
for ``windows-x64``:

    <app>\\director-studio.pyw          launcher entry (pythonw: no console) -> app.launcher
    <app>\\DirectorStudio.cmd / .vbs    console launcher / hidden-start fallback
    <app>\\ds-install.json              marker + package defaults
    <app>\\backend, frontend\\dist        DS
    <app>\\harness, runtime\\node        Harness + Node 22 win-x64 (Koffi win32-x64 binary)
    <app>\\runtime\\python               PBS 3.13 msvc (backend) + runtime\\comfy-tools, runtime\\bin\\*.cmd
    <app>\\runtime\\ComfyUI              pinned ComfyUI + KJNodes [+ H3 continuation] + ds_nodes
    <app>\\runtime\\comfy-python         PBS 3.13 msvc + torch (cu128 | cu130 | cpu)
    <app>\\runtime\\llama-swap           llama-swap (MIT)
    <app>\\runtime\\llama.cpp            llama-server (MIT), CUDA build + cudart per variant
    <app>\\runtime\\ffmpeg\\bin           BtbN win64 LGPL ffmpeg.exe / ffprobe.exe
    <app>\\THIRD_PARTY_NOTICES.txt, SOURCE_OFFER.txt, licenses\\

ComfyUI choice: pinned source + python-build-standalone + hash-locked uv installs,
*not* the official ComfyUI_windows_portable 7z.  Reasons: (1) one reproducible,
``--require-hashes`` dependency set per variant, shared with Linux (same torch pins,
same ComfyUI commit, KJNodes/H3 deps in the same lock) instead of an opaque
python_embeded with unlocked extra installs on top; (2) the whole tree can be
staged and verified on a Linux build host (uv installs win_amd64 wheels with
``--python-platform``), no 7-Zip/py7zr; (3) variant choice (cu128/cu130/cpu)
and no duplicate ComfyUI/update scripts; (4) the portable's embeddable Python
(``._pth``, no venv/pip by default) is awkward to extend.  Cost: we track
ComfyUI's Windows deps ourselves (the lock does it) and must test on real Windows.

Installer: Inno Setup (installer/windows/DirectorStudio.iss), per-user install to
%LOCALAPPDATA%\\Programs\\DirectorStudio, data in %LOCALAPPDATA%\\DirectorStudio
(never touched by the uninstaller), DiskSpanning for the >2 GB payload.
GPL/AGPL programs are only bundled and executed as separate processes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import build_linux as common
import fetch
import notices

MANIFEST = common.MANIFEST
LOCKS = HERE / "locks"
PLATFORM = "windows-x64"
UV_PLATFORM = MANIFEST["windows"]["uv_platform"]
PY_VERSION = common.PY_VERSION
WIN = HERE / "windows"
ON_WINDOWS = os.name == "nt"
# Windows MAX_PATH is 260; the install root is about
# "C:\Users\<user>\AppData\Local\Programs\DirectorStudio\" (~55-70 chars).
INSTALL_PREFIX_BUDGET = 80
MAX_PATH = 260

log = common.log
run = common.run


# ---- locks --------------------------------------------------------------------------------

def lock_path(name: str) -> Path:
    return LOCKS / f"windows-{name}.txt"


def compile_lock(name: str, text: str, *, torch_backend: str | None = None) -> Path:
    LOCKS.mkdir(parents=True, exist_ok=True)
    source = LOCKS / f"windows-{name}.in"
    source.write_text(text, encoding="utf-8")
    out = lock_path(name)
    cmd = [common.uv(), "pip", "compile", source, "--python-version", PY_VERSION, "--python-platform", UV_PLATFORM,
           "--generate-hashes", "--no-header", "--output-file", out, "--quiet", "--only-binary", ":all:"]
    if torch_backend:
        cmd += ["--torch-backend", torch_backend]
    run(cmd)
    return out


def cmd_lock(args: argparse.Namespace) -> None:
    compile_lock("backend", common.backend_requirements_in())
    compile_lock("comfy-tools", common.tools_requirements_in())
    for variant in args.variant:
        compile_lock(f"comfy-{variant}", common.comfy_requirements_in(), torch_backend=variant)
    log(f"Windows locks written to {LOCKS}")


def ensure_lock(name: str, text_fn, torch_backend: str | None = None) -> Path:
    path = lock_path(name)
    if not path.is_file():
        compile_lock(name, text_fn(), torch_backend=torch_backend)
    return path


# ---- installs -------------------------------------------------------------------------

def site_packages(python_root: Path) -> Path:
    return python_root / "Lib" / "site-packages"


def uv_install_command(python_root: Path, lock: Path, *, target: Path | None = None,
                       torch_backend: str | None = None, native: bool = ON_WINDOWS) -> list:
    """uv command installing ``lock`` for the Windows interpreter at ``python_root``.

    On Windows the bundled python.exe is used directly; on another host the wheels
    are installed for x86_64-pc-windows-msvc into ``Lib\\site-packages`` (or
    ``target``) - pure file placement, no code from the packages is executed.
    """
    cmd: list = [common.uv(), "pip", "install", "--require-hashes", "--no-deps", "--link-mode", "copy",
                 "--only-binary", ":all:", "-r", lock]
    if native:
        cmd += ["--python", python_root / "python.exe", "--compile-bytecode"]
        cmd += ["--target", target] if target is not None else ["--system", "--break-system-packages"]
    else:
        cmd += ["--python-platform", UV_PLATFORM, "--python-version", PY_VERSION,
                "--target", target if target is not None else site_packages(python_root)]
    if torch_backend:
        cmd += ["--torch-backend", torch_backend]
    return cmd


def pip_install(python_root: Path, lock: Path, *, target: Path | None = None, torch_backend: str | None = None) -> None:
    run(uv_install_command(python_root, lock, target=target, torch_backend=torch_backend),
        env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"})
    if not ON_WINDOWS:
        # console-script shims generated for the build host are useless on Windows
        shutil.rmtree((target or site_packages(python_root)) / "bin", ignore_errors=True)


def install_pbs(dest: Path, cache: Path) -> Path:
    fetch.install_component(MANIFEST["python"][PLATFORM], dest, cache=cache)
    exe = dest / "python.exe"
    if not exe.is_file() or not (dest / "pythonw.exe").is_file():
        raise SystemExit(f"python-build-standalone (windows) layout unexpected: {exe} missing")
    for marker in (dest / "Lib" / "EXTERNALLY-MANAGED", dest / "EXTERNALLY-MANAGED"):
        marker.unlink(missing_ok=True)
    return exe


def _native_smoke(cmd: list) -> None:
    if ON_WINDOWS:
        run(cmd)


# ---- steps --------------------------------------------------------------------------

def stage_harness(root: Path, node_bin: Path, cache: Path) -> None:
    env = {**os.environ, "PATH": f"{node_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    runtimes = json.loads((REPO / "packaging" / "portable-harness-runtimes.json").read_text(encoding="utf-8"))
    node = runtimes["runtimes"][PLATFORM]
    archive = fetch.cached_download(node["url"], node["sha256"], cache=cache, name=node["archive"])
    cmd = [sys.executable, REPO / "scripts" / "stage_portable_harness.py", "--repo-root", REPO,
           "--destination", root, "--target", PLATFORM, "--node-archive", archive]
    if not ON_WINDOWS:
        cmd.append("--cross-platform")
    run(cmd, env=env)
    manifest_path = root / "portable-manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for legacy in ("python", "comfy_bootstrap"):  # fields of the legacy embeddable-Python portable
        data.pop(legacy, None)
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


CMD_WRAPPER = (
    "@echo off\r\n"
    "rem {name}: GPL tool executed as a separate process; kept out of the backend's site-packages.\r\n"
    "setlocal\r\n"
    "set \"PYTHONPATH=%~dp0..\\comfy-tools\"\r\n"
    "set PYTHONNOUSERSITE=1\r\n"
    "\"%~dp0..\\python\\python.exe\" -s -c \"{code}\" %*\r\n"
    "exit /b %ERRORLEVEL%\r\n"
)
WRAPPERS = {
    "comfy-mcp": "import sys; from comfy_mcp.server import main; sys.argv[0] = 'comfy-mcp'; sys.exit(main())",
    "comfy": "import sys; from comfy_cli.__main__ import main; sys.argv[0] = 'comfy'; sys.exit(main())",
}


def stage_backend_python(root: Path, cache: Path) -> None:
    runtime = root / "runtime"
    python_root = runtime / "python"
    install_pbs(python_root, cache)
    pip_install(python_root, ensure_lock("backend", common.backend_requirements_in))
    pip_install(python_root, ensure_lock("comfy-tools", common.tools_requirements_in), target=runtime / "comfy-tools")
    bindir = runtime / "bin"
    bindir.mkdir(exist_ok=True)
    for name, code in WRAPPERS.items():
        (bindir / f"{name}.cmd").write_bytes(CMD_WRAPPER.format(name=name, code=code).encode("ascii"))
    _native_smoke([python_root / "python.exe", "-s", "-c",
                   "import fastapi, uvicorn, httpx, psutil, websockets, pydantic_settings, mcp, mcp.client.stdio; print('backend deps ok')"])


def stage_binaries(root: Path, variant: str, cache: Path) -> None:
    fetch.install_component(MANIFEST["llama_swap"][PLATFORM], root / "runtime" / "llama-swap", cache=cache)
    fetch.install_component(MANIFEST["llama_cpp"][PLATFORM], root / "runtime" / "llama.cpp", cache=cache, variant=variant)
    fetch.install_component(MANIFEST["ffmpeg"][PLATFORM], root / "runtime" / "ffmpeg", cache=cache)
    _native_smoke([root / "runtime" / "llama-swap" / "llama-swap.exe", "-version"])
    _native_smoke([root / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe", "-hide_banner", "-version"])


def stage_comfy(root: Path, variant: str, cache: Path, without: tuple[str, ...] = ()) -> None:
    runtime = root / "runtime"
    c = MANIFEST["comfyui"]
    comfy = runtime / "ComfyUI"
    common.git_checkout(c["repo"], c["commit"], comfy, tag=c.get("tag"))
    for node in common.selected_custom_nodes(without):
        common.git_checkout(node["repo"], node["commit"], comfy / "custom_nodes" / node["name"])
    shutil.copytree(REPO / "comfy_nodes" / "ds_nodes", comfy / "custom_nodes" / "ds_nodes",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for writable in ("output", "input", "temp", "user"):
        shutil.rmtree(comfy / writable, ignore_errors=True)
    python_root = runtime / "comfy-python"
    install_pbs(python_root, cache)
    lock = ensure_lock(f"comfy-{variant}", common.comfy_requirements_in, torch_backend=variant)
    pip_install(python_root, lock, torch_backend=variant)
    _native_smoke([python_root / "python.exe", "-c",
                   "import torch, numpy, aiohttp, cv2; print('torch', torch.__version__, 'cuda', torch.version.cuda)"])


def stage_launcher(root: Path) -> None:
    for name in ("director-studio.pyw", "DirectorStudio.cmd", "DirectorStudio.vbs", "director-studio.ico",
                 "README-INSTALL.md"):
        shutil.copy2(WIN / name, root / name)


BYTECODE_DIRS = ("runtime/python/Lib", "runtime/comfy-python/Lib", "runtime/comfy-tools", "runtime/ComfyUI",
                 "backend/app", "comfy_nodes")
# test suites shipped inside wheels are never imported at runtime
BYTECODE_SKIP = r"[\\/](tests?|testing|idlelib|turtledemo|lib2to3)[\\/]"


def precompile_command(root: Path, python: str = sys.executable) -> list:
    """compileall for the Windows tree, run with a host CPython of the same minor version.

    Bytecode is platform independent; ``checked-hash`` pycs are validated against the
    source hash instead of mtimes, so they stay valid whatever timestamps the installer
    writes, and a changed .py is still recompiled.
    """
    dirs = [root / d for d in BYTECODE_DIRS if (root / d).is_dir()]
    return [python, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "checked-hash",
            "-x", BYTECODE_SKIP, *dirs]


def stage_bytecode(root: Path) -> None:
    if f"{sys.version_info.major}.{sys.version_info.minor}" != PY_VERSION:
        log(f"skip bytecode: build Python {sys.version.split()[0]} != bundled {PY_VERSION} (pyc magic differs)")
        return
    # compileall exits 1 when a (vendored, unused) file has a syntax error; that is not fatal
    result = subprocess.run([str(c) for c in precompile_command(root)], check=False, capture_output=True, text=True)
    errors = [line for line in result.stdout.splitlines() if line.startswith("***")]
    log(f"bytecode compiled (exit {result.returncode}, {len(errors)} files skipped)")


def install_defaults(variant: str) -> dict[str, str]:
    defaults = {
        "DS_COMFY_MODE": "managed",
        "DS_LLAMA_SWAP_MODE": "bundled",
        "DS_LLM_PROVIDER": "llama-swap",
        "DS_DIRECTOR_AGENT_RUNTIME": "harness",
        "DS_HARNESS_MANAGED": "true",
        # backslashes: cmd.exe misreads "/runtime" in a batch path as a switch
        "DS_COMFY_MCP_COMMAND": "${INSTALL_ROOT}\\runtime\\bin\\comfy-mcp.cmd",
        "DS_COMFY_MCP_COMFY_BIN": "${INSTALL_ROOT}\\runtime\\bin\\comfy.cmd",
    }
    if variant == "cpu":
        defaults["DS_COMFY_DEV_CPU"] = "true"
    return defaults


def write_marker(root: Path, variant: str, version: str, without: tuple[str, ...] = ()) -> dict:
    marker = {
        "schema": 1,
        "product": MANIFEST["product"],
        "version": version,
        "platform": PLATFORM,
        "variant": variant,
        "torch": MANIFEST["torch_variants"][variant],
        "built_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "source": common.git_describe(),
        "components": {
            "python": MANIFEST["python"]["version"],
            "comfyui": f"{MANIFEST['comfyui']['tag']} ({MANIFEST['comfyui']['commit'][:12]})",
            "custom_nodes": {n["name"]: n["commit"][:12] for n in common.selected_custom_nodes(without)},
            "llama_cpp": f"{MANIFEST['llama_cpp']['version']} ({MANIFEST['llama_cpp']['release']})",
            "llama_swap": MANIFEST["llama_swap"]["version"],
            "ffmpeg": MANIFEST["ffmpeg"]["version"],
            "comfy_tools": {k: v for k, v in MANIFEST["comfy_tools"].items() if k != "license"},
        },
        "defaults": install_defaults(variant),
    }
    (root / "ds-install.json").write_text(json.dumps(marker, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return marker


# ---- static validation ---------------------------------------------------------------

REQUIRED_FILES = (
    "ds-install.json", "director-studio.pyw", "DirectorStudio.cmd", "DirectorStudio.vbs", "director-studio.ico",
    "THIRD_PARTY_NOTICES.txt", "SOURCE_OFFER.txt", "portable-manifest.json",
    "backend/app/launcher.py", "backend/app/core/process/winjob.py", "backend/workflows",
    "frontend/dist/index.html",
    "harness/dist/server.js", "runtime/node/node.exe",
    "harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node",
    "runtime/python/python.exe", "runtime/python/pythonw.exe", "runtime/python/Lib/site-packages/fastapi",
    "runtime/python/Lib/site-packages/psutil", "runtime/comfy-tools/comfy_mcp", "runtime/comfy-tools/comfy_cli",
    "runtime/bin/comfy-mcp.cmd", "runtime/bin/comfy.cmd",
    "runtime/ComfyUI/main.py", "runtime/ComfyUI/custom_nodes/ComfyUI-KJNodes", "runtime/ComfyUI/custom_nodes/ds_nodes",
    "runtime/comfy-python/python.exe", "runtime/comfy-python/Lib/site-packages/torch",
    "runtime/llama-swap/llama-swap.exe", "runtime/llama.cpp/llama-server.exe", "runtime/ffmpeg/bin/ffmpeg.exe", "runtime/ffmpeg/bin/ffprobe.exe",
)
ELF_MAGIC = b"\x7fELF"


def check_tree(root: Path) -> dict:
    """Static checks a Linux host can do on a Windows staging tree."""
    problems: list[str] = []
    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            problems.append(f"missing: {rel}")
    marker = json.loads((root / "ds-install.json").read_text(encoding="utf-8")) if (root / "ds-install.json").is_file() else {}
    if marker and marker.get("platform") != PLATFORM:
        problems.append(f"marker platform {marker.get('platform')!r} != {PLATFORM}")
    for name in marker.get("components", {}).get("custom_nodes", {}):
        if not (root / "runtime" / "ComfyUI" / "custom_nodes" / name).is_dir():
            problems.append(f"marker lists custom node {name} that is not staged")
    foreign: list[str] = []
    longest = ("", 0)
    total = 0
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total += size
        count += 1
        win_len = len(str(PureWindowsPath(rel)))
        if win_len > longest[1]:
            longest = (rel, win_len)
        if path.suffix in (".so", ".dylib") or (path.suffix in ("", ".bin") and size > 4 and
                                                 rel.startswith("runtime/") and _is_elf(path)):
            foreign.append(rel)
    if foreign:
        problems.append(f"{len(foreign)} non-Windows binaries staged, e.g. {foreign[:5]}")
    if longest[1] + INSTALL_PREFIX_BUDGET > MAX_PATH:
        problems.append(f"path too long for MAX_PATH with a {INSTALL_PREFIX_BUDGET}-char install root: "
                        f"{longest[0]} ({longest[1]} chars)")
    for wrapper in ("runtime/bin/comfy-mcp.cmd", "runtime/bin/comfy.cmd", "DirectorStudio.cmd"):
        data = (root / wrapper).read_bytes() if (root / wrapper).is_file() else b"\r\n"
        if b"\n" in data.replace(b"\r\n", b""):
            problems.append(f"{wrapper}: LF line endings (cmd.exe needs CRLF)")
    for name, value in marker.get("defaults", {}).items():
        if "${INSTALL_ROOT}" in value and "/" in value:
            problems.append(f"default {name} uses forward slashes: {value}")
    sizes = {p.name: common.dir_size(p) for p in sorted(root.iterdir()) if p.is_dir()}
    if (root / "runtime").is_dir():
        sizes.update({f"runtime/{p.name}": common.dir_size(p) for p in sorted((root / "runtime").iterdir()) if p.is_dir()})
    return {"root": str(root), "ok": not problems, "problems": problems, "files": count, "bytes": total,
            "sizes": sizes, "longest_path": {"path": longest[0], "chars": longest[1]}}


def _is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == ELF_MAGIC
    except OSError:
        return False


# ---- Inno Setup -----------------------------------------------------------------------

def find_iscc(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for sub in ("Inno Setup 7", "Inno Setup 6", "Programs\\Inno Setup 7", "Programs\\Inno Setup 6"):
            candidate = Path(base) / sub / "ISCC.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def iscc_command(iscc: str, root: Path, out_dir: Path, version: str, variant: str) -> list[str]:
    numeric = ".".join((version.split("-")[0].split("+")[0].split(".") + ["0", "0", "0"])[:4])
    return [iscc, "/Qp", f"/DAppVersion={version}", f"/DAppVersionNumeric={numeric}", f"/DVariant={variant}",
            f"/DSourceDir={root}", f"/DOutputDir={out_dir}", f"/DAppId={MANIFEST['windows']['app_id']}",
            str(WIN / "DirectorStudio.iss")]


# ---- driver -------------------------------------------------------------------------

def staging_root(out_dir: Path, version: str, variant: str) -> Path:
    return out_dir / "staging" / f"DirectorStudio-{version}-windows-x64-{variant}"


def cmd_stage(args: argparse.Namespace) -> Path:
    variant = args.variant[0]
    if variant not in MANIFEST["torch_variants"]:
        raise SystemExit(f"unknown variant {variant}")
    version = args.version or common._app_version()
    out_dir = args.out.resolve()
    root = staging_root(out_dir, version, variant)
    reuse = bool(args.reuse_staging) and root.is_dir()
    if root.exists() and not reuse:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    cache = args.cache.resolve()
    node_bin = Path(args.node_bin).resolve() if args.node_bin else Path(shutil.which("node") or "node").parent
    without = tuple(args.without or ())
    steps = [
        ("backend source", lambda: common.stage_backend(root)),
        ("frontend", lambda: common.stage_frontend(root, node_bin)),
        ("harness + node", lambda: stage_harness(root, node_bin, cache)),
        ("backend python", lambda: stage_backend_python(root, cache)),
        ("llama.cpp + llama-swap + ffmpeg", lambda: stage_binaries(root, variant, cache)),
        ("comfyui", lambda: stage_comfy(root, variant, cache, without)),
        ("launcher", lambda: stage_launcher(root)),
        ("bytecode", lambda: stage_bytecode(root)),
    ]
    done_file = root / ".build-steps"
    done = set(done_file.read_text().split("\n")) if reuse and done_file.is_file() else set()
    for label, step in steps:
        if label in done:
            log(f"== {label} (reused from previous run)")
            continue
        log(f"== {label}")
        step()
        done.add(label)
        done_file.write_text("\n".join(sorted(done)))
    done_file.unlink(missing_ok=True)
    marker = write_marker(root, variant, version, without)
    log("== notices")
    licences = notices.write_all(root, MANIFEST, node_bin=node_bin, platform=PLATFORM)
    report = check_tree(root)
    report.update(variant=variant, version=version, marker=marker, licences=licences)
    (out_dir / f"{root.name}.stage.json").write_text(json.dumps(report, indent=2) + "\n")
    log(json.dumps({k: report[k] for k in ("root", "ok", "problems", "bytes", "files", "longest_path")}))
    if not report["ok"]:
        raise SystemExit("staging tree failed static checks: " + "; ".join(report["problems"]))
    return root


def cmd_check(args: argparse.Namespace) -> None:
    report = check_tree(args.root.resolve())
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


def cmd_build(args: argparse.Namespace) -> None:
    root = cmd_stage(args)
    iscc = find_iscc(args.iscc)
    version = args.version or common._app_version()
    out_dir = args.out.resolve()
    if not iscc:
        raise SystemExit("ISCC.exe (Inno Setup 6.3+) not found; install it or pass --iscc")
    run(iscc_command(iscc, root, out_dir, version, args.variant[0]))
    for setup in sorted(out_dir.glob(f"DirectorStudio-{version}-windows-x64-{args.variant[0]}-setup*")):
        if setup.suffix in (".exe", ".bin"):
            (setup.parent / (setup.name + ".sha256")).write_text(f"{fetch.sha256_of(setup)}  {setup.name}\n")
            log(f"{setup.name}: {setup.stat().st_size} bytes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("lock", "stage", "check", "build"))
    parser.add_argument("--variant", action="append", choices=sorted(MANIFEST["torch_variants"]),
                        help="torch build: cu128 (default) | cu130 | cpu")
    parser.add_argument("--out", type=Path, default=REPO / "dist-installer-win")
    parser.add_argument("--root", type=Path, help="staging tree for 'check'")
    parser.add_argument("--cache", type=Path, default=fetch.DEFAULT_CACHE)
    parser.add_argument("--node-bin", default=None, help="directory containing node/npm 22 (build machine)")
    parser.add_argument("--version", default=None)
    parser.add_argument("--without", action="append", default=[], metavar="COMPONENT",
                        help="leave out an optional component (e.g. h3-continuation)")
    parser.add_argument("--reuse-staging", action="store_true")
    parser.add_argument("--iscc", default=None, help="path to ISCC.exe")
    args = parser.parse_args(argv)
    args.variant = args.variant or ["cu128"]
    if args.command == "check" and not args.root:
        parser.error("check needs --root")
    {"lock": cmd_lock, "stage": cmd_stage, "check": cmd_check, "build": cmd_build}[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
