from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.core.llm.openai_compatible import OpenAICompatibleClient
from app.core.llm.provider import UnsupportedLLMFeatureError


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="http://127.0.0.1:1234/v1",
        api_key=None,
        timeout=10,
        transport=httpx.MockTransport(handler),
    )


def _chat_response(
    *,
    content: str = "",
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": "checked references",
                        "tool_calls": tool_calls or [],
                    },
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_lists_models_through_openai_compatible_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "model-b", "object": "model", "created": 1, "owned_by": "local"},
                    {"id": "model-a", "object": "model", "created": 1, "owned_by": "local"},
                ],
            },
        )

    client = _client(handler)
    try:
        assert await client.list_models() == ["model-b", "model-a"]
        assert await client.health() is True
    finally:
        await client.close()

    assert [request.url.path for request in requests] == ["/v1/models", "/v1/models"]
    assert requests[0].headers["authorization"] == "Bearer not-needed"


@pytest.mark.asyncio
async def test_chat_response_converts_images_tools_schema_and_result() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _chat_response(
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "save", "arguments": '{"ok":true}'},
                }
            ],
            finish_reason="tool_calls",
        )

    client = _client(handler)
    try:
        result = await client.chat_response(
            "vision-model",
            messages=[
                {
                    "role": "user",
                    "content": "inspect",
                    "images": ["aGVsbG8="],
                }
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "save",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            format={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            require_vision=True,
        )
    finally:
        await client.close()

    sent = bodies[0]
    assert sent["messages"][0]["content"] == [
        {"type": "text", "text": "inspect"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,aGVsbG8="},
        },
    ]
    assert sent["tools"][0]["function"]["name"] == "save"
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "director_output",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }
    assert result == {
        "content": "",
        "thinking": "checked references",
        "tool_calls": [
            {"id": "call_1", "name": "save", "arguments": {"ok": True}}
        ],
        "finish_reason": "tool_calls",
    }


@pytest.mark.asyncio
async def test_stream_normalizes_reasoning_and_text_deltas() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        events = [
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": None,
                        "delta": {"reasoning_content": "thinking"},
                    }
                ],
            },
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": None,
                        "delta": {"content": "hello"},
                    }
                ],
            },
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        body += "data: [DONE]\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    client = _client(handler)
    chunks: list[dict[str, str]] = []
    try:
        stream: AsyncIterator[dict[str, str]] = client.generate_stream(
            "test-model",
            "hello",
        )
        async for chunk in stream:
            chunks.append(chunk)
    finally:
        await client.close()

    assert chunks == [
        {"kind": "think", "text": "thinking"},
        {"kind": "token", "text": "hello"},
    ]


@pytest.mark.asyncio
async def test_schema_rejection_retries_once_without_response_format() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "response_format json_schema is not supported",
                        "type": "invalid_request_error",
                    }
                },
            )
        return _chat_response(content='{"ok":true}')

    client = _client(handler)
    try:
        result = await client.chat_response(
            "test-model",
            messages=[{"role": "user", "content": "return status"}],
            format={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
    finally:
        await client.close()

    assert len(bodies) == 2
    assert "response_format" not in bodies[1]
    assert "Return only valid JSON" in bodies[1]["messages"][0]["content"]
    assert result["content"] == '{"ok":true}'


@pytest.mark.asyncio
async def test_required_vision_rejection_is_not_retried_as_text() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "This model does not support image_url input",
                    "type": "invalid_request_error",
                }
            },
        )

    client = _client(handler)
    try:
        with pytest.raises(UnsupportedLLMFeatureError, match="image_url") as error:
            await client.chat_response(
                "text-model",
                messages=[
                    {"role": "user", "content": "look", "images": ["aGVsbG8="]}
                ],
                require_vision=True,
            )
    finally:
        await client.close()

    assert error.value.feature == "vision"
    assert attempts == 1


@pytest.mark.asyncio
async def test_authentication_error_is_not_retried_as_compatibility_fallback() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            401,
            json={"error": {"message": "invalid API key", "type": "authentication_error"}},
        )

    client = _client(handler)
    try:
        with pytest.raises(Exception) as error:
            await client.chat_response(
                "test-model",
                messages=[{"role": "user", "content": "hello"}],
                tools=[{"type": "function", "function": {"name": "save"}}],
            )
    finally:
        await client.close()

    assert not isinstance(error.value, UnsupportedLLMFeatureError)
    assert attempts == 1
