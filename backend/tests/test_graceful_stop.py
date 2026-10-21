"""Cross-platform graceful backend stop via a stop-request file (DS_STOP_FILE)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from app.core.process.managed import LaunchSpec, ManagedProcess, request_stop_via_file, reserve_loopback_port
from app.core.system import control

BACKEND = Path(__file__).resolve().parents[1]

POLLER = (
    "import pathlib, sys, time\n"
    "p = pathlib.Path(sys.argv[1])\n"
    "while not p.exists(): time.sleep(0.05)\n"
    "print('graceful exit', flush=True)\n"
)
IGNORER = "import time\nwhile True: time.sleep(0.1)\n"


def _managed(tmp_path: Path, code: str) -> ManagedProcess:
    stop_file = tmp_path / "child.stop"

    async def health() -> bool:
        return True

    def build() -> LaunchSpec:
        return LaunchSpec(argv=[sys.executable, "-c", code, str(stop_file)], cwd=tmp_path,
                          env={"PATH": os.environ.get("PATH", "")}, health=health, startup_timeout=10)

    return ManagedProcess(name="child", build=build, log_path=tmp_path / "child.log", pid_file=tmp_path / "child.pid",
                          stop_file=stop_file, graceful_stop=True, restart_on_crash=False)


@pytest.mark.asyncio
async def test_stop_file_lets_child_exit_cleanly(tmp_path):
    proc = _managed(tmp_path, POLLER)
    (tmp_path / "child.stop").write_text("stale")  # a stale request must not stop the new child
    await proc.start(wait=True)
    assert not (tmp_path / "child.stop").exists()
    child = proc._process
    started = time.monotonic()
    await proc.stop(timeout=10)
    assert child.returncode == 0  # exited by itself, not by a signal
    assert time.monotonic() - started < 5
    assert "graceful exit" in (tmp_path / "child.log").read_text()
    assert not (tmp_path / "child.stop").exists()


@pytest.mark.asyncio
async def test_stop_file_ignored_falls_back_to_kill(tmp_path):
    proc = _managed(tmp_path, IGNORER)
    await proc.start(wait=True)
    child = proc._process
    started = time.monotonic()
    await proc.stop(timeout=1)
    assert child.poll() is not None  # killed by the kill_tree fallback (psutil reaps it, so no signal code)
    assert time.monotonic() - started >= 1.0  # the stop file was given its full timeout first
    assert "graceful exit" not in (tmp_path / "child.log").read_text()
    assert not (tmp_path / "child.stop").exists()


def test_request_stop_via_file_times_out(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", IGNORER])
    try:
        assert request_stop_via_file(child, tmp_path / "x" / "stop", 0.3) is False
        assert (tmp_path / "x" / "stop").exists()
    finally:
        child.kill()
        child.wait()


def test_watch_stop_file_sets_should_exit(tmp_path, monkeypatch):
    class FakeServer:
        should_exit = False

    server = FakeServer()
    monkeypatch.setattr(control, "_server", server)
    (tmp_path / "stop").write_text("stale")
    thread = control.watch_stop_file(tmp_path / "stop", interval=0.02)
    assert not (tmp_path / "stop").exists()  # stale request cleared at start
    time.sleep(0.1)
    assert server.should_exit is False
    (tmp_path / "stop").write_text("now")
    thread.join(2)
    assert server.should_exit is True and not (tmp_path / "stop").exists()


def test_real_backend_runs_lifespan_shutdown_on_stop_file(tmp_path):
    """python -m app.serve exits 0 through uvicorn's graceful shutdown when DS_STOP_FILE appears."""
    port = reserve_loopback_port()
    stop_file = tmp_path / "backend.stop"
    env = {**os.environ, "DS_STOP_FILE": str(stop_file), "DS_DATA_DIR": str(tmp_path / "data"),
           "DS_COMFY_MODE": "external-local", "DS_OLLAMA_MODE": "system", "DS_HARNESS_MANAGED": "false",
           "DS_DIRECTOR_AGENT_RUNTIME": "legacy", "PYTHONUNBUFFERED": "1"}
    log = open(tmp_path / "serve.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "app.serve", "--port", str(port)], cwd=BACKEND, env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/setup/state", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert proc.poll() is None, (tmp_path / "serve.log").read_text()[-2000:]
            time.sleep(0.3)
        assert request_stop_via_file(proc, stop_file, 30) is True
        assert proc.returncode == 0
        text = (tmp_path / "serve.log").read_text()
        assert "Application shutdown complete" in text, text[-2000:]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        log.close()


@pytest.mark.asyncio
async def test_launcher_wires_stop_file_into_backend(tmp_path, monkeypatch):
    import argparse

    from app import launcher
    from app.runtime_paths import resolve_runtime_paths

    (tmp_path / "ds-install.json").write_text("{}")
    paths = resolve_runtime_paths(source_root=tmp_path, environ={"DS_HOME": str(tmp_path / "home")})
    args = argparse.Namespace(port=18999, host=None, log_dir=None, harness_port=0, node=None, open=False,
                              startup_timeout=5)
    monkeypatch.setattr(launcher.os, "environ", {"PATH": "/usr/bin"})
    instance = launcher.Launcher(args, paths)
    spec = instance._backend_spec()
    assert spec.env["DS_STOP_FILE"] == str(instance.cfg.run_dir / "backend.stop")
    await asyncio.sleep(0)


def test_serve_bounds_graceful_shutdown_so_open_streams_cannot_block_stop(monkeypatch):
    import uvicorn

    from app import serve
    from app.config import settings

    seen = {}

    class FakeServer:
        started = True

        def __init__(self, config):
            seen["config"] = config

        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.delenv("DS_STOP_FILE", raising=False)
    monkeypatch.setattr(settings, "port", settings.port)
    monkeypatch.setattr(settings, "host", settings.host)
    monkeypatch.setattr(control, "register_server", lambda server: None)
    assert serve.main(["--port", str(reserve_loopback_port())]) == 0
    # must be well under the launcher's 20 s stop-file wait so lifespan shutdown still runs
    assert 0 < seen["config"].timeout_graceful_shutdown <= 10


@pytest.mark.skipif(os.name == "nt", reason="TIME_WAIT does not block bind() on Windows")
def test_port_is_free_ignores_time_wait_after_server_stop():
    import socket

    from app.core.process.managed import port_is_free

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()  # server side closes first -> its end of the connection enters TIME_WAIT
    time.sleep(0.1)
    client.close()
    server.close()
    assert port_is_free(port)
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # like uvicorn
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        assert not port_is_free(port)
    finally:
        holder.close()
