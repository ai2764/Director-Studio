from __future__ import annotations

from typing import Iterable

from ...core.llm import LLMProvider, get_llm_provider
from .skill_loader import with_director_skill


class DirectorLLMPlanProvider:
    """Director planning adapter backed by the configured active LLM provider."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        model: str | None = None,
    ) -> None:
        self.provider = provider or get_llm_provider()
        self._fixed_model = model
        self.client = self.provider.client

    @property
    def model(self) -> str:
        if self._fixed_model:
            return self._fixed_model
        return str(self.provider.model_status().get("model") or "").strip()

    async def complete(
        self,
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        return await self.client.generate(self.model, prompt)

    async def complete_with_images(
        self,
        system: str,
        user: str,
        *,
        images: list[str],
        guides: Iterable[str] = (),
    ) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        return await self.client.chat(
            self.model,
            prompt,
            images=images,
            require_vision=True,
        )
