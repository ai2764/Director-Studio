from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from typing import BinaryIO, Callable, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values


_CHILD_ENVIRONMENT_KEYS = (
    "SystemRoot",
    "WINDIR",
    "SystemDrive",
    "ComSpec",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)
_REQUIRED_CAPABILITIES = {"native-sessions-v1", "context-envelope-v2"}


class HarnessStartupError(RuntimeError):
    def __init__(self, stage: str, log_path: Path, detail: str) -> None:
        self.stage = stage
        self.log_path = log_path
        self.detail = detail
        super().__init__(
            f"Harness {stage} failed: {detail}. Log: {log_path}"
        )


@dataclass(frozen=True)
class PortableHarnessSettings:
    runtime: Literal["legacy", "harness"]
    managed: bool


@dataclass(frozen=True)
class BundledHarnessPaths:
    node: Path
    entry: Path
    root: Path
    session_root: Path
    log_path: Path


@dataclass
class ManagedHarness:
    process: subprocess.Popen[bytes]
    base_url: str
    token: str
    log_path: Path
    _log_file: BinaryIO = field(repr=False)

    def stop(self, timeout: float = 5.0) -> None:
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=timeout)
        finally:
            self._log_file.close()


def _error_log_path(env_file: Path) -> Path:
    return env_file.parent / "data" / "logs" / "harness-sidecar.log"


def _boolean(value: str, *, env_file: Path) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HarnessStartupError(
        "configuration",
        _error_log_path(env_file),
        "DS_HARNESS_MANAGED must be true or false",
    )


def read_portable_harness_settings(
    env_file: Path,
    environ: Mapping[str, str],
) -> PortableHarnessSettings:
    configured = {
        key: value
        for key, value in dotenv_values(env_file).items()
        if value is not None
    } if env_file.is_file() else {}

    runtime = str(
        environ.get(
            "DS_DIRECTOR_AGENT_RUNTIME",
            configured.get("DS_DIRECTOR_AGENT_RUNTIME", "harness"),
        )
    ).strip().lower()
    if runtime not in {"legacy", "harness"}:
        raise HarnessStartupError(
            "configuration",
            _error_log_path(env_file),
            "DS_DIRECTOR_AGENT_RUNTIME must be legacy or harness",
        )

    raw_managed = str(
        environ.get(
            "DS_HARNESS_MANAGED",
            configured.get("DS_HARNESS_MANAGED", "true"),
        )
    )
    return PortableHarnessSettings(
        runtime=runtime,
        managed=_boolean(raw_managed, env_file=env_file),
    )


def bundled_harness_paths(
    install_root: Path,
    data_root: Path,
) -> BundledHarnessPaths:
    root = install_root / "harness"
    return BundledHarnessPaths(
        node=install_root / "runtime" / "node" / "node.exe",
        entry=root / "dist" / "server.js",
        root=root,
        session_root=data_root / "harness-sessions",
        log_path=data_root / "logs" / "harness-sidecar.log",
    )


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def probe_harness_health(base_url: str, token: str) -> Mapping[str, object] | None:
    request = Request(
        f"{base_url}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=0.5) as response:
            value = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _validate_health(health: Mapping[str, object]) -> str | None:
    if health.get("ok") is not True:
        return "health did not report ok"
    if health.get("service") != "director-studio-harness":
        return "health service identity does not match Director Studio Harness"
    if health.get("protocol") != 1:
        return "health protocol is not supported"
    capabilities = health.get("capabilities")
    if not isinstance(capabilities, list) or not _REQUIRED_CAPABILITIES.issubset(
        {item for item in capabilities if isinstance(item, str)}
    ):
        return "health capabilities are incomplete"
    return None


def _child_environment(
    environ: Mapping[str, str],
    *,
    token: str,
    port: int,
    session_root: Path,
) -> dict[str, str]:
    child = {
        key: str(environ[key])
        for key in _CHILD_ENVIRONMENT_KEYS
        if environ.get(key) is not None
    }
    child.update(
        DS_HARNESS_INTERNAL_TOKEN=token,
        DS_HARNESS_PORT=str(port),
        DS_HARNESS_PARENT_PID=str(os.getpid()),
        DS_HARNESS_SESSION_ROOT=str(session_root),
    )
    return child


def start_managed_harness(
    install_root: Path,
    data_root: Path,
    startup_timeout: float = 20.0,
    *,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    health_probe: Callable[
        [str, str], Mapping[str, object] | None
    ] = probe_harness_health,
    environ: Mapping[str, str] | None = None,
    port_factory: Callable[[], int] = reserve_loopback_port,
    token_factory: Callable[[], str] = lambda: secrets.token_hex(32),
) -> ManagedHarness:
    paths = bundled_harness_paths(install_root, data_root)
    if not paths.node.is_file():
        raise HarnessStartupError(
            "runtime", paths.log_path, f"bundled Node is missing: {paths.node}"
        )
    if not paths.entry.is_file():
        raise HarnessStartupError(
            "runtime", paths.log_path, f"Harness entry module is missing: {paths.entry}"
        )

    try:
        paths.session_root.mkdir(parents=True, exist_ok=True)
        paths.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = paths.log_path.open("wb")
    except OSError as exc:
        raise HarnessStartupError(
            "storage", paths.log_path, f"cannot prepare Harness data directories: {exc}"
        ) from exc

    token = token_factory()
    port = port_factory()
    base_url = f"http://127.0.0.1:{port}"
    child_environment = _child_environment(
        os.environ if environ is None else environ,
        token=token,
        port=port,
        session_root=paths.session_root,
    )
    process: subprocess.Popen[bytes] | None = None
    try:
        process = popen(
            [str(paths.node), str(paths.entry)],
            cwd=paths.root,
            env=child_environment,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + max(0.0, startup_timeout)
        while True:
            returncode = process.poll()
            if returncode is not None:
                raise HarnessStartupError(
                    "startup",
                    paths.log_path,
                    f"sidecar exited before readiness with exit code {returncode}",
                )
            health = health_probe(base_url, token)
            if health is not None:
                mismatch = _validate_health(health)
                if mismatch is not None:
                    raise HarnessStartupError(
                        "capabilities", paths.log_path, mismatch
                    )
                return ManagedHarness(
                    process=process,
                    base_url=base_url,
                    token=token,
                    log_path=paths.log_path,
                    _log_file=log_file,
                )
            if time.monotonic() >= deadline:
                raise HarnessStartupError(
                    "readiness",
                    paths.log_path,
                    f"sidecar was not ready within {startup_timeout:g} seconds",
                )
            time.sleep(0.1)
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        log_file.close()
        raise
