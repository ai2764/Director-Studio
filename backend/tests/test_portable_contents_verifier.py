from __future__ import annotations

import importlib.util
import io
import os
import stat
import subprocess
import sys
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts" / "verify_portable_contents.py"

spec = importlib.util.spec_from_file_location("verify_portable_contents", VERIFIER_PATH)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


def _package_fixture(tmp_path: Path, flavor):
    package = tmp_path / flavor.name
    package.mkdir()
    for name in verifier.required_package_files(flavor):
        (package / name).write_bytes(b"fixture")
    return package


def _write_archive(
    path: Path,
    package: Path,
    flavor,
    *,
    separator: str = "/",
    executable_bytes: bytes | None = None,
    archive_env_bytes: bytes | None = None,
    extra_entries: tuple[str, ...] = (),
    tar_symlink: bool = False,
    tar_device: bool = False,
    zip_special_mode: int | None = None,
) -> Path:
    members = [
        (name, (package / name).read_bytes())
        for name in verifier.required_package_files(flavor)
    ]
    if executable_bytes is not None:
        members = [
            (name, executable_bytes if name == flavor.executable else contents)
            for name, contents in members
        ]
    if archive_env_bytes is not None:
        members = [
            (name, archive_env_bytes if name == ".env" else contents)
            for name, contents in members
        ]
    members.extend((name, b"unexpected") for name in extra_entries)

    if flavor.archive_kind == "zip":
        with zipfile.ZipFile(path, "w") as archive:
            for name, contents in members:
                archive.writestr(
                    f"{flavor.name}{separator}{name}",
                    contents,
                )
            if zip_special_mode is not None:
                special = zipfile.ZipInfo(f"{flavor.name}/special-entry")
                special.create_system = 3
                special.external_attr = zip_special_mode << 16
                archive.writestr(special, b"special")
        return path

    with tarfile.open(path, "w:gz") as archive:
        for name, contents in members:
            member = tarfile.TarInfo(f"{flavor.name}{separator}{name}")
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
        if tar_symlink:
            member = tarfile.TarInfo(f"{flavor.name}/launch-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "launch.sh"
            archive.addfile(member)
        if tar_device:
            member = tarfile.TarInfo(f"{flavor.name}/device")
            member.type = tarfile.CHRTYPE
            member.devmajor = 1
            member.devminor = 3
            archive.addfile(member)
    return path


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


def test_embedded_policy_allows_python_workflow_profiles_package():
    entries = set(verifier.REQUIRED_EMBEDDED)
    entries.add("app/workflow_profiles/h3/store.py")
    verifier.verify_embedded_entries(entries)


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_clean_package_requires_platform_files(tmp_path: Path, platform: str):
    flavor = verifier.FLAVORS[platform]
    package = tmp_path / flavor.name
    package.mkdir()
    for name in verifier.required_package_files(flavor):
        (package / name).write_text("fixture", encoding="utf-8")
    verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize(
    "directory",
    ["data", "projects", "jobs", "outputs", "tests", "workflow_profiles"],
)
def test_package_tree_rejects_durable_or_test_directories(tmp_path: Path, directory: str):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    (package / directory).mkdir()

    with pytest.raises(ValueError, match=directory):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize("filename", ["credentials.json", "private.key"])
def test_package_tree_rejects_credential_like_files(tmp_path: Path, filename: str):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    (package / filename).write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="credential"):
        verifier.verify_package_tree(package, flavor)


def test_package_tree_rejects_active_secret(tmp_path: Path):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    (package / ".env").write_text("DS_H3_MINIMAX_API_KEY=secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="active secret"):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_archive_rejects_active_secret_in_archived_env(tmp_path: Path, platform: str):
    flavor = verifier.FLAVORS[platform]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / ("portable.zip" if flavor.archive_kind == "zip" else "portable.tar.gz"),
        package,
        flavor,
        archive_env_bytes=b"DS_H3_MINIMAX_API_KEY=secret\n",
    )

    with pytest.raises(ValueError, match="active secret"):
        verifier.verify_archive(archive, package, flavor)


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_archive_accepts_normalized_members(tmp_path: Path, platform: str):
    flavor = verifier.FLAVORS[platform]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / ("portable.zip" if flavor.archive_kind == "zip" else "portable.tar.gz"),
        package,
        flavor,
        separator="\\",
    )

    verifier.verify_archive(archive, package, flavor)


def test_archive_requires_exactly_one_executable(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        archive = _write_archive(
            tmp_path / "portable.zip",
            package,
            flavor,
            extra_entries=(flavor.executable,),
        )

    with pytest.raises(ValueError, match="exactly one"):
        verifier.verify_archive(archive, package, flavor)


def test_archive_rejects_members_outside_package_root(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(tmp_path / "portable.zip", package, flavor)
    with zipfile.ZipFile(archive, "a") as contents:
        contents.writestr("outside.txt", "unexpected")

    with pytest.raises(ValueError, match="top-level package"):
        verifier.verify_archive(archive, package, flavor)


@pytest.mark.parametrize("member_name, message", [
    ("/Director-Studio-Legacy-Windows-x64/extra", "absolute"),
    ("Director-Studio-Legacy-Windows-x64/../extra", "traversal"),
])
def test_archive_rejects_unsafe_member_paths(
    tmp_path: Path, member_name: str, message: str
):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(tmp_path / "portable.zip", package, flavor)
    with zipfile.ZipFile(archive, "a") as contents:
        contents.writestr(member_name, "unexpected")

    with pytest.raises(ValueError, match=message):
        verifier.verify_archive(archive, package, flavor)


def test_archive_rejects_executable_with_different_bytes(tmp_path: Path):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / "portable.tar.gz",
        package,
        flavor,
        executable_bytes=b"different executable",
    )

    with pytest.raises(ValueError, match="different"):
        verifier.verify_archive(archive, package, flavor)


@pytest.mark.parametrize("special_mode", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO])
def test_zip_archive_rejects_unix_special_entries(tmp_path: Path, special_mode: int):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / "portable.zip",
        package,
        flavor,
        zip_special_mode=special_mode,
    )

    with pytest.raises(ValueError, match="special"):
        verifier.verify_archive(archive, package, flavor)


def test_tar_archive_rejects_symlink(tmp_path: Path):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / "portable.tar.gz", package, flavor, tar_symlink=True
    )

    with pytest.raises(ValueError, match="links"):
        verifier.verify_archive(archive, package, flavor)


def test_tar_archive_rejects_device(tmp_path: Path):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / "portable.tar.gz", package, flavor, tar_device=True
    )

    with pytest.raises(ValueError, match="devices"):
        verifier.verify_archive(archive, package, flavor)


def test_zip_archive_rejects_symlink(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(tmp_path / "portable.zip", package, flavor)
    link = zipfile.ZipInfo(f"{flavor.name}/linked-file")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as contents:
        contents.writestr(link, "DirectorStudio.exe")

    with pytest.raises(ValueError, match="links"):
        verifier.verify_archive(archive, package, flavor)


@pytest.mark.parametrize(
    "directory",
    ["DATA", "PROJECTS", "JOBS", "OUTPUTS", "TESTS", "WORKFLOW_PROFILES"],
)
def test_windows_package_rejects_forbidden_directory_case_insensitively(
    tmp_path: Path, directory: str
):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    (package / directory).mkdir()

    with pytest.raises(ValueError, match="forbidden directory"):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize(
    "directory",
    ["DATA", "PROJECTS", "JOBS", "OUTPUTS", "TESTS", "WORKFLOW_PROFILES"],
)
def test_windows_archive_rejects_forbidden_directory_case_insensitively(
    tmp_path: Path, directory: str
):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / "portable.zip",
        package,
        flavor,
        extra_entries=(f"{directory}/state.json",),
    )

    with pytest.raises(ValueError, match="forbidden directory"):
        verifier.verify_archive(archive, package, flavor)


def test_cli_rejects_incomplete_embedded_resource_listing(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    built_executable = tmp_path / flavor.executable
    built_executable.write_bytes((package / flavor.executable).read_bytes())
    archive = _write_archive(tmp_path / "portable.zip", package, flavor)
    viewer = tmp_path / "viewer" / "PyInstaller" / "utils" / "cliutils"
    viewer.mkdir(parents=True)
    for package_dir in (viewer.parents[1], viewer.parents[0], viewer):
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (viewer / "archive_viewer.py").write_text(
        "print('workflows/h3_ref2va.api.json')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{viewer.parents[2]}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER_PATH),
            "--platform",
            "windows",
            "--package-root",
            str(package),
            "--executable",
            str(built_executable),
            "--archive",
            str(archive),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "embedded resources are missing" in result.stderr
