"""Loopback reverse proxy for a remote ComfyUI (e.g. behind a Cloudflare tunnel).

comfy-mcp / comfy-cli only talk to plain-http loopback ComfyUI URLs and cannot
send auth headers, so DS exposes ``http://127.0.0.1:<port>`` and forwards to the
remote base URL, injecting auth headers.

Written for Director Studio (Apache-2.0). The overall structure (httpx streaming
request/response + bidirectional WebSocket pump) follows the public design of
WSH032/fastapi-proxy-lib (Apache-2.0) and valohai/asgiproxy (MIT); no code was
copied from GPL/AGPL projects.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger("director_studio.comfy_proxy")

HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
    "transfer-encoding", "upgrade", "host",
})
STRIP_REQUEST = HOP_BY_HOP | {"origin", "referer", "cookie"}
# ComfyUI-Manager and other admin surfaces DS must never drive remotely.
BLOCKED_PREFIXES = (
    "manager", "customnode", "snapshot", "externalmodel", "comfyui_manager", "internal", "v2/manager",
    "v2/customnode", "v2/snapshot", "v2/externalmodel", "v2/comfyui_manager",
)


class RemoteUrlError(ValueError):
    pass


def normalize_remote_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise RemoteUrlError("remote URL must be http(s)://host[:port][/prefix]")
    if parts.username or parts.password:
        raise RemoteUrlError("do not put credentials in the URL; use the auth settings")
    if parts.query or parts.fragment:
        raise RemoteUrlError("remote URL must not contain a query or fragment")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def is_blocked_path(path: str) -> bool:
    clean = path.lstrip("/").lower()
    if clean.startswith("api/"):
        clean = clean[4:]
    return any(clean == p or clean.startswith(p + "/") for p in BLOCKED_PREFIXES)


@dataclass
class ProxyStats:
    http_requests: int = 0
    ws_sessions: int = 0
    blocked: int = 0
    upstream_errors: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class RemoteComfyProxy:
    remote_url: str
    headers: dict[str, str] = field(default_factory=dict)
    verify_tls: bool = True
    port: int = 0
    connect_timeout: float = 10.0
    read_timeout: float = 600.0
    require_headers: dict[str, str] | None = None  # dev/test only: act as an auth gate
    stats: ProxyStats = field(default_factory=ProxyStats)

    def __post_init__(self) -> None:
        self.remote_url = normalize_remote_url(self.remote_url)
        self._server: Any = None
        self._serve_task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        self._sock: socket.socket | None = None
        self.app = Starlette(routes=[
            WebSocketRoute("/{path:path}", self._ws),
            Route("/{path:path}", self._http, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
        ])

    # ---- url helpers -----------------------------------------------------------
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def upstream_http(self, path: str, query: str) -> str:
        return f"{self.remote_url}/{path.lstrip('/')}" + (f"?{query}" if query else "")

    def upstream_ws(self, path: str, query: str) -> str:
        parts = urlsplit(self.upstream_http(path, query))
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.remote_url.startswith("https"):
            return None
        ctx = ssl.create_default_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _gate_ok(self, headers: Any) -> bool:
        if not self.require_headers:
            return True
        return all(headers.get(k) == v for k, v in self.require_headers.items())

    def _rewrite_location(self, value: str) -> str:
        if value.startswith(self.remote_url):
            return self.url + value[len(self.remote_url):]
        return value

    # ---- HTTP --------------------------------------------------------------------
    async def _http(self, request: Request) -> Response:
        path = request.path_params.get("path", "")
        if is_blocked_path(path):
            self.stats.blocked += 1
            return JSONResponse({"error": "blocked by Director Studio proxy"}, status_code=403)
        if not self._gate_ok(request.headers):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self.stats.http_requests += 1
        assert self._client is not None
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in STRIP_REQUEST]
        headers = [(k, v) for k, v in headers if k.lower() not in {h.lower() for h in self.headers}]
        headers += list(self.headers.items())
        body = request.stream() if request.method not in {"GET", "HEAD", "OPTIONS"} else None
        upstream = self._client.build_request(
            request.method, self.upstream_http(path, request.url.query), headers=headers, content=body,
        )
        try:
            response = await self._client.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            self.stats.upstream_errors += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"[:300]
            return JSONResponse({"error": "upstream unreachable", "detail": self.stats.last_error}, status_code=502)
        out_headers = []
        for key, value in response.headers.multi_items():
            lower = key.lower()
            if lower in HOP_BY_HOP or lower == "set-cookie":
                continue
            if lower == "location":
                value = self._rewrite_location(value)
            out_headers.append((key, value))

        async def body_iter():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()

        result = StreamingResponse(body_iter(), status_code=response.status_code)
        result.raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in out_headers]
        return result

    # ---- WebSocket ---------------------------------------------------------------
    async def _ws(self, websocket: WebSocket) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        path = websocket.path_params.get("path", "")
        if is_blocked_path(path) or not self._gate_ok(websocket.headers):
            self.stats.blocked += 1
            await websocket.close(code=4403)
            return
        target = self.upstream_ws(path, websocket.url.query)
        try:
            upstream = await connect(
                target, additional_headers=list(self.headers.items()), ssl=self._ssl_context(),
                max_size=None, open_timeout=self.connect_timeout, ping_interval=20, ping_timeout=60,
                origin=None, proxy=None,
            )
        except Exception as exc:  # noqa: BLE001
            self.stats.upstream_errors += 1
            self.stats.last_error = f"ws: {type(exc).__name__}: {exc}"[:300]
            await websocket.close(code=1011)
            return
        await websocket.accept()
        self.stats.ws_sessions += 1

        async def client_to_upstream() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
            except (WebSocketDisconnect, ConnectionClosed):
                pass
            finally:
                await upstream.close()

        async def upstream_to_client() -> None:
            try:
                async for data in upstream:
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(data)
            except (ConnectionClosed, RuntimeError):
                pass
            finally:
                try:
                    await websocket.close(code=upstream.close_code or 1000)
                except RuntimeError:
                    pass

        tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # ---- lifecycle ---------------------------------------------------------------
    async def start(self) -> None:
        import uvicorn

        self._client = httpx.AsyncClient(
            verify=self._ssl_context() or True, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(self.read_timeout, connect=self.connect_timeout),
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.listen(128)
        sock.setblocking(False)
        self._sock = sock
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(self.app, log_level="warning", lifespan="off", ws="websockets-sansio",
                                ws_max_size=64 * 1024 * 1024, proxy_headers=False, server_header=False)
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._serve_task = asyncio.create_task(self._server.serve(sockets=[sock]), name="comfy-remote-proxy")
        for _ in range(100):
            if self._server.started:
                break
            if self._serve_task.done():
                break
            await asyncio.sleep(0.05)
        logger.info("remote ComfyUI proxy %s -> %s", self.url, self.remote_url)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, 5)
            except (asyncio.TimeoutError, Exception):
                self._serve_task.cancel()
        if self._client is not None:
            await self._client.aclose()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._server = self._serve_task = self._client = self._sock = None

    def status(self) -> dict[str, Any]:
        return {"url": self.url, "remote": self.remote_url, "running": bool(self._server and self._server.started),
                "stats": self.stats.as_dict()}
