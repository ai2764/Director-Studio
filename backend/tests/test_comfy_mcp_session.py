import asyncio
import sys

import pytest

from app.config import settings
from app.integrations.comfy_mcp import ComfyMcpClient, ComfyMcpError, PersistentMcpToolSession


@pytest.mark.asyncio
async def test_missing_mcp_package_surfaces_as_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", None)
    client = ComfyMcpClient(session=PersistentMcpToolSession())
    with pytest.raises(ComfyMcpError, match="mcp"):
        await asyncio.wait_for(client.call_tool("upload_file", {}), 5)


class _FakeSession(PersistentMcpToolSession):
    def __init__(self):
        super().__init__()
        self.started: list[str] = []

    async def _serve(self, queue, url=None):
        self.started.append(url)
        while True:
            name, _args, response = await queue.get()
            if name is None:
                response.set_result(None)
                return
            response.set_result(url)


@pytest.mark.asyncio
async def test_comfy_url_change_restarts_session(monkeypatch):
    monkeypatch.setattr(settings, "comfy_base_url", "http://127.0.0.1:8188")
    session = _FakeSession()
    assert await session.call_tool("job", {}) == "http://127.0.0.1:8188"
    assert await session.call_tool("job", {}) == "http://127.0.0.1:8188"
    monkeypatch.setattr(settings, "comfy_base_url", "http://127.0.0.1:9100")
    assert await session.call_tool("job", {}) == "http://127.0.0.1:9100"
    assert session.started == ["http://127.0.0.1:8188", "http://127.0.0.1:9100"]
    await session.aclose()
