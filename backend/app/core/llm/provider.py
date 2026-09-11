from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Literal, Protocol, TypedDict


class LLMResult(TypedDict, total=False):
    content: str
    thinking: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str


class UnsupportedLLMFeatureError(RuntimeError):
    def __init__(
        self,
        feature: Literal["tools", "response_format", "vision"],
        message: str,
    ) -> None:
        self.feature = feature
        super().__init__(message)


class LLMClient(Protocol):
    async def list_models(self) -> list[str]: ...

    async def health(self) -> bool: ...

    async def generate(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> str: ...

    async def chat(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> str: ...

    async def chat_response(
        self,
        model: str,
        *,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        format: dict[str, Any] | str | None = None,
        require_vision: bool = False,
        **kwargs: Any,
    ) -> LLMResult: ...

    def generate_stream(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, str]]: ...


class LLMLifecycle(Protocol):
    uses_local_gpu: bool
    release_failure_is_fatal: bool

    async def prepare(
        self,
        model: str,
        on_status: Callable[[str], Any] | None = None,
    ) -> None: ...

    async def release(self, models: Sequence[str]) -> None: ...

    async def status(self, model: str) -> dict[str, Any]: ...


class LLMProvider(Protocol):
    """Complete Director-facing LLM boundary."""

    provider_id: str
    client: LLMClient
    lifecycle: LLMLifecycle

    async def list_models(self) -> list[str]: ...

    def model_status(self) -> dict: ...

    def select_model(self, model: str, *, persist: bool) -> str: ...
