"""Restart handshake between the backend and the DS launcher.

The launcher (``python -m app.launcher``) starts the backend through
``python -m app.serve`` with ``DS_LAUNCHER_PID`` set.  ``POST /api/system/restart``
asks the running uvicorn server to shut down gracefully (lifespan stops the
managed ComfyUI/Ollama) and the server process exits with ``RESTART_EXIT_CODE``;
the launcher treats that code as a planned restart, re-reads ``.env`` and starts
Harness + backend again.  Cross-platform: no signals are involved.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

RESTART_EXIT_CODE = 75  # EX_TEMPFAIL
BOOT_ID = uuid.uuid4().hex[:12]
BOOT_TIME = time.time()

_server: Any = None  # uvicorn.Server registered by app.serve
_restart_requested = False


def register_server(server: Any) -> None:
    global _server
    _server = server


def supervisor_available() -> bool:
    """True when a DS launcher supervises this process and can restart it."""
    pid = os.environ.get("DS_LAUNCHER_PID", "").strip()
    if not pid.isdigit() or _server is None:
        return False
    try:
        import psutil

        return psutil.pid_exists(int(pid)) and os.getppid() == int(pid)
    except Exception:  # pragma: no cover - psutil missing
        return False


def watch_stop_file(path: "os.PathLike[str] | str", *, interval: float = 0.5) -> Any:
    """Start a daemon thread that stops the registered server when ``path`` appears.

    Used by the launcher's graceful stop (``DS_STOP_FILE``): uvicorn then runs the
    lifespan shutdown, which stops the managed ComfyUI/Ollama, and exits 0.  A thread
    (not a task) so it works before/independently of uvicorn's event loop.
    """
    import threading
    from pathlib import Path

    target = Path(path)
    target.unlink(missing_ok=True)

    def run() -> None:
        while True:
            time.sleep(interval)
            try:
                present = target.exists()
            except OSError:
                present = False
            if present and _server is not None:
                target.unlink(missing_ok=True)
                _server.should_exit = True
                return

    thread = threading.Thread(target=run, name="ds-stop-file", daemon=True)
    thread.start()
    return thread


def restart_requested() -> bool:
    return _restart_requested


def request_restart(delay: float = 0.4) -> bool:
    """Schedule a graceful shutdown with the restart exit code."""
    global _restart_requested
    if not supervisor_available():
        return False
    _restart_requested = True

    async def _later() -> None:
        await asyncio.sleep(delay)  # let the HTTP response flush first
        if _server is not None:
            _server.should_exit = True

    asyncio.get_running_loop().create_task(_later())
    return True


def info() -> dict[str, Any]:
    return {
        "boot_id": BOOT_ID,
        "boot_time": BOOT_TIME,
        "supervisor_available": supervisor_available(),
        "launcher_pid": int(os.environ["DS_LAUNCHER_PID"]) if os.environ.get("DS_LAUNCHER_PID", "").isdigit() else None,
    }
