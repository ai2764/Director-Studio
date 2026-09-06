from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx

from ...config import settings


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.comfy_base_url).rstrip("/")
        self.client_id = str(uuid.uuid4())

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{self.base_url}/system_stats")
            r.raise_for_status()
            return r.json()

    async def get_object_info(self) -> dict[str, Any]:
        """Return live ComfyUI node metadata used for workflow boundary labels."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.base_url}/object_info")
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ComfyError("ComfyUI /object_info returned a non-object response")
        return payload

    async def upload_image(
        self,
        data: bytes,
        filename: str,
        *,
        image_type: str = "input",
        overwrite: bool = True,
    ) -> str:
        """Upload image to ComfyUI input folder. Returns the server-side filename."""
        files = {
            "image": (filename, data, _guess_mime(filename)),
        }
        form = {
            "type": image_type,
            "overwrite": "true" if overwrite else "false",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{self.base_url}/upload/image", files=files, data=form)
            if r.status_code >= 400:
                raise ComfyError(f"Upload failed ({r.status_code}): {r.text[:500]}")
            payload = r.json()
            # {"name": "...", "subfolder": "", "type": "input"}
            name = payload.get("name")
            sub = payload.get("subfolder") or ""
            if not name:
                raise ComfyError(f"Unexpected upload response: {payload}")
            return f"{sub}/{name}".lstrip("/") if sub else name

    async def queue_prompt(self, prompt: dict[str, Any]) -> str:
        body = {"prompt": prompt, "client_id": self.client_id}
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{self.base_url}/prompt", json=body)
            if r.status_code >= 400:
                raise ComfyError(f"Queue prompt failed ({r.status_code}): {r.text[:800]}")
            data = r.json()
            if "error" in data:
                raise ComfyError(json.dumps(data["error"], ensure_ascii=False)[:800])
            node_errors = data.get("node_errors") or {}
            if node_errors:
                raise ComfyError(f"Node errors: {json.dumps(node_errors, ensure_ascii=False)[:800]}")
            prompt_id = data.get("prompt_id")
            if not prompt_id:
                raise ComfyError(f"No prompt_id in response: {data}")
            return prompt_id

    async def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{self.base_url}/history/{prompt_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            return data.get(prompt_id)

    async def wait_for_completion(
        self,
        prompt_id: str,
        *,
        poll_interval: float | None = None,
        timeout: float | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        poll_interval = poll_interval if poll_interval is not None else settings.poll_interval_sec
        timeout = timeout if timeout is not None else settings.job_timeout_sec
        elapsed = 0.0
        while elapsed < timeout:
            if cancel_event and cancel_event.is_set():
                raise ComfyError("Job cancelled")
            hist = await self.get_history(prompt_id)
            if hist and hist.get("outputs") is not None:
                status = (hist.get("status") or {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    msgs = status.get("messages") or []
                    raise ComfyError(f"ComfyUI job failed: {msgs!r}"[:800])
                return hist
            # Also check queue for presence
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise ComfyError(f"Timed out after {timeout:.0f}s waiting for prompt {prompt_id}")

    async def download_image(
        self,
        filename: str,
        *,
        subfolder: str = "",
        folder_type: str = "output",
    ) -> bytes:
        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.get(f"{self.base_url}/view", params=params)
            if r.status_code >= 400:
                raise ComfyError(f"Download failed ({r.status_code}) for {filename}")
            return r.content

    async def interrupt(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{self.base_url}/interrupt")

    async def free_memory(
        self,
        *,
        unload_models: bool = True,
        free_memory: bool = True,
    ) -> dict[str, Any]:
        """
        Ask ComfyUI to unload models and free GPU memory (POST /free).

        Returns a small stats snapshot {vram_before, vram_after, free_bytes_gained}.
        Used before Director LLM turns so Ollama can reclaim VRAM after image/video jobs.
        """
        body = {
            "unload_models": unload_models,
            "free_memory": free_memory,
        }

        async def _vram_free() -> int | None:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(f"{self.base_url}/system_stats")
                    if r.status_code >= 400:
                        return None
                    data = r.json()
                    devices = data.get("devices") or []
                    if not devices:
                        return None
                    # torch free or total-used depending on Comfy build
                    d0 = devices[0]
                    if "vram_free" in d0:
                        return int(d0["vram_free"])
                    total = d0.get("vram_total")
                    used = d0.get("vram_used") or d0.get("torch_vram_used")
                    if total is not None and used is not None:
                        return int(total) - int(used)
                    return None
            except Exception:
                return None

        before = await _vram_free()
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Primary endpoint (ComfyUI core)
            r = await client.post(f"{self.base_url}/free", json=body)
            if r.status_code >= 400:
                # Some builds expose /api/free
                r2 = await client.post(f"{self.base_url}/api/free", json=body)
                if r2.status_code >= 400:
                    raise ComfyError(
                        f"Comfy free_memory failed ({r.status_code}): {r.text[:400]}"
                    )
            # Second pass: soft_empty_cache after unload is more reliable on some builds
            await client.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
            )
        after = await _vram_free()
        gained = None
        if before is not None and after is not None:
            gained = after - before
        return {
            "vram_free_before": before,
            "vram_free_after": after,
            "vram_free_gained": gained,
        }


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")

