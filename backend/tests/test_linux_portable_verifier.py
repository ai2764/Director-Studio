from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPO_ROOT / "scripts" / "verify_linux_portable.py"

spec = importlib.util.spec_from_file_location("verify_linux_portable", VERIFIER_PATH)
assert spec and spec.loader
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="requires POSIX process semantics"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _process_with_executable(executable: Path) -> bool:
    needle = executable.name.encode()
    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            command_line = (process_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in command_line:
            return True
    return False


def _write_executable(package: Path, body: str) -> None:
    executable = package / "DirectorStudio"
    executable.write_text(
        "#!/usr/bin/env python3\n" + body,
        encoding="utf-8",
    )
    executable.chmod(0o755)


def _write_healthy_executable(package: Path) -> None:
    _write_executable(
        package,
        """
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.mkdir("data")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            payload = {"ok": True}
        elif self.path == "/api/workflow-profiles/h3":
            payload = {
                "active": {"profile_id": "builtin-official-h3", "source": "builtin"},
                "profiles": [],
            }
        elif self.path in {"/", "/mobile", "/docs"}:
            self.send_response(200)
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["DS_PORT"])), Handler)
server.serve_forever()
""",
    )


def test_verify_runtime_uses_copy_and_checks_health_profiles_and_pages(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    _write_healthy_executable(package)
    port = _free_port()

    result = verifier.verify_runtime(package, port, timeout_sec=10)

    assert result["health"]["ok"] is True
    assert result["active_h3"] == "builtin-official-h3"
    assert result["frontend_status"] == 200
    assert result["mobile_status"] == 200
    assert result["docs_status"] == 200
    assert not _process_with_executable(package / "DirectorStudio")
    assert not (package / "data").exists()


def test_verify_runtime_reports_early_exit_output_and_cleans_process_group(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    _write_executable(
        package,
        """
import sys
print("fake stdout", flush=True)
print("fake stderr", file=sys.stderr, flush=True)
raise SystemExit(23)
""",
    )

    with pytest.raises(RuntimeError, match="fake stdout.*fake stderr"):
        verifier.verify_runtime(package, _free_port(), timeout_sec=10)

    assert not _process_with_executable(package / "DirectorStudio")


def test_verify_runtime_times_out_health_and_cleans_process_group(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    _write_executable(
        package,
        """
import time
time.sleep(30)
""",
    )

    with pytest.raises(TimeoutError, match="health"):
        verifier.verify_runtime(package, _free_port(), timeout_sec=0.5)

    assert not _process_with_executable(package / "DirectorStudio")
