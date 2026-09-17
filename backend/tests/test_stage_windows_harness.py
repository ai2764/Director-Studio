from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "stage_windows_harness.py"
CONFIG = REPO_ROOT / "packaging" / "windows-harness-runtime.json"

spec = importlib.util.spec_from_file_location("stage_windows_harness", SCRIPT)
assert spec is not None and spec.loader is not None
stager = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stager
spec.loader.exec_module(stager)


def _node_zip(path: Path, *, unsafe: bool = False) -> Path:
    root = "node-v22.23.2-win-x64"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{root}/node.exe", b"private-node")
        archive.writestr(f"{root}/LICENSE", "Node license")
        archive.writestr(f"{root}/npm.cmd", "must not ship")
        if unsafe:
            archive.writestr(f"{root}/../escape.txt", "escape")
    return path


def test_runtime_config_pins_audited_node_and_koffi() -> None:
    config = stager.load_runtime_config(CONFIG)

    assert config.version == "22.23.2"
    assert config.archive == "node-v22.23.2-win-x64.zip"
    assert config.sha256 == (
        "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97"
    )
    assert config.koffi_binary.as_posix() == (
        "harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node"
    )


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "node.zip"
    archive.write_bytes(b"not the pinned archive")

    with pytest.raises(stager.StageError, match="checksum"):
        stager.verify_sha256(archive, "0" * 64)


def test_node_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _node_zip(tmp_path / "node.zip", unsafe=True)
    config = stager.RuntimeConfig(
        platform="windows",
        arch="x64",
        version="22.23.2",
        archive="node-v22.23.2-win-x64.zip",
        url="https://example.invalid/node.zip",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        koffi_binary=Path(
            "harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node"
        ),
    )

    with pytest.raises(stager.StageError, match="unsafe archive member"):
        stager.extract_node_runtime(archive, tmp_path / "package", config)


def test_node_extraction_copies_only_runtime_and_license(tmp_path: Path) -> None:
    archive = _node_zip(tmp_path / "node.zip")
    config = stager.RuntimeConfig(
        platform="windows",
        arch="x64",
        version="22.23.2",
        archive="node-v22.23.2-win-x64.zip",
        url="https://example.invalid/node.zip",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        koffi_binary=Path(
            "harness/node_modules/@koromix/koffi-win32-x64/win32_x64/koffi.node"
        ),
    )

    stager.extract_node_runtime(archive, tmp_path / "package", config)

    runtime = tmp_path / "package" / "runtime" / "node"
    assert (runtime / "node.exe").read_bytes() == b"private-node"
    assert (runtime / "LICENSE").read_text(encoding="utf-8") == "Node license"
    assert not (runtime / "npm.cmd").exists()


def test_harness_stage_uses_separate_clean_install_trees(tmp_path: Path) -> None:
    destination = tmp_path / "package"
    config = stager.load_runtime_config(CONFIG)
    commands: list[tuple[list[str], Path]] = []

    def fake_run(command, *, cwd, check):
        assert check is True
        cwd = Path(cwd)
        commands.append((list(command), cwd))
        if command[1:] == ["run", "build"]:
            (cwd / "dist").mkdir()
            (cwd / "dist" / "server.js").write_text("// built", encoding="utf-8")
        if command[1:] == ["ci", "--omit=dev"]:
            (cwd / "node_modules" / "@vitest").mkdir(parents=True)
            koffi = cwd / "node_modules" / "@koromix" / "koffi-win32-x64" / "win32_x64" / "koffi.node"
            koffi.parent.mkdir(parents=True)
            koffi.write_bytes(b"native")
            package = cwd / "node_modules" / "example-runtime"
            package.mkdir()
            (package / "package.json").write_text(
                json.dumps({"name": "example-runtime", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (package / "LICENSE").write_text("Example license", encoding="utf-8")

    stager.stage_harness(
        REPO_ROOT,
        destination,
        config,
        npm="npm.cmd",
        runner=fake_run,
    )

    assert [command for command, _cwd in commands] == [
        ["npm.cmd", "ci"],
        ["npm.cmd", "run", "build"],
        ["npm.cmd", "ci", "--omit=dev"],
    ]
    assert commands[0][1] == commands[1][1]
    assert commands[2][1] != commands[0][1]
    assert (destination / "harness" / "dist" / "server.js").is_file()
    assert (destination / config.koffi_binary).is_file()
    assert not (destination / "harness" / "node_modules" / "tsx").exists()
    assert not (destination / "harness" / "node_modules" / "typescript").exists()
    assert not (destination / "harness" / "node_modules" / "@vitest").exists()
    licenses = json.loads(
        (destination / "harness" / "THIRD_PARTY_LICENSES.json").read_text(
            encoding="utf-8"
        )
    )
    assert licenses == [
        {
            "name": "example-runtime",
            "path": "example-runtime/LICENSE",
            "text": "Example license",
            "version": "1.0.0",
        }
    ]


def test_manifest_is_deterministic_and_contains_lock_digest(tmp_path: Path) -> None:
    destination = tmp_path / "package"
    destination.mkdir()
    config = stager.load_runtime_config(CONFIG)

    stager.write_portable_manifest(REPO_ROOT, destination, config)

    manifest = json.loads(
        (destination / "portable-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "entrypoint": "harness/dist/server.js",
        "format": 1,
        "harness": {
            "package_lock_sha256": hashlib.sha256(
                (REPO_ROOT / "harness" / "package-lock.json").read_bytes()
            ).hexdigest(),
            "version": "0.1.0",
        },
        "node": {
            "archive_sha256": config.sha256,
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
            "packages": {"comfy-cli": "1.20.0", "comfy-mcp": "0.10.0"},
        },
        "platform": "win-x64",
    }
    assert "timestamp" not in manifest
    assert str(REPO_ROOT) not in json.dumps(manifest)
