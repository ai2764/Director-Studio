from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "stage_portable_harness.py"
CONFIG = REPO_ROOT / "packaging" / "portable-harness-runtimes.json"


def _load_stager():
    spec = importlib.util.spec_from_file_location("stage_portable_harness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_config_covers_every_published_target() -> None:
    stager = _load_stager()
    configs = stager.load_runtime_configs(CONFIG)

    assert set(configs) == {
        "windows-x64", "linux-x86_64", "macos-arm64", "macos-x86_64"
    }
    assert {config.node_name for config in configs.values()} == {"node", "node.exe"}
    assert all(len(config.sha256) == 64 for config in configs.values())
    assert all(config.koffi_binary.as_posix().endswith("koffi.node") for config in configs.values())


@pytest.mark.parametrize("archive_kind", ["zip", "tar.gz", "tar.xz"])
def test_runtime_extraction_copies_only_node_and_license(tmp_path: Path, archive_kind: str) -> None:
    stager = _load_stager()
    root = "node-runtime"
    archive = tmp_path / ("node.zip" if archive_kind == "zip" else f"node.{archive_kind}")
    entries = {
        f"{root}/bin/node": b"private-node",
        f"{root}/LICENSE": b"Node license",
        f"{root}/bin/npm": b"must not ship",
    }
    if archive_kind == "zip":
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, value in entries.items():
                bundle.writestr(name, value)
    else:
        mode = "w:gz" if archive_kind == "tar.gz" else "w:xz"
        with tarfile.open(archive, mode) as bundle:
            for name, value in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(value)
                bundle.addfile(info, io.BytesIO(value))
    config = stager.RuntimeConfig(
        target="test", platform="test", arch="x64", version="22.23.2",
        archive=archive.name, archive_kind=archive_kind, archive_root=root,
        url="https://example.invalid/node", sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        node_member="bin/node", node_name="node",
        koffi_binary=Path("harness/node_modules/koffi/koffi.node"),
    )

    stager.extract_node_runtime(archive, tmp_path / "package", config)

    runtime = tmp_path / "package" / "runtime" / "node"
    assert (runtime / "node").read_bytes() == b"private-node"
    assert (runtime / "LICENSE").read_bytes() == b"Node license"
    assert not (runtime / "npm").exists()


def test_manifest_records_target_specific_runtime(tmp_path: Path) -> None:
    stager = _load_stager()
    config = stager.load_runtime_configs(CONFIG)["linux-x86_64"]
    destination = tmp_path / "package"
    destination.mkdir()

    stager.write_portable_manifest(REPO_ROOT, destination, config)

    manifest = json.loads((destination / "portable-manifest.json").read_text(encoding="utf-8"))
    assert manifest["platform"] == "linux-x86_64"
    assert manifest["node"] == {
        "archive_sha256": config.sha256,
        "executable": "runtime/node/node",
        "version": "22.23.2",
    }
    assert "python" not in manifest
    assert "comfy_bootstrap" not in manifest


def test_tar_extraction_ignores_unselected_symlinks(tmp_path: Path) -> None:
    stager = _load_stager()
    archive = tmp_path / "node.tar.gz"
    root = "node-runtime"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, value in {
            f"{root}/bin/node": b"private-node",
            f"{root}/LICENSE": b"Node license",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            bundle.addfile(info, io.BytesIO(value))
        link = tarfile.TarInfo(f"{root}/bin/corepack")
        link.type = tarfile.SYMTYPE
        link.linkname = "../lib/node_modules/corepack/shims/corepack"
        bundle.addfile(link)
    config = stager.RuntimeConfig(
        target="test", platform="test", arch="arm64", version="22.23.2",
        archive=archive.name, archive_kind="tar.gz", archive_root=root,
        url="https://example.invalid/node", sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        node_member="bin/node", node_name="node",
        koffi_binary=Path("harness/node_modules/koffi/koffi.node"),
    )

    stager.extract_node_runtime(archive, tmp_path / "package", config)

    assert (tmp_path / "package" / "runtime" / "node" / "node").is_file()


def test_tar_extraction_rejects_symlink_for_selected_node(tmp_path: Path) -> None:
    stager = _load_stager()
    archive = tmp_path / "node.tar.gz"
    root = "node-runtime"
    with tarfile.open(archive, "w:gz") as bundle:
        license_info = tarfile.TarInfo(f"{root}/LICENSE")
        license_info.size = len(b"Node license")
        bundle.addfile(license_info, io.BytesIO(b"Node license"))
        link = tarfile.TarInfo(f"{root}/bin/node")
        link.type = tarfile.SYMTYPE
        link.linkname = "node-real"
        bundle.addfile(link)
    config = stager.RuntimeConfig(
        target="test", platform="test", arch="arm64", version="22.23.2",
        archive=archive.name, archive_kind="tar.gz", archive_root=root,
        url="https://example.invalid/node", sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        node_member="bin/node", node_name="node",
        koffi_binary=Path("harness/node_modules/koffi/koffi.node"),
    )

    with pytest.raises(stager.StageError, match="not a file"):
        stager.extract_node_runtime(archive, tmp_path / "package", config)
