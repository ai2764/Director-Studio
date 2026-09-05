from __future__ import annotations

import pytest

from app.core.vram import ollama_client as ollama_module
from app.core.vram.ollama_client import OllamaClient


@pytest.mark.asyncio
async def test_require_vision_does_not_fall_back_to_text(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    class VisionRejected(RuntimeError):
        pass

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def chat(self, **request):
            calls.append(request)
            raise VisionRejected(
                "Multimodal data provided, but model does not support multimodal requests."
            )

    monkeypatch.setattr(ollama_module, "AsyncClient", _Client)

    with pytest.raises(VisionRejected):
        await OllamaClient().chat(
            "ornith:35b",
            "inspect",
            images=["abc"],
            require_vision=True,
        )

    assert len(calls) == 1
    assert calls[0]["messages"][-1]["images"] == ["abc"]
