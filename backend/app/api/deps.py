"""Workflow dependency status and downloads (local admin only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..core.comfy.runtime import comfy_runtime
from ..core.deps.manager import DepsError, deps_manager
from .security import require_local_admin

router = APIRouter(tags=["deps"])
admin = [Depends(require_local_admin)]


@router.get("/deps", dependencies=admin)
async def deps_status() -> dict[str, Any]:
    return await deps_manager.status()


@router.post("/deps/install", dependencies=admin)
async def deps_install(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        if payload.get("workflow"):
            jobs = await deps_manager.start_workflow(str(payload["workflow"]))
        else:
            jobs = [deps_manager.start(str(item.get("kind")), str(item.get("id"))) for item in payload.get("items") or []]
    except DepsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"jobs": [j.as_dict() for j in jobs]}


@router.post("/deps/cancel", dependencies=admin)
async def deps_cancel(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"cancelled": deps_manager.cancel(str(payload.get("kind")), str(payload.get("id")))}


@router.post("/deps/restart-comfy", dependencies=admin)
async def deps_restart_comfy() -> dict[str, Any]:
    if comfy_runtime.managed is None:
        raise HTTPException(status_code=409, detail="only the managed ComfyUI can be restarted by DS")
    result = await comfy_runtime.reconfigure()
    deps_manager.restart_needed = False
    return {"result": result, **comfy_runtime.status()}
