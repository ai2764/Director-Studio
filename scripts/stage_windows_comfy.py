from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Sequence
from urllib.request import Request, urlopen
import zipfile

CONFIG_PATH = Path(__file__).resolve().parents[1] / "packaging" / "windows-comfy-runtime.json"
_PYTHON_FILES = (
    "python.exe",
    "python313.dll",
    "python313.zip",
    "python313._pth",
    "LICENSE.txt",
)


class StageError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    platform: str
    arch: str
    version: str
    archive: str
    url: str
    sha256: str
    pip_version: str
    pip_wheel: str
    pip_url: str
    pip_sha256: str
    comfy_mcp_version: str
    comfy_cli_version: str


def load_runtime_config(path: Path = CONFIG_PATH) -> RuntimeConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = RuntimeConfig(
            platform=str(raw["platform"]),
            arch=str(raw["arch"]),
            version=str(raw["python_version"]),
            archive=str(raw["python_archive"]),
            url=str(raw["python_url"]),
            sha256=str(raw["python_sha256"]),
            pip_version=str(raw["pip_version"]),
            pip_wheel=str(raw["pip_wheel"]),
            pip_url=str(raw["pip_url"]),
            pip_sha256=str(raw["pip_sha256"]),
            comfy_mcp_version=str(raw["comfy_mcp_version"]),
            comfy_cli_version=str(raw["comfy_cli_version"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageError(f"Invalid Windows Comfy runtime config: {exc}") from exc
    if config.platform != "windows" or config.arch != "x64":
        raise StageError("Windows Comfy runtime config must target windows x64")
    for label, checksum in (
        ("Python", config.sha256),
        ("pip", config.pip_sha256),
    ):
        if len(checksum) != 64 or any(
            char not in "0123456789abcdef" for char in checksum
        ):
            raise StageError(
                f"{label} checksum must be 64 lowercase hexadecimal characters"
            )
    if config.pip_version != "25.1.1" or config.pip_wheel != "pip-25.1.1-py3-none-any.whl":
        raise StageError("Windows bootstrap pip version is not approved")
    if config.comfy_mcp_version != "0.10.0" or config.comfy_cli_version != "1.20.0":
        raise StageError("Windows Comfy runtime package versions are not approved")
    return config


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise StageError(f"Could not read Python archive: {exc}") from exc
    actual = digest.hexdigest()
    if actual != expected:
        raise StageError(
            f"Python archive checksum mismatch: expected {expected}, got {actual}"
        )


def _safe_zip_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    normalized = info.filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part not in {"", "."})
    mode = info.external_attr >> 16
    if (
        path.is_absolute()
        or not parts
        or ".." in parts
        or (len(parts[0]) >= 2 and parts[0][1] == ":")
        or stat.S_IFMT(mode) == stat.S_IFLNK
    ):
        raise StageError(f"unsafe archive member: {info.filename}")
    return parts


def extract_python_runtime(
    archive_path: Path,
    destination: Path,
    config: RuntimeConfig,
) -> None:
    verify_sha256(archive_path, config.sha256)
    found: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                parts = _safe_zip_parts(info)
                if len(parts) != 1:
                    raise StageError(f"unsafe archive member: {info.filename}")
                name = parts[0]
                if info.is_dir():
                    raise StageError(f"Python runtime entry is not a file: {name}")
                if name in found:
                    raise StageError(f"Python archive has duplicate entry: {name}")
                found[name] = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise StageError(f"Could not extract Python archive: {exc}") from exc
    missing = sorted(set(_PYTHON_FILES) - set(found))
    if missing:
        raise StageError(f"Python archive is missing: {', '.join(missing)}")

    runtime = destination / "runtime" / "python"
    runtime.mkdir(parents=True, exist_ok=True)
    for name, contents in found.items():
        if name not in {"LICENSE.txt", "pythonw.exe", "python313._pth"}:
            (runtime / name).write_bytes(contents)
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\nLib/site-packages\n"
        "../../data/tools/comfy/site-packages\nimport site\n",
        encoding="utf-8",
    )
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    (site_packages / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "import site\n"
        "import sys\n\n"
        "_private_site = (Path(sys.executable).resolve().parents[2] / "
        "'data' / 'tools' / 'comfy' / 'site-packages')\n"
        "if _private_site.is_dir():\n"
        "    site.addsitedir(str(_private_site))\n",
        encoding="utf-8",
    )
    licenses = destination / "THIRD_PARTY_LICENSES"
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / "python.txt").write_bytes(found["LICENSE.txt"])


def extract_pip_runtime(
    wheel_path: Path,
    destination: Path,
    config: RuntimeConfig,
) -> None:
    verify_sha256(wheel_path, config.pip_sha256)
    site = destination / "runtime" / "python" / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, ...]] = set()
    license_text: str | None = None
    try:
        with zipfile.ZipFile(wheel_path) as wheel:
            for info in wheel.infolist():
                parts = _safe_zip_parts(info)
                if parts in seen:
                    raise StageError(f"pip wheel has duplicate entry: {info.filename}")
                seen.add(parts)
                if not (
                    parts[0] == "pip"
                    or parts[0] == f"pip-{config.pip_version}.dist-info"
                ):
                    raise StageError(f"pip wheel has unexpected entry: {info.filename}")
                target = site.joinpath(*parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(wheel.read(info))
                if parts[-1].lower() in {"license", "license.txt", "copying"}:
                    license_text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise StageError(f"Could not extract pinned pip wheel: {exc}") from exc
    if not (site / "pip" / "__init__.py").is_file() or not license_text:
        raise StageError("Pinned pip wheel is incomplete")
    licenses = destination / "THIRD_PARTY_LICENSES"
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / "pip.txt").write_text(license_text, encoding="utf-8")


def write_bootstrap_metadata(
    destination: Path,
    lock_path: Path,
    config: RuntimeConfig,
) -> None:
    validate_requirements_lock(lock_path, config)
    runtime = destination / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    bundled_lock = runtime / "comfy-requirements.lock"
    shutil.copyfile(lock_path, bundled_lock)
    manifest = {
        "lock_sha256": hashlib.sha256(bundled_lock.read_bytes()).hexdigest(),
        "packages": {
            "comfy-cli": config.comfy_cli_version,
            "comfy-mcp": config.comfy_mcp_version,
        },
    }
    (runtime / "comfy-bootstrap.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def validate_requirements_lock(path: Path, config: RuntimeConfig) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StageError(f"Could not read Comfy requirements lock: {exc}") from exc
    logical = re.sub(r"\\\r?\n\s*", " ", content)
    requirements = [
        line.strip()
        for line in logical.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not requirements:
        raise StageError("Comfy requirements lock is empty")
    for requirement in requirements:
        if "==" not in requirement:
            raise StageError(f"Comfy requirement is not exact: {requirement}")
        if not re.search(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)", requirement):
            raise StageError(f"Comfy requirement has no valid hash: {requirement}")
    normalized = "\n".join(requirements).lower().replace("_", "-")
    for package, version in (
        ("comfy-mcp", config.comfy_mcp_version),
        ("comfy-cli", config.comfy_cli_version),
    ):
        if not re.search(rf"(?:^|\n){re.escape(package)}=={re.escape(version)}(?:\s|$)", normalized):
            raise StageError(f"Comfy requirements lock is missing {package}=={version}")


def write_comfy_launcher(runtime: Path) -> None:
    try:
        from pip._vendor.distlib.scripts import ScriptMaker
    except ImportError as exc:
        raise StageError("Build Python does not provide pip distlib") from exc
    maker = ScriptMaker(None, str(runtime))
    maker.clobber = True
    maker.executable = "python.exe"
    maker.variants = {""}
    outputs = [Path(path) for path in maker.make("comfy = comfy_cli.__main__:main")]
    launcher = runtime / "comfy.exe"
    if launcher not in outputs or not launcher.is_file():
        raise StageError("Could not generate relocatable comfy.exe launcher")


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "Director-Studio-Portable-Builder"})
    try:
        with urlopen(request, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except OSError as exc:
        raise StageError(f"Could not download pinned Python archive: {exc}") from exc


def stage_windows_comfy(
    repo_root: Path,
    destination: Path,
    *,
    python_archive: Path | None = None,
    pip_wheel: Path | None = None,
) -> None:
    config = load_runtime_config()
    lock_path = repo_root / "packaging" / "windows-comfy-requirements.lock"
    runtime = destination / "runtime" / "python"
    if runtime.exists():
        raise StageError(f"Comfy runtime staging destination already exists: {runtime}")
    destination.mkdir(parents=True, exist_ok=True)

    if python_archive is not None:
        extract_python_runtime(python_archive, destination, config)
    else:
        with tempfile.TemporaryDirectory(prefix="director-python-download-") as temporary:
            downloaded = Path(temporary) / config.archive
            _download(config.url, downloaded)
            extract_python_runtime(downloaded, destination, config)

    if pip_wheel is not None:
        extract_pip_runtime(pip_wheel, destination, config)
    else:
        with tempfile.TemporaryDirectory(prefix="director-pip-download-") as temporary:
            downloaded = Path(temporary) / config.pip_wheel
            _download(config.pip_url, downloaded)
            extract_pip_runtime(downloaded, destination, config)

    write_comfy_launcher(runtime)
    write_bootstrap_metadata(destination, lock_path, config)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage Windows Comfy MCP runtime")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path)
    parser.add_argument("--pip-wheel", type=Path)
    args = parser.parse_args(argv)
    try:
        stage_windows_comfy(
            args.repo_root.resolve(),
            args.destination.resolve(),
            python_archive=args.python_archive.resolve() if args.python_archive else None,
            pip_wheel=args.pip_wheel.resolve() if args.pip_wheel else None,
        )
    except (OSError, ValueError, subprocess.CalledProcessError, StageError) as exc:
        parser.exit(1, f"Windows Comfy staging failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
