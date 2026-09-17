from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


@dataclass(frozen=True)
class PackageFlavor:
    name: str
    executable: str
    wrappers: tuple[str, ...]
    archive_kind: Literal["zip", "tar"]
    bundled_harness: bool = False


FLAVORS = {
    "macos-arm64": PackageFlavor(
        "Director-Studio-macOS-arm64",
        "DirectorStudio",
        ("install-tools.sh", "launch.sh", "Launch.command", "Install-Tools.command"),
        "tar",
    ),
    "macos-x86_64": PackageFlavor(
        "Director-Studio-macOS-x86_64",
        "DirectorStudio",
        ("install-tools.sh", "launch.sh", "Launch.command", "Install-Tools.command"),
        "tar",
    ),
    "windows": PackageFlavor(
        "Director-Studio-Windows-x64",
        "DirectorStudio.exe",
        (),
        "zip",
        True,
    ),
    "linux": PackageFlavor(
        "Director-Studio-Linux-x86_64",
        "DirectorStudio",
        ("install-tools.sh", "launch.sh"),
        "tar",
    ),
}

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WINDOWS_RUNTIME_CONFIG = json.loads(
    (_REPO_ROOT / "packaging" / "windows-harness-runtime.json").read_text(
        encoding="utf-8"
    )
)
WINDOWS_KOFFI_BINARY = str(_WINDOWS_RUNTIME_CONFIG["koffi_binary"])
_WINDOWS_NODE_VERSION = str(_WINDOWS_RUNTIME_CONFIG["node_version"])
_WINDOWS_NODE_SHA256 = str(_WINDOWS_RUNTIME_CONFIG["node_sha256"])
_WINDOWS_COMFY_CONFIG = json.loads(
    (_REPO_ROOT / "packaging" / "windows-comfy-runtime.json").read_text(
        encoding="utf-8"
    )
)
_WINDOWS_COMFY_LOCK_SHA256 = hashlib.sha256(
    (_REPO_ROOT / "packaging" / "windows-comfy-requirements.lock").read_bytes()
).hexdigest()
_WINDOWS_HARNESS_FILES = (
    "runtime/node/node.exe",
    "runtime/node/LICENSE",
    "harness/dist/server.js",
    "harness/package.json",
    "harness/THIRD_PARTY_LICENSES.json",
    WINDOWS_KOFFI_BINARY,
    "portable-manifest.json",
)
_WINDOWS_COMFY_FILES = (
    "runtime/python/python.exe",
    "runtime/python/python3.dll",
    "runtime/python/python313.dll",
    "runtime/python/python313.zip",
    "runtime/python/python313._pth",
    "runtime/python/comfy.exe",
    "runtime/python/Lib/site-packages/pip/__init__.py",
    "runtime/python/Lib/site-packages/pip-25.1.1.dist-info/METADATA",
    "runtime/python/Lib/site-packages/pip-25.1.1.dist-info/licenses/LICENSE.txt",
    "runtime/python/Lib/site-packages/sitecustomize.py",
    "runtime/comfy-bootstrap.json",
    "runtime/comfy-requirements.lock",
    "THIRD_PARTY_LICENSES/python.txt",
    "THIRD_PARTY_LICENSES/pip.txt",
)
_WINDOWS_FORBIDDEN_DEV_PACKAGES = (
    "harness/node_modules/tsx",
    "harness/node_modules/typescript",
    "harness/node_modules/vitest",
    "harness/node_modules/@vitest",
)

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

FORBIDDEN_DIRECTORY_NAMES = {
    "data",
    "projects",
    "jobs",
    "outputs",
    "tests",
    "workflow_profiles",
}
_CREDENTIAL_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".kdbx"}
_CREDENTIAL_NAME_PARTS = ("credential", "secret", "token")
_PUBLIC_CA_BUNDLE_PATHS = {("certifi", "cacert.pem")}
_SECRET_KEY_PATTERN = re.compile(r"(?:secret|token|password|api[_-]?key)", re.I)
_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:/")


def required_package_files(flavor: PackageFlavor) -> tuple[str, ...]:
    installer_files = () if flavor.bundled_harness else (
        "Install-Tools.py",
        "portable-tools-requirements.txt",
    )
    common = (
        flavor.executable,
        *flavor.wrappers,
        *installer_files,
        ".env",
        "README.md",
    )
    return common + (
        _WINDOWS_HARNESS_FILES + _WINDOWS_COMFY_FILES
        if flavor.bundled_harness
        else ()
    )


def _normalize_path(value: str) -> str:
    return "/".join(part for part in value.replace("\\", "/").split("/") if part)


def parse_pyinstaller_listing(text: str) -> set[str]:
    entries: set[str] = set()
    for line in text.splitlines():
        candidate = line.strip()
        quoted = re.search(r",\s*'([^']+)'\s*$", candidate)
        if quoted:
            candidate = quoted.group(1)
        elif not re.fullmatch(r"[^\s,]+", candidate):
            continue
        candidate = _normalize_path(candidate.strip("'\""))
        if candidate:
            entries.add(candidate)
    return entries


def _validated_parts(path: str, *, label: str) -> tuple[str, ...]:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_PATH_PATTERN.match(normalized):
        raise ValueError(f"{label} contains an absolute path: {path}")
    raw_parts = normalized.split("/")
    if ".." in raw_parts:
        raise ValueError(f"{label} contains path traversal: {path}")
    parts = tuple(part for part in raw_parts if part not in ("", "."))
    if not parts:
        raise ValueError(f"{label} has an empty path")
    return parts


def _validate_content_path(parts: tuple[str, ...], *, label: str) -> None:
    for index, part in enumerate(parts[:-1]):
        part_lower = part.lower()
        if part_lower == "workflow_profiles" and not (
            index == 1 and parts[0].lower() == "app"
        ):
            raise ValueError(f"{label} contains forbidden directory: workflow_profiles")
    for index, part in enumerate(parts[:-1]):
        part_lower = part.lower()
        if part_lower not in FORBIDDEN_DIRECTORY_NAMES:
            continue
        if (
            part_lower == "workflow_profiles"
            and index == 1
            and parts[0].lower() == "app"
        ):
            continue
        raise ValueError(f"{label} contains forbidden directory: {part}")

    filename = parts[-1].lower()
    normalized_parts = tuple(part.lower() for part in parts)
    if filename == "active.json":
        raise ValueError(f"{label} contains forbidden active workflow state: {parts[-1]}")
    if filename.startswith("test_") or ".test." in filename:
        raise ValueError(f"{label} contains forbidden test content: {parts[-1]}")
    if (
        (
            Path(filename).suffix in _CREDENTIAL_SUFFIXES
            and normalized_parts not in _PUBLIC_CA_BUNDLE_PATHS
        )
        or any(part in filename for part in _CREDENTIAL_NAME_PARTS)
    ):
        raise ValueError(f"{label} contains credential-like file: {parts[-1]}")


def _is_under(parts: tuple[str, ...], prefix: str) -> bool:
    normalized = "/".join(part.lower() for part in parts)
    expected = prefix.lower()
    return normalized == expected or normalized.startswith(expected + "/")


def _validate_package_content_path(
    parts: tuple[str, ...],
    *,
    label: str,
    flavor: PackageFlavor,
) -> None:
    if flavor.bundled_harness and parts[0].lower() in {
        "install-tools.cmd",
        "install-tools.py",
        "portable-tools-requirements.txt",
    }:
        raise ValueError(f"{label} contains obsolete Windows installer: {parts[0]}")
    if not flavor.bundled_harness and parts[0].lower() in {"harness", "runtime"}:
        raise ValueError(f"{label} contains unsupported Harness runtime content")
    if flavor.bundled_harness and _is_under(parts, "harness/node_modules"):
        for forbidden in _WINDOWS_FORBIDDEN_DEV_PACKAGES:
            if _is_under(parts, forbidden):
                raise ValueError(
                    f"{label} contains development package: {forbidden}"
                )
        return
    if flavor.bundled_harness and _is_under(
        parts, "runtime/python/Lib/site-packages"
    ):
        if len(parts) == 4 or (len(parts) == 5 and parts[4] == "directory"):
            return
        package_name = parts[4].lower()
        if package_name == "sitecustomize.py" and len(parts) == 5:
            return
        if package_name.split("-", 1)[0] in {"comfy_mcp", "comfy_cli"}:
            raise ValueError(
                f"{label} contains first-launch dependency: {parts[4]}"
            )
        if package_name.split("-", 1)[0] in {"setuptools", "wheel", "uv"}:
            raise ValueError(f"{label} contains Python build tool: {parts[4]}")
        if package_name != "pip" and not package_name.startswith("pip-"):
            raise ValueError(f"{label} contains unexpected bootstrap package: {parts[4]}")
        dependency_parts = tuple(part.lower() for part in parts[4:])
        if any(part in {"test", "tests", "__pycache__"} for part in dependency_parts[:-1]):
            raise ValueError(f"{label} contains forbidden test or cache directory")
        filename = dependency_parts[-1]
        if filename.startswith("test_") or ".test." in filename:
            raise ValueError(f"{label} contains forbidden test content: {parts[-1]}")
        if Path(filename).suffix in _CREDENTIAL_SUFFIXES and not (
            len(dependency_parts) >= 2
            and dependency_parts[-2:] == ("certifi", "cacert.pem")
        ):
            raise ValueError(f"{label} contains credential-like file: {parts[-1]}")
        return
    _validate_content_path(parts, label=label)


def _contains_absolute_value(value: object) -> bool:
    if isinstance(value, dict):
        return any(_contains_absolute_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_value(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/")
    return normalized.startswith("/") or bool(_DRIVE_PATH_PATTERN.match(normalized))


def verify_portable_manifest(root: Path) -> None:
    manifest_path = root / "portable-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"portable manifest is unreadable: {exc}") from exc
    if manifest.get("format") != 1:
        raise ValueError("portable manifest format is unsupported")
    if manifest.get("platform") != "win-x64":
        raise ValueError("portable manifest platform must be win-x64")
    if manifest.get("entrypoint") != "harness/dist/server.js":
        raise ValueError("portable manifest Harness entrypoint is invalid")
    node = manifest.get("node")
    harness = manifest.get("harness")
    python = manifest.get("python")
    comfy_bootstrap = manifest.get("comfy_bootstrap")
    if not all(
        isinstance(section, dict)
        for section in (node, harness, python, comfy_bootstrap)
    ):
        raise ValueError("portable manifest runtime sections are invalid")
    if node.get("version") != _WINDOWS_NODE_VERSION:
        raise ValueError("portable manifest Node version does not match runtime config")
    if node.get("archive_sha256") != _WINDOWS_NODE_SHA256:
        raise ValueError("portable manifest Node checksum does not match runtime config")
    expected_lock = hashlib.sha256(
        (_REPO_ROOT / "harness" / "package-lock.json").read_bytes()
    ).hexdigest()
    if harness.get("package_lock_sha256") != expected_lock:
        raise ValueError("portable manifest Harness lock checksum is invalid")
    try:
        package = json.loads(
            (root / "harness" / "package.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"packaged Harness metadata is unreadable: {exc}") from exc
    if harness.get("version") != package.get("version"):
        raise ValueError("portable manifest Harness version does not match package")
    if python.get("version") != _WINDOWS_COMFY_CONFIG["python_version"]:
        raise ValueError("portable manifest Python version does not match runtime config")
    if python.get("archive_sha256") != _WINDOWS_COMFY_CONFIG["python_sha256"]:
        raise ValueError("portable manifest Python checksum does not match runtime config")
    if comfy_bootstrap.get("pip_version") != _WINDOWS_COMFY_CONFIG["pip_version"]:
        raise ValueError("portable manifest pip version does not match runtime config")
    if comfy_bootstrap.get("pip_wheel_sha256") != _WINDOWS_COMFY_CONFIG["pip_sha256"]:
        raise ValueError("portable manifest pip checksum does not match runtime config")
    if comfy_bootstrap.get("requirements_lock_sha256") != _WINDOWS_COMFY_LOCK_SHA256:
        raise ValueError("portable manifest Comfy lock checksum is invalid")
    if comfy_bootstrap.get("packages") != {
        "comfy-cli": _WINDOWS_COMFY_CONFIG["comfy_cli_version"],
        "comfy-mcp": _WINDOWS_COMFY_CONFIG["comfy_mcp_version"],
    }:
        raise ValueError("portable manifest Comfy package versions are invalid")
    for digest in (
        node.get("archive_sha256"),
        harness.get("package_lock_sha256"),
        python.get("archive_sha256"),
        comfy_bootstrap.get("pip_wheel_sha256"),
        comfy_bootstrap.get("requirements_lock_sha256"),
    ):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("portable manifest contains an invalid digest")
    if _contains_absolute_value(manifest):
        raise ValueError("portable manifest contains an absolute path")


def verify_windows_harness_runtime(root: Path) -> None:
    if not (root / WINDOWS_KOFFI_BINARY).is_file():
        raise ValueError("Windows Harness Koffi binary is missing")
    for forbidden in _WINDOWS_FORBIDDEN_DEV_PACKAGES:
        if (root / forbidden).exists():
            raise ValueError(f"package contains development package: {forbidden}")
    for emitted in (root / "harness" / "dist").glob("*.test.js"):
        raise ValueError(f"package contains compiled Harness test: {emitted.name}")
    verify_portable_manifest(root)


def verify_embedded_entries(entries: set[str]) -> None:
    normalized = {_normalize_path(entry) for entry in entries}
    missing = REQUIRED_EMBEDDED - normalized
    if missing:
        raise ValueError(f"embedded resources are missing: {', '.join(sorted(missing))}")
    for entry in normalized:
        _validate_content_path(_validated_parts(entry, label="embedded resources"), label="embedded resources")


def _verify_env_content_has_no_active_secrets(content: bytes) -> None:
    for raw_line in content.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if _SECRET_KEY_PATTERN.search(key) and value.strip().strip("'\""):
            raise ValueError(f"package contains an active secret in .env: {key}")


def _verify_env_has_no_active_secrets(env_path: Path) -> None:
    _verify_env_content_has_no_active_secrets(env_path.read_bytes())


def _verify_windows_portable_readme(readme_path: Path) -> None:
    try:
        text = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Windows portable instructions are unreadable: {exc}") from exc
    lowered = text.lower()
    required = ("directorstudio.exe", ".env", "data")
    forbidden = (
        "build the windows portable package",
        "source installation",
        "macos portable installation",
        "linux portable installation",
        "architecture",
    )
    if (
        len(text) > 12_000
        or any(item not in lowered for item in required)
        or any(item in lowered for item in forbidden)
    ):
        raise ValueError(
            "Windows README must contain portable instructions only, not repository development documentation"
        )


def verify_package_tree(root: Path, flavor: PackageFlavor) -> None:
    if not root.is_dir():
        raise ValueError(f"package root does not exist: {root}")
    if flavor.bundled_harness and not (root / WINDOWS_KOFFI_BINARY).is_file():
        raise ValueError("Windows Harness Koffi binary is missing")
    missing = [name for name in required_package_files(flavor) if not (root / name).is_file()]
    if missing:
        raise ValueError(f"package is missing required files: {', '.join(missing)}")

    for path in root.rglob("*"):
        relative = path.relative_to(root)
        parts = _validated_parts(relative.as_posix(), label="package")
        if path.is_symlink():
            raise ValueError(f"package contains links: {relative.as_posix()}")
        if path.is_dir():
            _validate_package_content_path(
                parts + ("directory",), label="package", flavor=flavor
            )
        else:
            _validate_package_content_path(parts, label="package", flavor=flavor)
    _verify_env_has_no_active_secrets(root / ".env")
    if flavor.bundled_harness:
        _verify_windows_portable_readme(root / "README.md")
        verify_windows_harness_runtime(root)


def _hash_stream(stream) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_archive_names(names: Iterable[str], flavor: PackageFlavor) -> tuple[list[str], str]:
    normalized_names: list[str] = []
    executable_name = f"{flavor.name}/{flavor.executable}"
    for name in names:
        parts = _validated_parts(name, label="archive")
        if parts[0] != flavor.name:
            raise ValueError(f"archive member is outside the top-level package: {name}")
        if len(parts) > 1:
            _validate_package_content_path(
                parts[1:], label="archive", flavor=flavor
            )
        normalized_names.append("/".join(parts))
    if normalized_names.count(executable_name) != 1:
        raise ValueError(f"archive must contain exactly one {flavor.executable}")
    duplicate_names = sorted(
        name for name, count in Counter(normalized_names).items() if count > 1
    )
    if duplicate_names:
        raise ValueError(
            f"archive contains duplicate normalized members: {', '.join(duplicate_names)}"
        )
    missing = {
        f"{flavor.name}/{name}" for name in required_package_files(flavor)
    } - set(normalized_names)
    if missing:
        raise ValueError(f"archive is missing required files: {', '.join(sorted(missing))}")
    return normalized_names, executable_name


def _required_archive_names(flavor: PackageFlavor) -> set[str]:
    return {f"{flavor.name}/{name}" for name in required_package_files(flavor)}


def _verify_zip_archive(archive_path: Path, package_root: Path, flavor: PackageFlavor) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        for info in infos:
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type == stat.S_IFLNK:
                raise ValueError(f"archive contains links: {info.filename}")
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError(f"archive contains special entry: {info.filename}")
        names, executable_name = _verify_archive_names((info.filename for info in infos), flavor)
        required_names = _required_archive_names(flavor)
        for name, info in zip(names, infos, strict=True):
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if name in required_names and (
                info.is_dir() or file_type not in (0, stat.S_IFREG)
            ):
                raise ValueError(f"required archive entry is not a regular file: {name}")
        env_info = infos[names.index(f"{flavor.name}/.env")]
        with archive.open(env_info) as archived_env:
            _verify_env_content_has_no_active_secrets(archived_env.read())
        executable_info = infos[names.index(executable_name)]
        with archive.open(executable_info) as packaged, (package_root / flavor.executable).open("rb") as built:
            if _hash_stream(packaged) != _hash_stream(built):
                raise ValueError("archive contains a different executable than the built package")


def _verify_tar_archive(archive_path: Path, package_root: Path, flavor: PackageFlavor) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if member.issym() or member.islnk():
                raise ValueError(f"archive contains links: {member.name}")
            if member.isdev():
                raise ValueError(f"archive contains devices: {member.name}")
            if not member.isfile() and not member.isdir():
                raise ValueError(f"archive contains unsupported member: {member.name}")
        names, executable_name = _verify_archive_names((member.name for member in members), flavor)
        required_names = _required_archive_names(flavor)
        for name, member in zip(names, members, strict=True):
            if name in required_names and not member.isfile():
                raise ValueError(f"required archive entry is not a regular file: {name}")
        env_member = members[names.index(f"{flavor.name}/.env")]
        archived_env = archive.extractfile(env_member)
        if archived_env is None:
            raise ValueError(f"archive .env is unreadable: {env_member.name}")
        with archived_env:
            _verify_env_content_has_no_active_secrets(archived_env.read())
        executable_member = members[names.index(executable_name)]
        packaged = archive.extractfile(executable_member)
        if packaged is None:
            raise ValueError(f"archive executable is unreadable: {executable_name}")
        with packaged, (package_root / flavor.executable).open("rb") as built:
            if _hash_stream(packaged) != _hash_stream(built):
                raise ValueError("archive contains a different executable than the built package")


def verify_archive(archive: Path, package_root: Path, flavor: PackageFlavor) -> None:
    verify_package_tree(package_root, flavor)
    if flavor.archive_kind == "zip":
        _verify_zip_archive(archive, package_root, flavor)
    else:
        _verify_tar_archive(archive, package_root, flavor)


def _embedded_entries(executable: Path) -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller.utils.cliutils.archive_viewer",
            "-l",
            str(executable),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise ValueError(f"could not inspect embedded resources: {detail[0] if detail else executable}")
    return parse_pyinstaller_listing(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a portable Director Studio package")
    parser.add_argument("--platform", choices=sorted(FLAVORS), required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)

    try:
        flavor = FLAVORS[args.platform]
        verify_package_tree(args.package_root, flavor)
        if (args.executable is None) != (args.archive is None):
            raise ValueError("--executable and --archive must be supplied together")
        if args.executable is not None and args.archive is not None:
            with args.executable.open("rb") as built, (args.package_root / flavor.executable).open("rb") as packaged:
                if _hash_stream(built) != _hash_stream(packaged):
                    raise ValueError("package executable differs from the inspected build")
            verify_embedded_entries(_embedded_entries(args.executable))
            verify_archive(args.archive, args.package_root, flavor)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"Portable package verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
