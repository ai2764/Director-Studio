from __future__ import annotations

import importlib.util
import hashlib
import io
import json
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
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "portable-manifest.json":
            path.write_text(
                json.dumps(
                    {
                        "entrypoint": "harness/dist/server.js",
                        "format": 1,
                        "harness": {
                            "package_lock_sha256": hashlib.sha256(
                                (REPO_ROOT / "harness" / "package-lock.json").read_bytes()
                            ).hexdigest(),
                            "version": "0.1.0",
                        },
                        "node": {
                            "archive_sha256": "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97",
                            "version": "22.23.2",
                        },
                        "python": {
                            "archive_sha256": "90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907",
                            "version": "3.13.14",
                        },
                        "comfy_bootstrap": {
                            "pip_version": "25.1.1",
                            "pip_wheel_sha256": "2913a38a2abf4ea6b64ab507bd9e967f3b53dc1ede74b01b0931e1ce548751af",
                            "requirements_lock_sha256": hashlib.sha256(
                                (REPO_ROOT / "packaging" / "windows-comfy-requirements.lock").read_bytes()
                            ).hexdigest(),
                            "packages": {
                                "comfy-cli": "1.20.0",
                                "comfy-mcp": "0.10.0",
                            },
                        },
                        "platform": "win-x64",
                    }
                ),
                encoding="utf-8",
            )
        elif name == "harness/package.json":
            path.write_text(
                json.dumps({"name": "director-studio-harness-sidecar", "version": "0.1.0"}),
                encoding="utf-8",
            )
        elif name == "README.md":
            path.write_text(
                "# Windows portable instructions\n\n"
                "Extract the complete ZIP, configure `.env`, preserve `data`, "
                "and run `DirectorStudio.exe`.\n",
                encoding="utf-8",
            )
        else:
            path.write_bytes(b"fixture")
    return package


def _write_archive(
    path: Path,
    package: Path,
    flavor,
    *,
    separator: str = "/",
    executable_bytes: bytes | None = None,
    archive_env_bytes: bytes | None = None,
    duplicate_env_bytes: bytes | None = None,
    duplicate_env_name: str = "./.env",
    extra_entries: tuple[str, ...] = (),
    tar_symlink: bool = False,
    tar_device: bool = False,
    zip_special_mode: int | None = None,
    required_directory: str | None = None,
    required_directory_without_slash: bool = False,
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
    if duplicate_env_bytes is not None:
        members.append((duplicate_env_name, duplicate_env_bytes))

    if flavor.archive_kind == "zip":
        with zipfile.ZipFile(path, "w") as archive:
            for name, contents in members:
                if name == required_directory:
                    entry_name = f"{flavor.name}/{name}"
                    if required_directory_without_slash:
                        directory = zipfile.ZipInfo(entry_name)
                        directory.create_system = 3
                        directory.external_attr = stat.S_IFDIR << 16
                        archive.writestr(directory, b"")
                    else:
                        archive.writestr(f"{entry_name}/", b"")
                    continue
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
            if name == required_directory:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
                continue
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


def test_embedded_policy_allows_certifi_public_ca_bundle():
    entries = set(verifier.REQUIRED_EMBEDDED)
    entries.add("certifi/cacert.pem")
    verifier.verify_embedded_entries(entries)


def test_embedded_policy_still_rejects_private_pem():
    entries = set(verifier.REQUIRED_EMBEDDED)
    entries.add("tls/private.pem")
    with pytest.raises(ValueError, match="credential"):
        verifier.verify_embedded_entries(entries)


@pytest.mark.parametrize("platform", ["windows", "linux", "macos-arm64", "macos-x86_64"])
def test_clean_package_requires_platform_files(tmp_path: Path, platform: str):
    flavor = verifier.FLAVORS[platform]
    package = _package_fixture(tmp_path, flavor)
    verifier.verify_package_tree(package, flavor)


def test_windows_package_requires_bundled_harness_runtime():
    windows = verifier.FLAVORS["windows"]

    assert windows.name == "Director-Studio-Windows-x64"
    assert "runtime/node/node.exe" in verifier.required_package_files(windows)
    assert "harness/dist/server.js" in verifier.required_package_files(windows)
    assert "portable-manifest.json" in verifier.required_package_files(windows)
    assert "runtime/python/python.exe" in verifier.required_package_files(windows)
    assert "runtime/python/comfy.exe" in verifier.required_package_files(windows)
    assert "runtime/python/Lib/site-packages/pip/__init__.py" in verifier.required_package_files(windows)
    assert "runtime/comfy-bootstrap.json" in verifier.required_package_files(windows)
    assert "runtime/python/Lib/site-packages/sitecustomize.py" in verifier.required_package_files(windows)
    assert "runtime/comfy-requirements.lock" in verifier.required_package_files(windows)
    assert "THIRD_PARTY_LICENSES/pip.txt" in verifier.required_package_files(windows)
    assert "runtime/python/Lib/site-packages/comfy_mcp/__init__.py" not in verifier.required_package_files(windows)
    assert "THIRD_PARTY_LICENSES/comfy-mcp.txt" not in verifier.required_package_files(windows)
    assert "Install-Tools.cmd" not in verifier.required_package_files(windows)
    assert "Install-Tools.py" not in verifier.required_package_files(windows)
    assert "portable-tools-requirements.txt" not in verifier.required_package_files(windows)
    assert "runtime/node/node.exe" not in verifier.required_package_files(
        verifier.FLAVORS["linux"]
    )
    assert "runtime/python/python.exe" not in verifier.required_package_files(
        verifier.FLAVORS["linux"]
    )


def test_windows_package_rejects_full_repository_readme(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    (package / "README.md").write_text(
        (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="portable instructions"):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize("package_name", ["setuptools", "wheel", "uv"])
def test_windows_package_rejects_python_build_tools(
    tmp_path: Path, package_name: str
) -> None:
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    forbidden = package / "runtime" / "python" / "Lib" / "site-packages" / package_name
    forbidden.mkdir(parents=True)
    (forbidden / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="build tool"):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize("package_name", ["comfy_mcp", "comfy_cli"])
def test_windows_package_rejects_first_launch_dependencies(
    tmp_path: Path, package_name: str
) -> None:
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    module = package / "runtime/python/Lib/site-packages" / package_name / "__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text("# must be downloaded on first launch", encoding="utf-8")

    with pytest.raises(ValueError, match="first-launch dependency"):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize(
    "filename",
    ["Install-Tools.cmd", "Install-Tools.py", "portable-tools-requirements.txt"],
)
def test_windows_package_rejects_obsolete_manual_installer(
    tmp_path: Path, filename: str
) -> None:
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    (package / filename).write_text("obsolete", encoding="utf-8")

    with pytest.raises(ValueError, match="obsolete Windows installer"):
        verifier.verify_package_tree(package, flavor)


def test_windows_archive_policy_accepts_site_packages_directory_entry() -> None:
    verifier._validate_package_content_path(
        ("runtime", "python", "Lib", "site-packages"),
        label="archive",
        flavor=verifier.FLAVORS["windows"],
    )


def test_cli_can_verify_staged_package_before_archive_creation(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)

    assert verifier.main(
        ["--platform", "windows", "--package-root", str(package)]
    ) == 0


def test_windows_package_rejects_missing_koffi_binary(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    (package / verifier.WINDOWS_KOFFI_BINARY).unlink()

    with pytest.raises(ValueError, match="Koffi"):
        verifier.verify_package_tree(package, flavor)


@pytest.mark.parametrize("package_name", ["tsx", "typescript", "vitest", "@vitest/runner"])
def test_windows_package_rejects_development_dependencies(
    tmp_path: Path,
    package_name: str,
):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    forbidden = package / "harness" / "node_modules" / package_name / "package.json"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="development package"):
        verifier.verify_package_tree(package, flavor)


def test_windows_package_rejects_wrong_runtime_manifest(tmp_path: Path):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    manifest = package / "portable-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["platform"] = "linux-x86_64"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest platform"):
        verifier.verify_package_tree(package, flavor)


def test_linux_package_rejects_harness_runtime_content(tmp_path: Path):
    flavor = verifier.FLAVORS["linux"]
    package = _package_fixture(tmp_path, flavor)
    entry = package / "harness" / "dist" / "server.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("fixture", encoding="utf-8")

    with pytest.raises(ValueError, match="Harness runtime"):
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
def test_archive_rejects_duplicate_normalized_env_member(
    tmp_path: Path, platform: str
):
    flavor = verifier.FLAVORS[platform]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / ("portable.zip" if flavor.archive_kind == "zip" else "portable.tar.gz"),
        package,
        flavor,
        duplicate_env_bytes=b"DS_H3_MINIMAX_API_KEY=secret\n",
    )

    with pytest.raises(ValueError, match="duplicate"):
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


@pytest.mark.parametrize("platform", ["windows", "linux"])
@pytest.mark.parametrize("required_name", [".env", "README.md"])
def test_archive_requires_required_entries_to_be_regular_files(
    tmp_path: Path, platform: str, required_name: str
):
    flavor = verifier.FLAVORS[platform]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / ("portable.zip" if flavor.archive_kind == "zip" else "portable.tar.gz"),
        package,
        flavor,
        required_directory=required_name,
    )

    with pytest.raises(ValueError, match="regular file"):
        verifier.verify_archive(archive, package, flavor)


def test_zip_required_entry_rejects_directory_mode_without_trailing_slash(
    tmp_path: Path,
):
    flavor = verifier.FLAVORS["windows"]
    package = _package_fixture(tmp_path, flavor)
    archive = _write_archive(
        tmp_path / "portable.zip",
        package,
        flavor,
        required_directory=".env",
        required_directory_without_slash=True,
    )

    with pytest.raises(ValueError, match="regular file"):
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
    ("/Director-Studio-Windows-x64/extra", "absolute"),
    ("Director-Studio-Windows-x64/../extra", "traversal"),
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
