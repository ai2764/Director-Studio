from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from ..config import settings


class BridgeFailureStage(str, Enum):
    configuration = "configuration"
    health = "health"
    source_validation = "source_validation"
    upload = "upload"
    submission = "submission"
    generation = "generation"
    artifact_selection = "artifact_selection"
    download = "download"
    image_validation = "image_validation"


class ChatGptBridgeError(RuntimeError):
    def __init__(self, stage: BridgeFailureStage, message: str):
        self.stage = stage
        super().__init__(f"{stage.value}: {message}")


@dataclass(frozen=True)
class ValidatedImage:
    format: str
    mime: str


@dataclass(frozen=True)
class BridgeGenerationResult:
    image_bytes: bytes
    filename: str
    mime: str
    validated_format: str
    request_id: str
    session_id: str
    artifact_id: str
    artifact_name: str

    def provenance(self) -> dict[str, Any]:
        return {
            "provider": "gpt",
            "request_id": self.request_id,
            "session_id": self.session_id,
            "artifact_id": self.artifact_id,
            "artifact_name": self.artifact_name,
            "artifact_mime": self.mime,
            "artifact_bytes": len(self.image_bytes),
            "validated_format": self.validated_format,
        }


def read_bridge_api_token(env_file: Path) -> str:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ChatGptBridgeError(
            BridgeFailureStage.configuration,
            "The configured ChatGPT Bridge env file is unreadable",
        ) from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() == "API_TOKEN":
            token = value.strip().strip('"').strip("'")
            if token:
                return token
    raise ChatGptBridgeError(
        BridgeFailureStage.configuration,
        "API_TOKEN is missing from the configured ChatGPT Bridge env file",
    )


def _normalized_mime(value: str) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def validate_image_bytes(data: bytes, *, declared_mime: str = "") -> ValidatedImage:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = ValidatedImage("png", "image/png")
    elif data.startswith(b"\xff\xd8\xff"):
        detected = ValidatedImage("jpeg", "image/jpeg")
    elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        detected = ValidatedImage("webp", "image/webp")
    else:
        raise ChatGptBridgeError(
            BridgeFailureStage.image_validation,
            "The Bridge artifact is not a supported PNG, JPEG, or WebP image",
        )
    declared = _normalized_mime(declared_mime)
    compatible = {detected.mime}
    if detected.format == "jpeg":
        compatible.add("image/jpg")
    if declared and declared not in compatible:
        raise ChatGptBridgeError(
            BridgeFailureStage.image_validation,
            f"The Bridge artifact MIME {declared!r} does not match its {detected.format} bytes",
        )
    return detected


class ChatGptBridgeClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_sec: float,
        max_file_bytes: int,
        max_total_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
        action_delay_sec: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_token = api_token
        self.timeout_sec = timeout_sec
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self._transport = transport
        self.action_delay_sec = max(0.0, float(action_delay_sec))
        self._sleep = sleep or asyncio.sleep

    @classmethod
    def from_settings(cls) -> "ChatGptBridgeClient":
        if not settings.gpt_bridge_configured:
            raise ChatGptBridgeError(
                BridgeFailureStage.configuration,
                "Configure DS_GPT_BRIDGE_BASE_URL and DS_GPT_BRIDGE_ENV_FILE",
            )
        assert settings.gpt_bridge_base_url is not None
        assert settings.gpt_bridge_env_file is not None
        return cls(
            base_url=settings.gpt_bridge_base_url,
            api_token=read_bridge_api_token(settings.gpt_bridge_env_file),
            timeout_sec=settings.gpt_bridge_timeout_sec,
            max_file_bytes=settings.gpt_bridge_max_file_mb * 1024 * 1024,
            max_total_bytes=settings.gpt_bridge_max_total_mb * 1024 * 1024,
            action_delay_sec=settings.gpt_bridge_action_delay_sec,
        )

    async def _pace_browser_writes(self) -> None:
        if self.action_delay_sec > 0:
            await self._sleep(self.action_delay_sec)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        stage: BridgeFailureStage,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout_sec,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, json=json_body)
        except (httpx.HTTPError, OSError) as exc:
            raise ChatGptBridgeError(
                stage,
                f"ChatGPT Bridge request failed during {stage.value}: {type(exc).__name__}",
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("detail") or payload.get("error") or "")
            except ValueError:
                detail = ""
            suffix = f": {detail[:400]}" if detail else ""
            raise ChatGptBridgeError(
                stage,
                f"ChatGPT Bridge returned HTTP {response.status_code} during {stage.value}{suffix}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ChatGptBridgeError(
                stage,
                f"ChatGPT Bridge returned invalid JSON during {stage.value}",
            ) from exc
        if not isinstance(payload, dict):
            raise ChatGptBridgeError(stage, f"ChatGPT Bridge returned an invalid {stage.value} payload")
        return payload

    async def health(self) -> dict[str, Any]:
        payload = await self._json_request("GET", "/health", stage=BridgeFailureStage.health)
        if not payload.get("ok") or not payload.get("activeClient"):
            if payload.get("needsSelection"):
                reason = "Multiple ChatGPT tabs are connected; select one in ChatGPT Bridge"
            else:
                reason = "No ready ChatGPT extension tab is connected"
            raise ChatGptBridgeError(BridgeFailureStage.health, reason)
        return payload

    async def _open_fresh_chat_tab(self, health: dict[str, Any]) -> str:
        active_client = health.get("activeClient")
        source_client_id = str(
            active_client.get("id") if isinstance(active_client, dict) else ""
        ).strip()
        if not source_client_id:
            raise ChatGptBridgeError(
                BridgeFailureStage.health,
                "The active ChatGPT extension tab has no client ID",
            )
        payload = await self._json_request(
            "POST",
            "/browser/tabs/open",
            stage=BridgeFailureStage.submission,
            json_body={
                "url": "https://chatgpt.com/",
                "active": True,
                "select": True,
                "sourceClientId": source_client_id,
            },
        )
        fresh_client = payload.get("selectedClient") or payload.get("client")
        fresh_client_id = str(
            fresh_client.get("id") if isinstance(fresh_client, dict) else ""
        ).strip()
        if not fresh_client_id:
            raise ChatGptBridgeError(
                BridgeFailureStage.submission,
                "ChatGPT Bridge opened a fresh tab without returning its client ID",
            )
        return fresh_client_id

    def _validate_upload_budget(self, ordered_images: list[tuple[str, str, bytes]]) -> None:
        total = 0
        for logical_name, _filename, data in ordered_images:
            size = len(data)
            if size > self.max_file_bytes:
                raise ChatGptBridgeError(
                    BridgeFailureStage.source_validation,
                    f"{logical_name} exceeds the configured per-file upload limit",
                )
            total += size
        if total > self.max_total_bytes:
            raise ChatGptBridgeError(
                BridgeFailureStage.source_validation,
                "The ordered GPT source pack exceeds the configured total upload limit",
            )

    @staticmethod
    def _select_single_ready_image(artifacts: Any) -> dict[str, Any]:
        if not isinstance(artifacts, list):
            artifacts = []
        ready = []
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            state = str(
                item.get("phase") or item.get("state") or item.get("status") or ""
            ).upper()
            kind = str(item.get("kind") or "").lower()
            mime = _normalized_mime(str(item.get("mime") or ""))
            if state == "READY" and (kind == "image" or mime.startswith("image/")):
                ready.append(item)
        if len(ready) != 1:
            raise ChatGptBridgeError(
                BridgeFailureStage.artifact_selection,
                f"Expected exactly one READY image artifact; received {len(ready)}",
            )
        artifact_id = str(ready[0].get("id") or "").strip()
        if not artifact_id:
            raise ChatGptBridgeError(
                BridgeFailureStage.artifact_selection,
                "The READY image artifact has no downloadable ID",
            )
        return ready[0]

    async def _download_artifact(self, artifact: dict[str, Any]) -> tuple[bytes, str]:
        artifact_id = str(artifact["id"])
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout_sec,
                transport=self._transport,
            ) as client:
                response = await client.get(f"/artifacts/{artifact_id}/download")
        except (httpx.HTTPError, OSError) as exc:
            raise ChatGptBridgeError(
                BridgeFailureStage.download,
                f"ChatGPT Bridge artifact download failed: {type(exc).__name__}",
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ChatGptBridgeError(
                BridgeFailureStage.download,
                f"ChatGPT Bridge artifact download returned HTTP {response.status_code}",
            )
        declared = response.headers.get("content-type") or str(artifact.get("mime") or "")
        return response.content, declared

    async def generate_image(
        self,
        prompt: str,
        ordered_images: list[tuple[str, str, bytes]],
    ) -> BridgeGenerationResult:
        self._validate_upload_budget(ordered_images)
        health = await self.health()
        source_client_id = await self._open_fresh_chat_tab(health)
        await self._pace_browser_writes()

        file_ids: list[str] = []
        for _logical_name, filename, data in ordered_images:
            validated_source = validate_image_bytes(data)
            payload = await self._json_request(
                "POST",
                "/files",
                stage=BridgeFailureStage.upload,
                json_body={
                    "name": filename,
                    "mime": validated_source.mime,
                    "contentBase64": base64.b64encode(data).decode("ascii"),
                },
            )
            file_data = payload.get("file")
            file_id = str(file_data.get("id") if isinstance(file_data, dict) else "").strip()
            if not file_id:
                raise ChatGptBridgeError(
                    BridgeFailureStage.upload,
                    "ChatGPT Bridge accepted a source upload without returning a file ID",
                )
            file_ids.append(file_id)
            await self._pace_browser_writes()

        response = await self._json_request(
            "POST",
            "/chat",
            stage=BridgeFailureStage.submission,
            json_body={
                # A fresh chat is a tab-creation operation. Navigating an
                # already leased tab through ChatGPT's New Chat control can
                # transiently disconnect it and lose the selected client.
                "newSession": False,
                "sourceClientId": source_client_id,
                "message": prompt,
                "attachments": file_ids,
                "output": {"expected": "image", "required": True},
            },
        )
        artifact = self._select_single_ready_image(response.get("artifacts"))
        image_bytes, declared_mime = await self._download_artifact(artifact)
        validated = validate_image_bytes(image_bytes, declared_mime=declared_mime)
        artifact_mime = _normalized_mime(str(artifact.get("mime") or ""))
        if artifact_mime.startswith("image/"):
            validate_image_bytes(image_bytes, declared_mime=artifact_mime)
        extension = "jpg" if validated.format == "jpeg" else validated.format
        artifact_name = str(artifact.get("name") or "generated-image").strip() or "generated-image"
        filename = artifact_name
        if Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            filename = f"{filename}.{extension}"
        session = response.get("session")
        session_id = str(session.get("id") if isinstance(session, dict) else response.get("sessionId") or "")
        return BridgeGenerationResult(
            image_bytes=image_bytes,
            filename=filename,
            mime=validated.mime,
            validated_format=validated.format,
            request_id=str(response.get("requestId") or response.get("request_id") or ""),
            session_id=session_id,
            artifact_id=str(artifact["id"]),
            artifact_name=artifact_name,
        )
