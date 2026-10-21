from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.llm.extra_headers import llm_extra_headers
from app.core.net_headers import HeaderParseError, parse_headers


def test_parse_headers_formats():
    assert parse_headers('{"X-A": "1"}') == {"X-A": "1"}
    assert parse_headers("X-A: 1\n# c\nX-B=two=2\n") == {"X-A": "1", "X-B": "two=2"}
    assert parse_headers("Authorization: Basic a=b") == {"Authorization": "Basic a=b"}
    for bad in ("[1]", "no separator", "Bad Name: x", "Host: x", "{"):
        with pytest.raises(HeaderParseError):
            parse_headers(bad)


def test_llm_extra_headers_reach_openai_client():
    from app.core.llm.openai_compatible import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url="http://127.0.0.1:1/v1", api_key=None,
                                    extra_headers=llm_extra_headers("CF-Access-Client-Id: a"))
    assert client._client.default_headers["CF-Access-Client-Id"] == "a"
    assert llm_extra_headers("garbage line") == {}


def test_ollama_client_uses_extra_headers(monkeypatch):
    from app.config import settings
    from app.core.vram.ollama_client import OllamaClient

    monkeypatch.setattr(settings, "llm_extra_headers", '{"X-Tunnel": "t"}')
    assert OllamaClient("http://127.0.0.1:1")._headers == {"X-Tunnel": "t"}


def _load_prompt_list():
    path = Path(__file__).resolve().parents[2] / "comfy_nodes" / "ds_nodes" / "prompt_list.py"
    spec = importlib.util.spec_from_file_location("ds_prompt_list", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ds_prompt_list_semantics():
    module = _load_prompt_list()
    node = module.DSPromptList()
    prompts, bodies, help_text = node.run("Keep it.", "a\n\n b \nc\nd", "", 1, 2)
    assert prompts == ["Keep it. b", "Keep it. c"]
    assert bodies == ["b", "c"]
    assert "DS Prompt List" in help_text
    assert module.DSPromptList.OUTPUT_IS_LIST == (True, True, False)
    required = module.DSPromptList.INPUT_TYPES()["required"]
    assert set(required) == {"prepend_text", "multiline_text", "append_text", "start_index", "max_rows"}
