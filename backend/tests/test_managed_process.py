from __future__ import annotations

import asyncio
import sys

import httpx
import psutil
import pytest

from app.core.process.managed import LaunchSpec, ManagedProcess, reserve_loopback_port


# Plain TCPServer: http.server's HTTPServer.server_bind() calls socket.getfqdn(), which can block for
# tens of seconds on macOS CI runners (reverse DNS) and blow the startup timeout.
SERVER = ("import http.server, socketserver, sys; socketserver.TCPServer.allow_reuse_address = True; "
          "socketserver.TCPServer(('127.0.0.1', int(sys.argv[1])), http.server.SimpleHTTPRequestHandler).serve_forever()")


def make(tmp_path, port):
    async def health() -> bool:
        try:
            async with httpx.AsyncClient(timeout=1) as client:
                return (await client.get(f"http://127.0.0.1:{port}/")).status_code == 200
        except httpx.HTTPError:
            return False

    def build() -> LaunchSpec:
        return LaunchSpec(argv=[sys.executable, "-c", SERVER, str(port)],
                          cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, health=health, startup_timeout=20)

    return ManagedProcess(name="test", build=build, log_path=tmp_path / "log.txt", pid_file=tmp_path / "p.json",
                          backoff_initial=0.2)


@pytest.mark.asyncio
async def test_start_crash_restart_stop(tmp_path):
    port = reserve_loopback_port()
    proc = make(tmp_path, port)
    await proc.start(wait=True)
    assert proc.state == "ready"
    first = proc.pid
    assert (tmp_path / "p.json").exists()
    psutil.Process(first).kill()
    for _ in range(100):
        await asyncio.sleep(0.2)
        if proc.state == "ready" and proc.pid and proc.pid != first:
            break
    assert proc.state == "ready" and proc.pid != first and proc.restarts == 1
    second = proc.pid
    await proc.stop()
    assert proc.state == "stopped" and not psutil.pid_exists(second) or psutil.Process(second).status() == "zombie"
    assert not (tmp_path / "p.json").exists()


@pytest.mark.asyncio
async def test_orphan_cleanup(tmp_path):
    port = reserve_loopback_port()
    proc = make(tmp_path, port)
    await proc.start(wait=True)
    orphan = proc.pid
    proc._process = None  # simulate a crashed parent that left the child + pid file behind
    proc._stopping = True
    other = make(tmp_path, port)
    assert other.cleanup_orphan() is True
    await asyncio.sleep(0.2)
    assert not psutil.pid_exists(orphan) or psutil.Process(orphan).status() == "zombie"
    await proc.stop()
