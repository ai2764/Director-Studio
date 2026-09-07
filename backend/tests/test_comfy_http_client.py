"""ComfyUI HTTP metadata boundary tests."""

from __future__ import annotations

import httpx
import pytest

from app.core.comfy.client import ComfyClient


@pytest.mark.asyncio
async def test_get_object_info_returns_complete_node_metadata(monkeypatch) -> None:
    payload = {
        "VHS_VideoCombine": {
            "display_name": "Video Combine",
            "output": ["VHS_FILENAMES"],
            "output_node": True,
        }
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/object_info"
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler), timeout=kwargs["timeout"])

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    assert await ComfyClient("http://comfy.test").get_object_info() == payload
