"""Settings, setup wizard, doctor and ComfyUI runtime endpoints (local admin only)."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..core.comfy.runtime import comfy_runtime, is_loopback_url, preview_launch
from ..core.config_store import service
from ..core.system import control
from ..core.net_headers import HeaderParseError, auth_headers, parse_headers
from ..runtime_paths import runtime_paths
from ..settings_registry import schema
from .security import admin_problem, require_local_admin

router = APIRouter(tags=["config"])
admin = [Depends(require_local_admin)]


def _error(exc: service.ConfigError) -> JSONResponse:
    return JSONResponse({"detail": exc.message, "fields": exc.fields}, status_code=exc.status)


@router.get("/config", dependencies=admin)
async def get_config() -> dict[str, Any]:
    data = service.get_config()
    data["schema"] = schema()
    data["comfy_runtime"] = comfy_runtime.status()
    return data


@router.get("/config/schema", dependencies=admin)
async def get_schema() -> dict[str, Any]:
    return schema()


@router.put("/config", dependencies=admin)
async def put_config(payload: dict[str, Any] = Body(...)):
    try:
        return await service.put_config(str(payload.get("revision") or ""), payload.get("changes") or {})
    except service.ConfigError as exc:
        return _error(exc)


# ---- setup -------------------------------------------------------------------------

@router.get("/setup/state")
async def setup_state(request: Request) -> dict[str, Any]:
    state = service.setup_state()
    state["admin_allowed"] = admin_problem(request) is None
    return state


@router.post("/setup/complete", dependencies=admin)
async def setup_complete(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return service.complete_setup(skipped=bool((payload or {}).get("skipped")))


@router.get("/system/info", dependencies=admin)
async def system_info() -> dict[str, Any]:
    from ..main import APP_VERSION

    return {
        "version": APP_VERSION,
        "frozen": runtime_paths.frozen,
        "env_file": str(service.env_path()),
        "data_dir": str(settings.data_dir),
        "logs_dir": str(settings.data_dir / "logs"),
        "install_root": str(runtime_paths.install_root),
        # what this process actually serves on (the launcher may override DS_PORT/DS_HOST)
        "serving_host": settings.host,
        "serving_port": int(settings.port),
        "serving_url": f"http://{'127.0.0.1' if settings.host in ('0.0.0.0', '::', '') else settings.host}:{int(settings.port)}",
        "installed": runtime_paths.installed,
        "install_info": dict(runtime_paths.install_info),
        "restart_pending": sorted(service.restart_pending),
        "comfy_runtime": comfy_runtime.status(),
        **control.info(),
    }


@router.post("/system/restart", dependencies=admin)
async def system_restart(force: bool = False):
    """Restart Harness + backend (+ managed ComfyUI/Ollama) through the DS launcher."""
    if not control.supervisor_available():
        return JSONResponse(
            {"detail": "Director Studio was not started by the DS launcher; restart it manually.",
             "supervisor_available": False},
            status_code=409,
        )
    if not force and service.pipeline_busy():
        return JSONResponse({"detail": "A generation job is running; try again when it finishes.",
                             "busy": True}, status_code=409)
    control.request_restart()
    return JSONResponse({"restarting": True, "boot_id": control.BOOT_ID}, status_code=202)


# ---- doctor / comfy runtime ----------------------------------------------------------

@router.get("/doctor", dependencies=admin)
async def doctor() -> dict[str, Any]:
    from ..core.doctor import run_doctor

    return await run_doctor()


@router.get("/comfy/runtime", dependencies=admin)
async def comfy_status(log: bool = False) -> dict[str, Any]:
    from ..core.vram.shared_gpu import resolve_shared_gpu

    data = comfy_runtime.status()
    data["shared_gpu"] = resolve_shared_gpu().as_dict()
    if log and comfy_runtime.managed is not None:
        data["log_tail"] = comfy_runtime.managed.tail_log()
    return data


@router.post("/comfy/restart", dependencies=admin)
async def comfy_restart() -> dict[str, Any]:
    result = await comfy_runtime.reconfigure()
    return {"result": result, **comfy_runtime.status()}


@router.get("/comfy/launch-preview", dependencies=admin)
async def comfy_launch_preview() -> dict[str, Any]:
    return await asyncio.to_thread(preview_launch, settings)


# ---- draft connection tests --------------------------------------------------------

def _origin(url: str) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.hostname}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def _secret(draft: Any, saved: str | None, *, draft_url: str, saved_url: str, label: str) -> str | None:
    """Resolve a draft secret. ``{"secret":"keep"}`` reuses the saved value only for the same host."""
    if isinstance(draft, dict):
        action = draft.get("secret")
        if action == "keep":
            if not saved:
                return None
            if _origin(draft_url) != _origin(saved_url):
                raise HTTPException(400, f"{label}: the URL changed; re-enter the secret to test against a new host")
            return saved
        if action == "clear":
            return None
        if action == "set":
            return str(draft.get("value") or "")
        raise HTTPException(400, f"{label}: invalid secret action")
    if draft is None:
        return None
    return str(draft)


def _check_url(url: str, label: str) -> str:
    parts = urlsplit(url or "")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise HTTPException(400, f"{label} must be an http(s) URL")
    if parts.username or parts.password:
        raise HTTPException(400, f"{label}: credentials in URLs are not allowed")
    return url.rstrip("/")


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:300]


def _llm_target(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve the draft LLM connection (provider, base URL, secrets) from the wizard form."""
    provider = str(payload.get("provider") or settings.llm_provider)
    if provider == "ollama":
        mode = str(payload.get("ollama_mode") or settings.ollama_mode)
        if mode == "bundled":
            from ..core.llm.ollama_runtime import ollama_runtime

            if not ollama_runtime.bundled:
                raise HTTPException(409, "bundled Ollama is not running yet; save the settings first")
            base = ollama_runtime.effective_url
        else:
            base = _check_url(str(payload.get("ollama_base_url") or settings.ollama_base_url), "Ollama URL")
        saved_base = settings.ollama_base_url
    elif provider == "llama-swap" and str(payload.get("llama_swap_mode") or settings.llama_swap_mode) == "bundled":
        from ..core.llm.llama_swap_runtime import llama_swap_runtime

        base = llama_swap_runtime.base_url()
        if base is None:
            raise HTTPException(409, "built-in llama.cpp (llama-swap) is not running yet; save the settings first")
        saved_base = base
    else:
        defaults = {"lm-studio": "http://127.0.0.1:1234/v1", "llama-swap": "http://127.0.0.1:11435/v1",
                    "openai-compatible": "https://api.openai.com/v1"}
        if provider not in defaults:
            raise HTTPException(400, "unknown provider")
        base = _check_url(str(payload.get("base_url") or defaults[provider]), "Base URL")
        saved_base = settings.llm_base_url or defaults.get(settings.llm_provider, "")
    api_key = _secret(payload.get("api_key", {"secret": "keep"}), settings.llm_api_key,
                      draft_url=base, saved_url=saved_base, label="API key")
    raw_headers = _secret(payload.get("extra_headers", {"secret": "keep"}), settings.llm_extra_headers,
                          draft_url=base, saved_url=saved_base, label="Extra headers")
    try:
        headers = parse_headers(raw_headers)
    except HeaderParseError as exc:
        raise HTTPException(400, f"extra headers: {exc}") from None
    return {"provider": provider, "base": base, "api_key": api_key, "headers": headers}


@router.post("/config/test/llm", dependencies=admin)
async def test_llm(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    started = time.monotonic()
    try:
        target = _llm_target(payload)
        provider, base, api_key, headers = target["provider"], target["base"], target["api_key"], target["headers"]
        if provider == "ollama":
            from ..core.vram.ollama_client import OllamaClient

            client = OllamaClient(base, timeout=15.0, headers=headers)
            models = await asyncio.wait_for(client.list_models(), 20)
        else:
            from ..core.llm.openai_compatible import OpenAICompatibleClient

            client = OpenAICompatibleClient(base_url=base, api_key=api_key, timeout=15.0, extra_headers=headers)
            try:
                models = await asyncio.wait_for(client.list_models(), 20)
            finally:
                await client.close()
        return {"ok": True, "models": list(models)[:300], "latency_ms": int((time.monotonic() - started) * 1000)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _safe_error(exc), "latency_ms": int((time.monotonic() - started) * 1000)}


@router.post("/config/test/llm-capabilities", dependencies=admin)
async def test_llm_capabilities(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Probe tool calling + vision of ``payload.model`` on the draft LLM connection."""
    from ..core.llm import capabilities

    model = str(payload.get("model") or "").strip()
    if not model or len(model) > 200:
        raise HTTPException(400, "choose a model first")
    target = _llm_target(payload)
    runtime = str(payload.get("agent_runtime") or settings.director_agent_runtime)
    timeout = min(600.0, float(payload.get("timeout_sec") or 180.0))
    try:
        if target["provider"] == "ollama":
            result = await capabilities.probe_ollama(target["base"], model, headers=target["headers"], timeout=timeout)
        else:
            result = await capabilities.probe_openai(target["base"], model, api_key=target["api_key"],
                                                     headers=target["headers"], timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _safe_error(exc)}
    result["ok"] = True
    result["provider"] = target["provider"]
    return capabilities.summarize(result, runtime)


async def _probe_comfy(base: str) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    result: dict[str, Any] = {"ok": False}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), trust_env=False,
                                 follow_redirects=False) as client:
        response = await client.get(f"{base}/system_stats")
        if response.status_code != 200:
            result["error"] = f"HTTP {response.status_code} from /system_stats"
            return result
        stats = response.json()
    system = stats.get("system") or {}
    result["system"] = {
        "comfyui_version": system.get("comfyui_version"),
        "python_version": str(system.get("python_version", ""))[:20],
        "devices": [{"name": d.get("name"), "type": d.get("type"), "vram_total": d.get("vram_total"),
                     "vram_free": d.get("vram_free")} for d in stats.get("devices") or []],
    }
    ws_url = "ws" + base[4:] + "/ws?clientId=ds-config-test"
    try:
        async with connect(ws_url, open_timeout=10, origin=None, proxy=None) as ws:
            first = await asyncio.wait_for(ws.recv(), 10)
            result["ws_ok"] = bool(first)
    except Exception as exc:  # noqa: BLE001
        result["ws_ok"] = False
        result["ws_error"] = _safe_error(exc)
    result["ok"] = True
    return result


@router.post("/config/test/comfy", dependencies=admin)
async def test_comfy(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    mode = str(payload.get("mode") or settings.comfy_mode)
    try:
        if mode == "external-local":
            base = _check_url(str(payload.get("base_url") or ""), "ComfyUI URL")
            if not is_loopback_url(base):
                raise HTTPException(400, "local mode only accepts loopback URLs; use remote mode")
            return await _probe_comfy(base)
        if mode == "managed":
            preview = await asyncio.to_thread(preview_launch, settings)
            result: dict[str, Any] = {"ok": bool(preview.get("ok")), "preview": preview}
            if comfy_runtime.mode == "managed" and comfy_runtime.managed is not None:
                result["managed"] = comfy_runtime.managed.status()
                if comfy_runtime.managed.state == "ready":
                    result.update(await _probe_comfy(settings.comfy_base_url))
            return result
        if mode == "remote":
            from ..core.comfy.remote_proxy import RemoteComfyProxy, RemoteUrlError, normalize_remote_url

            try:
                remote = normalize_remote_url(str(payload.get("remote_url") or ""))
            except RemoteUrlError as exc:
                raise HTTPException(400, str(exc)) from None
            auth_type = str(payload.get("auth_type") or "none")
            secret = _secret(payload.get("auth", {"secret": "keep"}), settings.comfy_remote_auth,
                             draft_url=remote, saved_url=settings.comfy_remote_url, label="Remote credentials")
            try:
                headers = auth_headers(auth_type, secret)
            except HeaderParseError as exc:
                raise HTTPException(400, str(exc)) from None
            proxy = RemoteComfyProxy(remote_url=remote, headers=headers,
                                     verify_tls=bool(payload.get("verify_tls", True)), read_timeout=30)
            await proxy.start()
            try:
                result = await _probe_comfy(proxy.url)
            finally:
                await proxy.stop()
            result["via_proxy"] = True
            return result
        raise HTTPException(400, "unknown mode")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": _safe_error(exc)}


@router.post("/config/test/minimax", dependencies=admin)
async def test_minimax(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    base = _check_url(str(payload.get("base_url") or settings.h3_minimax_base_url), "MiniMax URL")
    key = _secret(payload.get("api_key", {"secret": "keep"}), settings.h3_minimax_api_key,
                  draft_url=base, saved_url=settings.h3_minimax_base_url, label="MiniMax key")
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "API key is empty"}
    if any(ch.isspace() for ch in key) or len(key) < 20:
        return {"ok": False, "error": "API key format looks wrong (too short or contains spaces)"}
    # Free check: query a non-existent task. This never creates a generation, so it costs nothing.
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.get(f"{base}/v2/query/video_generation/ds-key-check-0",
                                        headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"cannot reach MiniMax: {_safe_error(exc)}"}
    status_code = None
    try:
        body = response.json()
        status_code = ((body or {}).get("base_resp") or {}).get("status_code")
    except ValueError:
        body = None
    if response.status_code in {401, 403} or status_code in {1004, 2049}:
        return {"ok": False, "error": "MiniMax rejected the API key", "http_status": response.status_code}
    return {
        "ok": True,
        "http_status": response.status_code,
        "note": "Key accepted by a free task-query call (no video generated, no cost). "
                "Balance/quota is not verified.",
        "note_zh": "已通过免费的任务查询接口验证（未生成视频，不扣费）；未校验余额/额度。",
    }
