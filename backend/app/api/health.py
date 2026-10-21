from fastapi import APIRouter

from ..config import settings
from ..core.comfy import ComfyClient
from ..core.llm import get_llm_provider
from ..core.schemas import HealthResponse

router = APIRouter(tags=["system"])


def _runtime_summary() -> dict:
    from ..core.comfy.runtime import comfy_runtime

    status = comfy_runtime.status()
    summary = {"mode": status["mode"], "pending_reconfigure": status["pending_reconfigure"]}
    if "managed" in status:
        summary["state"] = status["managed"]["state"]
    if "proxy" in status:
        summary["proxy_running"] = status["proxy"]["running"]
    if status.get("error"):
        summary["error"] = status["error"]
    return summary


def _ollama_summary(provider_id: str) -> dict:
    if provider_id == "llama-swap":
        from ..core.llm.llama_swap_runtime import llama_swap_runtime

        st = llama_swap_runtime.status()
        out = {"llama_swap_mode": st["mode"], "llama_swap_url": st["effective_url"]}
        if "managed" in st:
            out["llama_swap_state"] = st["managed"]["state"]
        if st.get("error"):
            out["llama_swap_error"] = st["error"]
        return out
    if provider_id != "ollama":
        return {}
    from ..core.llm.ollama_runtime import ollama_runtime

    status = ollama_runtime.status()
    summary = {"ollama_mode": status["mode"], "ollama_url": status["effective_url"]}
    if "managed" in status:
        summary["ollama_state"] = status["managed"]["state"]
    if status.get("error"):
        summary["ollama_error"] = status["error"]
    return summary


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
                **_ollama_summary(provider.provider_id),
            },
            "comfy_runtime": _runtime_summary(),
        },
    }
    if settings.video_context_enabled:
        payload["instance"] = "video-context"
        payload["capabilities"] = {"video_context": True}
    return HealthResponse(**payload)
