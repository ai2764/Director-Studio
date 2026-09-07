"""Storage must never follow links outside or inside the profile root."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from test_h3_profile_store import installed_custom_store

from app.workflow_profiles.h3 import H3ProfileStore, ProfileStorageError


def _link(path: Path, target: Path, kind: str) -> None:
    if kind == "hardlink":
        os.link(target, path)
    elif kind == "junction":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            pytest.skip(f"Windows refused junction creation: {result.stderr}")
    else:
        path.symlink_to(target, target_is_directory=target.is_dir())


@pytest.mark.parametrize(
    "filename", ["workflow.api.json", "profile.json", "validation.json", "active.json"]
)
@pytest.mark.parametrize(
    "kind", ["hardlink"] if os.name == "nt" else ["symlink", "hardlink"]
)
def test_linked_profile_files_force_fallback(tmp_path, monkeypatch, filename, kind):
    store = installed_custom_store(tmp_path, monkeypatch)
    path = (
        store.active_path
        if filename == "active.json"
        else store.profiles_dir / "custom" / filename
    )
    target = tmp_path / f"external-{filename}"
    target.write_bytes(path.read_bytes())
    path.unlink()
    _link(path, target, kind)
    resolved = store.resolve_active()
    assert resolved.source == "builtin"
    assert resolved.warning is not None
    with pytest.raises(ProfileStorageError):
        store._atomic_write_bytes(path, b"replacement")
    assert target.read_bytes() != b"replacement"


@pytest.mark.parametrize(
    "filename", ["workflow.api.json", "mapping.json", "validation.json", "test.json"]
)
@pytest.mark.parametrize(
    "kind", ["hardlink"] if os.name == "nt" else ["symlink", "hardlink"]
)
def test_import_file_links_are_rejected_on_read_and_write(tmp_path, filename, kind):
    store = H3ProfileStore(root=tmp_path / "profiles")
    import_id = store.create_import({"node": {"class_type": "Example", "inputs": {}}})
    directory = store.imports_dir / import_id
    target = tmp_path / "outside.json"
    target.write_text("{}")
    path = directory / filename
    path.unlink(missing_ok=True)
    _link(path, target, kind)
    with pytest.raises(ProfileStorageError):
        store._read_json(path)
    with pytest.raises(ProfileStorageError):
        store._atomic_write_json(path, {"modified": True})
    assert target.read_text() == "{}"


@pytest.mark.parametrize(
    "boundary", ["root_ancestor", "h3", "imports", "import", "profiles", "profile"]
)
@pytest.mark.parametrize("kind", ["junction"] if os.name == "nt" else ["symlink"])
def test_linked_directory_boundaries_reject_reads_and_new_writes(
    tmp_path, boundary, kind
):
    base = tmp_path / "profiles"
    store = H3ProfileStore(root=base)
    import_id = "imp-" + "a" * 32
    relative = {
        "root_ancestor": Path("."),
        "h3": Path("h3"),
        "imports": Path("h3/imports"),
        "import": Path("h3/imports") / import_id,
        "profiles": Path("h3/profiles"),
        "profile": Path("h3/profiles/custom"),
    }[boundary]
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside"
    target.mkdir()
    (target / "record.json").write_text("{}")
    _link(path, target, kind)
    with pytest.raises(ProfileStorageError):
        store._read_json(path / "record.json")
    with pytest.raises(ProfileStorageError):
        store._atomic_write_json(path / "new" / "written.json", {"modified": True})
    assert not (target / "new").exists()


def test_store_rejects_direct_outside_path(tmp_path):
    store = H3ProfileStore(root=tmp_path / "profiles")
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    with pytest.raises(ProfileStorageError):
        store._read_json(outside)
    with pytest.raises(ProfileStorageError):
        store._atomic_write_json(outside, {"modified": True})


@pytest.mark.parametrize("filename", ["active.json", "imports", "profiles"])
def test_windows_reparse_attributes_are_rejected_before_reading(
    tmp_path, monkeypatch, filename
):
    import stat
    from types import SimpleNamespace

    store = H3ProfileStore(root=tmp_path / "profiles")
    store.root.mkdir(parents=True)
    path = store.root / filename
    path.write_text("{}")
    lstat = os.lstat

    def reparse_stat(candidate, *args, **kwargs):
        result = lstat(candidate, *args, **kwargs)
        if Path(candidate) == path:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_nlink=result.st_nlink,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return result

    monkeypatch.setattr(os, "lstat", reparse_stat)
    with pytest.raises(ProfileStorageError):
        store._read_json(path)
