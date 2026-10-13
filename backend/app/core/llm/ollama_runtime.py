"""Bundled ("内置") Ollama runtime owned by the backend.

``DS_OLLAMA_MODE=bundled`` starts DS's private Ollama binary (``runtime/ollama``,
MIT licensed, run as a separate process) on a free loopback port - never 11434,
so a user's own Ollama keeps working side by side - with ``OLLAMA_MODELS`` in the
DS data directory.  Like the ComfyUI runtime, the rest of DS keeps reading
``settings.ollama_base_url``; this module points it at the effective URL while
remembering the configured one.  Models are pulled through Ollama's own
``/api/pull`` (see ``app.api.ollama``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from ...config import settings
from ...runtime_paths import runtime_paths
from ..comfy.launch_options import child_environment
from ..process.managed import LaunchSpec, LoopLocalLock, ManagedProcess, port_is_free, reserve_loopback_port

logger = logging.getLogger("director_studio.ollama_runtime")

SYSTEM_DEFAULT_PORT = 11434


def ollama_dir(cfg: Any = settings) -> Path:
    configured = str(getattr(cfg, "ollama_dir", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return runtime_paths.install_root / "runtime" / "ollama"


def ollama_binary(cfg: Any = settings) -> Path | None:
    root = ollama_dir(cfg)
    names = ("ollama.exe",) if os.name == "nt" else ("bin/ollama", "ollama")
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def ollama_models_dir(cfg: Any = settings) -> Path:
    configured = str(getattr(cfg, "ollama_models_dir", "") or "").strip()
    return Path(configured).expanduser() if configured else Path(cfg.data_dir) / "ollama" / "models"


def wants_bundled(cfg: Any = settings) -> bool:
    return getattr(cfg, "ollama_mode", "system") == "bundled" and getattr(cfg, "llm_provider", "") == "ollama"


async def _version_ok(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get(f"{url}/api/version")
            return response.status_code == 200
    except httpx.HTTPError:
        return False


class OllamaRuntime:
    def __init__(self) -> None:
        self.configured_url: str = settings.ollama_base_url
        self.effective_url: str = settings.ollama_base_url
        self.managed: ManagedProcess | None = None
        self.error: str | None = None
        self.version: str | None = None
        self._port = 0
        self._lock = LoopLocalLock()
        self._signature: tuple[str, ...] = ()

    @property
    def bundled(self) -> bool:
        return self.managed is not None

    def _set_effective(self, url: str) -> bool:
        changed = url != settings.ollama_base_url
        self.effective_url = url
        settings.ollama_base_url = url
        return changed

    def _build(self) -> LaunchSpec:
        binary = ollama_binary()
        if binary is None:
            raise FileNotFoundError(f"bundled Ollama not found under {ollama_dir()}")
        if not self._port or (not settings.ollama_port and not port_is_free(self._port)):
            port = settings.ollama_port or reserve_loopback_port()
            while not settings.ollama_port and port == SYSTEM_DEFAULT_PORT:
                port = reserve_loopback_port()
            self._port = port
        models = ollama_models_dir()
        models.mkdir(parents=True, exist_ok=True)
        env = child_environment({})
        for key in [k for k in env if k.startswith("OLLAMA_")]:
            env.pop(key)  # a user's OLLAMA_HOST etc. must not redirect the private instance
        env.update(
            OLLAMA_HOST=f"127.0.0.1:{self._port}",
            OLLAMA_MODELS=str(models),
            OLLAMA_ORIGINS="http://127.0.0.1:*,http://localhost:*",
            # The director plans with long prompts; load models with DS's context size
            # (a user's own Ollama is configured by the user instead).
            OLLAMA_CONTEXT_LENGTH=str(int(settings.director_num_ctx)),
        )
        url = f"http://127.0.0.1:{self._port}"
        self._set_effective(url)
        argv = [str(binary), "serve"]
        return LaunchSpec(argv=argv, cwd=binary.parent, env=env, health=lambda: _version_ok(url),
                          startup_timeout=60.0, label=f"{binary} serve (127.0.0.1:{self._port}, models {models})")

    async def start(self) -> None:
        async with self._lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        self.error = None
        if not wants_bundled():
            if self._set_effective(self.configured_url):
                _rebuild_llm_clients()
            return
        if ollama_binary() is None:
            self.error = f"bundled Ollama not found under {ollama_dir()}"
            logger.error(self.error)
            return
        self._signature = _signature()
        self.managed = ManagedProcess(
            name="ollama", build=self._build,
            log_path=Path(settings.data_dir) / "logs" / "ollama.log",
            pid_file=Path(settings.data_dir) / "run" / "ollama.pid",
        )
        await self.managed.start()
        ready = await self.managed.wait_ready(60)
        if not ready:
            self.error = self.managed.last_error
        else:
            await self._read_version()
        _rebuild_llm_clients()

    async def _read_version(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                self.version = (await client.get(f"{self.effective_url}/api/version")).json().get("version")
        except (httpx.HTTPError, ValueError):
            self.version = None

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self.managed is not None:
            await self.managed.stop()
            self.managed = None
        self._port = 0

    async def reconfigure(self, configured_url: str | None = None) -> str:
        """Re-apply after LLM settings changed (mode/provider/dir/port/models dir)."""
        if configured_url is not None:
            self.configured_url = configured_url
        async with self._lock:
            want = wants_bundled()
            if (want and self.managed is not None and self.managed.state in {"ready", "starting"}
                    and self._signature == _signature()):
                return "unchanged"
            await self._stop_locked()
            await self._start_locked()
        return "bundled" if want else "system"

    def status(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mode": settings.ollama_mode,
            "bundled_active": self.managed is not None,
            "effective_url": self.effective_url,
            "configured_url": self.configured_url,
            "binary": str(ollama_binary() or ""),
            "available": ollama_binary() is not None,
            "models_dir": str(ollama_models_dir()),
            "version": self.version,
            "error": self.error,
        }
        if self.managed is not None:
            data["managed"] = self.managed.status()
        return data


def _signature() -> tuple[str, ...]:
    return (str(ollama_binary()), str(settings.ollama_port), str(ollama_models_dir()),
            str(settings.director_num_ctx))


def _rebuild_llm_clients() -> None:
    try:
        from ..config_store.service import rebuild_llm

        rebuild_llm()
    except Exception:  # noqa: BLE001 - best effort during startup/tests
        logger.debug("LLM client rebuild skipped", exc_info=True)


ollama_runtime = OllamaRuntime()
