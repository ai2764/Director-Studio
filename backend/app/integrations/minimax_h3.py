"""Async client for the official MiniMax H3 V2 video-generation API."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings


class MiniMaxH3Error(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or bool(
            self.status_code is not None and self.status_code >= 500
        )


@dataclass(frozen=True)
class MiniMaxH3Result:
    download_url: str
    task: dict[str, Any]


class MiniMaxH3Client:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        poll_interval_sec: float | None = None,
        timeout_sec: float | None = None,
        max_get_retries: int | None = None,
        retry_delay_sec: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or settings.h3_minimax_api_key
            or os.environ.get("MINIMAX_API_KEY")
            or ""
        ).strip()
        self.base_url = (base_url or settings.h3_minimax_base_url).rstrip("/")
        self.poll_interval_sec = (
            settings.h3_minimax_poll_interval_sec
            if poll_interval_sec is None
            else float(poll_interval_sec)
        )
        self.timeout_sec = (
            settings.h3_minimax_timeout_sec
            if timeout_sec is None
            else float(timeout_sec)
        )
        self.max_get_retries = (
            settings.h3_minimax_max_get_retries
            if max_get_retries is None
            else int(max_get_retries)
        )
        self.retry_delay_sec = (
            settings.h3_minimax_retry_delay_sec
            if retry_delay_sec is None
            else float(retry_delay_sec)
        )
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MiniMaxH3Error(
                "MiniMax H3 API key is not configured. Set MINIMAX_API_KEY "
                "or DS_H3_MINIMAX_API_KEY before using the minimax provider."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def create_video(self, payload: dict[str, Any]) -> str:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=120.0,
        ) as client:
            response = await client.post(
                f"{self.base_url}/v2/video_generation",
                headers=self._headers(),
                json=payload,
            )
        data = self._json_response(response, "create video task")
        task_id = str(data.get("task_id") or "").strip()
        if not task_id:
            raise MiniMaxH3Error("MiniMax create response did not include task_id")
        return task_id

    async def query_video(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=30.0,
        ) as client:
            response = await client.get(
                f"{self.base_url}/v2/query/video_generation/{task_id}",
                headers=self._headers(),
            )
        data = self._json_response(response, "query video task")
        task = data.get("task")
        if not isinstance(task, dict):
            raise MiniMaxH3Error("MiniMax query response did not include a task object")
        return task

    async def wait_for_video(
        self,
        task_id: str,
        *,
        cancel_event: asyncio.Event,
    ) -> MiniMaxH3Result:
        started = time.monotonic()
        while True:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            task = await self._query_with_retries(task_id, cancel_event=cancel_event)
            status = str(task.get("status") or "").lower()
            if status == "succeeded":
                content = task.get("content")
                url = str(content.get("url") if isinstance(content, dict) else "").strip()
                if not url:
                    raise MiniMaxH3Error(
                        "MiniMax task succeeded without a video download URL"
                    )
                return MiniMaxH3Result(download_url=url, task=task)
            if status in {"failed", "cancelled"}:
                error = task.get("error") or "unknown error"
                raise MiniMaxH3Error(
                    f"MiniMax video task {status}: {self._safe_error(error)}"
                )
            if time.monotonic() - started >= self.timeout_sec:
                raise MiniMaxH3Error(
                    f"MiniMax video task timed out after {self.timeout_sec:g} seconds"
                )
            if self.poll_interval_sec > 0:
                try:
                    await asyncio.wait_for(
                        cancel_event.wait(), timeout=self.poll_interval_sec
                    )
                except asyncio.TimeoutError:
                    pass

    async def download_video(self, download_url: str) -> bytes:
        # The result is commonly hosted on a signed CDN URL. Never forward the
        # MiniMax bearer token to that external host.
        for attempt in range(self.max_get_retries + 1):
            try:
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=300.0,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(download_url)
                if response.is_error:
                    error = MiniMaxH3Error(
                        f"MiniMax video download failed with HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                    if not error.retryable or attempt >= self.max_get_retries:
                        raise error
                else:
                    if not response.content:
                        raise MiniMaxH3Error(
                            "MiniMax video download returned an empty file"
                        )
                    return response.content
            except httpx.TransportError:
                if attempt >= self.max_get_retries:
                    raise
            if self.retry_delay_sec > 0:
                await asyncio.sleep(self.retry_delay_sec)
        raise MiniMaxH3Error("MiniMax video download retries exhausted")

    async def _query_with_retries(
        self,
        task_id: str,
        *,
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        for attempt in range(self.max_get_retries + 1):
            try:
                return await self.query_video(task_id)
            except MiniMaxH3Error as exc:
                if not exc.retryable or attempt >= self.max_get_retries:
                    raise
            except httpx.TransportError:
                if attempt >= self.max_get_retries:
                    raise
            if cancel_event.is_set():
                raise asyncio.CancelledError
            if self.retry_delay_sec > 0:
                try:
                    await asyncio.wait_for(
                        cancel_event.wait(), timeout=self.retry_delay_sec
                    )
                except asyncio.TimeoutError:
                    pass
        raise MiniMaxH3Error("MiniMax query retries exhausted")

    @classmethod
    def _json_response(
        cls,
        response: httpx.Response,
        operation: str,
    ) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise MiniMaxH3Error(
                f"MiniMax {operation} returned invalid JSON (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from exc
        if response.is_error:
            error = data.get("error") if isinstance(data, dict) else None
            raise MiniMaxH3Error(
                f"MiniMax {operation} failed (HTTP {response.status_code}): "
                f"{cls._safe_error(error)}",
                status_code=response.status_code,
            )
        if not isinstance(data, dict):
            raise MiniMaxH3Error(f"MiniMax {operation} returned an invalid response")
        return data

    @staticmethod
    def _safe_error(error: Any) -> str:
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "unknown error")[:500]
        return str(error or "unknown error")[:500]
