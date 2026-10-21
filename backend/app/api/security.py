"""Local-admin guard for configuration endpoints (docs/config-ui-plan.md 4.6)."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from ..config import settings

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
ADMIN_HEADER = "x-ds-admin"


def allowed_origin_ports() -> set[int]:
    ports = {5173, int(settings.port)}
    for key in ("DS_FRONTEND_PORT",):
        try:
            ports.add(int(os.environ[key]))
        except (KeyError, ValueError):
            pass
    return ports


def is_local_origin(origin: str) -> bool:
    try:
        parts = urlsplit(origin)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    return (parts.scheme in {"http", "https"} and (parts.hostname or "") in LOOPBACK_HOSTS
            and port in allowed_origin_ports())


def admin_problem(request: Request) -> str | None:
    """Return a reason string when the request is NOT a local admin request."""
    client = request.client.host if request.client else ""
    if client not in LOOPBACK_HOSTS:
        return "configuration is only available from this computer (loopback)"
    origin = request.headers.get("origin")
    if origin is not None and not is_local_origin(origin):
        return "cross-origin configuration requests are not allowed"
    if origin is None and request.headers.get("sec-fetch-site") == "cross-site":
        return "cross-site configuration requests are not allowed"
    if request.headers.get(ADMIN_HEADER) != "1":
        return "missing X-DS-Admin header"
    return None


async def require_local_admin(request: Request) -> None:
    problem = admin_problem(request)
    if problem:
        raise HTTPException(status_code=403, detail=problem)


CORS_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
