"""Ollama model management for the settings UI (list / pull with progress / delete).

Models come from Ollama's own registry downloader (``/api/pull``); DS only relays
progress.  Works against the bundled Ollama and a user's own Ollama alike
(whatever ``settings.ollama_base_url`` currently points at).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from ..config import settings
from ..core.llm.extra_headers import llm_extra_headers
from ..core.llm.ollama_runtime import ollama_runtime
from .security import require_local_admin

router = APIRouter(prefix="/llm/ollama", tags=["ollama"], dependencies=[Depends(require_local_admin)])

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:]{0,199}$")
# A few small, tool-capable starting points (shown as suggestions only).
SUGGESTED = [
    {"name": "qwen3:8b", "size": "5.2 GB", "note": "tools, good Chinese"},
    {"name": "qwen3:4b", "size": "2.5 GB", "note": "tools"},
    {"name": "qwen2.5vl:7b", "size": "6.0 GB", "note": "vision"},
    {"name": "gemma3:4b", "size": "3.3 GB", "note": "vision"},
    {"name": "qwen2.5:0.5b", "size": "398 MB", "note": "tiny test model, tools"},
]


def _base() -> str:
    return settings.ollama_base_url.rstrip("/")


def _client(timeout: float | httpx.Timeout = 15.0) -> httpx.AsyncClient:
    headers = {} if ollama_runtime.bundled else llm_extra_headers()
    return httpx.AsyncClient(timeout=timeout, headers=headers, trust_env=False)


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not MODEL_RE.match(name) or ".." in name:
        raise HTTPException(400, "invalid model name")
    return name


def pull_error_hint(error: str | None) -> dict[str, str] | None:
    """Actionable hints for common pull failures (fake-IP proxies, offline, unknown tag)."""
    text = (error or "").lower()
    if not text:
        return None
    if "non-public" in text or "redirect target not allowed" in text:
        return {
            "zh": "模型下载地址被解析成了保留/内网 IP（常见于 Clash 等代理的 fake-ip 模式，Ollama 出于安全会拒绝）。"
                  "请在代理的 fake-ip-filter 中加入 +.ollama.com、+.r2.cloudflarestorage.com（以及 +.hf.co），或切换为 real-ip/TUN 模式后重试。",
            "en": "The download host resolved to a reserved/private IP (typical for fake-IP proxy modes such as Clash); "
                  "Ollama refuses that for safety. Exclude +.ollama.com and +.r2.cloudflarestorage.com from fake-ip, or use real-IP mode.",
        }
    if "file does not exist" in text or "not found" in text:
        return {"zh": "模型名或标签不存在，请到 ollama.com/library 核对名称（例如 qwen3:8b）。",
                "en": "Unknown model or tag; check the name on ollama.com/library (e.g. qwen3:8b)."}
    if "connect" in text or "timeout" in text or "dial" in text:
        return {"zh": "无法连接模型仓库，请检查网络/代理（Ollama 读取 HTTPS_PROXY 环境变量）。",
                "en": "Cannot reach the registry; check network/proxy (Ollama honours HTTPS_PROXY)."}
    return None


class PullJob:
    def __init__(self, name: str) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.name = name
        self.status = "queued"
        self.state = "running"  # running | success | error | cancelled
        self.total = 0
        self.completed = 0
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.layers: dict[str, list[int]] = {}
        self.task: asyncio.Task[None] | None = None

    def view(self) -> dict[str, Any]:
        total = sum(v[0] for v in self.layers.values()) or self.total
        done = sum(v[1] for v in self.layers.values()) or self.completed
        return {
            "id": self.id, "name": self.name, "state": self.state, "status": self.status,
            "total": total, "completed": done,
            "percent": round(100.0 * done / total, 1) if total else None,
            "error": self.error, "hint": pull_error_hint(self.error), "started": self.started,
            "finished": self.finished,
        }


_jobs: dict[str, PullJob] = {}


async def _run_pull(job: PullJob) -> None:
    try:
        async with _client(httpx.Timeout(None, connect=10.0)) as client:
            async with client.stream("POST", f"{_base()}/api/pull", json={"model": job.name, "stream": True}) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    raise RuntimeError(f"HTTP {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("error"):
                        raise RuntimeError(str(event["error"]))
                    job.status = str(event.get("status") or job.status)
                    digest = event.get("digest")
                    if digest and event.get("total"):
                        job.layers[str(digest)] = [int(event.get("total") or 0), int(event.get("completed") or 0)]
                    if job.status == "success":
                        job.state = "success"
        if job.state != "success":
            job.state = "error"
            job.error = job.error or "pull ended without success"
    except asyncio.CancelledError:
        job.state, job.error = "cancelled", "cancelled"
    except Exception as exc:  # noqa: BLE001
        job.state, job.error = "error", f"{type(exc).__name__}: {exc}"[:300]
    finally:
        job.finished = time.time()


@router.get("/models")
async def list_models() -> dict[str, Any]:
    result: dict[str, Any] = {"base_url": _base(), "runtime": ollama_runtime.status(), "suggested": SUGGESTED}
    try:
        async with _client() as client:
            tags = (await client.get(f"{_base()}/api/tags")).json()
            try:
                running = (await client.get(f"{_base()}/api/ps")).json().get("models") or []
            except (httpx.HTTPError, ValueError):
                running = []
            version = (await client.get(f"{_base()}/api/version")).json().get("version")
    except (httpx.HTTPError, ValueError) as exc:
        result.update(reachable=False, error=f"{type(exc).__name__}: {exc}"[:300], models=[])
        return result
    loaded = {m.get("name") for m in running}
    models = []
    for item in tags.get("models") or []:
        details = item.get("details") or {}
        models.append({
            "name": item.get("name"), "size": item.get("size"), "modified_at": item.get("modified_at"),
            "family": details.get("family"), "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"), "loaded": item.get("name") in loaded,
        })
    result.update(reachable=True, version=version, models=models)
    return result


@router.get("/models/{name:path}/info")
async def model_info(name: str) -> dict[str, Any]:
    name = _check_name(name)
    async with _client() as client:
        response = await client.post(f"{_base()}/api/show", json={"model": name})
    if response.status_code != 200:
        raise HTTPException(response.status_code if response.status_code < 500 else 502, response.text[:300])
    data = response.json()
    return {"name": name, "capabilities": data.get("capabilities") or [], "details": data.get("details") or {},
            "context_length": next((v for k, v in (data.get("model_info") or {}).items() if k.endswith(".context_length")), None)}


@router.post("/pull", status_code=202)
async def pull(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = _check_name(str(payload.get("name") or ""))
    for job in _jobs.values():
        if job.name == name and job.state == "running":
            return job.view()
    job = PullJob(name)
    _jobs[job.id] = job
    job.task = asyncio.create_task(_run_pull(job))
    # keep the job list short
    for old in sorted(_jobs.values(), key=lambda j: j.started)[:-20]:
        if old.state != "running":
            _jobs.pop(old.id, None)
    return job.view()


@router.get("/pulls")
async def pulls() -> dict[str, Any]:
    return {"jobs": [j.view() for j in sorted(_jobs.values(), key=lambda j: j.started, reverse=True)]}


@router.delete("/pulls/{job_id}")
async def cancel_pull(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown pull job")
    if job.task and not job.task.done():
        job.task.cancel()
        try:
            await job.task
        except asyncio.CancelledError:
            pass
    return job.view()


@router.delete("/models/{name:path}")
async def delete_model(name: str) -> dict[str, Any]:
    name = _check_name(name)
    async with _client() as client:
        response = await client.request("DELETE", f"{_base()}/api/delete", json={"model": name})
    if response.status_code == 404:
        raise HTTPException(404, "model not found")
    if response.status_code != 200:
        raise HTTPException(502, response.text[:300])
    return {"deleted": name}
