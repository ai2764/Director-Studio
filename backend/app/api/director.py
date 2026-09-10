"""Director agent control endpoints (wake / VRAM)."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..agents.director.context_io import load_agent_context
from ..config import settings
from ..core.llm import LLMProvider, get_llm_provider
from ..core.vram import get_director_model, get_orchestrator

logger = logging.getLogger("director_studio.api.director")

router = APIRouter(tags=["director"])


class WakeBody(BaseModel):
    project_id: str | None = None
    keep: bool = Field(
        default=False,
        description="If true, leave LLM loaded; otherwise release after warm.",
    )
    reload_context: bool = Field(
        default=True,
        description="If project_id set, load agent context and ping the model with it.",
    )


class WakeResponse(BaseModel):
    ok: bool
    provider: str
    model: str
    project_id: str | None = None
    last_phase: str | None = None
    agent_reply: str | None = None
    llm_released: bool = True


class DirectorModelBody(BaseModel):
    model: str = Field(
        ...,
        min_length=1,
        description="Model identifier from the active provider catalog.",
    )
    persist: bool = Field(
        default=True,
        description="Write choice to data/director_model.json (survives process restart).",
    )


@router.get("/director/model")
async def get_model(provider: LLMProvider = Depends(get_llm_provider)) -> dict:
    """Current Director LLM and the active provider's model catalog."""
    status = provider.model_status()
    reachable = True
    try:
        available = await provider.list_models()
    except Exception as e:
        logger.warning("list %s models failed: %s", provider.provider_id, e)
        available = []
        reachable = False
    selected = str(status.get("model") or "").strip()
    if reachable and available and selected not in available:
        provider.select_model(available[0], persist=True)
        status = provider.model_status()
    return {
        **status,
        "provider": provider.provider_id,
        "reachable": reachable,
        "available": available,
    }


@router.put("/director/model")
async def put_model(
    body: DirectorModelBody,
    provider: LLMProvider = Depends(get_llm_provider),
) -> dict:
    """Hot-switch Director plan model without restarting the backend."""
    try:
        name = provider.select_model(body.model, persist=body.persist)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        **provider.model_status(),
        "provider": provider.provider_id,
        "model": name,
    }


@router.post("/director/wake", response_model=WakeResponse)
async def wake_director(body: WakeBody | None = None) -> WakeResponse:
    """
    Prepare the active Director LLM after Comfy generation.

    Use before the next plan / rewrite_prompt turn when VRAM was given to Comfy.
    """
    body = body or WakeBody()
    orch = get_orchestrator()
    provider = getattr(orch, "provider", None) or get_llm_provider()
    model = str(provider.model_status().get("model") or "").strip()
    agent_reply: str | None = None
    last_phase: str | None = None

    try:
        async with orch.llm_session(release_on_exit=not body.keep):
            await orch.ensure_llm_ready()
            if body.project_id and body.reload_context:
                ctx = load_agent_context(body.project_id)
                if ctx is None:
                    raise HTTPException(404, f"no agent context for {body.project_id}")
                last_phase = ctx.last_phase
                import json

                summary = json.dumps(
                    {
                        "project_id": ctx.project_id,
                        "last_phase": ctx.last_phase,
                        "shot_ids": [s.get("id") for s in ctx.shot_summaries[:12]],
                    },
                    ensure_ascii=False,
                )
                agent_reply = await provider.client.generate(
                    model,
                    "You are the Director Studio local agent. "
                    "Acknowledge context reload in one short sentence.\n"
                    f"CONTEXT:\n{summary}\n",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wake_director failed")
        raise HTTPException(503, f"wake failed: {e}") from e

    return WakeResponse(
        ok=True,
        provider=provider.provider_id,
        model=model,
        project_id=body.project_id,
        last_phase=last_phase,
        agent_reply=(agent_reply or "").strip() or None,
        llm_released=not body.keep,
    )


@router.get("/director/vram")
async def vram_status() -> dict:
    orch = get_orchestrator()
    provider = getattr(orch, "provider", None)
    model = (
        str(provider.model_status().get("model") or "").strip()
        if provider is not None
        else get_director_model()
    )
    reservations = await orch.generation_reservations()
    ollama_vram = 0
    ollama_loaded: list[dict] = []
    llm_runtime: dict = {
        "provider": getattr(provider, "provider_id", "ollama"),
        "model": model,
        "ready": bool(orch._llm_ready),
        "loaded_instances": [],
    }
    if provider is not None:
        try:
            llm_runtime = await provider.lifecycle.status(model)
        except Exception as exc:
            llm_runtime = {
                **llm_runtime,
                "ready": False,
                "error": str(exc),
            }
        orch._llm_ready = bool(llm_runtime.get("ready"))
        if provider.provider_id == "ollama":
            ollama_vram = int(llm_runtime.get("size_vram") or 0)
            ollama_loaded = list(llm_runtime.get("loaded_instances") or [])
    else:
        # Compatibility for older injected orchestrators.
        try:
            ollama_loaded = await orch.ollama.loaded_models()
            ollama_vram = await orch.ollama.model_vram_bytes(model)
        except Exception:
            pass
        llm_runtime.update(
            {
                "ready": ollama_vram > 0,
                "size_vram": ollama_vram,
                "loaded_instances": ollama_loaded,
            }
        )
    # Legacy injected Ollama orchestrators do not expose lifecycle status.
    if provider is None:
        orch._llm_ready = ollama_vram > 0
    return {
        "provider": llm_runtime.get("provider", "ollama"),
        "owner": orch.owner,
        "comfy_pipeline": orch.comfy_pipeline,
        "policy": orch.policy,
        "models": list(orch.models),
        "model": model,
        "llm_runtime": llm_runtime,
        "llm_ready": orch._llm_ready,
        "llm_keep_loaded": bool(getattr(settings, "llm_keep_loaded", True)),
        "ollama_size_vram": ollama_vram,
        "ollama_on_gpu": ollama_vram > 0,
        "ollama_ps": [
            {
                "name": m.get("name"),
                "size": m.get("size"),
                "size_vram": m.get("size_vram"),
            }
            for m in ollama_loaded
        ],
        "queue_waiters": getattr(orch, "_waiters", 0),
        "chat_locked": bool(reservations),
        "generation_count": len(reservations),
        "generation_jobs": [asdict(item) for item in reservations],
        "acquire_timeout_sec": orch.acquire_timeout_sec,
        "last_comfy_free": getattr(orch, "last_comfy_free", None),
        "last_comfy_free_error": getattr(orch, "last_comfy_free_error", None),
    }


@router.post("/director/free-comfy")
async def free_comfy_models() -> dict:
    """Force ComfyUI unload_models + free_memory (same as Plan does)."""
    orch = get_orchestrator()
    try:
        stats = await orch.release_comfy_models(require_ok=True)
    except Exception as e:
        raise HTTPException(503, f"Comfy free failed: {e}") from e
    return {"ok": True, "stats": stats}
