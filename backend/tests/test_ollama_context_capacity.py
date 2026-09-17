import pytest

from app.core.vram.ollama_client import OllamaClient


def test_ollama_requests_do_not_override_server_context_window():
    assert "num_ctx" not in OllamaClient()._opts()


@pytest.mark.asyncio
async def test_ollama_reads_context_capacity_from_matching_loaded_model(monkeypatch):
    client = OllamaClient()

    async def loaded_models():
        return [
            {"name": "other:latest", "context_length": 8192},
            {"name": "qwen:27b", "context_length": 131072},
        ]

    monkeypatch.setattr(client, "loaded_models", loaded_models)

    assert await client.context_capacity("qwen:27b") == 131072
    assert await client.context_capacity("missing:latest") is None
