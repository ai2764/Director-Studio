"""ComfyUI runtime manager: managed (bundled) / external-local / remote.

The rest of DS keeps reading ``settings.comfy_base_url`` at call time, so this
module points that attribute at the *effective* URL (managed instance or the
loopback proxy) while remembering the user-configured value.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ...config import settings
from ...runtime_paths import runtime_paths
from ..net_headers import auth_headers
from ..process.managed import LaunchSpec, LoopLocalLock, ManagedProcess, port_is_free, reserve_loopback_port
from . import launch_options as lo
from .remote_proxy import RemoteComfyProxy

logger = logging.getLogger("director_studio.comfy_runtime")

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def is_loopback_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK


def runtime_dir() -> Path:
    return Path(settings.data_dir) / "comfy_runtime"


def models_dir(cfg: Any = settings) -> Path:
    configured = str(getattr(cfg, "comfy_models_dir", "") or "").strip()
    return Path(configured).expanduser() if configured else Path(cfg.data_dir) / "models"


def user_custom_nodes_dir(cfg: Any = settings) -> Path:
    """User-installed ComfyUI node packs (outside the program folder; see write_extra_model_paths)."""
    return Path(cfg.data_dir) / "comfy" / "custom_nodes"


def comfy_dir(cfg: Any = settings) -> Path | None:
    configured = str(getattr(cfg, "comfy_dir", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    for candidate in (runtime_paths.install_root / "runtime" / "ComfyUI",
                      runtime_paths.install_root / "ComfyUI_windows_portable" / "ComfyUI"):
        if (candidate / "main.py").is_file():
            return candidate
    return None


def user_directory_args(allowed: set[str], cfg: Any = settings) -> list[str]:
    """Installed packages keep ComfyUI's writable folders in the DS data dir (survive upgrades)."""
    if not runtime_paths.installed:
        return []
    base = Path(cfg.data_dir) / "comfy"
    args: list[str] = []
    for flag, name in (("--user-directory", "user"), ("--input-directory", "input"),
                       ("--output-directory", "output"), ("--temp-directory", "temp")):
        if flag in allowed:
            (base / name).mkdir(parents=True, exist_ok=True)
            args += [flag, str(base / name)]
    return args


async def _http_ok(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get(url)
            return response.status_code == 200
    except httpx.HTTPError:
        return False


def preview_launch(cfg: Any = settings, *, port: int = 0) -> dict[str, Any]:
    """Build (but do not run) the managed ComfyUI command for the UI preview."""
    directory = comfy_dir(cfg)
    result: dict[str, Any] = {"comfy_dir": str(directory) if directory else None, "ok": False}
    if directory is None or not (directory / "main.py").is_file():
        result["error"] = "ComfyUI folder not found (DS_COMFY_DIR)"
        return result
    python = lo.find_comfy_python(directory, getattr(cfg, "comfy_python", ""))
    if python is None:
        result["error"] = "ComfyUI Python not found (DS_COMFY_PYTHON)"
        return result
    result["python"] = str(python)
    try:
        probe = lo.probe_runtime(python, directory, runtime_dir())
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"cannot run ComfyUI --help: {exc}"
        return result
    allowed = set(probe["options"])
    yaml_path = runtime_dir() / "extra_model_paths.yaml"
    built = lo.build_launch(cfg, port=port or 8188, extra_model_paths=yaml_path, allowed=allowed,
                            sage_available=bool(probe.get("sage")))
    result.update(ok=True, args=["main.py", *built.args], env=sorted(built.env), warnings=built.warnings,
                  sage_available=bool(probe.get("sage")), supported_options=len(allowed),
                  models_dir=str(models_dir(cfg)))
    return result


class ComfyRuntime:
    def __init__(self) -> None:
        self.mode: str = "external-local"
        self.configured_url: str = settings.comfy_base_url
        self.effective_url: str = settings.comfy_base_url
        self.managed: ManagedProcess | None = None
        self.proxy: RemoteComfyProxy | None = None
        self.warnings: list[str] = []
        self.error: str | None = None
        self.pending_reconfigure = False
        self._lock = LoopLocalLock()
        self._port: int = 0
        self._deferred_task: asyncio.Task[None] | None = None

    # ---- effective url plumbing ------------------------------------------------
    async def _set_effective(self, url: str) -> None:
        changed = url != settings.comfy_base_url
        self.effective_url = url
        settings.comfy_base_url = url
        if changed:
            await _recycle_clients()

    # ---- managed -----------------------------------------------------------------
    def _build_managed(self) -> LaunchSpec:
        directory = comfy_dir()
        if directory is None or not (directory / "main.py").is_file():
            raise lo.LaunchOptionError("ComfyUI folder not found; set DS_COMFY_DIR")
        python = lo.find_comfy_python(directory, settings.comfy_python)
        if python is None:
            raise lo.LaunchOptionError("ComfyUI Python not found; set DS_COMFY_PYTHON")
        probe = lo.probe_runtime(python, directory, runtime_dir())
        if not self._port or (not settings.comfy_port and not port_is_free(self._port)):
            self._port = settings.comfy_port or reserve_loopback_port()
        yaml_path = lo.write_extra_model_paths(models_dir(), runtime_dir() / "extra_model_paths.yaml",
                                               user_custom_nodes_dir())
        built = lo.build_launch(settings, port=self._port, extra_model_paths=yaml_path,
                                allowed=set(probe["options"]), sage_available=bool(probe.get("sage")))
        self.warnings = built.warnings
        url = f"http://127.0.0.1:{self._port}"
        self.effective_url = url
        settings.comfy_base_url = url
        argv = [str(python), "-u", "main.py", *built.args, *user_directory_args(set(probe["options"]))]
        return LaunchSpec(
            argv=argv, cwd=directory, env=lo.child_environment(built.env),
            health=lambda: _http_ok(f"{url}/system_stats"),
            startup_timeout=float(settings.comfy_startup_timeout_sec), label=" ".join(argv),
        )

    # ---- public API ---------------------------------------------------------------
    async def start(self) -> None:
        async with self._lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        self.mode = settings.comfy_mode
        self.error = None
        self.warnings = []
        if self.mode == "managed":
            directory = comfy_dir()
            python = lo.find_comfy_python(directory, settings.comfy_python) if directory else None
            if directory is not None and python is not None and (directory / "main.py").is_file():
                try:  # warm the --help cache off the event loop
                    await asyncio.to_thread(lo.probe_runtime, python, directory, runtime_dir())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ComfyUI probe failed: %s", exc)
            log_dir = Path(settings.data_dir) / "logs"
            self.managed = ManagedProcess(
                name="comfyui", build=self._build_managed, log_path=log_dir / "comfyui.log",
                pid_file=runtime_dir() / "comfyui.pid",
            )
            await self.managed.start()
            # _build_managed runs inside the supervisor task; give it a tick to choose a port.
            for _ in range(200):
                if self.managed.state != "stopped" or self.managed.last_error:
                    break
                await asyncio.sleep(0.01)
            await _recycle_clients()
        elif self.mode == "remote":
            try:
                headers = auth_headers(settings.comfy_remote_auth_type, settings.comfy_remote_auth)
                self.proxy = RemoteComfyProxy(
                    remote_url=settings.comfy_remote_url, headers=headers,
                    verify_tls=settings.comfy_remote_verify_tls, port=settings.comfy_proxy_port,
                )
                await self.proxy.start()
                await self._set_effective(self.proxy.url)
            except Exception as exc:  # noqa: BLE001
                self.error = f"remote proxy failed: {exc}"
                logger.error(self.error)
                self.proxy = None
        else:
            await self._set_effective(self.configured_url)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self.managed is not None:
            await self.managed.stop()
            self.managed = None
        if self.proxy is not None:
            await self.proxy.stop()
            self.proxy = None
        self._port = 0

    async def reconfigure(self, configured_url: str | None = None, *, force: bool = False) -> str:
        """Re-apply comfy settings. Returns applied | deferred."""
        if configured_url is not None:
            self.configured_url = configured_url
        if not force and _comfy_busy():
            self.pending_reconfigure = True
            self._schedule_deferred()
            return "deferred"
        async with self._lock:
            self.pending_reconfigure = False
            await self._stop_locked()
            await self._start_locked()
        return "applied"

    async def restart_managed(self) -> str:
        return await self.reconfigure(force=False)

    def _schedule_deferred(self) -> None:
        if self._deferred_task and not self._deferred_task.done():
            return

        async def waiter() -> None:
            while self.pending_reconfigure:
                await asyncio.sleep(2.0)
                if not _comfy_busy():
                    await self.reconfigure(force=True)

        self._deferred_task = asyncio.create_task(waiter())

    def status(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mode": self.mode,
            "effective_url": self.effective_url,
            "configured_url": self.configured_url,
            "pending_reconfigure": self.pending_reconfigure,
            "warnings": self.warnings,
            "error": self.error,
        }
        if self.managed is not None:
            data["managed"] = self.managed.status()
            data["models_dir"] = str(models_dir())
        if self.proxy is not None:
            data["proxy"] = self.proxy.status()
        return data


def _comfy_busy() -> bool:
    try:
        from ..vram.orchestrator import _orchestrator  # noqa: PLC0415

        orch = _orchestrator
    except Exception:  # noqa: BLE001
        return False
    if orch is None:
        return False
    return orch.owner == "comfy" or bool(getattr(orch, "_generation_reservations", {}))


async def _recycle_clients() -> None:
    """Make every cached Comfy client pick up the new effective URL."""
    try:
        from ..jobs import runner  # noqa: PLC0415

        await runner._comfy_mcp_client.aclose()
    except Exception:  # noqa: BLE001
        logger.debug("comfy-mcp recycle failed", exc_info=True)
    try:
        from ..vram import orchestrator as orch_mod  # noqa: PLC0415
        from .client import ComfyClient  # noqa: PLC0415

        orch = orch_mod._orchestrator
        if orch is not None and isinstance(orch.comfy, ComfyClient):
            orch.comfy = None
    except Exception:  # noqa: BLE001
        logger.debug("orchestrator comfy reset failed", exc_info=True)


comfy_runtime = ComfyRuntime()
