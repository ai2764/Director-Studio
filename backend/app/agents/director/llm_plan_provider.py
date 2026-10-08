from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import Iterable

from ...config import settings
from ...core.llm import LLMProvider, get_llm_provider
from .skill_loader import with_director_skill
from .context_metrics import observe_request
from ...core.prompt_errors import PromptContextOverflow, PromptOutputTruncated, is_context_overflow

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

    @asynccontextmanager
    async def _input_budget(self, prompt: str, *, output_tokens: int | None = None, images=False):
        lifecycle = getattr(self.provider, "lifecycle", None)
        count = getattr(lifecycle, "prompt_token_count", None)
        capacity = getattr(lifecycle, "context_capacity", None)
        if not images and callable(count) and callable(capacity):
            try:
                async with asyncio.timeout(10):
                    window = await capacity(self.model)
                    tokens = await count(self.model, prompt) if window else None
            except Exception as exc:
                # Other server versions may lack tokenizer endpoints. Their
                # provider error is still classified below; never silently trim.
                logging.getLogger(__name__).warning("Writer token preflight unavailable: %s", type(exc).__name__)
            else:
                reserve = output_tokens if output_tokens is not None else settings.director_num_predict
                logging.getLogger(__name__).info("Writer input budget: input_tokens=%s output_reserve=%s context_window=%s",
                                                 tokens, reserve, window)
                if tokens is not None and tokens + reserve > window:
                    raise PromptContextOverflow(f"Input {tokens} tokens + output reserve {reserve} > window {window}.")
        try:
            yield
        except Exception as exc:
            if is_context_overflow(exc) and not isinstance(exc, PromptContextOverflow):
                raise PromptContextOverflow(str(exc)) from exc
            raise

    async def complete(
        self,
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str:
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        observe_request("writer.generate", [{"role": "user", "content": prompt}])
        async with self._input_budget(prompt):
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
        async with self._input_budget(prompt, images=True):
            return await self.client.chat(
                self.model, prompt, images=images, require_vision=True,
            )

    async def complete_bounded(self, system: str, user: str, *, max_tokens: int,
                               guides: Iterable[str] = (), schema: dict | None = None,
                               images: list[str] | None = None) -> str:
        """Short prompt audits and candidate drafts have explicit output budgets."""
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides)
        message = {"role": "user", "content": prompt}
        if images:
            message["images"] = list(images)
        observe_request("writer.bounded", [message], image_count=len(images or []))
        deadline = min(PROMPT_CALL_TIMEOUT_SEC, settings.llm_timeout_sec)
        try:
            async with asyncio.timeout(deadline), self._input_budget(prompt, output_tokens=max_tokens, images=bool(images)):
                response = await self.client.chat_response(self.model,
                    messages=[message], format=schema,
                    options={"num_predict": max_tokens, "temperature": 0.1},
                    **({"require_vision": True} if images else {}))
        except TimeoutError as exc:
            # Transport failure is not a rejected creative draft: do not repair/retry it.
            raise TimeoutError(f"Prompt generation/review timed out after {deadline:g}s; "
                               "no completed result was saved and no automatic retry was started") from exc
        if response.get("finish_reason") in {"length", "max_tokens"}:
            raise PromptOutputTruncated("Prompt review output was truncated at its output budget")
        return str(response.get("content") or "")

    async def complete_bounded_with_images(self, system: str, user: str, *, images: list[str],
                                           max_tokens: int, guides=(), schema=None) -> str:
        return await self.complete_bounded(system, user, max_tokens=max_tokens,
                                           guides=guides, schema=schema, images=images)
