"""THIRD_PARTY_NOTICES.txt, SOURCE_OFFER.txt and licenses/ for the installer package.

Python environments are scanned with pip-licenses (MIT, build-time only; run
against each bundled interpreter via ``--python``), npm trees with
license-checker-rseidelsohn (BSD-3-Clause, via npx).  Native components come
from installer/manifest.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

COPYLEFT = re.compile(r"(A?GPL|LGPL|GNU (Lesser |Affero )?General Public|MPL|EPL|CDDL)", re.I)


def _log(msg: str) -> None:
    print(f"[notices] {msg}", flush=True)


def _pip_licenses() -> list[str]:
    exe = shutil.which("pip-licenses") or str(Path(sys.executable).with_name("pip-licenses"))
    if not Path(exe).exists():
        raise SystemExit("pip-licenses is required on the build machine (pip install -r installer/requirements-build.txt)")
    return [exe]


def scan_python(python: Path, *, pythonpath: Path | None = None, label: str) -> list[dict]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    out = subprocess.run(
        [*_pip_licenses(), "--python", str(python), "--format=json", "--with-urls", "--with-license-file",
         "--with-notice-file", "--no-license-path", "--from=mixed", "--with-system"],
        capture_output=True, text=True, env=env, check=True).stdout
    rows = json.loads(out)
    if pythonpath is not None:  # keep only what lives in the target dir
        names = {p.name.split("-")[0].lower().replace("_", "-") for p in pythonpath.glob("*.dist-info")}
        rows = [r for r in rows if r["Name"].lower().replace("_", "-") in names]
    for row in rows:
        row["env"] = label
    return [r for r in rows if r["Name"] not in ("pip", "setuptools", "wheel")] + \
        [r for r in rows if r["Name"] in ("pip", "setuptools", "wheel")]


def _metadata_license(meta: str) -> str:
    """Best licence string from a METADATA file (License-Expression > License > classifiers)."""
    headers: dict[str, list[str]] = {}
    for line in meta.split("\n\n", 1)[0].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            headers.setdefault(key.strip().lower(), []).append(value.strip())
    if headers.get("license-expression"):
        return headers["license-expression"][0]
    lic = (headers.get("license") or [""])[0]
    if lic and lic.upper() != "UNKNOWN" and len(lic) < 120:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in headers.get("classifier", []) if c.startswith("License ::")]
    return "; ".join(classifiers) or "UNKNOWN"


def scan_site_dir(target: Path, *, label: str) -> list[dict]:
    """Licences of packages installed with ``pip install --target`` (read *.dist-info directly).

    pip-licenses only sees an interpreter's sys.path, so a separate ``--target``
    directory (runtime/comfy-tools, kept out of the backend's site-packages on
    purpose) is scanned here from its dist-info metadata and licence files.
    """
    rows: list[dict] = []
    for dist in sorted(target.glob("*.dist-info")):
        meta_path = dist / "METADATA"
        if not meta_path.is_file():
            continue
        meta = meta_path.read_text(encoding="utf-8", errors="replace")
        head = meta.split("\n\n", 1)[0]
        get = lambda key: next((l.split(":", 1)[1].strip() for l in head.splitlines()  # noqa: E731
                                if l.lower().startswith(key.lower() + ":")), "")
        url = get("Home-page") or next((l.split(",", 1)[-1].strip() for l in head.splitlines()
                                        if l.lower().startswith("project-url:")), "")
        texts = []
        for lf in sorted([*dist.glob("licenses/**/*"), *dist.glob("LICEN[CS]E*"), *dist.glob("COPYING*"),
                          *dist.glob("NOTICE*")]):
            if lf.is_file():
                texts.append(lf.read_text(encoding="utf-8", errors="replace"))
        rows.append({"Name": get("Name"), "Version": get("Version"), "License": _metadata_license(meta),
                     "URL": url, "LicenseText": "\n\n".join(texts), "env": label})
    return rows


def scan_node_modules(node_modules: Path, *, label: str) -> list[dict]:
    """Licence id from each package.json (+ its LICENSE/COPYING/NOTICE file) under node_modules."""
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for package_json in sorted(node_modules.rglob("package.json")):
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        name, version = package.get("name"), package.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or (name, version) in seen:
            continue
        seen.add((name, version))
        lic = package.get("license") or package.get("licenses") or "UNKNOWN"
        if isinstance(lic, dict):
            lic = lic.get("type", "UNKNOWN")
        elif isinstance(lic, list):
            lic = " OR ".join(str(x.get("type", x) if isinstance(x, dict) else x) for x in lic)
        repo = package.get("repository") or package.get("homepage") or ""
        if isinstance(repo, dict):
            repo = repo.get("url", "")
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in sorted(package_json.parent.iterdir())
                 if p.is_file() and p.name.lower().startswith(("license", "licence", "copying", "notice"))]
        rows.append({"Name": name, "Version": version, "License": str(lic), "URL": str(repo),
                     "LicenseText": "\n\n".join(texts), "env": label})
    return rows


def scan_npm(project: Path, node_bin: Path, *, label: str, production: bool = True) -> list[dict]:
    npx = node_bin / "npx" if (node_bin / "npx").exists() and (node_bin / "npx").resolve().exists() else shutil.which("npx")
    if npx is None:
        _log("npx not found; frontend licences not scanned")
        return []
    cmd = [str(npx), "--yes", "license-checker-rseidelsohn@4.4.2", "--json", "--start", str(project)]
    if production:
        cmd.append("--production")
    env = {**os.environ, "PATH": f"{node_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        _log(f"license-checker failed for {project}: {result.stderr[-400:]}")
        return []
    rows = []
    for key, value in json.loads(result.stdout).items():
        name, _, version = key.rpartition("@")
        text = ""
        lf = value.get("licenseFile")
        if lf and Path(lf).is_file() and Path(lf).name.lower().startswith(("license", "licence", "copying", "notice")):
            text = Path(lf).read_text(encoding="utf-8", errors="replace")
        rows.append({"Name": name, "Version": version, "License": str(value.get("licenses")),
                     "URL": value.get("repository") or "", "LicenseText": text, "env": label})
    return rows


def _fetch_text(url: str) -> str:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ds-installer"}), timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"(could not fetch {url}: {exc})"


def native_components(root: Path, manifest: dict, platform: str | None = None) -> list[dict]:
    platform = platform or PLATFORM_KEY
    c, ff, py = manifest["comfyui"], manifest["ffmpeg"], manifest["python"]
    lcpp, lswap = manifest["llama_cpp"], manifest["llama_swap"]
    lcpp_dir = root / "runtime" / "llama.cpp"
    comps = [
        {"Name": "CPython (python-build-standalone)", "Version": f"{py['version']}+{py['release']}", "License": py["license"],
         "URL": "https://github.com/astral-sh/python-build-standalone", "path": "runtime/python, runtime/comfy-python",
         "license_files": sorted(str(p.relative_to(root)).replace("\\", "/") for pattern in ("lib/python3.*/LICENSE.txt", "LICENSE.txt")
                                for p in (root / "runtime" / "python").glob(pattern))},
        {"Name": "Node.js", "Version": json.loads((root / "portable-manifest.json").read_text())["node"]["version"]
         if (root / "portable-manifest.json").is_file() else "22", "License": "MIT and others",
         "URL": "https://nodejs.org", "path": "runtime/node",
         "license_files": sorted(str(p.relative_to(root)) for p in (root / "runtime" / "node").glob("LICENSE*"))},
        {"Name": "ComfyUI", "Version": f"{c['tag']} ({c['commit']})", "License": c["license"], "URL": c["repo"],
         "path": "runtime/ComfyUI", "license_files": ["runtime/ComfyUI/LICENSE"], "copyleft": True,
         "source": f"{c['repo'].removesuffix('.git')}/tree/{c['commit']}"},
        *[{"Name": n["name"], "Version": n["commit"], "License": n["license"], "URL": n["repo"],
           "path": f"runtime/ComfyUI/custom_nodes/{n['name']}",
           "license_files": [f"runtime/ComfyUI/custom_nodes/{n['name']}/LICENSE"], "copyleft": True,
           "source": f"{n['repo'].removesuffix('.git')}/tree/{n['commit']}"} for n in manifest["custom_nodes"]
          if (root / "runtime" / "ComfyUI" / "custom_nodes" / n["name"]).is_dir()],
        {"Name": "llama.cpp (llama-server)", "Version": f"{lcpp['version']} ({lcpp['release']}, {lcpp['commit'][:12]})",
         "License": lcpp["license"], "URL": lcpp["source"], "path": "runtime/llama.cpp",
         "license_files": ["licenses/llama.cpp-LICENSE", "licenses/llama.cpp-LICENSE-jsonhpp"] + sorted(
             str(p.relative_to(root)).replace("\\", "/") for p in lcpp_dir.glob("LICENSE*")),
         "note": "Includes nlohmann/json (MIT) and, on Windows, LLVM OpenMP (libomp, Apache-2.0 WITH LLVM-exception)."},
        {"Name": "llama-swap", "Version": lswap["version"], "License": lswap["license"], "URL": lswap["source"],
         "path": "runtime/llama-swap", "license_files": ["runtime/llama-swap/LICENSE.md"]},
        {"Name": "FFmpeg (static, LGPL build)", "Version": ff["version"], "License": ff["license"], "URL": ff["source"],
         "path": "runtime/ffmpeg", "license_files": ["runtime/ffmpeg/LICENSE.txt"], "copyleft": True,
         "source": f"{ff['source']} ; build scripts {ff['build_scripts']} ({ff['build']}) ; binary {ff.get(platform, ff.get(PLATFORM_KEY, {})).get('url', '?')}"},
    ]
    return comps


PLATFORM_KEY = "linux-x86_64"

# Packages whose wheels carry no machine-readable licence metadata. Each entry was
# checked against the upstream repository's LICENSE file (see "evidence").
LICENSE_OVERRIDES: dict[str, dict[str, str]] = {
    "comfy-aimdo": {
        "license": "GPL-3.0",
        "url": "https://github.com/Comfy-Org/comfy-aimdo",
        "license_url": "https://raw.githubusercontent.com/Comfy-Org/comfy-aimdo/master/LICENSE",
        "evidence": "repo LICENSE + wheel dist-info/licenses/LICENSE: 'licensed under the GNU General Public License v3.0'",
    },
    "comfyui-frontend-package": {
        "license": "GPL-3.0",
        "url": "https://github.com/Comfy-Org/ComfyUI_frontend",
        "license_url": "https://raw.githubusercontent.com/Comfy-Org/ComfyUI_frontend/v{version}/LICENSE",
        "evidence": "built from Comfy-Org/ComfyUI_frontend (GitHub licence: GPL-3.0); the wheel ships no licence file",
    },
    "cookiecutter": {
        "license": "BSD-3-Clause",
        "url": "https://github.com/cookiecutter/cookiecutter",
        "license_url": "https://raw.githubusercontent.com/cookiecutter/cookiecutter/v{version}/LICENSE",
        "evidence": "repo LICENSE (BSD 3-Clause) + wheel dist-info/licenses/LICENSE",
    },
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def apply_overrides(rows: list[dict], *, fetch=None) -> list[dict]:
    """Fill licence id (and missing licence text) for packages listed in LICENSE_OVERRIDES."""
    fetch = fetch or _fetch_text
    for row in rows:
        override = LICENSE_OVERRIDES.get(_norm(str(row.get("Name", ""))))
        if not override:
            continue
        row["License"] = override["license"]
        if str(row.get("URL") or "").strip().upper() in ("", "UNKNOWN", "NONE"):
            row["URL"] = override["url"]
        row["LicenseSource"] = override["evidence"]
        if not (row.get("LicenseText") or "").strip() or row["LicenseText"].strip() == "UNKNOWN":
            row["LicenseText"] = fetch(override["license_url"].format(version=row.get("Version", "")))
    return rows


def header_lines(has_ds_license: bool, gpl_nodes: tuple[str, ...] | list[str] = ("ComfyUI-KJNodes",)) -> list[str]:
    gpl = ", ".join(["ComfyUI", *gpl_nodes, "comfy-cli", "comfyui-frontend-package", "comfy-aimdo"])
    return [
        *(["Director Studio's own licence: see licenses/DirectorStudio-LICENSE."] if has_ds_license else
          ["Director Studio's own source code: no licence file was included in this build (the",
           "project has not published a LICENSE yet); all rights reserved by its authors until then."]),
        "The package bundles the third-party components listed below. Copyleft programs -",
        f"GPL-3.0: {gpl};",
        "AGPL-3.0: comfy-mcp; LGPL-2.1+: FFmpeg (and any further packages flagged below) - are",
        "shipped as separate programs, unmodified, and only executed as separate processes;",
        "Director Studio does not link to or include their code. See SOURCE_OFFER.txt.",
    ]


# NVIDIA CUDA / cuDNN redistributables shipped inside torch wheels (Windows: torch/lib/*.dll,
# Linux: nvidia-* wheels) and next to llama-server (runtime/llama.cpp, Windows CUDA builds).  They are proprietary NVIDIA
# files redistributed unmodified under NVIDIA's EULAs, not open source licences.
NVIDIA_PREFIXES = ("cudart", "cublas", "cudnn", "cufft", "curand", "cusolver", "cusparse", "nvrtc", "nvjitlink",
                   "cupti", "nvtoolsext", "nvperf", "nccl", "cufile", "nvshmem", "cudss", "cusparselt",
                   "libcudart", "libcublas", "libcudnn", "libcufft", "libcurand", "libcusolver", "libcusparse",
                   "libnvrtc", "libnvjitlink", "libcupti", "libnvtoolsext", "libnvperf", "libnccl", "libcufile",
                   "libnvshmem", "libcudss", "libcusparselt", "libnvtx")
MSVC_PREFIXES = ("msvcp140", "vcruntime140", "concrt140", "vccorlib140", "vcomp140")
NVIDIA_LICENCES = [
    "NVIDIA CUDA Toolkit EULA (redistributable files, Attachment A): https://docs.nvidia.com/cuda/eula/index.html",
    "NVIDIA cuDNN Software License Agreement: https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html",
    "NVIDIA NCCL licence (BSD-3-Clause): https://github.com/NVIDIA/nccl/blob/master/LICENSE.txt",
]


def _binary_name(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".dll") or ".so" in name


def redistributable_binaries(root: Path, prefixes: tuple[str, ...]) -> dict[str, list[str]]:
    """``{directory (relative): [file names]}`` for vendor runtime DLLs/.so files in ``runtime/``."""
    found: dict[str, list[str]] = {}
    runtime = root / "runtime"
    if not runtime.is_dir():
        return found
    for path in runtime.rglob("*"):
        if path.is_file() and _binary_name(path) and path.name.lower().startswith(prefixes):
            rel = path.parent.relative_to(root).as_posix()
            found.setdefault(rel, []).append(path.name)
    return {k: sorted(v) for k, v in sorted(found.items())}


def vendor_runtime_notice(root: Path) -> list[str]:
    nvidia = redistributable_binaries(root, NVIDIA_PREFIXES)
    msvc = redistributable_binaries(root, MSVC_PREFIXES)
    out: list[str] = []
    if nvidia:
        out += ["", "NVIDIA CUDA runtime components (proprietary, redistributed unmodified)", "-" * 72,
                "The GPU builds of PyTorch (runtime/comfy-python) and llama.cpp (runtime/llama.cpp) include NVIDIA",
                "CUDA runtime and cuDNN libraries. They are NVIDIA proprietary redistributable files, not",
                "open source; they are shipped unmodified, only as part of those programs, and remain subject",
                "to NVIDIA's terms:",
                *(f"  - {line}" for line in NVIDIA_LICENCES),
                "Use requires a compatible NVIDIA driver installed by the user (not included). Files:"]
        out += [f"  {directory}: {', '.join(names)}" for directory, names in nvidia.items()]
    if msvc:
        out += ["", "Microsoft Visual C++ runtime (redistributable files)", "-" * 72,
                "Shipped unmodified inside the bundled programs under the Microsoft Visual Studio",
                "redistribution terms (https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files). Files:"]
        out += [f"  {directory}: {', '.join(names)}" for directory, names in msvc.items()]
    return out


def _site_packages(python_root: Path) -> Path | None:
    for candidate in (python_root / "Lib" / "site-packages", *sorted(python_root.glob("lib/python3.*/site-packages"))):
        if candidate.is_dir():
            return candidate
    return None


def write_all(root: Path, manifest: dict, *, node_bin: Path, platform: str | None = None) -> dict:
    """Write THIRD_PARTY_NOTICES.txt, SOURCE_OFFER.txt and licenses/.

    On the build host's own platform the bundled interpreters are asked via pip-licenses;
    for a foreign target (Windows staged on Linux) dist-info metadata is read directly.
    """
    platform = platform or PLATFORM_KEY
    foreign = platform != PLATFORM_KEY
    lic_dir = root / "licenses"
    lic_dir.mkdir(exist_ok=True)
    lcpp_tag = manifest["llama_cpp"]["version"]
    (lic_dir / "llama.cpp-LICENSE").write_text(
        _fetch_text(f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{lcpp_tag}/LICENSE"))
    (lic_dir / "llama.cpp-LICENSE-jsonhpp").write_text(
        _fetch_text(f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{lcpp_tag}/licenses/LICENSE-jsonhpp"))
    ds_license = Path(__file__).resolve().parents[1] / "LICENSE"
    has_ds_license = ds_license.is_file()
    if has_ds_license:
        shutil.copy2(ds_license, lic_dir / "DirectorStudio-LICENSE")

    rows: list[dict] = []
    for sub, label in (("python", "backend (runtime/python)"), ("comfy-python", "ComfyUI (runtime/comfy-python)")):
        if sub == "comfy-python":
            rows += scan_site_dir(root / "runtime" / "comfy-tools", label="comfy tools (runtime/comfy-tools, separate process)")
        if not foreign:
            rows += scan_python(root / "runtime" / sub / "bin" / "python3", label=label)
        elif (site := _site_packages(root / "runtime" / sub)) is not None:
            rows += scan_site_dir(site, label=label)
    repo = Path(__file__).resolve().parents[1]
    rows += scan_npm(repo / "frontend", node_bin, label="frontend (bundled in frontend/dist)")
    rows += scan_node_modules(root / "harness" / "node_modules", label="harness (harness/node_modules)")
    apply_overrides(rows)
    natives = native_components(root, manifest, platform)
    staged_nodes = [n["name"] for n in manifest["custom_nodes"]
                    if (root / "runtime" / "ComfyUI" / "custom_nodes" / n["name"]).is_dir()]

    out: list[str] = [
        "Director Studio - third-party notices",
        "=" * 72,
        *header_lines(has_ds_license, staged_nodes),
        "",
        "Native components / programs",
        "-" * 72,
    ]
    for comp in natives:
        out.append(f"* {comp['Name']} {comp['Version']} - {comp['License']} - {comp['URL']}")
        out.append(f"    location: {comp['path']}; licence text: {', '.join(comp['license_files']) or '(see upstream)'}")
        if comp.get("note"):
            out.append(f"    {comp['note']}")
    vendor = vendor_runtime_notice(root)
    out += vendor
    if vendor:
        (lic_dir / "VENDOR-RUNTIMES.txt").write_text("\n".join(vendor).lstrip("\n") + "\n", encoding="utf-8")
    by_env: dict[str, list[dict]] = {}
    for row in rows:
        by_env.setdefault(row["env"], []).append(row)
    for env, items in by_env.items():
        out += ["", f"Packages: {env}", "-" * 72]
        for row in sorted(items, key=lambda r: r["Name"].lower()):
            out.append(f"* {row['Name']} {row['Version']} - {row.get('License')} - {row.get('URL') or ''}")
    out += ["", "", "Full licence texts", "=" * 72]
    seen: set[str] = set()
    for row in rows:
        text = (row.get("LicenseText") or "").strip()
        if not text or text == "UNKNOWN":
            continue
        key = f"{row['Name']}@{row['Version']}"
        if key in seen:
            continue
        seen.add(key)
        notice = (row.get("NoticeText") or "").strip()
        out += ["", f"---- {key} ({row.get('License')}) ----", text]
        if notice and notice != "UNKNOWN":
            out += ["", notice]
    (root / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(out) + "\n", encoding="utf-8")

    copyleft_pkgs = [r for r in rows if COPYLEFT.search(str(r.get("License") or ""))]
    offer = [
        "Director Studio - source code offer for copyleft components",
        "=" * 72,
        "This package contains the following components licensed under the GNU GPL, LGPL or",
        "other copyleft licences. They are unmodified upstream releases, distributed as",
        "separate programs. The exact corresponding source code is available at the URLs",
        "below. For at least three years after this package was distributed, the distributor",
        "will also provide the complete corresponding source code on request for no more",
        "than the cost of physically performing the distribution (contact: see README).",
        "",
    ]
    for comp in natives:
        if comp.get("copyleft"):
            offer.append(f"* {comp['Name']} {comp['Version']} ({comp['License']})\n    source: {comp['source']}")
    tools = manifest["comfy_tools"]
    for name in ("comfy-cli", "comfy-mcp"):
        offer.append(f"* {name} {tools[name]} (runtime/comfy-tools)\n    source: https://pypi.org/project/{name}/{tools[name]}/#files")
    for row in sorted(copyleft_pkgs, key=lambda r: r["Name"].lower()):
        if row["Name"] in ("comfy-cli", "comfy-mcp"):
            continue
        url = row.get("URL") or ""
        src = f"https://pypi.org/project/{row['Name']}/{row['Version']}/#files" if "npm" not in row["env"] and "harness" not in row["env"] and "frontend" not in row["env"] \
            else f"https://www.npmjs.com/package/{row['Name']}/v/{row['Version']}"
        offer.append(f"* {row['Name']} {row['Version']} ({row['License']}) [{row['env']}]\n    source: {src} {url}")
    (root / "SOURCE_OFFER.txt").write_text("\n".join(offer) + "\n", encoding="utf-8")
    unknown = sorted({f"{r['Name']} {r['Version']} [{r['env']}]" for r in rows
                      if str(r.get("License") or "UNKNOWN").strip().upper() in ("UNKNOWN", "", "NONE", "[]")})
    if unknown:
        out_unknown = ["", "Packages without machine-readable licence metadata (review manually):"] + [f"* {u}" for u in unknown]
        with open(root / "THIRD_PARTY_NOTICES.txt", "a", encoding="utf-8") as fh:
            fh.write("\n".join(out_unknown) + "\n")
    summary = {"unknown": unknown, "python_packages": sum(1 for r in rows if "npm" not in r["env"]), "rows": len(rows),
               "copyleft": [f"{r['Name']} {r['Version']} ({r['License']})" for r in copyleft_pkgs]}
    (lic_dir / "inventory.json").write_text(json.dumps(
        {"native": natives, "packages": [{k: v for k, v in r.items() if k not in ("LicenseText", "NoticeText")} for r in rows]},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    _log(f"{len(rows)} packages, copyleft: {summary['copyleft']}, unknown licence: {unknown}")
    return summary
