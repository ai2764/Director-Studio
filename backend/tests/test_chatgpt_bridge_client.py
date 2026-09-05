from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.chatgpt_bridge import (
    BridgeFailureStage,
    ChatGptBridgeClient,
    ChatGptBridgeError,
    read_bridge_api_token,
    validate_image_bytes,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"png-payload"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-payload" + b"\xff\xd9"
WEBP = b"RIFF" + (12).to_bytes(4, "little") + b"WEBP" + b"webp-payload"


def _client(
    handler,
    *,
    max_file_bytes=1024,
    max_total_bytes=8192,
    action_delay_sec=0,
    sleep=None,
):
    return ChatGptBridgeClient(
        base_url="http://bridge.test",
        api_token="local-secret-token",
        timeout_sec=10,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        transport=httpx.MockTransport(handler),
        action_delay_sec=action_delay_sec,
        sleep=sleep,
    )


def _successful_handler(seen: list[httpx.Request], *, image_bytes=PNG, mime="image/png"):
    upload_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_count
        seen.append(request)
        assert request.headers["authorization"] == "Bearer local-secret-token"
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "clients": 1,
                    "needsSelection": False,
                    "activeClient": {
                        "id": "client_existing",
                        "url": "https://chatgpt.com/c/existing",
                    },
                },
            )
        if request.url.path == "/browser/tabs/open":
            return httpx.Response(
                201,
                json={
                    "ok": True,
                    "client": {"id": "client_fresh", "url": "https://chatgpt.com/"},
                    "selectedClient": {"id": "client_fresh", "url": "https://chatgpt.com/"},
                },
            )
        if request.url.path == "/files":
            upload_count += 1
            return httpx.Response(200, json={"ok": True, "file": {"id": f"file_{upload_count}"}})
        if request.url.path == "/chat":
            return httpx.Response(
                200,
                json={
                    "requestId": "request_public",
                    "session": {"id": "session_public"},
                    "artifacts": [
                        {
                            "id": "artifact_public",
                            "kind": "image",
                            "name": "generated-image.png",
                            "mime": mime,
                            "state": "READY",
                        }
                    ],
                },
            )
        if request.url.path == "/artifacts/artifact_public/download":
            return httpx.Response(200, content=image_bytes, headers={"content-type": mime})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler


def _four_images():
    return [(f"Image{index}", f"ref-{index}.png", PNG) for index in range(1, 5)]


def test_reads_api_token_without_returning_other_env_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BRIDGE_" "TOKEN=extension-only\nAPI_" "TOKEN=http-api-secret\nOTHER=value\n",
        encoding="utf-8",
    )

    assert read_bridge_api_token(env_file) == "http-api-secret"


def test_bridge_error_string_includes_typed_failure_stage():
    error = ChatGptBridgeError(BridgeFailureStage.submission, "browser disconnected")

    assert str(error) == "submission: browser disconnected"


@pytest.mark.parametrize(
    "contents",
    ["BRIDGE_" "TOKEN=x\n", "API_" "TOKEN=\n"],
)
def test_missing_api_token_is_a_sanitized_configuration_error(tmp_path: Path, contents: str):
    env_file = tmp_path / ".env"
    env_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ChatGptBridgeError) as caught:
        read_bridge_api_token(env_file)

    assert caught.value.stage == BridgeFailureStage.configuration
    assert contents.strip() not in str(caught.value)


@pytest.mark.asyncio
async def test_generate_opens_fresh_tab_then_uploads_four_images_in_order():
    seen: list[httpx.Request] = []
    client = _client(_successful_handler(seen))

    result = await client.generate_image(
        "Image1 controls set. Image2, Image3, and Image4 control subjects. Return one image.",
        _four_images(),
    )

    uploads = [request for request in seen if request.url.path == "/files"]
    assert [json.loads(request.content)["name"] for request in uploads] == [
        "ref-1.png",
        "ref-2.png",
        "ref-3.png",
        "ref-4.png",
    ]
    fresh_tab = next(request for request in seen if request.url.path == "/browser/tabs/open")
    fresh_tab_payload = json.loads(fresh_tab.content)
    assert fresh_tab_payload == {
        "url": "https://chatgpt.com/",
        "active": True,
        "select": True,
        "sourceClientId": "client_existing",
    }
    chat = next(request for request in seen if request.url.path == "/chat")
    payload = json.loads(chat.content)
    assert payload["newSession"] is False
    assert payload["sourceClientId"] == "client_fresh"
    assert payload["attachments"] == ["file_1", "file_2", "file_3", "file_4"]
    assert payload["output"] == {"expected": "image", "required": True}
    assert result.image_bytes == PNG
    assert result.request_id == "request_public"
    assert result.session_id == "session_public"
    assert result.artifact_id == "artifact_public"


@pytest.mark.asyncio
async def test_generate_spaces_browser_writes_with_configured_action_delay():
    seen: list[httpx.Request] = []
    timeline: list[str] = []
    base_handler = _successful_handler(seen)

    def handler(request: httpx.Request) -> httpx.Response:
        timeline.append(request.url.path)
        return base_handler(request)

    async def fake_sleep(seconds: float) -> None:
        timeline.append(f"sleep:{seconds}")

    client = _client(handler, action_delay_sec=1.5, sleep=fake_sleep)

    await client.generate_image(
        "Use both references and return one image.",
        _four_images()[:2],
    )

    assert timeline == [
        "/health",
        "/browser/tabs/open",
        "sleep:1.5",
        "/files",
        "sleep:1.5",
        "/files",
        "sleep:1.5",
        "/chat",
        "/artifacts/artifact_public/download",
    ]


@pytest.mark.asyncio
async def test_generate_allows_text_only_image_requests_without_uploads():
    seen: list[httpx.Request] = []
    client = _client(_successful_handler(seen))

    result = await client.generate_image(
        "Create one full-body cinematic character design on a plain background.",
        [],
    )

    assert not [request for request in seen if request.url.path == "/files"]
    chat = next(request for request in seen if request.url.path == "/chat")
    assert json.loads(chat.content)["attachments"] == []
    assert result.image_bytes == PNG


@pytest.mark.parametrize(
    ("image_bytes", "mime", "expected_format"),
    [(PNG, "image/png", "png"), (JPEG, "image/jpeg", "jpeg"), (WEBP, "image/webp", "webp")],
)
def test_validates_supported_image_signatures(image_bytes: bytes, mime: str, expected_format: str):
    validated = validate_image_bytes(image_bytes, declared_mime=mime)
    assert validated.format == expected_format
    assert validated.mime == mime


@pytest.mark.parametrize(
    ("image_bytes", "mime"),
    [(b"<html>login</html>", "text/html"), (b'{"error":"bad"}', "application/json"), (b"", "image/png"), (PNG, "image/jpeg")],
)
def test_rejects_invalid_or_mismatched_downloads(image_bytes: bytes, mime: str):
    with pytest.raises(ChatGptBridgeError) as caught:
        validate_image_bytes(image_bytes, declared_mime=mime)
    assert caught.value.stage == BridgeFailureStage.image_validation


@pytest.mark.asyncio
async def test_rejects_upload_budget_before_any_http_request():
    seen: list[httpx.Request] = []
    client = _client(lambda request: seen.append(request), max_file_bytes=8, max_total_bytes=64)

    with pytest.raises(ChatGptBridgeError) as caught:
        await client.generate_image("Return one image", [("Image1", "large.png", PNG)])

    assert caught.value.stage == BridgeFailureStage.source_validation
    assert seen == []


@pytest.mark.asyncio
async def test_unhealthy_bridge_reports_health_stage_without_upload_or_chat():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": False, "clients": 0, "activeClient": None})

    with pytest.raises(ChatGptBridgeError) as caught:
        await _client(handler).generate_image("Return one image", [("Image1", "ref.png", PNG)])

    assert caught.value.stage == BridgeFailureStage.health
    assert [request.url.path for request in seen] == ["/health"]


@pytest.mark.parametrize(
    "artifacts",
    [
        [],
        [
            {"id": "a", "kind": "image", "state": "READY", "mime": "image/png"},
            {"id": "b", "kind": "image", "state": "READY", "mime": "image/png"},
        ],
        [{"id": "a", "kind": "image", "state": "GENERATING", "mime": "image/png"}],
    ],
)
@pytest.mark.asyncio
async def test_requires_exactly_one_ready_image_artifact(artifacts):
    seen: list[httpx.Request] = []
    base_handler = _successful_handler(seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chat":
            seen.append(request)
            return httpx.Response(200, json={"requestId": "r", "artifacts": artifacts})
        return base_handler(request)

    with pytest.raises(ChatGptBridgeError) as caught:
        await _client(handler).generate_image("Return one image", [("Image1", "ref.png", PNG)])

    assert caught.value.stage == BridgeFailureStage.artifact_selection
    assert sum(request.url.path == "/chat" for request in seen) == 1


@pytest.mark.asyncio
async def test_accepts_bridge_native_phase_ready_artifact():
    seen: list[httpx.Request] = []
    base_handler = _successful_handler(seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chat":
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "requestId": "request_native",
                    "session": {"id": "session_native"},
                    "artifacts": [
                        {
                            "id": "artifact_public",
                            "kind": "image",
                            "name": "image",
                            "mime": "image/png",
                            "phase": "READY",
                        }
                    ],
                },
            )
        return base_handler(request)

    result = await _client(handler).generate_image(
        "Image1 controls the set. Return one image.",
        [("Image1", "ref.png", PNG)],
    )

    assert result.request_id == "request_native"


@pytest.mark.asyncio
async def test_accepts_generic_artifact_metadata_when_download_is_valid_png():
    seen: list[httpx.Request] = []
    base_handler = _successful_handler(seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chat":
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "requestId": "request_generic_mime",
                    "session": {"id": "session_generic_mime"},
                    "artifacts": [
                        {
                            "id": "artifact_public",
                            "kind": "image",
                            "name": "generated image",
                            "mime": "application/octet-stream",
                            "phase": "READY",
                        }
                    ],
                },
            )
        if request.url.path == "/artifacts/artifact_public/download":
            seen.append(request)
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return base_handler(request)

    result = await _client(handler).generate_image(
        "Image1 controls the set. Return one image.",
        [("Image1", "ref.png", PNG)],
    )

    assert result.validated_format == "png"
    assert result.mime == "image/png"


@pytest.mark.asyncio
async def test_submission_failure_is_not_retried_and_does_not_leak_token():
    seen: list[httpx.Request] = []
    base_handler = _successful_handler(seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chat":
            seen.append(request)
            return httpx.Response(503, json={"detail": "browser tab disconnected"})
        return base_handler(request)

    with pytest.raises(ChatGptBridgeError) as caught:
        await _client(handler).generate_image("Return one image", [("Image1", "ref.png", PNG)])

    assert caught.value.stage == BridgeFailureStage.submission
    assert sum(request.url.path == "/chat" for request in seen) == 1
    assert "local-secret-token" not in str(caught.value)
