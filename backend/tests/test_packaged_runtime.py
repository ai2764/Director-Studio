from pathlib import Path

from app.runtime_paths import resolve_runtime_paths


def test_frozen_paths_keep_durable_files_beside_executable(tmp_path: Path):
    bundle_root = tmp_path / "bundle"
    executable = tmp_path / "install" / "DirectorStudio.exe"

    result = resolve_runtime_paths(
        frozen=True,
        bundle_root=bundle_root,
        executable=executable,
    )

    assert result.frozen is True
    assert result.bundle_root == bundle_root
    assert result.install_root == executable.parent
    assert result.data_root == executable.parent / "data"
    assert result.env_file == executable.parent / ".env"


def test_source_paths_preserve_repository_layout(tmp_path: Path):
    result = resolve_runtime_paths(frozen=False, source_root=tmp_path)

    assert result.frozen is False
    assert result.bundle_root == tmp_path
    assert result.install_root == tmp_path
    assert result.data_root == tmp_path / "data"
    assert result.env_file == tmp_path / "backend" / ".env"
