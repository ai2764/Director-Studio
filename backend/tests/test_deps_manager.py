"""Dependency downloader: resumable + verified model downloads, pinned node packs, modes, API."""

from __future__ import annotations

import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import subprocess
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.core.deps import catalog, manager
from app.core.deps.manager import DepsError, DepsManager, Job

PAYLOAD = bytes(range(256)) * 12_000  # ~3 MB
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class Server:
    """Loopback file server with Range support; can cut a response short or demand auth."""

    def __init__(self) -> None:
        self.cut_after: int | None = None
        self.require_auth = False
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "range": self.headers.get("Range"),
                                       "auth": self.headers.get("Authorization")})
                if outer.require_auth and not self.headers.get("Authorization"):
                    self.send_response(401)
                    self.end_headers()
                    return
                start = 0
                if self.headers.get("Range"):
                    start = int(self.headers["Range"].split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
                else:
                    self.send_response(200)
                body = PAYLOAD[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if outer.cut_after is not None:
                    self.wfile.write(body[: outer.cut_after])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.fixture
def server():
    srv = Server()
    yield srv
    srv.close()


@pytest.fixture
def managed(tmp_path, monkeypatch):
    for attr, value in (("comfy_mode", "managed"), ("data_dir", tmp_path / "data"),
                        ("comfy_models_dir", str(tmp_path / "models")), ("hf_token", ""),
                        ("deps_hf_endpoint", ""), ("comfy_hf_endpoint", ""), ("deps_concurrency", 2)):
        monkeypatch.setattr(settings, attr, value)
    return tmp_path


def fake_model(server: Server, **extra) -> dict:
    return {"file": "tiny.safetensors", "folder": "vae", "size": len(PAYLOAD), "sha256": SHA,
            "sources": [{"kind": "url", "url": f"{server.url}/tiny.safetensors"}], **extra}


@pytest.mark.asyncio
async def test_interrupted_download_resumes_with_range_and_is_verified(managed, server, monkeypatch):
    entry = fake_model(server)
    monkeypatch.setattr(catalog, "model", lambda f: entry if f == entry["file"] else None)
    mgr = DepsManager()
    server.cut_after = 1_000_000
    job = mgr.start("model", "tiny.safetensors")
    await job.task
    assert job.state == "failed" and "1000000" in job.error
    part = managed / "models" / "vae" / "tiny.safetensors.part"
    assert part.stat().st_size == 1_000_000
    server.cut_after = None
    job = mgr.start("model", "tiny.safetensors")
    await job.task
    assert job.state == "done", job.error
    assert server.requests[-1]["range"] == "bytes=1000000-"
    final = managed / "models" / "vae" / "tiny.safetensors"
    assert final.read_bytes() == PAYLOAD and not part.exists()
    status = mgr._model_state(entry, None)
    assert status["state"] == "installed"


@pytest.mark.asyncio
async def test_hash_mismatch_fails_and_removes_partial(managed, server, monkeypatch):
    entry = fake_model(server, sha256="0" * 64)
    monkeypatch.setattr(catalog, "model", lambda f: entry)
    mgr = DepsManager()
    job = mgr.start("model", entry["file"])
    await job.task
    assert job.state == "failed" and "sha256 mismatch" in job.error
    assert not list((managed / "models" / "vae").iterdir())


@pytest.mark.asyncio
async def test_cancel_keeps_partial_for_resume(managed, server, monkeypatch):
    entry = fake_model(server)
    monkeypatch.setattr(catalog, "model", lambda f: entry)
    started = asyncio.Event()
    real = manager.download_file

    async def slow(url, part, job, **kw):
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(PAYLOAD[:1234])
        started.set()
        await asyncio.sleep(30)
        return await real(url, part, job, **kw)

    monkeypatch.setattr(manager, "download_file", slow)
    mgr = DepsManager()
    job = mgr.start("model", entry["file"])
    await started.wait()
    assert mgr.cancel("model", entry["file"])
    await asyncio.gather(job.task, return_exceptions=True)
    assert job.state == "cancelled"
    assert (managed / "models" / "vae" / "tiny.safetensors.part").stat().st_size == 1234
    assert mgr._model_state(entry, None)["partial"] == 1234


@pytest.mark.asyncio
async def test_gated_model_asks_for_token_and_token_only_goes_to_hf(managed, server, monkeypatch):
    monkeypatch.setattr(settings, "deps_hf_endpoint", server.url)  # loopback server acts as the HF mirror
    server.require_auth = True
    job = Job("model", "x", len(PAYLOAD))
    with pytest.raises(manager._GatedError, match="token"):
        await manager.download_file(f"{server.url}/f", managed / "f.part", job, expected_size=len(PAYLOAD),
                                    headers=manager._auth_for(f"{server.url}/f"))
    monkeypatch.setattr(settings, "hf_token", "hf_SECRET123")
    assert manager._auth_for(f"{server.url}/f") == {"Authorization": "Bearer hf_SECRET123"}
    assert manager._auth_for("https://huggingface.co/a/b") == {"Authorization": "Bearer hf_SECRET123"}
    assert manager._auth_for("https://modelscope.cn/models/a") == {}
    assert manager._auth_for("https://evil.example/x") == {}
    assert manager._redact("bad hf_SECRET123 here") == "bad *** here"
    await manager.download_file(f"{server.url}/f", managed / "f.part", job, expected_size=len(PAYLOAD),
                                headers=manager._auth_for(f"{server.url}/f"))
    assert server.requests[-1]["auth"] == "Bearer hf_SECRET123"


def test_hf_mirror_and_pinned_revision_urls(managed, monkeypatch):
    entry = catalog.model("qwen_image_vae.safetensors")
    rev = catalog.hf_source(entry)["revision"]
    monkeypatch.setattr(settings, "deps_hf_endpoint", "https://hf-mirror.com")
    urls = catalog.model_urls(entry, hf_endpoint=manager.hf_endpoint())
    assert urls[0] == f"https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/{rev}/split_files/vae/qwen_image_vae.safetensors"
    assert urls[1].startswith("https://modelscope.cn/models/Comfy-Org/Qwen-Image_ComfyUI/resolve/master/")


@pytest.fixture
def local_pack_repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not available")
    repo = tmp_path / "upstream"
    repo.mkdir()
    (repo / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
    (repo / "requirements.txt").write_text("")
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v1")
    run("config", "uploadpack.allowAnySHA1InWant", "true")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    (repo / "later.txt").write_text("not pinned")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v2")
    return {"id": "TestPack", "name": "TestPack", "repo": repo.as_uri(), "ref": commit, "commit": commit,
            "license": "MIT", "unlicensed": False, "provides": ["TestNode"], "requirements": [],
            "ships_with": None, "installable": True}


@pytest.mark.asyncio
async def test_node_pack_is_cloned_at_the_pinned_commit_into_user_custom_nodes(managed, local_pack_repo, monkeypatch):
    monkeypatch.setattr(catalog, "pack", lambda pid: local_pack_repo if pid == "TestPack" else None)
    mgr = DepsManager()
    job = mgr.start("pack", "TestPack")
    await job.task
    assert job.state == "done", job.error
    target = managed / "data" / "comfy" / "custom_nodes" / "TestPack"
    assert (target / "__init__.py").is_file() and not (target / "later.txt").exists()
    marker = json.loads((target / manager.MARKER).read_text())
    assert marker["commit"] == local_pack_repo["commit"] and job.source == "git"
    assert mgr.restart_needed
    assert mgr._pack_state(local_pack_repo, {"KSampler": {}})["state"] == "restart_needed"
    assert mgr._pack_state(local_pack_repo, {"TestNode": {}})["state"] == "installed"
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".")]


def test_requirements_use_the_comfy_python(managed, tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "requirements.txt").write_text("color-matcher\n")
    calls = []
    monkeypatch.setattr(manager, "comfy_python", lambda: tmp_path / "py")
    monkeypatch.setattr(manager.shutil, "which", lambda name: None)
    monkeypatch.setattr(manager, "_run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    manager.install_requirements({"requirements": ["requirements.txt", "missing.txt"]}, pack)
    assert calls == [[str(tmp_path / "py"), "-m", "pip", "install", "--disable-pip-version-check",
                      "-r", str(pack / "requirements.txt")]]


@pytest.mark.asyncio
async def test_remote_mode_is_status_only(managed, monkeypatch):
    monkeypatch.setattr(settings, "comfy_mode", "remote")
    mgr = DepsManager()
    with pytest.raises(DepsError, match="managed"):
        mgr.start("model", "qwen_image_vae.safetensors")

    async def view():
        return ({"KSampler": {}, "SimpleCalculatorKJ": {}}, {"vae": {"qwen_image_vae.safetensors"}, "loras": set()})

    monkeypatch.setattr(mgr, "_comfy_view", view)
    data = await mgr.status()
    assert data["mode"] == "remote" and not data["can_install"]
    scene = next(w for w in data["workflows"] if w["file"] == "QwenEdit2511_MultiAngle_SceneRef.api.json")
    items = {i["id"]: i for i in scene["items"]}
    assert items["qwen_image_vae.safetensors"]["state"] == "installed"
    assert items["Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"]["state"] == "missing"
    assert items["qwen_2.5_vl_7b_fp8_scaled.safetensors"]["state"] == "unknown"
    assert items["ComfyUI-KJNodes"]["state"] == "installed" and items["ComfyUI-KJNodes"]["path"] is None
    assert items["qwen_image_vae.safetensors"]["url"].startswith("https://huggingface.co/")


@pytest.mark.asyncio
async def test_managed_status_and_install_whole_workflow(managed, monkeypatch):
    mgr = DepsManager()

    async def view():
        return None, None

    monkeypatch.setattr(mgr, "_comfy_view", view)
    vae = managed / "models" / "vae" / "qwen_image_vae.safetensors"
    vae.parent.mkdir(parents=True)
    vae.write_bytes(b"x")
    data = await mgr.status()
    wf = next(w for w in data["workflows"] if w["file"] == "ref_frame_layout.api.json")
    states = {i["id"]: i["state"] for i in wf["items"]}
    assert states["qwen_image_vae.safetensors"] == "size_mismatch" and not wf["ready"]
    started = []
    monkeypatch.setattr(mgr, "start", lambda kind, item: started.append((kind, item)) or Job(kind, item, None))
    await mgr.start_workflow("ref_frame_layout.api.json")
    assert sorted(started) == sorted(("model", f) for f in catalog.workflow("ref_frame_layout.api.json")["models"])


def test_api_requires_admin_and_reports_mode(managed, monkeypatch):
    from app.api.deps import router

    async def status():
        return {"mode": "managed", "workflows": []}

    monkeypatch.setattr(manager.deps_manager, "status", status)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/deps").status_code in (401, 403)
    assert client.get("/api/deps", headers={"X-DS-Admin": "1"}).json()["mode"] == "managed"
    monkeypatch.setattr(settings, "comfy_mode", "remote")
    response = client.post("/api/deps/install", headers={"X-DS-Admin": "1"},
                           json={"items": [{"kind": "model", "id": "qwen_image_vae.safetensors"}]})
    assert response.status_code == 409


def test_bundled_packs_are_marked_and_count_as_installed(managed, tmp_path, monkeypatch):
    for p in catalog.node_packs():
        assert p["bundled"] == (p["id"] in {"ds_nodes", "ComfyUI-KJNodes", "ComfyUI-H3-Motion-Context"})
    assert not catalog.pack("H3-Optimizations")["bundled"]
    comfy = tmp_path / "ComfyUI"
    (comfy / "custom_nodes" / "ComfyUI-KJNodes").mkdir(parents=True)
    monkeypatch.setattr(manager, "comfy_dir", lambda cfg=None: comfy)
    mgr = DepsManager()
    kj = mgr._pack_state(catalog.pack("ComfyUI-KJNodes"), {"KSampler": {}})
    assert kj["state"] == "installed" and kj["bundled"]
    assert mgr._pack_state(catalog.pack("H3-Optimizations"), {"KSampler": {}})["state"] == "missing"
    assert mgr._pack_state(catalog.pack("ComfyUI-H3-Motion-Context"), {"KSampler": {}})["state"] == "missing"
