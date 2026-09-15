from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


_ENVIRONMENT_KEYS = (
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


class HarnessVerificationError(RuntimeError):
    pass


def host_result(method: str) -> dict:
    if method == "context":
        return {"system": "portable smoke", "state": "empty", "tools": []}
    if method == "llm":
        return {
            "content": "portable harness smoke test complete",
            "thinking": "",
            "tool_calls": [],
        }
    raise HarnessVerificationError(f"unknown host method: {method}")


def child_environment(
    parent: Mapping[str, str],
    *,
    token: str,
    port: int,
    session_root: Path,
) -> dict[str, str]:
    environment = {
        key: str(parent[key])
        for key in _ENVIRONMENT_KEYS
        if parent.get(key) is not None
    }
    environment.update(
        DS_HARNESS_INTERNAL_TOKEN=token,
        DS_HARNESS_PORT=str(port),
        DS_HARNESS_PARENT_PID=str(os.getpid()),
        DS_HARNESS_SESSION_ROOT=str(session_root),
    )
    return environment


def _connection(base_url: str, timeout: float) -> http.client.HTTPConnection:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise HarnessVerificationError("Harness smoke URL must be loopback HTTP")
    return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)


def _post_host_response(
    base_url: str,
    turn_id: str,
    request_id: str,
    token: str,
    data: dict,
    timeout: float,
) -> None:
    connection = _connection(base_url, timeout)
    try:
        payload = json.dumps({"ok": True, "data": data}).encode()
        connection.request(
            "POST",
            f"/turns/{turn_id}/responses/{request_id}",
            body=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        response = connection.getresponse()
        response.read()
        if response.status != 204:
            raise HarnessVerificationError(
                f"Harness rejected host response with HTTP {response.status}"
            )
    finally:
        connection.close()


def drive_turn(base_url: str, token: str, *, timeout: float = 20.0) -> str:
    turn_id = str(uuid4())
    payload = json.dumps(
        {
            "message": "Return the smoke-test response.",
            "history": [],
            "session_id": "portable-smoke",
            "context_window": 4096,
            "max_steps": 3,
        }
    ).encode()
    connection = _connection(base_url, timeout)
    try:
        connection.request(
            "POST",
            f"/turns/{turn_id}",
            body=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            response.read()
            raise HarnessVerificationError(
                f"Harness turn returned HTTP {response.status}"
            )
        while line := response.readline():
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HarnessVerificationError(
                    f"Harness emitted invalid NDJSON: {exc}"
                ) from exc
            event_type = event.get("type")
            if event_type == "request":
                method = event.get("method")
                request_id = event.get("id")
                if not isinstance(method, str) or not isinstance(request_id, str):
                    raise HarnessVerificationError("Harness emitted an invalid host request")
                _post_host_response(
                    base_url,
                    turn_id,
                    request_id,
                    token,
                    host_result(method),
                    timeout,
                )
            elif event_type == "error":
                raise HarnessVerificationError(
                    f"Harness turn failed: {event.get('code')}: {event.get('message')}"
                )
            elif event_type == "result":
                reply = event.get("reply")
                if reply != "portable harness smoke test complete":
                    raise HarnessVerificationError(
                        f"Harness returned unexpected smoke reply: {reply!r}"
                    )
                return reply
    finally:
        connection.close()
    raise HarnessVerificationError("Harness turn ended without a result")


def _healthy(base_url: str, token: str) -> bool:
    request = Request(
        f"{base_url}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=0.5) as response:
            health = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(health, dict)
        and health.get("ok") is True
        and health.get("service") == "director-studio-harness"
        and health.get("protocol") == 1
        and {"native-sessions-v1", "context-envelope-v2"}.issubset(
            set(health.get("capabilities") or [])
        )
    )


def run_bundled_harness(
    package_root: Path,
    *,
    port: int,
    timeout: float = 20.0,
) -> str:
    node = package_root / "runtime" / "node" / "node.exe"
    entry = package_root / "harness" / "dist" / "server.js"
    if not node.is_file():
        raise HarnessVerificationError(f"private node.exe is missing: {node}")
    if not entry.is_file():
        raise HarnessVerificationError(f"Harness server.js is missing: {entry}")
    token = secrets.token_hex(32)
    base_url = f"http://127.0.0.1:{port}"
    process: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="director-portable-harness-smoke-") as session:
        try:
            process = subprocess.Popen(
                [str(node), str(entry)],
                cwd=package_root / "harness",
                env=child_environment(
                    os.environ,
                    token=token,
                    port=port,
                    session_root=Path(session),
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + timeout
            while not _healthy(base_url, token):
                returncode = process.poll()
                if returncode is not None:
                    output = process.stdout.read().strip() if process.stdout else ""
                    raise HarnessVerificationError(
                        f"bundled Harness exited with {returncode}: {output}"
                    )
                if time.monotonic() >= deadline:
                    raise HarnessVerificationError(
                        f"bundled Harness was not healthy within {timeout:g} seconds"
                    )
                time.sleep(0.1)
            return drive_turn(base_url, token, timeout=timeout)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify bundled Harness offline")
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        reply = run_bundled_harness(
            args.package_root.resolve(),
            port=args.port,
            timeout=args.timeout,
        )
    except (OSError, ValueError, HarnessVerificationError) as exc:
        parser.exit(1, f"Bundled Harness verification failed: {exc}\n")
    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
