"""Bundled Ollama runtime (fake binary), model pull progress + hints, capability probes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import textwrap

import httpx
import pytest

from app.api import ollama as ollama_api
from app.config import settings
from app.core.llm import capabilities
from app.core.llm import ollama_runtime as rt

FAKE_OLLAMA = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    assert sys.argv[1:] == ["serve"], sys.argv
    host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/api/version":
                body = {{"version": "0.0.0-fake"}}
            elif self.path == "/env":
                body = {{k: v for k, v in os.environ.items() if k.startswith("OLLAMA_")}}
            else:
                self.send_response(404); self.end_headers(); return
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    HTTPServer((host, int(port)), H).serve_forever()
''')


@pytest.fixture
def fake_ollama(tmp_path, monkeypatch):
    root = tmp_path / "ollama"
    (root / "bin").mkdir(parents=True)
    binary = root / "bin" / "ollama"
    binary.write_text(FAKE_OLLAMA.format(python=sys.executable))
    binary.chmod(0o755)
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_mode", "bundled")
    monkeypatch.setattr(settings, "ollama_dir", str(root))
    monkeypatch.setattr(settings, "ollama_port", 0)
    monkeypatch.setattr(settings, "ollama_models_dir", "")
    monkeypatch.setattr(settings, "director_num_ctx", 16384)
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:11434")
    monkeypatch.setattr(rt, "_rebuild_llm_clients", lambda: None)
    runtime = rt.OllamaRuntime()
    yield runtime
    asyncio.run(runtime.stop())


def test_bundled_ollama_starts_on_private_port_with_ds_models_and_ctx(fake_ollama, tmp_path):
    runtime = fake_ollama

    async def scenario():
        await runtime.start()
        assert runtime.error is None, runtime.error
        assert runtime.bundled and runtime.version == "0.0.0-fake"
        url = runtime.effective_url
        assert url.startswith("http://127.0.0.1:") and not url.endswith(":11434")
        assert settings.ollama_base_url == url
        assert runtime.configured_url == "http://127.0.0.1:11434"
        async with httpx.AsyncClient(trust_env=False) as client:
            env = (await client.get(f"{url}/env")).json()
        assert env["OLLAMA_MODELS"] == str(Path(settings.data_dir) / "ollama" / "models")
        assert env["OLLAMA_CONTEXT_LENGTH"] == "16384"  # = director_num_ctx, not Ollama's 4096 default
        assert Path(env["OLLAMA_MODELS"]).is_dir()
        status = runtime.status()
        assert status["bundled_active"] and status["managed"]["state"] == "ready"
        assert (await runtime.reconfigure()) == "unchanged"
        settings.ollama_mode = "system"
        assert (await runtime.reconfigure()) == "system"
        assert not runtime.bundled and settings.ollama_base_url == "http://127.0.0.1:11434"

    asyncio.run(scenario())


def test_user_ollama_env_cannot_redirect_private_instance(fake_ollama, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    monkeypatch.setenv("OLLAMA_MODELS", "/somewhere/else")

    async def scenario():
        await fake_ollama.start()
        async with httpx.AsyncClient(trust_env=False) as client:
            env = (await client.get(f"{fake_ollama.effective_url}/env")).json()
        assert env["OLLAMA_HOST"].startswith("127.0.0.1:") and env["OLLAMA_MODELS"] != "/somewhere/else"
        await fake_ollama.stop()

    asyncio.run(scenario())


def test_missing_binary_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_mode", "bundled")
    monkeypatch.setattr(settings, "ollama_dir", str(tmp_path / "nope"))
    runtime = rt.OllamaRuntime()
    asyncio.run(runtime.start())
    assert not runtime.bundled and "not found" in (runtime.error or "")


# ---- pull progress ---------------------------------------------------------------------

def _mock_client(handler):
    def factory(timeout=15.0):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
    return factory


def test_pull_relays_ndjson_progress(monkeypatch):
    events = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "sha256:a", "total": 1000, "completed": 250},
        {"status": "pulling def", "digest": "sha256:b", "total": 1000, "completed": 1000},
        {"status": "verifying sha256 digest"},
        {"status": "success"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        assert json.loads(request.content) == {"model": "qwen3:8b", "stream": True}
        return httpx.Response(200, content="\n".join(json.dumps(e) for e in events).encode())

    monkeypatch.setattr(ollama_api, "_client", _mock_client(handler))
    job = ollama_api.PullJob("qwen3:8b")
    asyncio.run(ollama_api._run_pull(job))
    view = job.view()
    assert view["state"] == "success" and view["total"] == 2000 and view["completed"] == 1250
    assert view["percent"] == 62.5 and view["hint"] is None


@pytest.mark.parametrize("error, needle", [
    ("pull model manifest: Get \"https://registry.ollama.ai/...\": dial tcp 198.18.0.5:443: "
     "non-public IP address 198.18.0.5", "fake-ip"),
    ("pull model manifest: file does not exist", "ollama.com/library"),
])
def test_pull_error_hints(monkeypatch, error, needle):
    def handler(request):
        return httpx.Response(200, content=(json.dumps({"status": "pulling manifest"}) + "\n"
                                            + json.dumps({"error": error})).encode())

    monkeypatch.setattr(ollama_api, "_client", _mock_client(handler))
    job = ollama_api.PullJob("x:1")
    asyncio.run(ollama_api._run_pull(job))
    view = job.view()
    assert view["state"] == "error" and error[:40] in view["error"]
    assert needle in view["hint"]["zh"] and view["hint"]["en"]


def test_pull_rejects_bad_names():
    from fastapi import HTTPException

    for bad in ("", "../etc", "a b", "-x"):
        with pytest.raises(HTTPException):
            ollama_api._check_name(bad)
    assert ollama_api._check_name("hf.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M")


# ---- capability probes -----------------------------------------------------------------

def _patch_probe_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(capabilities.httpx, "AsyncClient", factory)


def test_probe_ollama_tools_yes_vision_not_declared(monkeypatch):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(request.url.path)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        assert body["tools"][0]["function"]["name"] == "get_weather"
        return httpx.Response(200, json={"message": {"tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]}})

    _patch_probe_client(monkeypatch, handler)
    result = capabilities.summarize(asyncio.run(capabilities.probe_ollama("http://o", "tiny")), "harness")
    assert result["tools"]["supported"] is True and result["vision"]["supported"] is False
    assert result["harness_ok"] and result["warnings"] == ["no_vision"]
    assert seen.count("/api/chat") == 1  # no image sent to a model without declared vision


def test_probe_ollama_no_tools_warns_for_harness(monkeypatch):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        body = json.loads(request.content)
        if "tools" in body:
            return httpx.Response(400, json={"error": "registry.ollama.ai/library/x does not support tools"})
        assert body["messages"][0]["images"]
        return httpx.Response(200, json={"message": {"content": "Red."}})

    _patch_probe_client(monkeypatch, handler)
    result = capabilities.summarize(asyncio.run(capabilities.probe_ollama("http://o", "x")), "harness")
    assert result["tools"]["supported"] is False and result["vision"]["supported"] is True
    assert result["warnings"] == ["harness_requires_tools"] and not result["harness_ok"]


def test_probe_openai_compatible(monkeypatch):
    def handler(request):
        assert request.headers["authorization"] == "Bearer k"
        body = json.loads(request.content)
        if "tools" in body:
            return httpx.Response(200, json={"choices": [{"message": {"content": "It is sunny."}}]})
        return httpx.Response(422, json={"error": "image input not supported"})

    _patch_probe_client(monkeypatch, handler)
    result = capabilities.summarize(asyncio.run(capabilities.probe_openai("http://lm/v1", "m", api_key="k")), "legacy")
    assert result["tools"]["supported"] is False and "text instead" in result["tools"]["detail"]
    assert result["vision"]["supported"] is False and result["warnings"] == ["no_vision"]


def test_solid_png_is_valid_image():
    import io

    from PIL import Image

    with Image.open(io.BytesIO(capabilities.solid_png())) as image:
        assert image.size == (16, 16) and image.getpixel((3, 3)) == (220, 20, 20)
