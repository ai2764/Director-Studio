from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from app import portable_comfy
from app.portable_comfy import PortableComfyError


KEYS = (
    "DS_COMFY_MCP_COMMAND",
    "DS_COMFY_MCP_ARGS",
    "DS_COMFY_MCP_COMFY_BIN",
)


def _bootstrap_runtime(root: Path) -> tuple[Path, dict[str, object]]:
    runtime = root / "runtime" / "python"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"python")
    (runtime / "comfy.exe").write_bytes(b"comfy")
    (runtime / "Lib" / "site-packages" / "pip").mkdir(parents=True)
    lock = root / "runtime" / "comfy-requirements.lock"
    lock.write_text(
        "comfy-cli==1.20.0 --hash=sha256:" + "a" * 64 + "\n"
        "comfy-mcp==0.10.0 --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    expected: dict[str, object] = {
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "packages": {"comfy-cli": "1.20.0", "comfy-mcp": "0.10.0"},
    }
    (root / "runtime" / "comfy-bootstrap.json").write_text(
        json.dumps(expected), encoding="utf-8"
    )
    return runtime, expected


def _populate_site(site: Path, manifest: dict[str, object]) -> None:
    for module, distribution in (
        ("comfy_mcp", "comfy-mcp"),
        ("comfy_cli", "comfy-cli"),
    ):
        module_path = site / module / "__init__.py"
        module_path.parent.mkdir(parents=True)
        module_path.write_text("# installed\n", encoding="utf-8")
        version = manifest["packages"][distribution]  # type: ignore[index]
        dist = site / f"{module}-{version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            f"Name: {distribution}\nVersion: {version}\n", encoding="utf-8"
        )
        rows: list[str] = []
        for path in (module_path, dist / "METADATA"):
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(path.read_bytes()).digest()
            ).rstrip(b"=").decode("ascii")
            rows.append(
                f"{path.relative_to(site).as_posix()},sha256={digest},{path.stat().st_size}"
            )
        rows.append(f"{dist.relative_to(site).as_posix()}/RECORD,,")
        (dist / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _installed_private_runtime(data_root: Path, manifest: dict[str, object]) -> Path:
    root = data_root / "tools" / "comfy"
    site = root / "site-packages"
    _populate_site(site, manifest)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return site


def test_prepare_source_checkout_without_bootstrap_leaves_environment_unchanged(
    tmp_path: Path,
) -> None:
    environ = {"KEEP": "yes"}

    assert portable_comfy.prepare_portable_comfy_defaults(
        tmp_path, tmp_path / "data", tmp_path / ".env", environ
    ) == {}
    assert environ == {"KEEP": "yes"}


def test_prepare_partial_bootstrap_fails_closed(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "python"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"python")

    with pytest.raises(PortableComfyError, match="incomplete.*comfy.exe"):
        portable_comfy.prepare_portable_comfy_defaults(
            tmp_path, tmp_path / "data", tmp_path / ".env", {}
        )


def test_prepare_reuses_matching_private_runtime_without_installing(
    tmp_path: Path,
) -> None:
    runtime, manifest = _bootstrap_runtime(tmp_path)
    data_root = tmp_path / "data"
    _installed_private_runtime(data_root, manifest)
    environ: dict[str, str] = {}
    health_commands: list[list[str]] = []

    def health_runner(command, **_kwargs):
        assert "pip" not in command
        health_commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    applied = portable_comfy.prepare_portable_comfy_defaults(
        tmp_path,
        data_root,
        tmp_path / ".env",
        environ,
        runner=health_runner,
    )

    assert applied == {
        "DS_COMFY_MCP_COMMAND": str(runtime / "python.exe"),
        "DS_COMFY_MCP_ARGS": "-m comfy_mcp.server",
        "DS_COMFY_MCP_COMFY_BIN": str(runtime / "comfy.exe"),
    }
    assert environ == applied
    assert len(health_commands) == 1


def test_prepare_installs_missing_private_runtime_before_applying_defaults(
    tmp_path: Path,
) -> None:
    runtime, manifest = _bootstrap_runtime(tmp_path)
    data_root = tmp_path / "data"
    commands: list[list[str]] = []

    def install_runner(command, **kwargs):
        commands.append([str(item) for item in command])
        if "--target" in command:
            target = Path(command[command.index("--target") + 1])
            _populate_site(target, manifest)
        return subprocess.CompletedProcess(command, 0)

    environ: dict[str, str] = {}
    portable_comfy.prepare_portable_comfy_defaults(
        tmp_path,
        data_root,
        tmp_path / ".env",
        environ,
        runner=install_runner,
    )

    assert commands and commands[0][:4] == [
        str(runtime / "python.exe"),
        "-m",
        "pip",
        "install",
    ]
    assert "--require-hashes" in commands[0]
    assert "--only-binary=:all:" in commands[0]
    assert commands[1][1] == "-S"
    assert (data_root / "tools" / "comfy" / "manifest.json").is_file()


def test_prepare_reinstalls_private_runtime_with_corrupted_record_file(
    tmp_path: Path,
) -> None:
    _runtime_path, manifest = _bootstrap_runtime(tmp_path)
    data_root = tmp_path / "data"
    site = _installed_private_runtime(data_root, manifest)
    (site / "comfy_mcp" / "__init__.py").write_text("corrupted", encoding="utf-8")
    installs = 0

    def runner(command, **_kwargs):
        nonlocal installs
        if "--target" in command:
            installs += 1
            _populate_site(Path(command[command.index("--target") + 1]), manifest)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    portable_comfy.prepare_portable_comfy_defaults(
        tmp_path, data_root, tmp_path / ".env", {}, runner=runner
    )

    assert installs == 1


def test_prepare_serializes_bootstrap_with_package_data_lock(tmp_path: Path) -> None:
    _bootstrap_runtime(tmp_path)
    tools = tmp_path / "data" / "tools"

    with portable_comfy._bootstrap_lock(tools):
        with pytest.raises(PortableComfyError, match="Timed out waiting"):
            portable_comfy.prepare_portable_comfy_defaults(
                tmp_path,
                tmp_path / "data",
                tmp_path / ".env",
                {},
                lock_timeout_sec=0,
            )


def test_prepare_explicit_command_skips_private_runtime_install(tmp_path: Path) -> None:
    _bootstrap_runtime(tmp_path)
    environ = {"DS_COMFY_MCP_COMMAND": "C:/custom/comfy-mcp.exe"}

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("explicit command must skip automatic installation")

    assert portable_comfy.prepare_portable_comfy_defaults(
        tmp_path,
        tmp_path / "data",
        tmp_path / ".env",
        environ,
        runner=unexpected_runner,
    ) == {}
    assert environ == {"DS_COMFY_MCP_COMMAND": "C:/custom/comfy-mcp.exe"}


def test_prepare_env_file_command_skips_private_runtime_install(tmp_path: Path) -> None:
    _bootstrap_runtime(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DS_COMFY_MCP_COMMAND=C:/custom/comfy-mcp.exe\n", encoding="utf-8"
    )

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("explicit command must skip automatic installation")

    assert portable_comfy.prepare_portable_comfy_defaults(
        tmp_path,
        tmp_path / "data",
        env_file,
        {},
        runner=unexpected_runner,
    ) == {}


def test_failed_upgrade_preserves_previous_private_runtime(tmp_path: Path) -> None:
    _runtime_path, manifest = _bootstrap_runtime(tmp_path)
    data_root = tmp_path / "data"
    old_manifest = {**manifest, "lock_sha256": "0" * 64}
    site = _installed_private_runtime(data_root, old_manifest)
    sentinel = site / "keep.txt"
    sentinel.write_text("old runtime", encoding="utf-8")

    def failed_runner(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(PortableComfyError, match="automatic installation failed"):
        portable_comfy.prepare_portable_comfy_defaults(
            tmp_path,
            data_root,
            tmp_path / ".env",
            {},
            runner=failed_runner,
        )

    assert sentinel.read_text(encoding="utf-8") == "old runtime"
    assert json.loads(
        (data_root / "tools" / "comfy" / "manifest.json").read_text(
            encoding="utf-8"
        )
    ) == old_manifest


def test_failed_post_activation_import_restores_previous_private_runtime(
    tmp_path: Path,
) -> None:
    _runtime_path, manifest = _bootstrap_runtime(tmp_path)
    data_root = tmp_path / "data"
    old_manifest = {**manifest, "lock_sha256": "0" * 64}
    old_site = _installed_private_runtime(data_root, old_manifest)
    sentinel = old_site / "keep.txt"
    sentinel.write_text("old runtime", encoding="utf-8")

    def runner(command, **_kwargs):
        if "--target" in command:
            _populate_site(Path(command[command.index("--target") + 1]), manifest)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if len(command) == 5 and command[1] == "-S":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="import failed")

    with pytest.raises(PortableComfyError, match="activated.*validation"):
        portable_comfy.prepare_portable_comfy_defaults(
            tmp_path,
            data_root,
            tmp_path / ".env",
            {},
            runner=runner,
        )

    assert sentinel.read_text(encoding="utf-8") == "old runtime"
    assert json.loads(
        (data_root / "tools" / "comfy" / "manifest.json").read_text(
            encoding="utf-8"
        )
    ) == old_manifest
