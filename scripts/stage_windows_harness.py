from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
from typing import Callable, Sequence
from urllib.request import Request, urlopen
import zipfile


CONFIG_PATH = Path(__file__).resolve().parents[1] / "packaging" / "windows-harness-runtime.json"
_DEV_PACKAGES = ("tsx", "typescript", "vitest", "@vitest")
_LICENSE_PREFIXES = ("license", "copying", "notice")


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
    koffi_binary: Path


def load_runtime_config(path: Path = CONFIG_PATH) -> RuntimeConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = RuntimeConfig(
            platform=str(raw["platform"]),
            arch=str(raw["arch"]),
            version=str(raw["node_version"]),
            archive=str(raw["node_archive"]),
            url=str(raw["node_url"]),
            sha256=str(raw["node_sha256"]),
            koffi_binary=Path(str(raw["koffi_binary"])),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageError(f"Invalid Windows Harness runtime config: {exc}") from exc
    if config.platform != "windows" or config.arch != "x64":
        raise StageError("Windows Harness runtime config must target windows x64")
    if len(config.sha256) != 64 or any(c not in "0123456789abcdef" for c in config.sha256):
        raise StageError("Node checksum must be 64 lowercase hexadecimal characters")
    return config


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise StageError(f"Could not read Node archive: {exc}") from exc
    actual = digest.hexdigest()
    if actual != expected:
        raise StageError(
            f"Node archive checksum mismatch: expected {expected}, got {actual}"
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


def extract_node_runtime(
    archive_path: Path,
    destination: Path,
    config: RuntimeConfig,
) -> None:
    verify_sha256(archive_path, config.sha256)
    expected_root = Path(config.archive).stem
    wanted = {
        (expected_root, "node.exe"): "node.exe",
        (expected_root, "LICENSE"): "LICENSE",
    }
    found: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                parts = _safe_zip_parts(info)
                if parts[0] != expected_root:
                    raise StageError(
                        f"unsafe archive member outside {expected_root}: {info.filename}"
                    )
                target = wanted.get(parts)
                if target is not None:
                    if info.is_dir():
                        raise StageError(f"Node runtime entry is not a file: {info.filename}")
                    found[target] = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise StageError(f"Could not extract Node archive: {exc}") from exc
    missing = sorted(set(wanted.values()) - set(found))
    if missing:
        raise StageError(f"Node archive is missing: {', '.join(missing)}")
    runtime = destination / "runtime" / "node"
    runtime.mkdir(parents=True, exist_ok=True)
    for name, contents in found.items():
        (runtime / name).write_bytes(contents)


def _copy_harness_source(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("node_modules", "dist", ".vitest"),
        dirs_exist_ok=True,
    )


def _collect_licenses(node_modules: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for package_json in sorted(node_modules.rglob("package.json")):
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        for candidate in sorted(package_json.parent.iterdir()):
            if not candidate.is_file() or not candidate.name.lower().startswith(_LICENSE_PREFIXES):
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            records.append(
                {
                    "name": name,
                    "path": candidate.relative_to(node_modules).as_posix(),
                    "text": text,
                    "version": version,
                }
            )
    return sorted(records, key=lambda item: (item["name"], item["path"]))


def _remove_empty_directories(root: Path) -> None:
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def stage_harness(
    repo_root: Path,
    destination: Path,
    config: RuntimeConfig,
    *,
    npm: str,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    source = repo_root / "harness"
    target = destination / "harness"
    if target.exists():
        raise StageError(f"Harness staging destination already exists: {target}")
    with tempfile.TemporaryDirectory(prefix="director-harness-build-") as temporary:
        temporary_root = Path(temporary)
        build_root = temporary_root / "build"
        runtime_root = temporary_root / "runtime"
        _copy_harness_source(source, build_root)
        runner([npm, "ci"], cwd=build_root, check=True)
        runner([npm, "run", "build"], cwd=build_root, check=True)
        emitted = build_root / "dist" / "server.js"
        if not emitted.is_file():
            raise StageError("Harness build did not emit dist/server.js")

        runtime_root.mkdir()
        shutil.copy2(source / "package.json", runtime_root / "package.json")
        shutil.copy2(source / "package-lock.json", runtime_root / "package-lock.json")
        runner([npm, "ci", "--omit=dev"], cwd=runtime_root, check=True)
        _remove_empty_directories(runtime_root / "node_modules")
        shutil.copytree(build_root / "dist", runtime_root / "dist")
        (runtime_root / "package-lock.json").unlink()
        shutil.copytree(runtime_root, target)

    koffi = destination / config.koffi_binary
    if not koffi.is_file():
        raise StageError(f"Production Harness is missing Koffi binary: {config.koffi_binary}")
    for relative in _DEV_PACKAGES:
        if (target / "node_modules" / relative).exists():
            raise StageError(f"Production Harness contains development package: {relative}")
    licenses = _collect_licenses(target / "node_modules")
    (target / "THIRD_PARTY_LICENSES.json").write_text(
        json.dumps(licenses, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_portable_manifest(
    repo_root: Path,
    destination: Path,
    config: RuntimeConfig,
) -> None:
    package = json.loads(
        (repo_root / "harness" / "package.json").read_text(encoding="utf-8")
    )
    lock_bytes = (repo_root / "harness" / "package-lock.json").read_bytes()
    comfy_config = json.loads(
        (repo_root / "packaging" / "windows-comfy-runtime.json").read_text(
            encoding="utf-8"
        )
    )
    comfy_lock = (
        repo_root / "packaging" / "windows-comfy-requirements.lock"
    ).read_bytes()
    manifest = {
        "comfy_bootstrap": {
            "packages": {
                "comfy-cli": str(comfy_config["comfy_cli_version"]),
                "comfy-mcp": str(comfy_config["comfy_mcp_version"]),
            },
            "pip_version": str(comfy_config["pip_version"]),
            "pip_wheel_sha256": str(comfy_config["pip_sha256"]),
            "requirements_lock_sha256": hashlib.sha256(comfy_lock).hexdigest(),
        },
        "entrypoint": "harness/dist/server.js",
        "format": 1,
        "harness": {
            "package_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "version": str(package["version"]),
        },
        "node": {
            "archive_sha256": config.sha256,
            "version": config.version,
        },
        "python": {
            "archive_sha256": str(comfy_config["python_sha256"]),
            "version": str(comfy_config["python_version"]),
        },
        "platform": "win-x64",
    }
    (destination / "portable-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "Director-Studio-Portable-Builder"})
    try:
        with urlopen(request, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except OSError as exc:
        raise StageError(f"Could not download pinned Node archive: {exc}") from exc


def stage_windows_harness(
    repo_root: Path,
    destination: Path,
    *,
    node_archive: Path | None = None,
) -> None:
    config = load_runtime_config()
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise StageError("npm is required on the build machine")
    destination.mkdir(parents=True, exist_ok=True)
    if node_archive is not None:
        extract_node_runtime(node_archive, destination, config)
    else:
        with tempfile.TemporaryDirectory(prefix="director-node-download-") as temporary:
            downloaded = Path(temporary) / config.archive
            _download(config.url, downloaded)
            extract_node_runtime(downloaded, destination, config)
    stage_harness(repo_root, destination, config, npm=npm)
    write_portable_manifest(repo_root, destination, config)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage Windows Harness portable runtime")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--node-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        stage_windows_harness(
            args.repo_root.resolve(),
            args.destination.resolve(),
            node_archive=args.node_archive.resolve() if args.node_archive else None,
        )
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError, StageError) as exc:
        parser.exit(1, f"Windows Harness staging failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
