from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ...config import settings
from ..vram.director_model import model_status, set_director_model
from .lifecycle import LMStudioLifecycle, RemoteLifecycle
from .ollama import OllamaLLMProvider
from .openai_compatible import OpenAICompatibleClient
from .provider import LLMClient, LLMLifecycle


@dataclass
class ActiveProvider:
    provider_id: str
    client: LLMClient
    lifecycle: LLMLifecycle
    catalog: Callable[[], Awaitable[list[str]]]

    async def list_models(self) -> list[str]:
        return await self.catalog()

    def model_status(self) -> dict[str, Any]:
        return model_status(self.provider_id)

    def select_model(self, model: str, *, persist: bool) -> str:
        return set_director_model(
            model,
            provider_id=self.provider_id,
            persist=persist,
        )


@lru_cache(maxsize=1)
def get_llm_provider():
    provider_id = settings.llm_provider
    if provider_id == "ollama":
        return OllamaLLMProvider()

    base_url = settings.llm_base_url.strip()
    if provider_id == "lm-studio":
        base_url = base_url or "http://127.0.0.1:1234/v1"
    else:
        base_url = base_url or "https://api.openai.com/v1"
    client = OpenAICompatibleClient(
        base_url=base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_sec,
    )
    if provider_id == "lm-studio":
        lifecycle = LMStudioLifecycle(
            base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_sec,
        )
        catalog = lifecycle.list_models
    else:
        lifecycle = RemoteLifecycle(client, provider_id=provider_id)
        catalog = client.list_models
    return ActiveProvider(
        provider_id=provider_id,
        client=client,
        lifecycle=lifecycle,
        catalog=catalog,
    )


def reset_llm_provider() -> None:
    get_llm_provider.cache_clear()
