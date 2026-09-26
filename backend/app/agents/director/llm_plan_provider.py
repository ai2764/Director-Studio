from __future__ import annotations

import asyncio
from collections.abc import Iterable

from ...config import settings
from ...core.llm import LLMProvider, get_llm_provider
from .skill_loader import with_director_skill
from .context_metrics import observe_request

PROMPT_CALL_TIMEOUT_SEC = 180.0


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
        observe_request("writer.generate", [{"role": "user", "content": prompt}])
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
        observe_request("writer.vision", [{"role": "user", "content": prompt}], image_count=len(images))
        return await self.client.chat(
            self.model,
            prompt,
            images=images,
            require_vision=True,
        )

    async def complete_bounded(self, system: str, user: str, *, max_tokens: int,
                               guides: Iterable[str] = (), schema: dict | None = None) -> str:
        """Short prompt audits and candidate drafts have explicit output budgets."""
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        observe_request("writer.bounded", [{"role": "user", "content": prompt}])
        deadline = min(PROMPT_CALL_TIMEOUT_SEC, settings.llm_timeout_sec)
        try:
            async with asyncio.timeout(deadline):
                response = await self.client.chat_response(self.model,
                    messages=[{"role": "user", "content": prompt}], format=schema,
                    options={"num_predict": max_tokens, "temperature": 0.1})
        except TimeoutError as exc:
            # Transport failure is not a rejected creative draft: do not repair/retry it.
            raise TimeoutError(f"Prompt generation/review timed out after {deadline:g}s; "
                               "no completed result was saved and no automatic retry was started") from exc
        if response.get("finish_reason") in {"length", "max_tokens"}:
            raise ValueError("Prompt review output was truncated at its output budget")
        return str(response.get("content") or "")
