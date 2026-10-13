#!/usr/bin/env python3
"""Manifest-driven builder for the self-contained Director Studio macOS package.

    python installer/build_macos.py lock  [--arch arm64|x86_64 ...]   # (re)generate uv locks
    python installer/build_macos.py build [--arch HOST] [--out DIR] [--archive]

Same design and layout as installer/build_linux.py (whose staging steps are reused):
python-build-standalone 3.13 + backend source, Harness + Node 22, the launcher config UI,
llama.cpp (official macOS release build, Metal on arm64) + llama-swap, ComfyUI with
ds_nodes / KJNodes / H3-Motion-Context and the workflow dependency downloader.

macOS specifics:
  * arm64 ("mps" variant): ComfyUI + PyTorch from PyPI (macOS arm64 wheels include MPS).
  * x86_64 ("nocomfy" variant): PyTorch stopped publishing Intel-mac wheels after 2.2.2
    (Python <= 3.12), which cannot run the pinned ComfyUI. The Intel package therefore ships
    everything except the local ComfyUI runtime and defaults to an external/remote ComfyUI.
  * FFmpeg is not bundled (no upstream LGPL macOS build); the backend falls back to
    ffmpeg/ffprobe on PATH (``brew install ffmpeg``) or ``DS_FFMPEG_DIR``.
  * Output: ``DirectorStudio-<ver>-macos-<arch>-<variant>.tar.gz`` with ``Director Studio.command``
    (double-click) and the POSIX ``director-studio`` launcher. Binaries are not notarized.

Must run on a Mac of the target architecture (the bundled interpreters are smoke-tested).
"""

from __future__ import annotations

import argparse
import json
import platform as _platform
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_linux as bl  # noqa: E402  (shared staging steps, manifest, lock helpers)
import fetch  # noqa: E402
import notices  # noqa: E402

MANIFEST = bl.MANIFEST
MAC = MANIFEST["macos"]
ARCHES = {
    "arm64": {"platform": "macos-arm64", "uv_platform": "aarch64-apple-darwin", "variant": "mps"},
    "x86_64": {"platform": "macos-x86_64", "uv_platform": "x86_64-apple-darwin", "variant": "nocomfy"},
}
# build_linux helpers read torch_variants[variant] for the install marker.
MANIFEST["torch_variants"].update(MAC["torch_variants"])
log = bl.log
run = bl.run


def host_arch() -> str:
    machine = _platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x86_64"


def configure(arch: str) -> dict:
    """Point the shared build_linux helpers at a macOS target."""
    spec = ARCHES[arch]
    bl.PLATFORM = spec["platform"]
    bl.UV_PLATFORM = spec["uv_platform"]
    return spec


# ---- lock files -------------------------------------------------------------------------

def lock_name(kind: str, arch: str) -> str:
    return f"macos-{kind}-{arch}"


def cmd_lock(args: argparse.Namespace) -> None:
    for arch in args.arch:
        spec = configure(arch)
        bl.compile_lock(lock_name("backend", arch), bl.backend_requirements_in())
        bl.compile_lock(lock_name("comfy-tools", arch), bl.tools_requirements_in())
        if spec["variant"] == "mps":
            # PyPI torch for macOS arm64 is the MPS build: no --torch-backend index.
            bl.compile_lock(lock_name("comfy-mps", arch), bl.comfy_requirements_in())
    log(f"locks written to {bl.LOCKS}")


# ---- build steps ----------------------------------------------------------------------

def stage_backend_python(root: Path, cache: Path, arch: str) -> None:
    """build_linux.stage_backend_python with the macOS lock names."""
    runtime = root / "runtime"
    python = bl.install_pbs(runtime / "python", cache)
    bl.pip_install(python, bl.LOCKS / f"{lock_name('backend', arch)}.txt")
    bl.pip_install(python, bl.LOCKS / f"{lock_name('comfy-tools', arch)}.txt", target=runtime / "comfy-tools")
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
    run([python, "-s", "-c", "import fastapi, uvicorn, httpx, psutil, websockets, pydantic_settings, mcp, "
         "mcp.client.stdio; print('backend deps ok')"])
    run([bindir / "comfy-mcp", "--help"], stdout=subprocess.DEVNULL)


def stage_binaries(root: Path, cache: Path) -> None:
    fetch.install_component(MANIFEST["llama_swap"][bl.PLATFORM], root / "runtime" / "llama-swap", cache=cache)
    fetch.install_component(MANIFEST["llama_cpp"][bl.PLATFORM], root / "runtime" / "llama.cpp", cache=cache)
    run([root / "runtime" / "llama-swap" / "llama-swap", "-version"], stdout=subprocess.DEVNULL)
    run([root / "runtime" / "llama.cpp" / "llama-server", "--version"], stderr=subprocess.STDOUT,
        stdout=subprocess.DEVNULL)


def stage_comfy(root: Path, cache: Path, arch: str, without: tuple[str, ...]) -> None:
    runtime = root / "runtime"
    c = MANIFEST["comfyui"]
    comfy = runtime / "ComfyUI"
    bl.git_checkout(c["repo"], c["commit"], comfy, tag=c.get("tag"))
    for node in bl.selected_custom_nodes(without):
        bl.git_checkout(node["repo"], node["commit"], comfy / "custom_nodes" / node["name"])
    shutil.copytree(bl.REPO / "comfy_nodes" / "ds_nodes", comfy / "custom_nodes" / "ds_nodes",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for writable in ("output", "input", "temp", "user"):
        shutil.rmtree(comfy / writable, ignore_errors=True)
    python = bl.install_pbs(runtime / "comfy-python", cache)
    bl.pip_install(python, bl.LOCKS / f"{lock_name('comfy-mps', arch)}.txt")
    run([python, "-c", "import torch, numpy, aiohttp, cv2; "
         "print('torch', torch.__version__, 'mps', torch.backends.mps.is_built())"])


def stage_launcher(root: Path) -> None:
    shutil.copy2(HERE / "linux" / "director-studio", root / "director-studio")
    for name in ("Director Studio.command",):
        shutil.copy2(HERE / "macos" / name, root / name)
    for name in ("director-studio", "Director Studio.command"):
        (root / name).chmod(0o755)
    shutil.copy2(HERE / "macos" / "README-INSTALL.md", root / "README-INSTALL.md")
    shutil.copy2(bl.REPO / "frontend" / "public" / "favicon.svg", root / "director-studio.svg")


def write_marker(root: Path, spec: dict, version: str, without: tuple[str, ...]) -> dict:
    marker = bl.write_marker(root, spec["variant"], version, without)
    defaults = marker["defaults"]
    defaults.pop("DS_COMFY_DEV_CPU", None)
    if spec["variant"] == "nocomfy":
        defaults["DS_COMFY_MODE"] = "external-local"  # bring your own ComfyUI (local or remote)
        marker["components"].pop("comfyui", None)
        marker["components"].pop("custom_nodes", None)
    marker["components"].pop("ffmpeg", None)
    marker["components"]["ffmpeg"] = "system (PATH / DS_FFMPEG_DIR)"
    (root / "ds-install.json").write_text(json.dumps(marker, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return marker


def write_notices(root: Path, node_bin: Path, platform: str) -> dict:
    original = notices.native_components

    def present_only(r: Path, manifest: dict, plat: str | None = None) -> list[dict]:
        # Only list programs that are actually in this package (no FFmpeg; no ComfyUI on Intel).
        return [c for c in original(r, manifest, plat)
                if any((r / p.strip()).exists() for p in c["path"].split(","))]

    notices.native_components = present_only
    try:
        return notices.write_all(root, MANIFEST, node_bin=node_bin, platform=platform)
    finally:
        notices.native_components = original


def cmd_build(args: argparse.Namespace) -> None:
    if sys.platform != "darwin":
        raise SystemExit("build_macos.py must run on macOS (bundled interpreters and binaries are smoke-tested)")
    arch = args.arch[0]
    if arch != host_arch():
        raise SystemExit(f"build on a {arch} Mac (host is {host_arch()})")
    spec = configure(arch)
    variant = spec["variant"]
    version = args.version or bl._app_version()
    out_dir = args.out.resolve()
    name = f"DirectorStudio-{version}-macos-{arch}-{variant}"
    root = out_dir / "staging" / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    cache = args.cache.resolve()
    node_bin = Path(args.node_bin).resolve() if args.node_bin else Path(shutil.which("node") or "node").parent
    without = tuple(args.without or ())
    steps = [
        ("backend source", lambda: bl.stage_backend(root)),
        ("frontend", lambda: bl.stage_frontend(root, node_bin)),
        ("harness + node", lambda: bl.stage_harness(root, node_bin, cache)),
        ("backend python", lambda: stage_backend_python(root, cache, arch)),
        ("llama.cpp + llama-swap", lambda: stage_binaries(root, cache)),
        ("launcher", lambda: stage_launcher(root)),
    ]
    if variant == "mps":
        steps.insert(5, ("comfyui", lambda: stage_comfy(root, cache, arch, without)))
    for label, step in steps:
        log(f"== {label}")
        step()
    marker = write_marker(root, spec, version, without)
    log("== notices")
    licence_summary = write_notices(root, node_bin, spec["platform"])
    report = {"root": str(root), "arch": arch, "variant": variant, "version": version,
              "bytes": bl.dir_size(root), "marker": marker, "licences": licence_summary}
    if args.archive:
        log("== archive")
        archive = bl.make_archive(root, out_dir)
        report["archive"] = str(archive)
        report["archive_bytes"] = archive.stat().st_size
    (out_dir / f"{name}.build.json").write_text(json.dumps(report, indent=2) + "\n")
    log(json.dumps({k: report[k] for k in ("root", "bytes", "archive", "archive_bytes") if k in report}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("lock", "build"))
    parser.add_argument("--arch", action="append", choices=sorted(ARCHES))
    parser.add_argument("--out", type=Path, default=bl.REPO / "dist-installer")
    parser.add_argument("--cache", type=Path, default=fetch.DEFAULT_CACHE)
    parser.add_argument("--node-bin", default=None, help="directory containing node/npm 22 (build machine)")
    parser.add_argument("--version", default=None)
    parser.add_argument("--archive", action="store_true", help="also produce the .tar.gz")
    parser.add_argument("--without", action="append", default=[], metavar="COMPONENT",
                        help="leave out an optional component (e.g. h3-continuation)")
    args = parser.parse_args(argv)
    args.arch = args.arch or ([host_arch()] if args.command == "build" else sorted(ARCHES))
    {"lock": cmd_lock, "build": cmd_build}[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
