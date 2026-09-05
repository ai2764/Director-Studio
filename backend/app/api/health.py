from fastapi import APIRouter

from ..core.comfy import ComfyClient
from ..core.schemas import HealthResponse
from ..core.vram.ollama_client import OllamaClient

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    comfy_ok = False
    comfy_error = None
    details: dict = {}
    try:
        details = await ComfyClient().health()
        comfy_ok = True
    except Exception as e:
        comfy_error = str(e)

    ollama_reachable = await OllamaClient().health()

    return HealthResponse(
        ok=True,
        comfy_reachable=comfy_ok,
        comfy_error=comfy_error,
        details={
            "comfy": details.get("system", {}) if details else {},
            "ollama_reachable": ollama_reachable,
        },
    )
