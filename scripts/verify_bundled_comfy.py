from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Sequence
_PRESERVED_ENVIRONMENT = (
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
)


class VerifyError(RuntimeError):
    pass


def sanitized_environment(
    runtime: Path,
    environ: Mapping[str, str],
) -> dict[str, str]:
    child = {
        key: value
        for key in _PRESERVED_ENVIRONMENT
        if (value := environ.get(key)) is not None
    }
    system_root = child.get("SystemRoot") or child.get("WINDIR")
    path_entries = [str(runtime)]
    if system_root:
        path_entries.append(str(Path(system_root) / "System32"))
    child["PATH"] = os.pathsep.join(path_entries)
    child["PYTHONNOUSERSITE"] = "1"
    child["PYTHONDONTWRITEBYTECODE"] = "1"
    return child


def _run_checked(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    stage: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    try:
        completed = runner(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerifyError(f"{stage} failed to run: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no output")[-4096:]
        raise VerifyError(f"{stage} failed with exit {completed.returncode}: {detail}")


def verify_bundled_comfy(
    package_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    runtime = package_root / "runtime" / "python"
    python = runtime / "python.exe"
    comfy = runtime / "comfy.exe"
    pip_module = runtime / "Lib" / "site-packages" / "pip"
    sitecustomize = runtime / "Lib" / "site-packages" / "sitecustomize.py"
    manifest_path = package_root / "runtime" / "comfy-bootstrap.json"
    lock = package_root / "runtime" / "comfy-requirements.lock"
    missing = [
        str(path)
        for path in (python, comfy, pip_module, sitecustomize, manifest_path, lock)
        if not path.exists()
    ]
    if missing:
        raise VerifyError("Comfy bootstrap runtime is incomplete; missing: " + ", ".join(missing))
    site = runtime / "Lib" / "site-packages"
    forbidden = [
        path.name
        for pattern in ("comfy_mcp*", "comfy_cli*")
        for path in site.glob(pattern)
    ]
    if forbidden:
        raise VerifyError(
            "First-launch dependencies must not be bundled: " + ", ".join(sorted(forbidden))
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifyError(f"Comfy bootstrap manifest is unreadable: {exc}") from exc
    if manifest.get("lock_sha256") != hashlib.sha256(lock.read_bytes()).hexdigest():
        raise VerifyError("Comfy bootstrap requirements checksum does not match")
    if manifest.get("packages") != {
        "comfy-cli": "1.20.0",
        "comfy-mcp": "0.10.0",
    }:
        raise VerifyError("Comfy bootstrap package versions are invalid")
    child_env = sanitized_environment(runtime, environ or os.environ)
    _run_checked(
        [str(python), "-m", "pip", "--version"],
        env=child_env,
        cwd=runtime,
        stage="private pip",
        runner=runner,
    )
    _run_checked(
        [
            str(python),
            "-c",
            "import importlib.util; assert importlib.util.find_spec('comfy_mcp') is None; assert importlib.util.find_spec('comfy_cli') is None",
        ],
        env=child_env,
        cwd=runtime,
        stage="first-launch dependency exclusion",
        runner=runner,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Windows Comfy first-launch bootstrap")
    parser.add_argument("--package-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verify_bundled_comfy(args.package_root.resolve())
    except VerifyError as exc:
        parser.exit(1, f"Comfy bootstrap verification failed: {exc}\n")
    print("Comfy first-launch bootstrap verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
