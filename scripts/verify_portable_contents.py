from __future__ import annotations

import argparse
import hashlib
import re
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


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
_SECRET_KEY_PATTERN = re.compile(r"(?:secret|token|password|api[_-]?key)", re.I)
_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:/")


def required_package_files(flavor: PackageFlavor) -> tuple[str, ...]:
    return (
        flavor.executable,
        *flavor.wrappers,
        "Install-Tools.py",
        "portable-tools-requirements.txt",
        ".env",
        "README.md",
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
    if filename == "active.json":
        raise ValueError(f"{label} contains forbidden active workflow state: {parts[-1]}")
    if filename.startswith("test_") or ".test." in filename:
        raise ValueError(f"{label} contains forbidden test content: {parts[-1]}")
    if (
        Path(filename).suffix in _CREDENTIAL_SUFFIXES
        or any(part in filename for part in _CREDENTIAL_NAME_PARTS)
    ):
        raise ValueError(f"{label} contains credential-like file: {parts[-1]}")


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


def verify_package_tree(root: Path, flavor: PackageFlavor) -> None:
    if not root.is_dir():
        raise ValueError(f"package root does not exist: {root}")
    missing = [name for name in required_package_files(flavor) if not (root / name).is_file()]
    if missing:
        raise ValueError(f"package is missing required files: {', '.join(missing)}")

    for path in root.rglob("*"):
        relative = path.relative_to(root)
        parts = _validated_parts(relative.as_posix(), label="package")
        if path.is_symlink():
            raise ValueError(f"package contains links: {relative.as_posix()}")
        if path.is_dir():
            _validate_content_path(parts + ("directory",), label="package")
        else:
            _validate_content_path(parts, label="package")
    _verify_env_has_no_active_secrets(root / ".env")


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
            _validate_content_path(parts[1:], label="archive")
        normalized_names.append("/".join(parts))
    if normalized_names.count(executable_name) != 1:
        raise ValueError(f"archive must contain exactly one {flavor.executable}")
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
            if name in required_names and info.is_dir():
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
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        flavor = FLAVORS[args.platform]
        verify_package_tree(args.package_root, flavor)
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
