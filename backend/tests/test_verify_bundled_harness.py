from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
from uuid import UUID

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_bundled_harness.py"
spec = importlib.util.spec_from_file_location("verify_bundled_harness", SCRIPT)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)


class ProtocolFixture(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ProtocolHandler)
        self.responses: dict[str, dict] = {}
        self.received = threading.Event()


class ProtocolHandler(BaseHTTPRequestHandler):
    server: ProtocolFixture

    def log_message(self, _format, *args):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        payload = json.dumps(
            {
                "ok": True,
                "service": "director-studio-harness",
                "protocol": 1,
                "capabilities": ["native-sessions-v1", "context-envelope-v2"],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if "/responses/" in self.path:
            request_id = self.path.rsplit("/", 1)[-1]
            self.server.responses[request_id] = body
            self.server.received.set()
            self.send_response(204)
            self.end_headers()
            return

        UUID(self.path.rsplit("/", 1)[-1])
        assert body["session_id"] == "portable-smoke"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for request_id, method in (("context-request", "context"), ("llm-request", "llm")):
            self.server.received.clear()
            event = {"type": "request", "id": request_id, "method": method, "params": {}}
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()
            assert self.server.received.wait(2)
        result = {
            "type": "result",
            "reply": "portable harness smoke test complete",
            "thinking": "",
        }
        self.wfile.write((json.dumps(result) + "\n").encode())
        self.wfile.flush()


def test_protocol_driver_services_context_and_llm_requests() -> None:
    server = ProtocolFixture()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = verifier.drive_turn(
            f"http://127.0.0.1:{server.server_address[1]}",
            "secret",
            timeout=3,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result == "portable harness smoke test complete"
    assert server.responses["context-request"] == {
        "ok": True,
        "data": {"system": "portable smoke", "state": "empty", "tools": []},
    }
    assert server.responses["llm-request"] == {
        "ok": True,
        "data": {
            "content": "portable harness smoke test complete",
            "thinking": "",
            "tool_calls": [],
        },
    }


def test_unknown_host_method_is_rejected() -> None:
    with pytest.raises(verifier.HarnessVerificationError, match="unknown host method"):
        verifier.host_result("filesystem")


def test_child_environment_excludes_system_node_and_provider_secrets(tmp_path: Path) -> None:
    result = verifier.child_environment(
        {
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\node",
            "NODE_OPTIONS": "--require bad.js",
            "DS_LLM_API_KEY": "secret",
        },
        token="a" * 64,
        port=19003,
        session_root=tmp_path,
    )

    assert result["SystemRoot"] == r"C:\Windows"
    assert result["DS_HARNESS_PORT"] == "19003"
    assert "PATH" not in result
    assert "NODE_OPTIONS" not in result
    assert "DS_LLM_API_KEY" not in result


def test_missing_private_node_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(verifier.HarnessVerificationError, match="node.exe"):
        verifier.run_bundled_harness(tmp_path, port=19004, timeout=0.1)
