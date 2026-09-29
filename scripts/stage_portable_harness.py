from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Callable, Sequence
from urllib.request import Request, urlopen
import zipfile


CONFIG_PATH = Path(__file__).resolve().parents[1] / "packaging" / "portable-harness-runtimes.json"
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
    target: str = "windows-x64"
    archive_kind: str = "zip"
    archive_root: str = ""
    node_member: str = "node.exe"
    node_name: str = "node.exe"


def load_runtime_configs(path: Path = CONFIG_PATH) -> dict[str, RuntimeConfig]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = str(raw["node_version"])
        configs = {
            target: RuntimeConfig(
                target=target,
                platform=str(item["platform"]),
                arch=str(item["arch"]),
                version=version,
                archive=str(item["archive"]),
                archive_kind=str(item["archive_kind"]),
                archive_root=str(item["archive_root"]),
                url=str(item["url"]),
                sha256=str(item["sha256"]),
                node_member=str(item["node_member"]),
                node_name=str(item["node_name"]),
                koffi_binary=Path(str(item["koffi_binary"])),
            )
            for target, item in raw["runtimes"].items()
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageError(f"Invalid portable Harness runtime config: {exc}") from exc
    expected = {"windows-x64", "linux-x86_64", "macos-arm64", "macos-x86_64"}
    if set(configs) != expected:
        raise StageError(f"Portable Harness runtimes must be exactly: {', '.join(sorted(expected))}")
    for config in configs.values():
        if config.archive_kind not in {"zip", "tar.gz", "tar.xz"}:
            raise StageError(f"Unsupported Node archive kind for {config.target}")
        if config.node_name not in {"node", "node.exe"}:
            raise StageError(f"Invalid Node executable name for {config.target}")
        if len(config.sha256) != 64 or any(c not in "0123456789abcdef" for c in config.sha256):
            raise StageError(f"Node checksum for {config.target} must be 64 lowercase hexadecimal characters")
    return configs


def load_runtime_config(path: Path = CONFIG_PATH, *, target: str = "windows-x64") -> RuntimeConfig:
    try:
        return load_runtime_configs(path)[target]
    except KeyError as exc:
        raise StageError(f"Unknown portable Harness target: {target}") from exc


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
        raise StageError(f"Node archive checksum mismatch: expected {expected}, got {actual}")


def _safe_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if path.is_absolute() or not parts or ".." in parts or (len(parts[0]) >= 2 and parts[0][1] == ":"):
        raise StageError(f"unsafe archive member: {name}")
    return parts


def extract_node_runtime(archive_path: Path, destination: Path, config: RuntimeConfig) -> None:
    verify_sha256(archive_path, config.sha256)
    root = config.archive_root or Path(config.archive).stem
    wanted = {
        tuple(PurePosixPath(root, config.node_member).parts): config.node_name,
        tuple(PurePosixPath(root, "LICENSE").parts): "LICENSE",
    }
    found: dict[str, bytes] = {}
    try:
        if config.archive_kind == "zip":
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    is_link = stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
                    parts = _safe_parts(info.filename)
                    if parts[0] != root:
                        raise StageError(f"unsafe archive member outside {root}: {info.filename}")
                    target = wanted.get(parts)
                    if target is not None:
                        if is_link or info.is_dir():
                            raise StageError(f"Node runtime entry is not a file: {info.filename}")
                        found[target] = archive.read(info)
        else:
            with tarfile.open(archive_path, "r:*") as archive:
                for member in archive.getmembers():
                    parts = _safe_parts(member.name)
                    if parts[0] != root:
                        raise StageError(f"unsafe archive member outside {root}: {member.name}")
                    target = wanted.get(parts)
                    if target is not None:
                        if not member.isfile():
                            raise StageError(f"Node runtime entry is not a file: {member.name}")
                        source = archive.extractfile(member)
                        if source is None:
                            raise StageError(f"Node runtime entry is unreadable: {member.name}")
                        found[target] = source.read()
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise StageError(f"Could not extract Node archive: {exc}") from exc
    missing = sorted(set(wanted.values()) - set(found))
    if missing:
        raise StageError(f"Node archive is missing: {', '.join(missing)}")
    runtime = destination / "runtime" / "node"
    runtime.mkdir(parents=True, exist_ok=True)
    for name, contents in found.items():
        path = runtime / name
        path.write_bytes(contents)
        path.chmod(0o755 if name == config.node_name else 0o644)


def _copy_harness_source(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("node_modules", "dist", ".vitest"), dirs_exist_ok=True)


def _collect_licenses(node_modules: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for package_json in sorted(node_modules.rglob("package.json")):
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        name, version = package.get("name"), package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        for candidate in sorted(package_json.parent.iterdir()):
            if not candidate.is_file() or not candidate.name.lower().startswith(_LICENSE_PREFIXES):
                continue
            try:
                contents = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            records.append({"name": name, "path": candidate.relative_to(node_modules).as_posix(), "text": contents, "version": version})
    return sorted(records, key=lambda item: (item["name"], item["path"]))


def _remove_empty_directories(root: Path) -> None:
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def stage_harness(repo_root: Path, destination: Path, config: RuntimeConfig, *, npm: str, runner: Callable[..., object] = subprocess.run) -> None:
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
    (target / "THIRD_PARTY_LICENSES.json").write_text(
        json.dumps(_collect_licenses(target / "node_modules"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_portable_manifest(repo_root: Path, destination: Path, config: RuntimeConfig) -> None:
    package = json.loads((repo_root / "harness" / "package.json").read_text(encoding="utf-8"))
    lock_bytes = (repo_root / "harness" / "package-lock.json").read_bytes()
    manifest = {
        "entrypoint": "harness/dist/server.js",
        "format": 1,
        "harness": {"package_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(), "version": str(package["version"])},
        "node": {
            "archive_sha256": config.sha256,
            "executable": f"runtime/node/{config.node_name}",
            "version": config.version,
        },
        "platform": config.platform,
    }
    if config.target == "windows-x64":
        comfy = json.loads((repo_root / "packaging" / "windows-comfy-runtime.json").read_text(encoding="utf-8"))
        comfy_lock = (repo_root / "packaging" / "windows-comfy-requirements.lock").read_bytes()
        manifest.update(
            python={"archive_sha256": str(comfy["python_sha256"]), "version": str(comfy["python_version"])},
            comfy_bootstrap={
                "packages": {"comfy-cli": str(comfy["comfy_cli_version"]), "comfy-mcp": str(comfy["comfy_mcp_version"])},
                "pip_version": str(comfy["pip_version"]),
                "pip_wheel_sha256": str(comfy["pip_sha256"]),
                "requirements_lock_sha256": hashlib.sha256(comfy_lock).hexdigest(),
            },
        )
    (destination / "portable-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "Director-Studio-Portable-Builder"})
    try:
        with urlopen(request, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except OSError as exc:
        raise StageError(f"Could not download pinned Node archive: {exc}") from exc


def stage_portable_harness(repo_root: Path, destination: Path, *, target: str, node_archive: Path | None = None) -> None:
    config = load_runtime_config(target=target)
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
    parser = argparse.ArgumentParser(description="Stage a native Harness portable runtime")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(load_runtime_configs()), required=True)
    parser.add_argument("--node-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        stage_portable_harness(
            args.repo_root.resolve(), args.destination.resolve(), target=args.target,
            node_archive=args.node_archive.resolve() if args.node_archive else None,
        )
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError, StageError) as exc:
        parser.exit(1, f"Portable Harness staging failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
