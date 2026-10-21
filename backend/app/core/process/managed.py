"""Generic background process supervisor (generalised from portable_harness.py).

Features: loopback port helper, log capture with simple rotation, health wait,
crash restart with exponential backoff, process-tree stop via psutil, pid-file
based orphan cleanup, and best-effort kill on interpreter exit / parent death.
"""

from __future__ import annotations

import asyncio
import atexit
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Awaitable, Callable, Literal, Mapping, Sequence
import weakref

import psutil

logger = logging.getLogger("director_studio.process")

State = Literal["stopped", "starting", "ready", "crashed", "stopping", "failed"]
_LIVE: "weakref.WeakSet[ManagedProcess]" = weakref.WeakSet()


class LoopLocalLock:
    """``asyncio.Lock`` that is re-created per event loop.

    Runtime singletons (managed ComfyUI / bundled Ollama) live for the whole
    process, but tests and ``TestClient`` run several event loops in sequence.  A
    plain ``asyncio.Lock`` binds to the first loop that ever contends on it and then
    raises "is bound to a different event loop" in every later loop.
    """

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _current(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    def locked(self) -> bool:
        return self._lock is not None and self._lock.locked()

    async def __aenter__(self) -> None:
        await self._current().acquire()

    async def __aexit__(self, *exc: object) -> None:
        assert self._lock is not None
        self._lock.release()


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            # Match uvicorn: a just-stopped server leaves TIME_WAIT sockets on POSIX that must
            # not count as "in use" (on Windows SO_REUSEADDR would allow stealing a live port).
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass
class LaunchSpec:
    argv: Sequence[str]
    cwd: Path
    env: Mapping[str, str]
    health: Callable[[], Awaitable[bool]]
    startup_timeout: float = 120.0
    label: str = ""  # human readable summary for status


def _linux_pdeathsig() -> None:  # pragma: no cover - runs in the child
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


def request_stop_via_file(proc: subprocess.Popen, stop_file: Path, timeout: float) -> bool:
    """Ask ``proc`` to exit by creating ``stop_file``; True if it exited within ``timeout``."""
    try:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        return False
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def kill_tree(pid: int, timeout: float = 8.0, *, root_first: bool = False) -> None:
    """Terminate ``pid`` and all descendants.

    ``root_first`` gives the root a chance to shut its own children down
    gracefully (e.g. the backend stopping its managed ComfyUI/Ollama) before
    any survivors are terminated.
    """
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        procs = root.children(recursive=True) + [root]
    except psutil.NoSuchProcess:
        procs = [root]
    if root_first:
        try:
            if os.name == "nt":  # pragma: no cover - windows: no SIGTERM semantics
                try:
                    root.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    # CTRL_BREAK needs a console shared with the child; children started
                    # with CREATE_NO_WINDOW (pythonw launcher) have none -> WinError 12/87.
                    root.terminate()
            else:
                root.terminate()
            root.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            pass
        except psutil.NoSuchProcess:
            pass
        procs = [p for p in procs if p.is_running()]
        timeout = min(timeout, 3.0)
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=2.0)


@dataclass(eq=False)
class ManagedProcess:
    """Supervise one long-running child process.

    ``build`` is called on every (re)start so ports/args can be refreshed.
    """

    name: str
    build: Callable[[], LaunchSpec]
    log_path: Path
    pid_file: Path
    restart_on_crash: bool = True
    max_restarts: int = 5
    restart_window_sec: float = 600.0
    backoff_initial: float = 2.0
    backoff_max: float = 60.0
    log_max_bytes: int = 10 * 1024 * 1024
    # Called with the exit code after an unplanned exit. Returning True marks the
    # exit as a *planned* restart (e.g. "restart to apply settings"): restart
    # immediately without counting it as a crash.
    exit_handler: Callable[[int | None], Awaitable[bool]] | None = None
    graceful_stop: bool = False  # SIGTERM the root first, then the remaining tree
    # Cross-platform graceful stop: the child polls this file (e.g. ``DS_STOP_FILE`` for
    # ``app.serve``) and shuts down cleanly; ``stop()`` creates it and waits before falling
    # back to kill_tree.  Needed on Windows, which has no SIGTERM for console-less children.
    stop_file: Path | None = None

    state: State = "stopped"
    last_error: str | None = None
    restarts: int = 0
    started_at: float | None = None
    spec_label: str = ""
    _process: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    _monitor: asyncio.Task[None] | None = field(default=None, repr=False)
    _stopping: bool = field(default=False, repr=False)
    _crash_times: list[float] = field(default_factory=list, repr=False)
    _ready: asyncio.Event | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _LIVE.add(self)

    # ---- status ------------------------------------------------------------
    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process and self._process.poll() is None else None

    def status(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "pid": self.pid,
            "restarts": self.restarts,
            "last_error": self.last_error,
            "uptime_sec": round(time.time() - self.started_at, 1) if self.started_at and self.pid else None,
            "log_path": str(self.log_path),
            "command": self.spec_label,
        }

    def tail_log(self, max_bytes: int = 16000) -> str:
        try:
            with open(self.log_path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                return handle.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    # ---- orphan handling ---------------------------------------------------
    def cleanup_orphan(self) -> bool:
        try:
            record = json.loads(self.pid_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return False
        pid = int(record.get("pid") or 0)
        killed = False
        if pid:
            try:
                proc = psutil.Process(pid)
                if abs(proc.create_time() - float(record.get("create_time") or 0)) < 1.0:
                    logger.warning("%s: killing orphaned process tree pid=%s", self.name, pid)
                    kill_tree(pid)
                    killed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.pid_file.unlink(missing_ok=True)
        return killed

    def _write_pid(self) -> None:
        if not self._process:
            return
        try:
            create_time = psutil.Process(self._process.pid).create_time()
        except psutil.NoSuchProcess:
            create_time = 0
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(json.dumps({
            "pid": self._process.pid,
            "create_time": create_time,
            "parent": os.getpid(),
            "name": self.name,
        }), encoding="utf-8")

    # ---- lifecycle -----------------------------------------------------------
    def _rotate_log(self) -> None:
        try:
            if self.log_path.stat().st_size > self.log_max_bytes:
                os.replace(self.log_path, self.log_path.with_suffix(self.log_path.suffix + ".1"))
        except FileNotFoundError:
            pass

    def _spawn(self, spec: LaunchSpec) -> None:
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)  # stale request from a previous run
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_log()
        log = open(self.log_path, "ab")
        log.write(f"\n===== [{time.strftime('%Y-%m-%d %H:%M:%S')}] starting {self.name}: {spec.label or ' '.join(spec.argv)}\n".encode())
        log.flush()
        kwargs: dict[str, object] = {}
        if os.name == "nt":  # pragma: no cover - windows
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
            if sys.platform.startswith("linux"):
                kwargs["preexec_fn"] = _linux_pdeathsig
        try:
            self._process = subprocess.Popen(
                list(spec.argv), cwd=str(spec.cwd), env=dict(spec.env),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs,
            )
        finally:
            log.close()
        self.started_at = time.time()
        self.spec_label = spec.label or " ".join(spec.argv)
        self._write_pid()

    async def _wait_ready(self, spec: LaunchSpec) -> bool:
        deadline = time.monotonic() + spec.startup_timeout
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                code = None if self._process is None else self._process.returncode
                self.last_error = f"exited during startup (code {code}); see {self.log_path}"
                return False
            try:
                if await spec.health():
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        self.last_error = f"not ready within {spec.startup_timeout:g}s; see {self.log_path}"
        return False

    async def start(self, *, wait: bool = False) -> None:
        """Start (non-blocking by default) and supervise the process."""
        if self._monitor and not self._monitor.done():
            return
        self._stopping = False
        self._ready = asyncio.Event()
        self.cleanup_orphan()
        self._monitor = asyncio.create_task(self._run(), name=f"supervisor:{self.name}")
        if wait:
            await self.wait_ready()

    async def wait_ready(self, timeout: float | None = None) -> bool:
        if self._ready is None:
            return False
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return self.state == "ready"

    async def _run(self) -> None:
        backoff = self.backoff_initial
        while not self._stopping:
            try:
                spec = self.build()
            except Exception as exc:
                self.state = "failed"
                self.last_error = f"configuration error: {exc}"
                logger.error("%s: %s", self.name, self.last_error)
                if self._ready:
                    self._ready.set()
                return
            self.state = "starting"
            try:
                self._spawn(spec)
            except OSError as exc:
                self.state = "failed"
                self.last_error = f"spawn failed: {exc}"
                if self._ready:
                    self._ready.set()
                return
            ok = await self._wait_ready(spec)
            if ok and not self._stopping:
                self.state = "ready"
                self.last_error = None
                backoff = self.backoff_initial
                logger.info("%s ready (pid=%s)", self.name, self.pid)
            if self._ready:
                self._ready.set()
            if not ok and self._process is not None and self._process.poll() is None:
                await asyncio.to_thread(kill_tree, self._process.pid)
            # Wait for exit.
            while self._process is not None and self._process.poll() is None and not self._stopping:
                await asyncio.sleep(0.5)
            if self._stopping:
                break
            code = self._process.returncode if self._process else None
            if self.exit_handler is not None:
                try:
                    planned = await self.exit_handler(code)
                except Exception:  # pragma: no cover - defensive
                    logger.exception("%s: exit handler failed", self.name)
                    planned = False
                if planned and not self._stopping:
                    logger.info("%s: planned restart (code=%s)", self.name, code)
                    self.pid_file.unlink(missing_ok=True)
                    self.restarts += 1
                    self.last_error = None
                    self._ready = asyncio.Event()
                    backoff = self.backoff_initial
                    continue
                if self._stopping:
                    break
            self.last_error = self.last_error or f"exited with code {code}"
            logger.warning("%s exited (code=%s): %s", self.name, code, self.last_error)
            self.pid_file.unlink(missing_ok=True)
            now = time.monotonic()
            self._crash_times = [t for t in self._crash_times if now - t < self.restart_window_sec] + [now]
            if not self.restart_on_crash or len(self._crash_times) > self.max_restarts:
                self.state = "crashed"
                return
            self.state = "crashed"
            self.restarts += 1
            logger.info("%s: restarting in %.0fs", self.name, backoff)
            await asyncio.sleep(backoff)
            backoff = min(self.backoff_max, backoff * 2)
            self.last_error = None
            self._ready = asyncio.Event()

    async def stop(self, timeout: float = 10.0) -> None:
        self._stopping = True
        self.state = "stopping"
        proc = self._process
        if proc is not None and proc.poll() is None and self.stop_file is not None:
            await asyncio.to_thread(request_stop_via_file, proc, self.stop_file, timeout)
        if proc is not None and proc.poll() is None:
            await asyncio.to_thread(kill_tree, proc.pid, timeout, root_first=self.graceful_stop)
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)
        if self._monitor and not self._monitor.done():
            self._monitor.cancel()
            try:
                await self._monitor
            except (asyncio.CancelledError, Exception):
                pass
        self._monitor = None
        self.pid_file.unlink(missing_ok=True)
        self.state = "stopped"
        if self._ready:
            self._ready.set()

    async def restart(self) -> None:
        await self.stop()
        self.restarts = 0
        self._crash_times.clear()
        await self.start()

    def _monitor_alive(self) -> bool:
        return self._monitor is not None and not self._monitor.done()

    def kill_now(self) -> None:
        """Synchronous emergency stop (atexit)."""
        self._stopping = True
        proc = self._process
        if proc is not None and proc.poll() is None:
            kill_tree(proc.pid, timeout=5.0)
        self.pid_file.unlink(missing_ok=True)


@atexit.register
def _kill_all_on_exit() -> None:  # pragma: no cover - interpreter shutdown
    for managed in list(_LIVE):
        try:
            managed.kill_now()
        except Exception:
            pass
