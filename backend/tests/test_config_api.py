from __future__ import annotations

import json
import os
import stat

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.config import router
from app.config import settings
from app.core.config_store import envfile, service

ADMIN = {"X-DS-Admin": "1"}
SECRET = "sk-test-SECRET-value-1234"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(f"# keep me\nDS_LLM_PROVIDER=openai-compatible\nDS_LLM_API_KEY={SECRET}\nDS_POLL_INTERVAL_SEC=1.5\n",
                    encoding="utf-8")
    monkeypatch.setattr(service, "_ENV_PATH_OVERRIDE", path)
    for key in [k for k in os.environ if k.startswith("DS_") or k == "MINIMAX_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    for attr in ("llm_provider", "llm_api_key", "poll_interval_sec", "comfy_mode", "llm_extra_headers",
                 "director_agent_runtime"):
        monkeypatch.setattr(settings, attr, getattr(settings, attr))
    service.restart_pending.clear()
    return path


def make_client(host: str = "127.0.0.1") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app, client=(host, 50000))


def test_requires_loopback_origin_and_admin_header(env_file):
    assert make_client("192.168.1.20").get("/api/config", headers=ADMIN).status_code == 403
    client = make_client()
    assert client.get("/api/config").status_code == 403
    assert client.get("/api/config", headers={**ADMIN, "Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/config", headers={**ADMIN, "Origin": "http://192.168.1.5:5173"}).status_code == 403
    assert client.get("/api/config", headers={**ADMIN, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/config", headers={**ADMIN, "Origin": "http://127.0.0.1:5173"}).status_code == 200
    state = make_client("10.0.0.2").get("/api/setup/state").json()
    assert state["admin_allowed"] is False


def test_get_masks_secrets(env_file):
    body = make_client().get("/api/config", headers=ADMIN).json()
    assert SECRET not in json.dumps(body)
    field = next(f for f in body["fields"] if f["key"] == "DS_LLM_API_KEY")
    assert field["secret"] == {"set": True, "hint": "…1234"}
    assert "value" not in field
    assert not any(f["key"] == "DS_HARNESS_INTERNAL_TOKEN" for f in body["fields"])


def test_put_round_trip_hot_apply_and_permissions(env_file):
    client = make_client()
    rev = client.get("/api/config", headers=ADMIN).json()["revision"]
    response = client.put("/api/config", headers=ADMIN, json={"revision": rev, "changes": {
        "DS_POLL_INTERVAL_SEC": 2.5, "DS_LLM_API_KEY": {"secret": "keep"},
        "DS_DIRECTOR_AGENT_RUNTIME": "legacy",  # harness is the default since upstream 69ad39e
        "DS_LLM_EXTRA_HEADERS": {"secret": "set", "value": "CF-Access-Client-Id: abc\nCF-Access-Client-Secret: xyz"},
    }})
    assert response.status_code == 200, response.text
    data = response.json()
    assert "DS_POLL_INTERVAL_SEC" in data["applied"]
    assert data["restart_required"] == ["DS_DIRECTOR_AGENT_RUNTIME"]
    assert settings.poll_interval_sec == 2.5
    text = env_file.read_text()
    assert text.startswith("# keep me\n") and f"DS_LLM_API_KEY={SECRET}" in text
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    body = client.get("/api/config", headers=ADMIN).json()
    headers = next(f for f in body["fields"] if f["key"] == "DS_LLM_EXTRA_HEADERS")
    assert headers["secret"]["hint"] == "CF-Access-Client-Id, CF-Access-Client-Secret"
    assert "xyz" not in json.dumps(body)
    assert body["restart_pending"] == ["DS_DIRECTOR_AGENT_RUNTIME"]


def test_put_conflict_on_external_edit(env_file):
    client = make_client()
    rev = client.get("/api/config", headers=ADMIN).json()["revision"]
    env_file.write_text(env_file.read_text() + "DS_JOB_TIMEOUT_SEC=99\n")
    response = client.put("/api/config", headers=ADMIN, json={"revision": rev, "changes": {"DS_POLL_INTERVAL_SEC": 3}})
    assert response.status_code == 409


def test_env_locked_keys_and_validation(env_file, monkeypatch):
    monkeypatch.setenv("DS_PORT", "8890")
    client = make_client()
    body = client.get("/api/config", headers=ADMIN).json()
    port = next(f for f in body["fields"] if f["key"] == "DS_PORT")
    assert port["locked"] is True and port["source"] == "env"
    rev = body["revision"]
    locked = client.put("/api/config", headers=ADMIN, json={"revision": rev, "changes": {"DS_PORT": 9000}})
    assert locked.status_code == 422 and "DS_PORT" in locked.json()["fields"]
    bad = client.put("/api/config", headers=ADMIN, json={"revision": rev, "changes": {"DS_LLM_PROVIDER": "nope"}})
    assert bad.status_code == 422
    internal = client.put("/api/config", headers=ADMIN,
                          json={"revision": rev, "changes": {"DS_HARNESS_INTERNAL_TOKEN": "x"}})
    assert internal.status_code == 422
    args = client.put("/api/config", headers=ADMIN,
                      json={"revision": rev, "changes": {"DS_COMFY_EXTRA_ARGS": "--listen 0.0.0.0"}})
    assert args.status_code == 422 and "DS_COMFY_EXTRA_ARGS" in args.json()["fields"]
    assert envfile.revision_of(env_file.read_text()) == rev


def test_secret_reuse_is_bound_to_host(env_file):
    client = make_client()
    settings.llm_api_key = SECRET
    response = client.post("/api/config/test/llm", headers=ADMIN, json={
        "provider": "openai-compatible", "base_url": "https://attacker.example/v1", "api_key": {"secret": "keep"}})
    assert response.status_code == 400
