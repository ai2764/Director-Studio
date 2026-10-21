"""DS_LLM_EXTRA_HEADERS support for OpenAI-compatible and Ollama clients."""

from __future__ import annotations

import logging

from ...config import settings
from ..net_headers import HeaderParseError, parse_headers

logger = logging.getLogger("director_studio.llm")


def llm_extra_headers(raw: str | None = None) -> dict[str, str]:
    value = settings.llm_extra_headers if raw is None else raw
    try:
        return parse_headers(value)
    except HeaderParseError as exc:
        logger.error("DS_LLM_EXTRA_HEADERS ignored: %s", exc)
        return {}
