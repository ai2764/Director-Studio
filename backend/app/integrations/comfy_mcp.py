"""Strict, persistent MCP transport for a local ComfyUI instance."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..config import settings


def _resolve_command(command: str) -> str:
    """Resolve console scripts installed beside the running Python on Windows."""
    discovered = shutil.which(command)
    if discovered:
        return discovered
    executable_dir = Path(sys.executable).resolve().parent
    candidates = [executable_dir / command, executable_dir / "Scripts" / command]
    if os.name == "nt" and not command.lower().endswith(".exe"):
        candidates.extend(path.with_suffix(".exe") for path in list(candidates))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return command


class ComfyMcpError(RuntimeError):
    pass


def _repair_save_video_dynamic_codec(
    graph: dict[str, Any], errors: list[Any]
) -> dict[str, Any] | None:
    """Bridge the old and new ComfyUI SaveVideo API input layouts."""

    repaired = copy.deepcopy(graph)
    changed = False
    for error in errors:
        if not isinstance(error, dict):
            continue
        if (
            error.get("code") != "required_input_missing"
            or error.get("field") != "format.codec"
        ):
            continue
        node_id = str(error.get("node_id") or "")
        node = repaired.get(node_id)
        if not isinstance(node, dict) or node.get("class_type") != "SaveVideo":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or "format.codec" in inputs:
            continue
        codec = inputs.get("codec")
        if not isinstance(codec, str) or not codec:
            continue
        inputs["format.codec"] = codec
        changed = True
    return repaired if changed else None


@dataclass(frozen=True)
class McpOutputFile:
    """One MCP-downloaded artifact plus the original Comfy output URL."""

    filename: str
    source_url: str
    data: bytes


class McpToolSession(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...

    async def aclose(self) -> None: ...


class PersistentMcpToolSession:
    """Own one stdio MCP session in a dedicated asyncio task.

    AnyIO contexts must exit in the same task that entered them. Job execution
    and application shutdown run in different tasks, so calls are serialized
    through this owner instead of exposing the contexts to their callers.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[
            tuple[str | None, dict[str, Any], asyncio.Future[Any]]
        ] | None = None
        self._worker: asyncio.Task[None] | None = None

    def _server_parameters(self) -> Any:
        from mcp import StdioServerParameters

        env = dict(os.environ)
        env["COMFY_BIN"] = _resolve_command(settings.comfy_mcp_comfy_bin)
        env["COMFY_LOCAL_URL"] = settings.comfy_base_url
        env.pop("COMFYUI_URL", None)
        return StdioServerParameters(
            command=_resolve_command(settings.comfy_mcp_command),
            args=shlex.split(settings.comfy_mcp_args, posix=False),
            env=env,
        )

    async def _serve(
        self,
        queue: asyncio.Queue[
            tuple[str | None, dict[str, Any], asyncio.Future[Any]]
        ],
    ) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        close_future: asyncio.Future[Any] | None = None
        failure: BaseException | None = None
        try:
            async with stdio_client(self._server_parameters()) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    while True:
                        name, arguments, response = await queue.get()
                        if name is None:
                            close_future = response
                            break
                        try:
                            result = await client.call_tool(name, arguments)
                        except BaseException as exc:
                            if not response.done():
                                response.set_exception(exc)
                        else:
                            if not response.done():
                                response.set_result(result)
        except BaseException as exc:
            failure = exc
        finally:
            if close_future is not None and not close_future.done():
                if failure is None:
                    close_future.set_result(None)
                else:
                    close_future.set_exception(failure)
            while not queue.empty():
                _name, _arguments, response = queue.get_nowait()
                if not response.done():
                    response.set_exception(
                        failure or RuntimeError("MCP session closed")
                    )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._lock:
            if self._worker is None or self._worker.done():
                self._queue = asyncio.Queue()
                self._worker = asyncio.create_task(self._serve(self._queue))
            assert self._queue is not None
            response = asyncio.get_running_loop().create_future()
            await self._queue.put((name, arguments, response))
            return await response

    async def aclose(self) -> None:
        async with self._lock:
            worker = self._worker
            queue = self._queue
            if worker is None or queue is None:
                return
            if not worker.done():
                response = asyncio.get_running_loop().create_future()
                await queue.put((None, {}, response))
                await response
            await worker
            self._worker = None
            self._queue = None


class ComfyMcpClient:
    """Local Comfy workflow operations over a deliberately narrow MCP surface."""

    ALLOWED_TOOLS = frozenset(
        {
            "server_info",
            "system_stats",
            "free_memory",
            "upload_file",
            "validate_workflow",
            "run_workflow",
            "job",
            "fetch_outputs",
        }
    )

    def __init__(
        self,
        *,
        session: McpToolSession | None = None,
        job_timeout_sec: float | None = None,
    ) -> None:
        self._session = session or PersistentMcpToolSession()
        self.job_timeout_sec = float(
            job_timeout_sec
            if job_timeout_sec is not None
            else settings.job_timeout_sec
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name not in self.ALLOWED_TOOLS:
            raise ComfyMcpError(f"MCP tool is not allowed: {name}")
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:
            raise ComfyMcpError(f"MCP tool {name} failed: {exc}") from exc

        texts = [
            str(item.text)
            for item in (getattr(result, "content", None) or [])
            if getattr(item, "type", None) == "text" and getattr(item, "text", None)
        ]
        payload: dict[str, Any] | None = None
        for text in texts:
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                payload = decoded
                break
        if payload is None:
            raise ComfyMcpError(f"MCP tool {name} returned no JSON object")
        if bool(getattr(result, "is_error", False)):
            message = payload.get("message") or payload.get("error") or payload
            raise ComfyMcpError(f"MCP tool {name} failed: {message}")
        return payload

    async def upload_inputs(
        self,
        job_id: str,
        inputs: dict[str, tuple[str, bytes]],
    ) -> dict[str, str]:
        if not inputs:
            return {}
        keys = list(inputs)
        with tempfile.TemporaryDirectory(prefix="director-studio-mcp-inputs-") as tmp:
            root = Path(tmp)
            paths: list[str] = []
            for key in keys:
                filename, data = inputs[key]
                extension = Path(filename).suffix.lower() or ".png"
                safe_job = re.sub(r"[^A-Za-z0-9_-]", "_", job_id)
                safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", key)
                path = root / f"ds_{safe_job}_{safe_key}{extension}"
                path.write_bytes(data)
                paths.append(str(path))
            payload = await self.call_tool(
                "upload_file",
                {"paths": paths, "overwrite": True},
            )

        uploads = payload.get("uploads") or []
        if len(uploads) != len(keys):
            raise ComfyMcpError(
                f"MCP uploaded {len(uploads)} inputs; expected {len(keys)}"
            )
        result: dict[str, str] = {}
        for key, item in zip(keys, uploads, strict=True):
            cloud_name = str((item or {}).get("cloud_name") or "").strip()
            if not cloud_name:
                raise ComfyMcpError(f"MCP upload returned no cloud_name for {key}")
            result[key] = cloud_name
        return result

    async def submit_workflow(self, graph: dict[str, Any]) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".api.json",
            prefix="director-studio-mcp-",
            delete=False,
        ) as handle:
            json.dump(graph, handle, ensure_ascii=False)
            workflow_path = Path(handle.name)
        try:
            validation = await self.call_tool(
                "validate_workflow",
                {"workflow_path": str(workflow_path)},
            )
            if validation.get("valid") is not True:
                repaired = _repair_save_video_dynamic_codec(
                    graph, validation.get("errors") or []
                )
                if repaired is not None:
                    workflow_path.write_text(
                        json.dumps(repaired, ensure_ascii=False), encoding="utf-8"
                    )
                    validation = await self.call_tool(
                        "validate_workflow",
                        {"workflow_path": str(workflow_path)},
                    )
            if validation.get("valid") is not True:
                errors = validation.get("errors") or []
                messages = [
                    str(item.get("message") or item)
                    for item in errors
                    if isinstance(item, dict)
                ]
                detail = "; ".join(messages) or json.dumps(errors)
                raise ComfyMcpError(f"MCP workflow validation failed: {detail}")
            queued = await self.call_tool(
                "run_workflow",
                {
                    "workflow_path": str(workflow_path),
                    "wait": False,
                    "timeout_seconds": 110.0,
                    "confirm_spend": False,
                },
            )
        finally:
            workflow_path.unlink(missing_ok=True)
        prompt_id = str(queued.get("prompt_id") or "").strip()
        if not prompt_id:
            raise ComfyMcpError("MCP run_workflow returned no prompt_id")
        return prompt_id

    async def wait_for_completion(
        self,
        prompt_id: str,
        *,
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.job_timeout_sec
        while True:
            if cancel_event.is_set():
                await self.cancel(prompt_id)
                raise asyncio.CancelledError
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ComfyMcpError(
                    f"MCP job timed out after {self.job_timeout_sec:g}s: {prompt_id}"
                )
            payload = await self.call_tool(
                "job",
                {
                    "action": "wait",
                    "prompt_id": prompt_id,
                    "timeout_seconds": min(30.0, remaining),
                },
            )
            status_payload = payload.get("status") if payload.get("timed_out") else payload
            if not isinstance(status_payload, dict):
                raise ComfyMcpError("MCP job returned an invalid status payload")
            status = str(status_payload.get("status") or "").lower()
            if status in {"completed", "succeeded"}:
                return status_payload
            if status in {"failed", "error", "cancelled", "canceled"}:
                detail = status_payload.get("error") or status_payload
                raise ComfyMcpError(f"MCP job {status}: {detail}")

    async def fetch_outputs(self, prompt_id: str) -> list[McpOutputFile]:
        with tempfile.TemporaryDirectory(prefix="director-studio-mcp-outputs-") as tmp:
            root = Path(tmp).resolve()
            payload = await self.call_tool(
                "fetch_outputs",
                {
                    "prompt_id": prompt_id,
                    "out_dir": str(root),
                    "url_only": False,
                    "inline_images": False,
                },
            )
            outputs: list[McpOutputFile] = []
            for item in payload.get("files") or []:
                path = Path(str((item or {}).get("path") or "")).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ComfyMcpError(
                        f"MCP output path is outside the requested directory: {path}"
                    )
                outputs.append(
                    McpOutputFile(
                        filename=path.name,
                        source_url=str((item or {}).get("url") or ""),
                        data=path.read_bytes(),
                    )
                )
            if not outputs:
                raise ComfyMcpError("MCP fetch_outputs returned no files")
            return outputs

    async def cancel(self, prompt_id: str) -> None:
        await self.call_tool(
            "job",
            {"action": "cancel", "prompt_id": prompt_id},
        )

    async def free_memory(self) -> dict[str, Any]:
        return await self.call_tool(
            "free_memory",
            {"unload_models": True, "free_memory": True},
        )

    async def aclose(self) -> None:
        await self._session.aclose()
