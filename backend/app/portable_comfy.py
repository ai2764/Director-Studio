from __future__ import annotations

import base64
import csv
from contextlib import contextmanager
from dataclasses import dataclass
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
from typing import MutableMapping
from uuid import uuid4

from dotenv import dotenv_values


_KEYS = (
    "DS_COMFY_MCP_COMMAND",
    "DS_COMFY_MCP_ARGS",
    "DS_COMFY_MCP_COMFY_BIN",
)


class PortableComfyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortableComfyCommands:
    command: Path
    args: str
    comfy_bin: Path

    def environment(self) -> dict[str, str]:
        return {
            "DS_COMFY_MCP_COMMAND": str(self.command),
            "DS_COMFY_MCP_ARGS": self.args,
            "DS_COMFY_MCP_COMFY_BIN": str(self.comfy_bin),
        }


def _configured_keys(env_file: Path, environ: MutableMapping[str, str]) -> set[str]:
    configured = set(environ)
    if env_file.is_file():
        configured.update(
            key for key, value in dotenv_values(env_file).items() if value is not None
        )
    return configured


def _apply_defaults(
    commands: PortableComfyCommands,
    env_file: Path,
    environ: MutableMapping[str, str],
) -> dict[str, str]:
    configured = _configured_keys(env_file, environ)
    applied: dict[str, str] = {}
    for key, value in commands.environment().items():
        if key in configured:
            continue
        environ[key] = value
        applied[key] = value
    return applied


def _load_bootstrap_manifest(install_root: Path) -> tuple[dict[str, object], Path]:
    bootstrap = install_root / "runtime" / "comfy-bootstrap.json"
    lock = install_root / "runtime" / "comfy-requirements.lock"
    try:
        expected = json.loads(bootstrap.read_text(encoding="utf-8"))
        packages = expected["packages"]
        expected_hash = expected["lock_sha256"]
        actual_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PortableComfyError(f"Comfy bootstrap metadata is invalid: {exc}") from exc
    if not isinstance(packages, dict) or packages != {
        "comfy-cli": "1.20.0",
        "comfy-mcp": "0.10.0",
    }:
        raise PortableComfyError("Comfy bootstrap package versions are invalid")
    if expected_hash != actual_hash:
        raise PortableComfyError("Comfy bootstrap requirements checksum does not match")
    return expected, lock


def _normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_distributions(lock: Path) -> dict[str, str]:
    try:
        logical = re.sub(r"\\\r?\n\s*", " ", lock.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PortableComfyError(f"Comfy requirements lock is unreadable: {exc}") from exc
    expected: dict[str, str] = {}
    for line in logical.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", stripped)
        if match is None:
            raise PortableComfyError(f"Comfy requirements lock has an invalid entry: {stripped}")
        name = _normalize_distribution(match.group(1))
        if name in expected:
            raise PortableComfyError(f"Comfy requirements lock repeats {name}")
        expected[name] = match.group(2)
    if not expected:
        raise PortableComfyError("Comfy requirements lock is empty")
    return expected


def _record_is_valid(site: Path, distribution: Path) -> bool:
    record = distribution / "RECORD"
    try:
        rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
    except (OSError, UnicodeDecodeError, csv.Error):
        return False
    site_root = site.resolve()
    if not rows:
        return False
    for row in rows:
        try:
            if len(row) != 3 or not row[0]:
                return False
            record_path = PurePosixPath(row[0].replace("\\", "/"))
            parts = record_path.parts
            if ".." in parts:
                non_parent = tuple(part for part in parts if part != "..")
                if len(non_parent) != 2 or non_parent[0].lower() not in {
                    "bin",
                    "scripts",
                }:
                    return False
                target = (site / "bin" / non_parent[1]).resolve()
            else:
                target = site.joinpath(*parts).resolve()
            if not target.is_relative_to(site_root) or not target.is_file():
                return False
            if row[2] and target.stat().st_size != int(row[2]):
                return False
            if row[1]:
                algorithm, separator, encoded = row[1].partition("=")
                if separator != "=" or algorithm != "sha256":
                    return False
                actual = base64.urlsafe_b64encode(
                    hashlib.sha256(target.read_bytes()).digest()
                ).rstrip(b"=").decode("ascii")
                if actual != encoded:
                    return False
        except (OSError, ValueError):
            return False
    return True


def _site_packages_are_valid(site: Path, lock: Path) -> bool:
    try:
        expected = _locked_distributions(lock)
    except PortableComfyError:
        return False
    actual: dict[str, tuple[str, Path]] = {}
    try:
        metadata_files = sorted(site.glob("*.dist-info/METADATA"))
        for metadata_path in metadata_files:
            metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
            name = _normalize_distribution(str(metadata["Name"]))
            version = str(metadata["Version"])
            if not name or not version or name in actual:
                return False
            actual[name] = (version, metadata_path.parent)
    except (OSError, TypeError):
        return False
    if {name: version for name, (version, _path) in actual.items()} != expected:
        return False
    return all(_record_is_valid(site, path) for _version, path in actual.values())


def _imports_are_healthy(
    python: Path,
    site: Path | None,
    *,
    runner,
) -> bool:
    if site is None:
        command = [str(python), "-c", "import comfy_mcp.server, comfy_cli, pywintypes"]
    else:
        command = [
            str(python),
            "-S",
            "-c",
            "import site,sys; site.addsitedir(sys.argv[1]); "
            "import comfy_mcp.server, comfy_cli, pywintypes",
            str(site),
        ]
    child_env = dict(os.environ)
    child_env.pop("PYTHONHOME", None)
    child_env.pop("PYTHONPATH", None)
    child_env["PYTHONNOUSERSITE"] = "1"
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=child_env,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _private_runtime_is_ready(
    root: Path,
    expected: dict[str, object],
    lock: Path,
    python: Path,
    *,
    runner,
) -> bool:
    try:
        actual = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        actual == expected
        and _site_packages_are_valid(root / "site-packages", lock)
        and _imports_are_healthy(python, None, runner=runner)
    )


@contextmanager
def _bootstrap_lock(tools: Path, *, timeout_sec: float = 300.0):
    tools.mkdir(parents=True, exist_ok=True)
    lock_path = tools / ".comfy-bootstrap.lock"
    deadline = time.monotonic() + timeout_sec
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise PortableComfyError(
                        "Timed out waiting for Comfy automatic installation"
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _install_private_runtime(
    *,
    python: Path,
    lock: Path,
    expected: dict[str, object],
    data_root: Path,
    runner,
) -> Path:
    tools = data_root / "tools"
    current = tools / "comfy"
    staging = tools / f".comfy-staging-{uuid4().hex}"
    backup = tools / f".comfy-backup-{uuid4().hex}"
    log = data_root / "logs" / "comfy-bootstrap.log"
    site = staging / "site-packages"
    tools.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    site.mkdir(parents=True)
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--require-hashes",
        "--only-binary=:all:",
        "--no-deps",
        "--no-compile",
        "--target",
        str(site),
        "--requirement",
        str(lock),
    ]
    print("[setup] Installing private Comfy tools for first launch...", flush=True)
    try:
        with log.open("w", encoding="utf-8") as output:
            runner(
                command,
                check=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
            )
        if not _site_packages_are_valid(site, lock):
            raise PortableComfyError("downloaded Comfy tools failed validation")
        if not _imports_are_healthy(python, site, runner=runner):
            raise PortableComfyError("downloaded Comfy tools failed import validation")
        (staging / "manifest.json").write_text(
            json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if current.exists():
            current.replace(backup)
        try:
            staging.replace(current)
            if not _private_runtime_is_ready(
                current, expected, lock, python, runner=runner
            ):
                raise PortableComfyError("activated Comfy tools failed validation")
        except (OSError, PortableComfyError):
            if current.exists():
                shutil.rmtree(current, ignore_errors=True)
            if backup.exists() and not current.exists():
                backup.replace(current)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except (OSError, subprocess.CalledProcessError, PortableComfyError) as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise PortableComfyError(
            f"Comfy automatic installation failed; see {log}: {exc}"
        ) from exc
    print("[setup] Private Comfy tools are ready.", flush=True)
    return current


def prepare_portable_comfy_defaults(
    install_root: Path,
    data_root: Path,
    env_file: Path,
    environ: MutableMapping[str, str],
    *,
    runner=subprocess.run,
    lock_timeout_sec: float = 300.0,
) -> dict[str, str]:
    """Prepare the package-owned Comfy environment unless explicitly overridden."""
    if "DS_COMFY_MCP_COMMAND" in _configured_keys(env_file, environ):
        return {}

    runtime = install_root / "runtime" / "python"
    python = runtime / "python.exe"
    comfy = runtime / "comfy.exe"
    pip_module = runtime / "Lib" / "site-packages" / "pip"
    bootstrap = install_root / "runtime" / "comfy-bootstrap.json"
    present = [path.exists() for path in (python, comfy, pip_module, bootstrap)]
    if not any(present):
        return {}
    missing = [
        str(path)
        for path, exists in zip((python, comfy, pip_module, bootstrap), present)
        if not exists
    ]
    if missing:
        raise PortableComfyError(
            "Comfy bootstrap runtime is incomplete; missing: " + ", ".join(missing)
        )

    expected, lock = _load_bootstrap_manifest(install_root)
    private_root = data_root / "tools" / "comfy"
    with _bootstrap_lock(data_root / "tools", timeout_sec=lock_timeout_sec):
        if not _private_runtime_is_ready(
            private_root, expected, lock, python, runner=runner
        ):
            private_root = _install_private_runtime(
                python=python,
                lock=lock,
                expected=expected,
                data_root=data_root,
                runner=runner,
            )

    return _apply_defaults(
        PortableComfyCommands(
            command=python,
            args="-m comfy_mcp.server",
            comfy_bin=comfy,
        ),
        env_file,
        environ,
    )
