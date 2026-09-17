from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from app.portable_harness import PortableHarnessSettings


ENTRYPOINT = Path(__file__).resolve().parents[1] / "packaging" / "entrypoint.py"


class FakeHandle:
    base_url = "http://127.0.0.1:19001"
    token = "generated-token"

    def __init__(self, events: list) -> None:
        self.events = events
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("stopped")


def _load_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch: PortableHarnessSettings,
):
    events: list = []
    handle = FakeHandle(events)

    fake_uvicorn = ModuleType("uvicorn")

    def run(_app, **kwargs):
        events.append(("uvicorn", kwargs["host"], kwargs["port"]))

    fake_uvicorn.run = run

    fake_paths = ModuleType("app.runtime_paths")
    fake_paths.runtime_paths = SimpleNamespace(
        env_file=tmp_path / ".env",
        install_root=tmp_path,
        data_root=tmp_path / "data",
    )

    fake_harness = ModuleType("app.portable_harness")
    fake_harness.HarnessStartupError = RuntimeError
    fake_harness.read_portable_harness_settings = lambda *_args: launch

    def start_managed_harness(*_args):
        events.append("sidecar-ready")
        return handle

    fake_harness.start_managed_harness = start_managed_harness

    fake_comfy = ModuleType("app.portable_comfy")
    fake_comfy.PortableComfyError = RuntimeError

    def prepare_portable_comfy_defaults(install_root, data_root, env_file, environ):
        events.append(("comfy-ready", install_root, data_root, env_file))
        applied = {
            "DS_COMFY_MCP_COMMAND": str(tmp_path / "runtime/python/python.exe"),
            "DS_COMFY_MCP_ARGS": "-m comfy_mcp.server",
            "DS_COMFY_MCP_COMFY_BIN": str(tmp_path / "runtime/python/comfy.exe"),
        }
        environ.update(applied)
        return applied

    fake_comfy.prepare_portable_comfy_defaults = prepare_portable_comfy_defaults

    fake_config = ModuleType("app.config")

    def config_getattr(name: str):
        if name != "settings":
            raise AttributeError(name)
        events.append(
            (
                "settings-loaded",
                os.environ.get("DS_HARNESS_BASE_URL"),
                os.environ.get("DS_HARNESS_INTERNAL_TOKEN"),
            )
        )
        return SimpleNamespace(
            host="127.0.0.1",
            port=8790,
            harness_base_url=os.environ.get("DS_HARNESS_BASE_URL", ""),
            harness_internal_token=os.environ.get("DS_HARNESS_INTERNAL_TOKEN", ""),
        )

    fake_config.__getattr__ = config_getattr

    fake_main = ModuleType("app.main")

    def main_getattr(name: str):
        if name != "create_app":
            raise AttributeError(name)
        return lambda: events.append("app-created") or object()

    fake_main.__getattr__ = main_getattr

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)
    monkeypatch.setitem(__import__("sys").modules, "app.runtime_paths", fake_paths)
    monkeypatch.setitem(__import__("sys").modules, "app.portable_harness", fake_harness)
    monkeypatch.setitem(__import__("sys").modules, "app.portable_comfy", fake_comfy)
    monkeypatch.setitem(__import__("sys").modules, "app.config", fake_config)
    monkeypatch.setitem(__import__("sys").modules, "app.main", fake_main)

    spec = importlib.util.spec_from_file_location("packaged_entrypoint_test", ENTRYPOINT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, events, handle


def test_managed_harness_is_ready_before_settings_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, events, handle = _load_entrypoint(
        monkeypatch,
        tmp_path,
        PortableHarnessSettings(runtime="harness", managed=True),
    )

    assert events == []
    module.main()

    assert events == [
        ("comfy-ready", tmp_path, tmp_path / "data", tmp_path / ".env"),
        "sidecar-ready",
        ("settings-loaded", "http://127.0.0.1:19001", "generated-token"),
        "app-created",
        ("uvicorn", "127.0.0.1", 8790),
        "stopped",
    ]
    assert handle.stop_calls == 1
    assert "DS_COMFY_MCP_COMMAND" not in os.environ
    assert "DS_COMFY_MCP_ARGS" not in os.environ
    assert "DS_COMFY_MCP_COMFY_BIN" not in os.environ


@pytest.mark.parametrize(
    "launch",
    [
        PortableHarnessSettings(runtime="legacy", managed=True),
        PortableHarnessSettings(runtime="harness", managed=False),
    ],
)
def test_unmanaged_modes_skip_bundled_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch: PortableHarnessSettings,
) -> None:
    if launch.runtime == "harness":
        monkeypatch.setenv("DS_HARNESS_BASE_URL", "http://127.0.0.1:19002")
        monkeypatch.setenv("DS_HARNESS_INTERNAL_TOKEN", "external-token")
    module, events, handle = _load_entrypoint(monkeypatch, tmp_path, launch)

    assert events == []
    module.main()

    assert "sidecar-ready" not in events
    assert handle.stop_calls == 0


def test_unmanaged_harness_requires_external_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DS_HARNESS_INTERNAL_TOKEN", raising=False)
    module, _events, _handle = _load_entrypoint(
        monkeypatch,
        tmp_path,
        PortableHarnessSettings(runtime="harness", managed=False),
    )

    with pytest.raises(RuntimeError, match="external Harness token"):
        module.main()


def test_run_reports_portable_comfy_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _events, _handle = _load_entrypoint(
        monkeypatch,
        tmp_path,
        PortableHarnessSettings(runtime="harness", managed=True),
    )
    reported: list[str] = []

    def fail_prepare(*_args):
        raise module.PortableComfyError("download failed")

    monkeypatch.setattr(module, "prepare_portable_comfy_defaults", fail_prepare)
    monkeypatch.setattr(module, "_report_startup_error", reported.append)

    with pytest.raises(SystemExit) as stopped:
        module.run()

    assert stopped.value.code == 1
    assert reported == ["Comfy tool setup failed: download failed"]
