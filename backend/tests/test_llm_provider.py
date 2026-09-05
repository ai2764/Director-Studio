from __future__ import annotations

import pytest

from app.api import director


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
    def __init__(self, available: list[str]) -> None:
        super().__init__()
        self.available = available
        self.current = ""

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
