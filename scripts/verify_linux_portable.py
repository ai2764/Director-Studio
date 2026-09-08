from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request


_POLL_INTERVAL_SEC = 0.25
_PROCESS_GROUP_TIMEOUT_SEC = 5.0
_OUTPUT_TAIL_CHARS = 4096


def _decode_tail(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-_OUTPUT_TAIL_CHARS:]


def _read_tail(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - _OUTPUT_TAIL_CHARS))
            return _decode_tail(stream.read())
    except OSError:
        return ""


def _runtime_exit_error(
    process: subprocess.Popen[bytes],
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> RuntimeError:
    return RuntimeError(
        "runtime exited with code "
        f"{process.returncode}; stdout={_read_tail(stdout_path)!r}; "
        f"stderr={_read_tail(stderr_path)!r}"
    )


def wait_for_health(
    process: subprocess.Popen[bytes],
    url: str,
    timeout_sec: float,
    *,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> dict[str, Any]:
    """Poll a packaged runtime health endpoint until it responds with JSON."""
    deadline = time.monotonic() + timeout_sec
    while True:
        if process.poll() is not None:
            raise _runtime_exit_error(process, stdout_path, stderr_path)
        try:
            with urllib.request.urlopen(url, timeout=_POLL_INTERVAL_SEC) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("health response must be a JSON object")
            return payload
        except (
            OSError,
            TimeoutError,
            ValueError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for health at {url}")
            time.sleep(min(_POLL_INTERVAL_SEC, remaining))


def _get(url: str, timeout_sec: float) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout_sec) as response:
        return response.status, response.read()


def _group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_process_group(
    process: subprocess.Popen[bytes], process_group_id: int
) -> None:
    if os.name == "nt":
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=_PROCESS_GROUP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_PROCESS_GROUP_TIMEOUT_SEC)
        return

    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        deadline = time.monotonic() + _PROCESS_GROUP_TIMEOUT_SEC
        while _group_exists(process_group_id) and time.monotonic() < deadline:
            time.sleep(0.05)

        if _group_exists(process_group_id):
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        process.wait(timeout=_PROCESS_GROUP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "could not reap packaged runtime after process-group cleanup"
        ) from exc


def _verification_environment(port: int) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("DS_")
    }
    environment.update({"DS_HOST": "127.0.0.1", "DS_PORT": str(port)})
    return environment


def verify_runtime(
    package_root: Path, port: int, timeout_sec: float
) -> dict[str, Any]:
    """Run and verify a Linux package without mutating its source directory."""
    process: subprocess.Popen[bytes] | None = None
    process_group_id: int | None = None
    with tempfile.TemporaryDirectory(prefix="director-studio-linux-verify-") as temp:
        runtime_root = Path(temp) / "package"
        shutil.copytree(package_root, runtime_root)
        executable = runtime_root / "DirectorStudio"
        stdout_path = Path(temp) / "stdout.log"
        stderr_path = Path(temp) / "stderr.log"
        env = _verification_environment(port)
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open(
                "wb"
            ) as stderr_file:
                process = subprocess.Popen(
                    [str(executable)],
                    cwd=runtime_root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            if os.name != "nt":
                process_group_id = os.getpgid(process.pid)

            health = wait_for_health(
                process,
                f"http://127.0.0.1:{port}/api/health",
                timeout_sec,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            if health.get("ok") is not True:
                raise RuntimeError(f"health check did not report ok: {health!r}")

            request_timeout = max(timeout_sec, 0.1)
            page_statuses: dict[str, int] = {}
            for path, result_key in (
                ("/", "frontend_status"),
                ("/mobile", "mobile_status"),
                ("/docs", "docs_status"),
            ):
                status, _ = _get(
                    f"http://127.0.0.1:{port}{path}", request_timeout
                )
                if status != 200:
                    raise RuntimeError(
                        f"{path} returned HTTP {status}; expected HTTP 200"
                    )
                page_statuses[result_key] = status
            profile_status, profile_body = _get(
                f"http://127.0.0.1:{port}/api/workflow-profiles/h3",
                request_timeout,
            )
            profile_payload = json.loads(profile_body.decode("utf-8"))
            active = profile_payload.get("active")
            if (
                profile_status != 200
                or not isinstance(active, dict)
                or active.get("profile_id") != "builtin-official-h3"
                or active.get("source") != "builtin"
            ):
                raise RuntimeError(
                    "runtime does not use the official built-in H3 profile"
                )
            return {
                "health": health,
                "active_h3": active["profile_id"],
                **page_statuses,
            }
        finally:
            if process is not None:
                if process_group_id is not None:
                    _stop_process_group(process, process_group_id)
                elif process.poll() is None:
                    process.terminate()
                    process.wait(timeout=_PROCESS_GROUP_TIMEOUT_SEC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a packaged Linux Director Studio runtime"
    )
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    args = parser.parse_args(argv)

    try:
        result = verify_runtime(args.package_root, args.port, args.timeout_sec)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Linux portable runtime verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
