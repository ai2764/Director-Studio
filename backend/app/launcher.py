"""Director Studio launcher / supervisor.

    python -m app.launcher [run] [--port N] [--harness-port N] [--open]
    python -m app.launcher stop | status

Owns the Harness sidecar (Node) and the FastAPI backend (``python -m app.serve``).
The backend in turn owns the managed ComfyUI and the bundled Ollama, so one
launcher process supervises the whole tree:

* planned restarts: ``POST /api/system/restart`` makes the backend exit with
  ``RESTART_EXIT_CODE``; the launcher re-reads ``.env``, restarts Harness and
  starts the backend again (restart-required settings take effect);
* crash restarts with exponential backoff (``ManagedProcess``);
* single instance per data directory (lock file), orphan cleanup via pid files
  with create-time checks, children die with the launcher (PDEATHSIG on Linux,
  own session so terminal signals reach only the launcher);
* works from a source checkout (dev, ``tsx`` Harness) and from an installed
  package (``ds-install.json`` marker, bundled Node + ``harness/dist``).

Windows: the launcher puts itself into a kill-on-close Job Object
(``core/process/winjob.py``) so the whole tree dies with it, children get
CREATE_NO_WINDOW, the browser is started outside the job, and ``stop`` asks
the running launcher to shut down gracefully through ``run/stop.request``
(Windows has no SIGTERM) before falling back to terminating it.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sys
import time
from typing import Mapping
import urllib.error
import urllib.request

from dotenv import dotenv_values
import psutil

from .core.process import winjob
from .core.process.managed import LaunchSpec, ManagedProcess, port_is_free, reserve_loopback_port
from .core.system.control import RESTART_EXIT_CODE
from .portable_harness import _validate_health, probe_harness_health
from .runtime_paths import RuntimePaths, resolve_runtime_paths

_HARNESS_ENV_KEYS = (
    "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TZ",
    "SystemRoot", "WINDIR", "SystemDrive", "ComSpec", "PATHEXT", "USERPROFILE",
    "LOCALAPPDATA", "APPDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)


STOP_REQUEST = "stop.request"


def log(message: str) -> None:
    print(f"[launcher {time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---- configuration -------------------------------------------------------------------

@dataclass
class LaunchConfig:
    paths: RuntimePaths
    host: str = "127.0.0.1"
    port: int = 8790
    runtime: str = "harness"  # harness | legacy
    harness_managed: bool = True
    data_dir: Path = field(default_factory=Path)
    log_dir: Path = field(default_factory=Path)
    run_dir: Path = field(default_factory=Path)

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::", ""} else self.host
        return f"http://{host}:{self.port}"


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def read_config(
    paths: RuntimePaths,
    *,
    environ: Mapping[str, str] | None = None,
    port: int | None = None,
    host: str | None = None,
    log_dir: Path | None = None,
) -> LaunchConfig:
    """Effective launcher settings: CLI > environment > .env > package defaults > code."""
    env = os.environ if environ is None else environ
    file_values = {
        k: v for k, v in (dotenv_values(paths.env_file) if paths.env_file.is_file() else {}).items()
        if v is not None
    }

    def get(key: str, default: str = "") -> str:
        if key in env:
            return str(env[key])
        if key in file_values:
            return str(file_values[key])
        return str(paths.install_defaults.get(key, default))

    data_dir = Path(get("DS_DATA_DIR") or paths.data_root).expanduser()
    if not data_dir.is_absolute():
        data_dir = (paths.install_root / "backend" / data_dir).resolve()
    runtime = get("DS_DIRECTOR_AGENT_RUNTIME", "harness").strip().lower() or "harness"
    return LaunchConfig(
        paths=paths,
        host=host or get("DS_HOST", "127.0.0.1") or "127.0.0.1",
        port=int(port or get("DS_PORT", "8790") or 8790),
        runtime=runtime if runtime in {"harness", "legacy"} else "harness",
        harness_managed=_truthy(get("DS_HARNESS_MANAGED", "true"), True),
        data_dir=data_dir,
        log_dir=Path(log_dir) if log_dir else data_dir / "logs",
        run_dir=data_dir / "run",
    )


# ---- commands --------------------------------------------------------------------------

def harness_command(paths: RuntimePaths, node_override: str | None = None) -> tuple[list[str], Path]:
    root = paths.install_root / "harness"
    node_name = "node.exe" if os.name == "nt" else "node"
    bundled_node = paths.install_root / "runtime" / "node" / node_name
    dist_entry = root / "dist" / "server.js"
    node = node_override or (str(bundled_node) if bundled_node.is_file() else shutil.which("node"))
    if not node:
        raise RuntimeError("Node.js 22+ is required for the Harness sidecar (not found)")
    if dist_entry.is_file() and (paths.installed or not (root / "src" / "server.ts").is_file()):
        return [node, str(dist_entry)], root
    return [node, "--import", "tsx", "src/server.ts"], root


def backend_python(paths: RuntimePaths) -> str:
    bundled = paths.install_root / "runtime" / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    if paths.installed and bundled.is_file():
        return str(bundled)
    return sys.executable


def _http_ok(url: str, headers: Mapping[str, str] | None = None, timeout: float = 2.0) -> bool:
    request = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---- supervisor --------------------------------------------------------------------

class Launcher:
    def __init__(self, args: argparse.Namespace, paths: RuntimePaths) -> None:
        self.args = args
        self.paths = paths
        self.cfg = read_config(paths, port=args.port, host=args.host, log_dir=args.log_dir)
        self.token = secrets.token_hex(32)
        self.harness_port = args.harness_port or 0
        self.stop_event = asyncio.Event()
        self.harness: ManagedProcess | None = None
        self.backend: ManagedProcess | None = None
        self.opened_browser = False
        self.job: winjob.KillOnCloseJob | None = None

    # -- harness
    def _harness_spec(self) -> LaunchSpec:
        argv, cwd = harness_command(self.paths, self.args.node)
        if not self.harness_port or not port_is_free(self.harness_port):
            if self.args.harness_port and not port_is_free(self.args.harness_port):
                raise RuntimeError(f"Harness port {self.args.harness_port} is in use")
            self.harness_port = self.args.harness_port or reserve_loopback_port()
        sessions = self.cfg.data_dir / "harness-sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        env = {k: os.environ[k] for k in _HARNESS_ENV_KEYS if k in os.environ}
        node_dir = str(Path(argv[0]).parent)
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        env.update(
            DS_HARNESS_INTERNAL_TOKEN=self.token,
            DS_HARNESS_PORT=str(self.harness_port),
            DS_HARNESS_SESSION_ROOT=str(sessions),
            DS_HARNESS_PARENT_PID=str(os.getpid()),
        )
        base = f"http://127.0.0.1:{self.harness_port}"
        token = self.token

        async def health() -> bool:
            value = await asyncio.to_thread(probe_harness_health, base, token)
            return value is not None and _validate_health(value) is None

        return LaunchSpec(argv=argv, cwd=cwd, env=env, health=health, startup_timeout=60,
                          label=f"{' '.join(argv)} (port {self.harness_port})")

    # -- backend
    def _backend_spec(self) -> LaunchSpec:
        cfg = self.cfg
        env = dict(os.environ)
        env["DS_LAUNCHER_PID"] = str(os.getpid())
        env["DS_STOP_FILE"] = str(self._backend_stop_file())
        env.setdefault("PYTHONUNBUFFERED", "1")
        if self.harness is not None:
            env["DS_HARNESS_BASE_URL"] = f"http://127.0.0.1:{self.harness_port}"
            env["DS_HARNESS_INTERNAL_TOKEN"] = self.token
            env["DS_HARNESS_MANAGED"] = "false"  # owned by the launcher, not the backend
        if self.paths.installed:
            env.setdefault("DS_HOME", str(self.paths.env_file.parent))
        argv = [backend_python(self.paths), "-m", "app.serve", "--host", cfg.host, "--port", str(cfg.port)]
        url = f"{cfg.url}/api/setup/state"

        async def health() -> bool:
            return await asyncio.to_thread(_http_ok, url)

        return LaunchSpec(argv=argv, cwd=self.paths.install_root / "backend", env=env, health=health,
                          startup_timeout=self.args.startup_timeout, label=" ".join(argv))

    def _backend_stop_file(self) -> Path:
        return self.cfg.run_dir / "backend.stop"

    async def _on_backend_exit(self, code: int | None) -> bool:
        if code != RESTART_EXIT_CODE or self.stop_event.is_set():
            return False
        log("restart requested by the backend: reloading configuration")
        old_port = self.cfg.port
        self.cfg = read_config(self.paths, port=self.args.port, host=self.args.host, log_dir=self.args.log_dir)
        if self.cfg.port != old_port:
            log(f"backend port changed {old_port} -> {self.cfg.port}")
        await self._sync_harness(restart=True)
        return True

    async def _sync_harness(self, *, restart: bool = False) -> None:
        want = self.cfg.runtime == "harness" and self.cfg.harness_managed
        if want and self.harness is None:
            self.harness = ManagedProcess(
                name="harness", build=self._harness_spec,
                log_path=self.cfg.log_dir / "harness.log", pid_file=self.cfg.run_dir / "harness.pid",
            )
            await self.harness.start()
        elif want and restart and self.harness is not None:
            await self.harness.restart()
        elif not want and self.harness is not None:
            await self.harness.stop()
            self.harness = None
        if self.harness is not None:
            if not await self.harness.wait_ready(90):
                raise RuntimeError(f"Harness failed to start: {self.harness.last_error}")
            log(f"harness ready on 127.0.0.1:{self.harness_port} (pid {self.harness.pid})")

    def _write_state(self) -> None:
        state = {
            "pid": os.getpid(),
            "create_time": psutil.Process().create_time(),
            "url": self.cfg.url,
            "port": self.cfg.port,
            "harness_port": self.harness_port or None,
            "install_root": str(self.paths.install_root),
            "env_file": str(self.paths.env_file),
            "started_at": time.time(),
        }
        path = self.cfg.run_dir / "launcher.json"
        # Atomic replace: status/stop must never read a half-written file and
        # conclude "not running" (state is rewritten on every backend (re)start).
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # pragma: no cover - windows: reader holds the file open
                if attempt == 9:
                    raise
                time.sleep(0.05)

    async def run(self) -> int:
        cfg = self.cfg
        if not port_is_free(cfg.port) and cfg.host in {"127.0.0.1", "localhost", "0.0.0.0"}:
            log(f"ERROR: port {cfg.port} is already in use; set DS_PORT or pass --port")
            return 2
        self._install_signal_handlers()
        self._contain_children()
        (cfg.run_dir / STOP_REQUEST).unlink(missing_ok=True)
        self._write_state()
        try:
            await self._sync_harness()
        except RuntimeError as exc:
            log(f"ERROR: {exc}")
            await self.shutdown()
            return 3
        self.backend = ManagedProcess(
            name="backend", build=self._backend_spec,
            log_path=cfg.log_dir / "backend.log", pid_file=cfg.run_dir / "backend.pid",
            exit_handler=self._on_backend_exit, graceful_stop=True,
            stop_file=self._backend_stop_file(),
        )
        await self.backend.start()
        watcher = asyncio.create_task(self._watch_ready())
        await self.stop_event.wait()
        watcher.cancel()
        await self.shutdown()
        return 0

    def _contain_children(self) -> None:
        try:
            self.job = winjob.contain_current_process()
        except OSError as exc:  # pragma: no cover - windows
            self.job = None
            log(f"warning: could not create a Windows job object ({exc}); children are cleaned up on clean exit only")
        if self.job is not None:  # pragma: no cover - windows
            log("child processes are contained in a kill-on-close job object")

    async def _watch_ready(self) -> None:
        """Report readiness after every (re)start and open the browser once."""
        last_boot = None
        while not self.stop_event.is_set():
            if consume_stop_request(self.cfg.run_dir):
                log("stop requested")
                self.stop_event.set()
                break
            backend = self.backend
            if backend is not None and backend.state == "ready" and backend.started_at != last_boot:
                last_boot = backend.started_at
                self._write_state()
                log(f"Director Studio ready: {self.cfg.url}  (logs: {self.cfg.log_dir})")
                if self.args.open and not self.opened_browser:
                    self.opened_browser = True
                    _open_browser(self.cfg.url)
            elif backend is not None and backend.state == "crashed" and not backend._monitor_alive():
                log(f"backend gave up after repeated crashes: {backend.last_error}")
                self.stop_event.set()
            await asyncio.sleep(0.5)

    async def shutdown(self) -> None:
        if self.backend is not None:
            log("stopping backend")
            await self.backend.stop(timeout=20)
        if self.harness is not None:
            log("stopping harness")
            await self.harness.stop()
        (self.cfg.run_dir / "launcher.json").unlink(missing_ok=True)
        log("stopped")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def stop(*_: object) -> None:
            self.stop_event.set()

        def restart(*_: object) -> None:
            if self.backend is not None:
                loop.create_task(self._manual_restart())

        if os.name == "nt":  # pragma: no cover - windows
            signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(stop))
            signal.signal(signal.SIGBREAK, lambda *_: loop.call_soon_threadsafe(stop))
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop)
        loop.add_signal_handler(signal.SIGHUP, restart)

    async def _manual_restart(self) -> None:
        log("SIGHUP: restarting backend + harness")
        self.cfg = read_config(self.paths, port=self.args.port, host=self.args.host, log_dir=self.args.log_dir)
        assert self.backend is not None
        await self.backend.stop(timeout=20)
        await self._sync_harness(restart=True)
        await self.backend.start()


def _open_browser(url: str) -> None:
    if os.name == "nt":  # pragma: no cover - windows
        try:
            winjob.open_url_outside_job(url)
            return
        except OSError:
            pass
    try:
        import webbrowser

        if not webbrowser.open(url):
            log(f"open {url} in your browser")
    except Exception:
        log(f"open {url} in your browser")


# ---- single instance ---------------------------------------------------------------

class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        try:
            if os.name == "nt":  # pragma: no cover - windows
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def instance_running(cfg: LaunchConfig) -> bool:
    """True when some launcher holds the per-data-dir instance lock."""
    probe = InstanceLock(cfg.run_dir / "launcher.lock")
    if probe.acquire():
        probe.release()
        return False
    return True


def _read_state_once(cfg: LaunchConfig) -> dict | None:
    try:
        state = json.loads((cfg.run_dir / "launcher.json").read_text(encoding="utf-8"))
        proc = psutil.Process(int(state["pid"]))
        if abs(proc.create_time() - float(state["create_time"])) < 1.0:
            return state
    except (OSError, ValueError, KeyError, psutil.Error):
        pass
    return None


def read_state(cfg: LaunchConfig, *, wait: float = 0.0) -> dict | None:
    """Launcher state; with ``wait`` keep retrying while the instance lock is held
    (state file being rewritten, or the launcher still starting up)."""
    deadline = time.monotonic() + wait
    while True:
        state = _read_state_once(cfg)
        if state is not None or time.monotonic() >= deadline or not instance_running(cfg):
            return state
        time.sleep(0.2)


def consume_stop_request(run_dir: Path) -> bool:
    """True (once) when ``stop`` asked this launcher to shut down via run/stop.request."""
    request = run_dir / STOP_REQUEST
    if not request.exists():
        return False
    request.unlink(missing_ok=True)
    return True


def stop_running(cfg: LaunchConfig, timeout: float = 40.0, *, windows: bool | None = None) -> bool:
    state = read_state(cfg, wait=5.0)
    if state is None:
        return False
    proc = psutil.Process(int(state["pid"]))
    if (os.name == "nt") if windows is None else windows:
        # No SIGTERM on Windows (terminate() == TerminateProcess): ask the launcher to
        # stop its children itself; its job object is the backstop if it is killed.
        (cfg.run_dir / STOP_REQUEST).write_text(str(time.time()), encoding="utf-8")
    else:
        proc.terminate()  # launcher stops its children gracefully
    try:
        proc.wait(timeout)
    except psutil.TimeoutExpired:
        from .core.process.managed import kill_tree

        kill_tree(proc.pid)
    finally:
        (cfg.run_dir / STOP_REQUEST).unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.launcher")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "stop", "status"))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None, help="backend/UI port (default DS_PORT or 8790)")
    parser.add_argument("--harness-port", type=int, default=0, help="fixed Harness port (default: free port)")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--node", default=None, help="node executable for the Harness (dev)")
    parser.add_argument("--open", action="store_true", help="open the browser when ready")
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    import logging

    logging.basicConfig(level=logging.INFO, format="[launcher %(asctime)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    paths = resolve_runtime_paths()
    cfg = read_config(paths, port=args.port, host=args.host, log_dir=args.log_dir)
    if args.command == "status":
        state = read_state(cfg, wait=2.0)
        if state is None and instance_running(cfg):
            state = {"running": True, "detail": "launcher holds the instance lock (starting up?)"}
        print(json.dumps(state or {"running": False}, indent=2))
        return 0 if state else 1
    if args.command == "stop":
        stopped = stop_running(cfg)
        log("stopped" if stopped else "not running")
        return 0

    lock = InstanceLock(cfg.run_dir / "launcher.lock")
    if not lock.acquire():
        state = read_state(cfg)
        url = state.get("url") if state else cfg.url
        log(f"Director Studio is already running: {url}")
        if args.open:
            _open_browser(str(url))
        return 0
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    stale = cfg.run_dir / "launcher.json"
    if stale.exists():
        log("cleaning up after a previous launcher that did not exit cleanly")
        stale.unlink(missing_ok=True)
    log(f"install root {paths.install_root} | config {paths.env_file} | data {cfg.data_dir}")
    return asyncio.run(Launcher(args, paths).run())


if __name__ == "__main__":
    sys.exit(main())
