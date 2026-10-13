"""DS launcher config, single-instance lock, restart endpoint, serve port sync, loop-local lock."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import launcher
from app.config import settings
from app.core.process.managed import LoopLocalLock
from app.core.system import control
from app.runtime_paths import resolve_runtime_paths

ADMIN = {"X-DS-Admin": "1"}


def _installed(tmp_path: Path, defaults: dict[str, str]) -> Path:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "ds-install.json").write_text(json.dumps({"format": 1, "variant": "cpu", "defaults": defaults}))
    return root


def test_installed_paths_expand_placeholders_and_use_ds_home(tmp_path):
    root = _installed(tmp_path, {"DS_COMFY_MCP_COMMAND": "${INSTALL_ROOT}/runtime/bin/comfy-mcp",
                                 "DS_X": "${DS_HOME}/x", "DS_COMFY_MODE": "managed"})
    home = tmp_path / "home"
    paths = resolve_runtime_paths(frozen=False, source_root=root, environ={"DS_HOME": str(home)})
    assert paths.installed
    assert paths.env_file == home / ".env"
    assert paths.data_root == home / "data"
    assert paths.install_defaults["DS_COMFY_MCP_COMMAND"] == f"{root.resolve()}/runtime/bin/comfy-mcp"
    assert paths.install_defaults["DS_X"] == f"{home}/x"


def test_installed_default_data_dir_is_xdg(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # XDG rule; macOS uses ~/Library/Application Support
    root = _installed(tmp_path, {})
    paths = resolve_runtime_paths(frozen=False, source_root=root, environ={"XDG_DATA_HOME": str(tmp_path / "xdg")})
    assert paths.env_file == tmp_path / "xdg" / "DirectorStudio" / ".env"


def test_read_config_precedence(tmp_path):
    root = _installed(tmp_path, {"DS_PORT": "1111", "DS_DIRECTOR_AGENT_RUNTIME": "legacy"})
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_runtime_paths(frozen=False, source_root=root, environ={"DS_HOME": str(home)})
    assert launcher.read_config(paths, environ={}).port == 1111
    (home / ".env").write_text("DS_PORT=2222\n")
    cfg = launcher.read_config(paths, environ={})
    assert cfg.port == 2222 and cfg.runtime == "legacy"
    assert launcher.read_config(paths, environ={"DS_PORT": "3333"}).port == 3333
    assert launcher.read_config(paths, environ={"DS_PORT": "3333"}, port=18890).port == 18890
    assert cfg.data_dir == home / "data" and cfg.run_dir == home / "data" / "run"


def test_instance_lock_is_exclusive(tmp_path):
    first = launcher.InstanceLock(tmp_path / "run" / "launcher.lock")
    second = launcher.InstanceLock(tmp_path / "run" / "launcher.lock")
    assert first.acquire()
    assert not second.acquire()


def test_read_state_ignores_stale_pid(tmp_path):
    root = _installed(tmp_path, {})
    paths = resolve_runtime_paths(frozen=False, source_root=root, environ={"DS_HOME": str(tmp_path / "h")})
    cfg = launcher.read_config(paths, environ={})
    cfg.run_dir.mkdir(parents=True)
    (cfg.run_dir / "launcher.json").write_text(json.dumps({"pid": 999999, "create_time": 1.0}))
    assert launcher.read_state(cfg) is None
    assert launcher.stop_running(cfg) is False


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app, base_url="http://127.0.0.1:8890", client=("127.0.0.1", 5000)) as test_client:
        yield test_client


def test_restart_without_supervisor_is_409(client, monkeypatch):
    monkeypatch.delenv("DS_LAUNCHER_PID", raising=False)
    response = client.post("/api/system/restart", headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["supervisor_available"] is False


def test_restart_requires_admin(client):
    assert client.post("/api/system/restart").status_code == 403


def test_restart_with_supervisor(client, monkeypatch):
    from app.core.config_store import service

    calls = []
    monkeypatch.setattr(control, "supervisor_available", lambda: True)
    monkeypatch.setattr(control, "request_restart", lambda delay=0.4: calls.append(delay) or True)
    monkeypatch.setattr(service, "pipeline_busy", lambda: True)
    busy = client.post("/api/system/restart", headers=ADMIN)
    assert busy.status_code == 409 and busy.json()["busy"] is True
    response = client.post("/api/system/restart?force=true", headers=ADMIN)
    assert response.status_code == 202
    assert response.json() == {"restarting": True, "boot_id": control.BOOT_ID}
    assert calls
    state = client.get("/api/setup/state").json()
    assert state["boot_id"] == control.BOOT_ID and state["supervisor_available"] is True


def test_supervisor_requires_launcher_parent(monkeypatch):
    import os

    monkeypatch.setattr(control, "_server", object())
    monkeypatch.setenv("DS_LAUNCHER_PID", str(os.getppid()))
    assert control.supervisor_available() is True
    monkeypatch.setenv("DS_LAUNCHER_PID", str(os.getpid()))  # not our parent
    assert control.supervisor_available() is False
    monkeypatch.setattr(control, "_server", None)
    assert control.supervisor_available() is False


def test_serve_syncs_cli_port_into_settings(monkeypatch):
    import uvicorn

    from app import serve

    seen = {}

    class FakeServer:
        started = True

        def __init__(self, config):
            seen["port"], seen["host"] = config.port, config.host

        def run(self):
            seen["settings_port"] = settings.port

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(control, "register_server", lambda server: None)
    monkeypatch.setattr(settings, "port", 8790)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    assert serve.main(["--port", "18890"]) == 0
    assert seen == {"port": 18890, "host": "127.0.0.1", "settings_port": 18890}
    from app.api.security import allowed_origin_ports, is_local_origin

    assert 18890 in allowed_origin_ports()
    assert is_local_origin("http://127.0.0.1:18890")


def test_loop_local_lock_survives_multiple_event_loops():
    lock = LoopLocalLock()

    async def contend():
        async def hold():
            async with lock:
                await asyncio.sleep(0.01)

        await asyncio.gather(hold(), hold(), hold())
        assert not lock.locked()

    for _ in range(3):  # a plain asyncio.Lock raises "bound to a different event loop" here
        asyncio.run(contend())


def test_settings_written_env_is_not_legacy(tmp_path, monkeypatch):
    from app.core.config_store import envfile, service

    env = tmp_path / ".env"
    monkeypatch.setattr(service, "env_path", lambda: env)
    assert service.setup_state()["legacy_env_detected"] is False  # no file
    env.write_text(envfile.apply_changes(None, {"DS_HOST": "127.0.0.1"}))
    assert service.setup_state()["legacy_env_detected"] is False  # only the managed block
    env.write_text("DS_LLM_PROVIDER=lm-studio\nDS_WEIRD=1\n" + env.read_text())
    assert service.setup_state()["legacy_env_detected"] is True  # hand-written entries


def test_skip_wizard_persists_and_system_info_reports_serving_port(client, monkeypatch):
    monkeypatch.setattr(settings, "port", 18890)
    assert client.get("/api/setup/state").json()["completed"] is False
    state = client.post("/api/setup/complete", headers=ADMIN, json={"skipped": True}).json()
    assert state["completed"] is True and state["skipped"] is True
    again = client.get("/api/setup/state").json()
    assert again["completed"] is True and again["skipped"] is True
    done = client.post("/api/setup/complete", headers=ADMIN).json()  # finishing the wizard later
    assert done["completed"] is True and done["skipped"] is False
    info = client.get("/api/system/info", headers=ADMIN).json()
    assert info["serving_port"] == 18890 and info["serving_url"] == "http://127.0.0.1:18890"
