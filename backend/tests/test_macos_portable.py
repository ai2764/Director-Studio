from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def builder():
    path = ROOT / "scripts" / "build_macos_portable.py"
    assert path.is_file(), "macOS portable builder is missing"
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("build_macos_portable", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(path.parent))


@pytest.mark.parametrize("system,arch", [("Windows", "AMD64"), ("Linux", "x86_64"), ("Darwin", "i386")])
def test_builder_rejects_incompatible_host_before_build(builder, monkeypatch, system, arch):
    monkeypatch.setattr(builder.platform, "system", lambda: system)
    monkeypatch.setattr(builder.platform, "machine", lambda: arch)
    with pytest.raises(RuntimeError, match="macOS|architecture"):
        builder.host_architecture()


@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_package_only_contains_shippable_files_and_preserves_permissions(builder, tmp_path, arch):
    executable = tmp_path / "DirectorStudio"
    executable.write_bytes(b"Mach-O fixture")
    package = tmp_path / f"Director-Studio-macOS-{arch}"
    builder.stage_package(ROOT, executable, package)
    archive = tmp_path / f"{package.name}.tar.gz"
    builder.create_archive(package, archive)
    with tarfile.open(archive) as contents:
        names = contents.getnames()
        for name in ("DirectorStudio", "Launch.command", "Install-Tools.command", "launch.sh", "install-tools.sh"):
            member = contents.getmember(f"{package.name}/{name}")
            assert member.mode & 0o111 == 0o111
            if name != "DirectorStudio":
                assert b"\r\n" not in contents.extractfile(member).read()
        assert not any("/data" in name or "/.env.example" in name for name in names)
    expected_env = (ROOT / "backend" / ".env.example").read_bytes().replace(
        b"DS_DIRECTOR_AGENT_RUNTIME=harness",
        b"DS_DIRECTOR_AGENT_RUNTIME=legacy",
    )
    assert (package / ".env").read_bytes() == expected_env


def test_staging_never_overwrites_existing_user_package(builder, tmp_path):
    package = tmp_path / "existing"
    package.mkdir()
    user_data = package / "data"
    user_data.mkdir()
    with pytest.raises(FileExistsError):
        builder.stage_package(ROOT, tmp_path / "unused", package)
    assert user_data.is_dir()
