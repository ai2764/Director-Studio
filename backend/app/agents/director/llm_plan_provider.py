from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from contextlib import asynccontextmanager
from collections.abc import Iterable

from ...config import settings
from ...core.llm import LLMProvider, get_llm_provider
from .skill_loader import with_director_skill
from .context_metrics import observe_request
from ...core.prompt_errors import PromptContextOverflow, PromptOutputTruncated, is_context_overflow

PROMPT_CALL_TIMEOUT_SEC = 180.0


@lru_cache(maxsize=4)
def _vision_client(base_url: str, api_key: str | None, timeout: float):
    from ...core.llm import OpenAICompatibleClient
    return OpenAICompatibleClient(base_url, api_key, timeout)


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

    @property
    def vision_model(self) -> str:
        return settings.director_vision_model.strip() or self.model

    @property
    def vision_client(self):
        endpoint = settings.director_vision_base_url.strip()
        return (_vision_client(endpoint, settings.llm_api_key, settings.llm_timeout_sec)
                if endpoint else self.client)

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
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides, writer_only=True)
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
        if settings.director_compact_vision_prompt and system.startswith("Inspect exactly one reference image"):
            # Pixel extraction already has a complete provenance/JSON contract.
            # Tool, soundtrack and production instructions do not belong here.
            prompt = (f"{system}\n\nKeep the description under 120 words and return at most 8 salient facts. "
                      f"Use concise evidence; do not enumerate repetitive details.\n\n{user}")
            observe_request("writer.vision", [{"role": "user", "content": prompt}], image_count=len(images))
            from .material_review import ReferenceObservation
            schema = ReferenceObservation.model_json_schema()
            schema["properties"]["facts"]["maxItems"] = 8
            async with asyncio.timeout(150), self._input_budget(prompt, output_tokens=2048, images=True):
                response = await self.vision_client.chat_response(
                    self.vision_model, messages=[{"role": "user", "content": prompt, "images": images}],
                    format=schema, require_vision=True,
                    options={"num_predict": 2048, "temperature": 0.3})
            if (response.get("finish_reason") or response.get("done_reason")) in {"length", "max_tokens"}:
                raise PromptOutputTruncated("Visual observation exceeded 2048 tokens; no partial observation accepted")
            return str(response.get("content") or "")
        else:
            prompt = with_director_skill(f"{system}\n\n{user}", guides=guides, writer_only=True)
        observe_request("writer.vision", [{"role": "user", "content": prompt}], image_count=len(images))
        async with self._input_budget(prompt, images=True):
            return await self.vision_client.chat(
                self.vision_model, prompt, images=images, require_vision=True,
            )

    async def complete_bounded(self, system: str, user: str, *, max_tokens: int,
                               guides: Iterable[str] = (), schema: dict | None = None,
                               images: list[str] | None = None) -> str:
        """Stage budgets are floors; an increased Director budget also applies here."""
        output_budget = max(max_tokens, settings.director_num_predict)
        prompt = with_director_skill(f"{system}\n\n{user}", guides=guides, writer_only=True)
        message = {"role": "user", "content": prompt}
        if images:
            message["images"] = list(images)
        observe_request("writer.bounded", [message], image_count=len(images or []))
        deadline = min(PROMPT_CALL_TIMEOUT_SEC, settings.llm_timeout_sec)
        try:
            async with asyncio.timeout(deadline), self._input_budget(prompt, output_tokens=output_budget, images=bool(images)):
                client = self.vision_client if images else self.client
                model = self.vision_model if images else self.model
                response = await client.chat_response(model,
                    messages=[message], format=schema,
                    options={"num_predict": output_budget, "temperature": 0.1},
                    **({"require_vision": True} if images else {}))
        except TimeoutError as exc:
            # Transport failure is not a rejected creative draft: do not repair/retry it.
            raise TimeoutError(f"Prompt generation/review timed out after {deadline:g}s; "
                               "no completed result was saved and no automatic retry was started") from exc
        finish_reason = response.get("finish_reason") or response.get("done_reason") or "unknown"
        usage = response.get("usage") or {}
        output_tokens = usage.get("output_tokens", "unknown")
        reasoning_tokens = usage.get("reasoning_tokens", "unknown")
        logging.getLogger(__name__).info(
            "Writer bounded output: stage_budget=%s configured_budget=%s requested_budget=%s "
            "finish_reason=%s output_tokens=%s reasoning_tokens=%s content_chars=%s thinking_chars=%s",
            max_tokens, settings.director_num_predict, output_budget, finish_reason,
            output_tokens, reasoning_tokens, len(response.get("content") or ""),
            len(response.get("thinking") or ""))
        if finish_reason in {"length", "max_tokens"}:
            raise PromptOutputTruncated(
                "Prompt generation/review output was truncated: "
                f"requested_budget={output_budget}, output_tokens={output_tokens}, "
                f"reasoning_tokens={reasoning_tokens}, finish_reason={finish_reason}. "
                "Increase DS_DIRECTOR_NUM_PREDICT if the model context has room for input plus output; "
                "reasoning can consume the same output budget. No truncated result was accepted.")
        return str(response.get("content") or "")

    async def complete_bounded_with_images(self, system: str, user: str, *, images: list[str],
                                           max_tokens: int, guides=(), schema=None) -> str:
        return await self.complete_bounded(system, user, max_tokens=max_tokens,
                                           guides=guides, schema=schema, images=images)
