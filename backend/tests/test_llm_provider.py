from __future__ import annotations

import pytest

from app.api import director
from app.config import settings
from app.core.llm import get_llm_provider, reset_llm_provider


class FakeProvider:
    provider_id = "fake-remote"

    def __init__(self) -> None:
        self.selected: tuple[str, bool] | None = None

    async def list_models(self) -> list[str]:
        return ["reasoner-large", "reasoner-fast"]

    def model_status(self) -> dict:
        return {"model": "reasoner-fast", "source": "provider"}

    def select_model(self, model: str, *, persist: bool) -> str:
        self.selected = (model, persist)
        return model


class EmptySelectionProvider(FakeProvider):
    def __init__(self, available: list[str], current: str = "") -> None:
        super().__init__()
        self.available = available
        self.current = current

    async def list_models(self) -> list[str]:
        return list(self.available)

    def model_status(self) -> dict:
        return {"model": self.current, "source": "env"}

    def select_model(self, model: str, *, persist: bool) -> str:
        self.selected = (model, persist)
        self.current = model
        return model


@pytest.mark.asyncio
async def test_director_model_catalog_comes_from_provider() -> None:
    result = await director.get_model(provider=FakeProvider())

    assert result == {
        "model": "reasoner-fast",
        "source": "provider",
        "provider": "fake-remote",
        "reachable": True,
        "available": ["reasoner-large", "reasoner-fast"],
    }


@pytest.mark.asyncio
async def test_director_model_selection_is_delegated_to_provider() -> None:
    provider = FakeProvider()
    result = await director.put_model(
        director.DirectorModelBody(model="reasoner-large", persist=False),
        provider=provider,
    )

    assert provider.selected == ("reasoner-large", False)
    assert result["model"] == "reasoner-large"
    assert result["provider"] == "fake-remote"


@pytest.mark.asyncio
async def test_director_selects_first_installed_model_when_unset() -> None:
    provider = EmptySelectionProvider(["local-first:latest", "local-second:latest"])

    result = await director.get_model(provider=provider)

    assert provider.selected == ("local-first:latest", True)
    assert result["model"] == "local-first:latest"
    assert result["available"] == ["local-first:latest", "local-second:latest"]


@pytest.mark.asyncio
async def test_director_leaves_model_empty_when_ollama_has_none() -> None:
    provider = EmptySelectionProvider([])

    result = await director.get_model(provider=provider)

    assert provider.selected is None
    assert result["model"] == ""
    assert result["available"] == []


@pytest.mark.asyncio
async def test_director_replaces_a_stale_model_with_first_available() -> None:
    provider = EmptySelectionProvider(
        ["current-first", "current-second"],
        current="removed-model",
    )

    result = await director.get_model(provider=provider)

    assert provider.selected == ("current-first", True)
    assert result["model"] == "current-first"


def test_factory_builds_one_active_lm_studio_provider(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "lm-studio")
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:1234/v1")
    monkeypatch.setattr(settings, "llm_api_key", None)
    reset_llm_provider()
    try:
        first = get_llm_provider()
        second = get_llm_provider()

        assert first is second
        assert first.provider_id == "lm-studio"
        assert first.lifecycle.uses_local_gpu is True
        assert first.lifecycle.release_failure_is_fatal is True
        assert first.client.base_url == "http://127.0.0.1:1234/v1"
    finally:
        reset_llm_provider()


def test_factory_defaults_generic_provider_to_openai_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "openai-compatible")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    reset_llm_provider()
    try:
        provider = get_llm_provider()

        assert provider.provider_id == "openai-compatible"
        assert provider.lifecycle.uses_local_gpu is False
        assert provider.client.base_url == "https://api.openai.com/v1"
    finally:
        reset_llm_provider()
