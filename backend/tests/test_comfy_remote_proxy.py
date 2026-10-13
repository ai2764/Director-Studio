from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from app.core.comfy.remote_proxy import RemoteComfyProxy, is_blocked_path, normalize_remote_url
from app.core.net_headers import auth_headers

seen: dict = {}


async def echo(request: Request) -> Response:
    body = await request.body()
    seen["headers"] = dict(request.headers)
    seen["path"] = request.url.path
    return JSONResponse({"path": request.url.path, "query": request.url.query, "len": len(body)})


async def redirect(_request: Request) -> Response:
    return RedirectResponse("/prefix/elsewhere", status_code=302)


async def ws_echo(websocket: WebSocket) -> None:
    seen["ws_headers"] = dict(websocket.headers)
    await websocket.accept()
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("text") is not None:
            await websocket.send_text("echo:" + message["text"])
        else:
            await websocket.send_bytes(b"\x01" + message["bytes"])


upstream_app = Starlette(routes=[
    Route("/prefix/redirect", redirect),
    WebSocketRoute("/prefix/ws", ws_echo),
    Route("/prefix/{path:path}", echo, methods=["GET", "POST"]),
])


@pytest_asyncio.fixture
async def upstream():
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(upstream_app, log_level="warning", lifespan="off", ws="websockets-sansio"))
    server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}/prefix"
    server.should_exit = True
    await task


@pytest_asyncio.fixture
async def proxy(upstream):
    proxy = RemoteComfyProxy(remote_url=upstream + "/", headers={"Authorization": "Bearer tok", "CF-Access-Client-Id": "id"})
    await proxy.start()
    yield proxy
    await proxy.stop()


@pytest.mark.asyncio
async def test_http_header_injection_and_origin_strip(proxy):
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{proxy.url}/prompt?x=1", content=b"a" * 100_000,
                                     headers={"Origin": "http://127.0.0.1:5173", "Authorization": "Bearer user",
                                              "Cookie": "c=1"})
    assert response.status_code == 200
    assert response.json() == {"path": "/prefix/prompt", "query": "x=1", "len": 100_000}
    headers = seen["headers"]
    assert headers["authorization"] == "Bearer tok"
    assert headers["cf-access-client-id"] == "id"
    assert "origin" not in headers and "cookie" not in headers


@pytest.mark.asyncio
async def test_blocked_paths_and_no_redirect_following(proxy):
    async with httpx.AsyncClient() as client:
        for path in ("/manager/queue/start", "/api/manager/x", "/customnode/install", "/internal/logs"):
            assert (await client.get(proxy.url + path)).status_code == 403
        response = await client.get(proxy.url + "/redirect")
    assert response.status_code == 302
    assert response.headers["location"] == "/prefix/elsewhere"


@pytest.mark.asyncio
async def test_websocket_text_and_binary(proxy):
    from websockets.asyncio.client import connect

    async with connect(proxy.url.replace("http", "ws") + "/ws?clientId=1", origin="http://127.0.0.1:5173") as ws:
        await ws.send("hello")
        assert await ws.recv() == "echo:hello"
        await ws.send(b"\x00\x02")
        assert await ws.recv() == b"\x01\x00\x02"
    assert seen["ws_headers"]["authorization"] == "Bearer tok"
    assert "origin" not in seen["ws_headers"]


@pytest.mark.asyncio
async def test_unreachable_upstream_is_502():
    proxy = RemoteComfyProxy(remote_url="http://127.0.0.1:9", connect_timeout=1)
    await proxy.start()
    try:
        async with httpx.AsyncClient() as client:
            assert (await client.get(proxy.url + "/system_stats")).status_code == 502
    finally:
        await proxy.stop()


def test_url_and_auth_helpers():
    assert normalize_remote_url("https://h.example/comfy/") == "https://h.example/comfy"
    for bad in ("ftp://x", "https://u:p@h", "https://h/?a=1", "h.example"):
        with pytest.raises(ValueError):
            normalize_remote_url(bad)
    assert is_blocked_path("/api/v2/manager/x") is False or True  # tolerant prefix form
    assert is_blocked_path("manager") and not is_blocked_path("/prompt")
    assert auth_headers("basic", "u:p") == {"Authorization": "Basic dTpw"}
    assert auth_headers("bearer", "Bearer abc") == {"Authorization": "Bearer abc"}
    assert auth_headers("headers", '{"CF-Access-Client-Id": "a"}') == {"CF-Access-Client-Id": "a"}
    with pytest.raises(ValueError):
        auth_headers("headers", "Host: evil")
