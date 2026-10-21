"""Parsing helpers for user-supplied HTTP headers (LLM extra headers, remote Comfy auth)."""

from __future__ import annotations

import base64
import json
import re

_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORBIDDEN = {"host", "content-length", "transfer-encoding", "connection", "upgrade"}


class HeaderParseError(ValueError):
    pass


def parse_headers(text: str | None) -> dict[str, str]:
    """Parse a JSON object or ``Key: value`` / ``Key=value`` lines."""
    raw = (text or "").strip()
    if not raw:
        return {}
    pairs: list[tuple[str, str]] = []
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise HeaderParseError(f"invalid JSON: {exc.msg}") from None
        if not isinstance(data, dict):
            raise HeaderParseError("headers JSON must be an object")
        pairs = [(str(k), str(v)) for k, v in data.items()]
    else:
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = ":" if ":" in line and (("=" not in line) or line.index(":") < line.index("=")) else "="
            if sep not in line:
                raise HeaderParseError("each line must be 'Key: value' or 'Key=value'")
            key, value = line.split(sep, 1)
            pairs.append((key.strip(), value.strip()))
    headers: dict[str, str] = {}
    for key, value in pairs:
        if not _TOKEN.match(key):
            raise HeaderParseError(f"invalid header name: {key!r}")
        if key.lower() in _FORBIDDEN:
            raise HeaderParseError(f"header {key} cannot be overridden")
        if any(ch in value for ch in "\r\n\x00"):
            raise HeaderParseError(f"header {key} contains control characters")
        headers[key] = value
    return headers


def header_names_hint(text: str | None) -> str:
    try:
        return ", ".join(parse_headers(text))
    except HeaderParseError:
        return "(invalid)"


def auth_headers(auth_type: str, secret: str | None) -> dict[str, str]:
    """Build upstream auth headers for remote ComfyUI."""
    secret = (secret or "").strip()
    if auth_type == "none" or not auth_type:
        return {}
    if not secret:
        raise HeaderParseError("credentials are empty")
    if auth_type == "bearer":
        token = secret.removeprefix("Bearer ").strip()
        if any(ch in token for ch in "\r\n\x00 "):
            raise HeaderParseError("bearer token must not contain spaces or control characters")
        return {"Authorization": f"Bearer {token}"}
    if auth_type == "basic":
        if ":" not in secret:
            raise HeaderParseError("basic credentials must be user:password")
        return {"Authorization": "Basic " + base64.b64encode(secret.encode("utf-8")).decode("ascii")}
    if auth_type == "headers":
        return parse_headers(secret)
    raise HeaderParseError(f"unknown auth type {auth_type}")
