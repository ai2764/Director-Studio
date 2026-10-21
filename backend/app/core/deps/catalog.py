"""Typed access to ``catalog.json`` - the single source of truth for workflow dependencies.

DS ships no third-party models and (from the downloader's point of view) no third-party node
packs: users fetch each item from its original publisher, under that publisher's licence.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

CATALOG_PATH = Path(__file__).with_name("catalog.json")
HF_ENDPOINT = "https://huggingface.co"


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def node_packs() -> list[dict[str, Any]]:
    return list(load()["node_packs"])


def models() -> list[dict[str, Any]]:
    return list(load()["models"])


def llm_models() -> list[dict[str, Any]]:
    """Recommended GGUF director LLMs for the built-in llama.cpp (llama-swap)."""
    return list(load().get("llm_models", []))


def workflows() -> list[dict[str, Any]]:
    return list(load()["workflows"])


def pack(pack_id: str) -> dict[str, Any] | None:
    return next((p for p in load()["node_packs"] if p["id"] == pack_id), None)


def model(file: str) -> dict[str, Any] | None:
    return next((m for m in [*load()["models"], *load().get("llm_models", [])] if m["file"] == file), None)


def workflow(file: str) -> dict[str, Any] | None:
    return next((w for w in load()["workflows"] if w["file"] == file), None)


def pack_for_class(class_type: str) -> dict[str, Any] | None:
    return next((p for p in load()["node_packs"] if class_type in p["provides"]), None)


def hf_source(entry: dict[str, Any]) -> dict[str, Any] | None:
    return next((s for s in entry["sources"] if s["kind"] == "hf"), None)


def model_urls(entry: dict[str, Any], *, hf_endpoint: str = "") -> list[str]:
    """Download URLs in preference order (pinned HF revision, optionally via a mirror, then ModelScope)."""
    urls: list[str] = []
    for src in entry["sources"]:
        if src["kind"] == "hf":
            path = quote(src["path"])
            base = (hf_endpoint or HF_ENDPOINT).rstrip("/")
            urls.append(f"{base}/{src['repo']}/resolve/{src.get('revision') or 'main'}/{path}")
        elif src["kind"] == "modelscope" and src.get("verified") is not False:
            urls.append(f"https://modelscope.cn/models/{src['repo']}/resolve/master/{quote(src['path'])}")
        elif src["kind"] == "url":
            urls.append(src["url"])
    return urls


def page_url(entry: dict[str, Any]) -> str:
    src = hf_source(entry)
    return f"{HF_ENDPOINT}/{src['repo']}" if src else (entry.get("license_url") or "")


def human_size(n: int | None) -> str:
    if not n:
        return "?"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} GB"
    return f"{round(n / 1_000_000)} MB"
