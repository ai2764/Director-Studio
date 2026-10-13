#!/usr/bin/env python3
"""Manifest-driven builder for the self-contained Director Studio Linux package.

    python installer/build_linux.py lock  --variant cpu         # (re)generate uv locks
    python installer/build_linux.py build --variant cpu [--out DIR] [--archive]

Packaging choice: python-build-standalone (PBS) 3.13 + the backend *source*
instead of PyInstaller.  ComfyUI must run from source with its own interpreter
anyway, PBS is relocatable, the launcher/backend/ComfyUI/llama.cpp are plain
processes (easy to supervise and to patch), and the same layout works on
Windows (PBS ships a msvc build).  The existing PyInstaller spec stays for the
legacy single-file Windows/macOS portable.

Layout (``<root>`` = extracted folder, user data lives in ~/.local/share/DirectorStudio
or ``DS_HOME`` and survives upgrades):

    <root>/director-studio            launcher script (python -m app.launcher)
    <root>/ds-install.json            marker + package defaults (DS_COMFY_MODE=managed ...)
    <root>/backend, frontend/dist     DS (licence: planned Apache-2.0; no LICENSE file yet)
    <root>/harness, runtime/node      Harness sidecar + Node 22
    <root>/runtime/python             PBS for the backend (+ runtime/comfy-tools: comfy-cli/comfy-mcp, GPL, separate dir)
    <root>/runtime/ComfyUI            ComfyUI (GPL-3.0) + KJNodes (GPL-3.0) + ds_nodes (DS)
                                      [+ optional h3-continuation: ComfyUI-H3-Motion-Context (GPL-3.0)]
    <root>/runtime/comfy-python       PBS for ComfyUI with torch (cpu|cu128|cu130)
    <root>/runtime/llama-swap         llama-swap (MIT)
    <root>/runtime/llama.cpp          llama-server, CPU build (MIT)
    <root>/runtime/ffmpeg             FFmpeg LGPL static build
    <root>/THIRD_PARTY_NOTICES.txt, SOURCE_OFFER.txt, licenses/

GPL/AGPL programs are only bundled and executed as separate processes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import fetch  # noqa: E402
import notices  # noqa: E402

MANIFEST = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
LOCKS = HERE / "locks"
PLATFORM = "linux-x86_64"
UV_PLATFORM = "x86_64-manylinux_2_28"
PY_VERSION = "3.13"
BACKEND_EXCLUDE_REQS = ("comfy-cli", "comfy-mcp", "pytest", "pytest-asyncio")
# The backend talks to comfy-mcp as an MCP *client* (app/integrations/comfy_mcp.py imports `mcp`).
# comfy-mcp itself (GPL) stays in runtime/comfy-tools, but the MIT `mcp` SDK must be in the app's Python,
# otherwise H3 render jobs fail at upload time. Keep in step with mcp in locks/comfy-tools.txt.
BACKEND_EXTRA_REQS = ("mcp==2.3.0",)


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def uv() -> str:
    found = shutil.which("uv")
    if not found:
        raise SystemExit("uv is required on the build machine (pip install uv)")
    return found


# ---- lock files -------------------------------------------------------------------------

def _raw(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ds-installer"}), timeout=60) as r:
        return r.read().decode()


def comfy_requirements_in() -> str:
    """ComfyUI (pinned commit) + custom node requirements -> one .in file."""
    c = MANIFEST["comfyui"]
    base = c["repo"].removesuffix(".git").replace("github.com", "raw.githubusercontent.com")
    pins = MANIFEST.get("torch_pins", {})
    lines = [f"# ComfyUI {c['tag']} ({c['commit']})", _raw(f"{base}/{c['commit']}/requirements.txt"),
             "# matching torch set (installer/manifest.json torch_pins)",
             *(f"{name}=={pins[name]}" for name in ("torch", "torchvision", "torchaudio") if name in pins)]
    for node in MANIFEST["custom_nodes"]:
        if not node.get("requirements", True):
            lines.append(f"# {node['name']} ({node['commit']}): no Python requirements")
            continue
        nbase = node["repo"].removesuffix(".git").replace("github.com", "raw.githubusercontent.com")
        lines += [f"# {node['name']} ({node['commit']})", _raw(f"{nbase}/{node['commit']}/requirements.txt")]
    return "\n".join(lines) + "\n"


def backend_requirements_in() -> str:
    keep = []
    for line in (REPO / "backend" / "requirements.txt").read_text().splitlines():
        name = line.split("#")[0].strip().split("=")[0].split(">")[0].split("<")[0].split("[")[0].strip().lower()
        if name and name in BACKEND_EXCLUDE_REQS:
            continue
        keep.append(line)
    keep += ["# MCP client SDK used by the backend (comfy-mcp itself lives in comfy-tools)", *BACKEND_EXTRA_REQS]
    return "\n".join(keep) + "\n"


def tools_requirements_in() -> str:
    t = MANIFEST["comfy_tools"]
    return f"comfy-cli=={t['comfy-cli']}\ncomfy-mcp=={t['comfy-mcp']}\n"


def compile_lock(name: str, text: str, *, torch_backend: str | None = None) -> Path:
    LOCKS.mkdir(parents=True, exist_ok=True)
    source = LOCKS / f"{name}.in"
    source.write_text(text, encoding="utf-8")
    out = LOCKS / f"{name}.txt"
    cmd = [uv(), "pip", "compile", source, "--python-version", PY_VERSION, "--python-platform", UV_PLATFORM,
           "--generate-hashes", "--no-header", "--output-file", out, "--quiet"]
    if torch_backend:
        cmd += ["--torch-backend", torch_backend]
    run(cmd)
    return out


def cmd_lock(args: argparse.Namespace) -> None:
    compile_lock("backend", backend_requirements_in())
    compile_lock("comfy-tools", tools_requirements_in())
    for variant in args.variant:
        compile_lock(f"comfy-{variant}", comfy_requirements_in(), torch_backend=variant)
    log(f"locks written to {LOCKS}")


# ---- build steps ----------------------------------------------------------------------

def install_pbs(dest: Path, cache: Path) -> Path:
    spec = MANIFEST["python"][PLATFORM]
    fetch.install_component(spec, dest, cache=cache)
    python = dest / "bin" / "python3"
    if not python.is_file():
        raise SystemExit(f"python-build-standalone layout unexpected: {python} missing")
    marker = next(dest.glob("lib/python3.*/EXTERNALLY-MANAGED"), None)
    if marker is not None:
        marker.unlink()
    return python


def pip_install(python: Path, lock: Path, *, target: Path | None = None, torch_backend: str | None = None) -> None:
    cmd = [uv(), "pip", "install", "--python", python, "--require-hashes", "--no-deps", "--compile-bytecode",
           "-r", lock, "--link-mode", "copy"]
    if target is not None:
        cmd += ["--target", target]
    else:
        cmd += ["--system", "--break-system-packages"]
    if torch_backend:
        cmd += ["--torch-backend", torch_backend]
    run(cmd, env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"})


def copy_tree(src: Path, dst: Path, *, ignore=("__pycache__", "*.pyc", ".pytest_cache", "tests", "node_modules", ".git")) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore), symlinks=False)


def stage_backend(root: Path) -> None:
    backend = root / "backend"
    backend.mkdir(parents=True)
    copy_tree(REPO / "backend" / "app", backend / "app")
    copy_tree(REPO / "backend" / "workflows", backend / "workflows", ignore=("__pycache__",))
    for name in (".env.example", "requirements.txt"):
        shutil.copy2(REPO / "backend" / name, backend / name)
    copy_tree(REPO / "comfy_nodes", root / "comfy_nodes")
    for doc in ("README.md", "docs/installer.md", "docs/models.md"):
        src = REPO / doc
        if src.is_file():
            (root / "docs").mkdir(exist_ok=True)
            shutil.copy2(src, root / "docs" / Path(doc).name)


def stage_frontend(root: Path, node_bin: Path) -> None:
    out = root / "frontend" / "dist"
    env = {**os.environ, "PATH": f"{node_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    run([node_bin / "node", "node_modules/vite/bin/vite.js", "build", "--outDir", out, "--emptyOutDir"],
        cwd=REPO / "frontend", env=env)
    if not (out / "index.html").is_file():
        raise SystemExit("frontend build did not produce index.html")


def stage_harness(root: Path, node_bin: Path, cache: Path) -> None:
    env = {**os.environ, "PATH": f"{node_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    runtimes = json.loads((REPO / "packaging" / "portable-harness-runtimes.json").read_text(encoding="utf-8"))
    node = runtimes["runtimes"][PLATFORM]
    archive = fetch.cached_download(node["url"], node["sha256"], cache=cache, name=node["archive"])
    run([sys.executable, REPO / "scripts" / "stage_portable_harness.py", "--repo-root", REPO,
         "--destination", root, "--target", PLATFORM, "--node-archive", archive], env=env)


def git_checkout(repo: str, commit: str, dest: Path, *, tag: str | None = None) -> None:
    dest.mkdir(parents=True)
    run(["git", "init", "-q", dest])
    run(["git", "-C", dest, "fetch", "-q", "--depth", "1", repo, commit])
    run(["git", "-C", dest, "checkout", "-q", "FETCH_HEAD"])
    head = subprocess.run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != commit:
        raise SystemExit(f"{repo}: expected {commit}, got {head}")
    shutil.rmtree(dest / ".git")


def selected_custom_nodes(without: list[str] | tuple[str, ...] = ()) -> list[dict]:
    """Manifest custom nodes minus optional components excluded with --without."""
    known = {n["component"] for n in MANIFEST["custom_nodes"] if n.get("optional")}
    unknown = set(without) - known
    if unknown:
        raise SystemExit(f"unknown optional component(s) {sorted(unknown)}; known: {sorted(known)}")
    return [n for n in MANIFEST["custom_nodes"] if not (n.get("optional") and n.get("component") in without)]


def stage_comfy(root: Path, variant: str, cache: Path, without: tuple[str, ...] = ()) -> None:
    runtime = root / "runtime"
    c = MANIFEST["comfyui"]
    comfy = runtime / "ComfyUI"
    git_checkout(c["repo"], c["commit"], comfy, tag=c.get("tag"))
    for node in selected_custom_nodes(without):
        git_checkout(node["repo"], node["commit"], comfy / "custom_nodes" / node["name"])
    shutil.copytree(REPO / "comfy_nodes" / "ds_nodes", comfy / "custom_nodes" / "ds_nodes",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for writable in ("output", "input", "temp", "user"):
        shutil.rmtree(comfy / writable, ignore_errors=True)  # live in the DS data dir instead
    python = install_pbs(runtime / "comfy-python", cache)
    lock = LOCKS / f"comfy-{variant}.txt"
    if not lock.is_file():
        compile_lock(f"comfy-{variant}", comfy_requirements_in(), torch_backend=variant)
    pip_install(python, lock, torch_backend=variant)
    run([python, "-c", "import torch, numpy, aiohttp, cv2; print('torch', torch.__version__, 'cuda', torch.version.cuda)"])


def stage_backend_python(root: Path, cache: Path) -> None:
    runtime = root / "runtime"
    python = install_pbs(runtime / "python", cache)
    for name, text in (("backend", backend_requirements_in()), ("comfy-tools", tools_requirements_in())):
        if not (LOCKS / f"{name}.txt").is_file():
            compile_lock(name, text)
    pip_install(python, LOCKS / "backend.txt")
    pip_install(python, LOCKS / "comfy-tools.txt", target=runtime / "comfy-tools")
    bindir = runtime / "bin"
    bindir.mkdir(exist_ok=True)
    wrappers = {
        "comfy-mcp": "import sys; from comfy_mcp.server import main; sys.argv[0] = 'comfy-mcp'; sys.exit(main())",
        "comfy": "import sys; from comfy_cli.__main__ import main; sys.argv[0] = 'comfy'; sys.exit(main())",
    }
    for name, code in wrappers.items():
        path = bindir / name
        path.write_text(
            "#!/bin/sh\n"
            "# GPL tool executed as a separate process; kept out of the backend's site-packages.\n"
            'here="$(cd "$(dirname "$0")/.." && pwd -P)"\n'
            f'PYTHONPATH="$here/comfy-tools" exec "$here/python/bin/python3" -s -c "{code}" "$@"\n',
            encoding="utf-8")
        path.chmod(0o755)
    run([python, "-s", "-c", "import fastapi, uvicorn, httpx, psutil, websockets, pydantic_settings, mcp, mcp.client.stdio; print('backend deps ok')"])
    run([bindir / "comfy-mcp", "--help"], stdout=subprocess.DEVNULL)


def stage_binaries(root: Path, variant: str, cache: Path) -> None:
    fetch.install_component(MANIFEST["llama_swap"][PLATFORM], root / "runtime" / "llama-swap", cache=cache)
    fetch.install_component(MANIFEST["llama_cpp"][PLATFORM], root / "runtime" / "llama.cpp", cache=cache, variant=variant)
    fetch.install_component(MANIFEST["ffmpeg"][PLATFORM], root / "runtime" / "ffmpeg", cache=cache)
    run([root / "runtime" / "llama-swap" / "llama-swap", "-version"], stdout=subprocess.DEVNULL)
    run([root / "runtime" / "llama.cpp" / "llama-server", "--version"], stderr=subprocess.STDOUT, stdout=subprocess.DEVNULL)
    run([root / "runtime" / "ffmpeg" / "bin" / "ffmpeg", "-hide_banner", "-version"], stdout=subprocess.DEVNULL)


def git_describe() -> dict[str, str]:
    def g(*a: str) -> str:
        return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True).stdout.strip()

    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(g("status", "--porcelain", "--untracked-files=no"))}


def write_marker(root: Path, variant: str, version: str, without: tuple[str, ...] = ()) -> dict:
    defaults = {
        "DS_COMFY_MODE": "managed",
        "DS_LLAMA_SWAP_MODE": "bundled",
        "DS_LLM_PROVIDER": "llama-swap",
        "DS_DIRECTOR_AGENT_RUNTIME": "harness",
        "DS_HARNESS_MANAGED": "true",
        "DS_COMFY_MCP_COMMAND": "${INSTALL_ROOT}/runtime/bin/comfy-mcp",
        "DS_COMFY_MCP_COMFY_BIN": "${INSTALL_ROOT}/runtime/bin/comfy",
    }
    if variant == "cpu":
        defaults["DS_COMFY_DEV_CPU"] = "true"  # CPU torch build: ComfyUI must run with --cpu
    marker = {
        "format": 1,
        "product": "Director Studio",
        "version": version,
        "platform": PLATFORM,
        "variant": variant,
        "torch": MANIFEST["torch_variants"][variant],
        "built_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "source": git_describe(),
        "components": {
            "python": MANIFEST["python"]["version"],
            "comfyui": f"{MANIFEST['comfyui']['tag']} ({MANIFEST['comfyui']['commit'][:12]})",
            "custom_nodes": {n["name"]: n["commit"][:12] for n in selected_custom_nodes(without)},
            "llama_cpp": f"{MANIFEST['llama_cpp']['version']} ({MANIFEST['llama_cpp']['release']})",
            "llama_swap": MANIFEST["llama_swap"]["version"],
            "ffmpeg": MANIFEST["ffmpeg"]["version"],
            "comfy_tools": {k: v for k, v in MANIFEST["comfy_tools"].items() if k != "license"},
        },
        "defaults": defaults,
    }
    (root / "ds-install.json").write_text(json.dumps(marker, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return marker


def stage_launcher(root: Path) -> None:
    for name in ("director-studio", "install-desktop-entry.sh"):
        shutil.copy2(HERE / "linux" / name, root / name)
        (root / name).chmod(0o755)
    shutil.copy2(HERE / "linux" / "director-studio.desktop.in", root / "director-studio.desktop.in")
    shutil.copy2(REPO / "frontend" / "public" / "favicon.svg", root / "director-studio.svg")
    shutil.copy2(HERE / "linux" / "README-INSTALL.md", root / "README-INSTALL.md")


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def make_archive(root: Path, out_dir: Path) -> Path:
    archive = out_dir / f"{root.name}.tar.gz"
    pigz = shutil.which("pigz")
    if pigz:
        run(["tar", "-C", root.parent, "-I", f"{pigz} -6", "-cf", archive, root.name])
    else:
        run(["tar", "-C", root.parent, "-czf", archive, root.name])
    digest = fetch.sha256_of(archive)
    (archive.parent / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n")
    return archive


def cmd_build(args: argparse.Namespace) -> None:
    if sys.platform != "linux":
        raise SystemExit("build_linux.py builds the Linux package; Windows packaging is a separate step")
    variant = args.variant[0]
    if variant not in MANIFEST["torch_variants"]:
        raise SystemExit(f"unknown variant {variant}")
    version = args.version or _app_version()
    out_dir = args.out.resolve()
    name = f"DirectorStudio-{version}-linux-x86_64-{variant}"
    work = out_dir / "staging"
    root = work / name
    reuse = bool(args.reuse_staging) and root.is_dir()
    if root.exists() and not reuse:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    cache = args.cache.resolve()
    node_bin = Path(args.node_bin).resolve() if args.node_bin else Path(shutil.which("node") or "node").parent
    steps = [
        ("backend source", lambda: stage_backend(root)),
        ("frontend", lambda: stage_frontend(root, node_bin)),
        ("harness + node", lambda: stage_harness(root, node_bin, cache)),
        ("backend python", lambda: stage_backend_python(root, cache)),
        ("llama.cpp + llama-swap + ffmpeg", lambda: stage_binaries(root, variant, cache)),
        ("comfyui", lambda: stage_comfy(root, variant, cache, tuple(args.without or ()))),
        ("launcher", lambda: stage_launcher(root)),
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
    marker = write_marker(root, variant, version, tuple(args.without or ()))
    log("== notices")
    licence_summary = notices.write_all(root, MANIFEST, node_bin=node_bin)
    sizes = {p.name: dir_size(p) for p in sorted(root.iterdir()) if p.is_dir()}
    sizes.update({f"runtime/{p.name}": dir_size(p) for p in sorted((root / "runtime").iterdir()) if p.is_dir()})
    report = {"root": str(root), "variant": variant, "version": version, "bytes": dir_size(root),
              "sizes": sizes, "marker": marker, "licences": licence_summary}
    if args.archive:
        log("== archive")
        archive = make_archive(root, out_dir)
        report["archive"] = str(archive)
        report["archive_bytes"] = archive.stat().st_size
    (out_dir / f"{name}.build.json").write_text(json.dumps(report, indent=2) + "\n")
    log(json.dumps({k: report[k] for k in ("root", "bytes", "archive", "archive_bytes") if k in report}))


def _app_version() -> str:
    import re

    text = (REPO / "backend" / "app" / "main.py").read_text()
    match = re.search(r'APP_VERSION = "([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("lock", "build"))
    parser.add_argument("--variant", action="append", choices=sorted(MANIFEST["torch_variants"]),
                        help="torch build: cpu | cu128 | cu130 (default cu128)")
    parser.add_argument("--out", type=Path, default=REPO / "dist-installer")
    parser.add_argument("--cache", type=Path, default=fetch.DEFAULT_CACHE)
    parser.add_argument("--node-bin", default=None, help="directory containing node/npm 22 (build machine)")
    parser.add_argument("--version", default=None)
    parser.add_argument("--archive", action="store_true", help="also produce the .tar.gz")
    parser.add_argument("--without", action="append", default=[], metavar="COMPONENT",
                        help="leave out an optional component (e.g. h3-continuation)")
    parser.add_argument("--reuse-staging", action="store_true",
                        help="resume an interrupted build: keep staging steps that already completed")
    args = parser.parse_args(argv)
    args.variant = args.variant or ["cu128"]
    {"lock": cmd_lock, "build": cmd_build}[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
