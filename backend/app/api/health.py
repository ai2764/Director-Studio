from fastapi import APIRouter

from ..config import settings
from ..core.comfy import ComfyClient
from ..core.llm import get_llm_provider
from ..core.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, response_model_exclude_unset=True)
async def health() -> HealthResponse:
    comfy_ok = False
    comfy_error = None
    details: dict = {}
    try:
        details = await ComfyClient().health()
        comfy_ok = True
    except Exception as e:
        comfy_error = str(e)

    provider = get_llm_provider()
    try:
        llm_reachable = await provider.client.health()
    except Exception:
        llm_reachable = False

    payload: dict = {
        "ok": True,
        "comfy_reachable": comfy_ok,
        "comfy_error": comfy_error,
        "details": {
            "comfy": details.get("system", {}) if details else {},
            "llm": {
                "provider": provider.provider_id,
                "reachable": llm_reachable,
            },
        },
    }
    if settings.video_context_enabled:
        payload["instance"] = "video-context"
        payload["capabilities"] = {"video_context": True}
    return HealthResponse(**payload)
