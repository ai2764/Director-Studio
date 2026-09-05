from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    """Director-facing boundary for model discovery and selection."""

    provider_id: str

    async def list_models(self) -> list[str]: ...

    def model_status(self) -> dict: ...

    def select_model(self, model: str, *, persist: bool) -> str: ...
