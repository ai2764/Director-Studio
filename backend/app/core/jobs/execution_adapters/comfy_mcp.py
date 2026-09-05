"""Execution adapter for local ComfyUI jobs submitted through Comfy MCP."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from ....integrations.comfy_mcp import ComfyMcpClient
from ...schemas import JobRecord, JobStatus
from .. import store

logger = logging.getLogger("director_studio.jobs.execution.comfy_mcp")


@dataclass(frozen=True)
class ComfyMcpExecutionRuntime:
    client_factory: Callable[[], ComfyMcpClient]
    prepare: Callable[[JobRecord], Awaitable[None]]
    finish: Callable[[JobRecord], Awaitable[None]]


class ComfyMcpExecutionAdapter:
    id = "comfy_mcp"
    interrupt_on_cancel = False

    def can_replay(self, pipeline: Any) -> bool:
        del pipeline
        return True

    async def run(
        self,
        job: JobRecord,
        pipeline: Any,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
        runtime: ComfyMcpExecutionRuntime,
    ) -> None:
        client = runtime.client_factory()
        try:
            await runtime.prepare(job)
            job.status = JobStatus.uploading
            store.save_job(job)
            uploaded = await client.upload_inputs(job.id, inputs)
            if cancel_event.is_set():
                raise asyncio.CancelledError

            prompt, resolved_seed = pipeline.build_prompt(
                job,
                uploaded_images=uploaded,
            )
            job.seed = resolved_seed
            job.status = JobStatus.running
            store.save_job(job)
            prompt_id = await client.submit_workflow(prompt)
            job = store.load_job(job.id) or job
            job.comfy_prompt_id = prompt_id
            store.save_job(job)
            await self._collect(job, pipeline, client, cancel_event)
        except asyncio.CancelledError:
            self._mark_cancelled(job.id)
        except Exception as exc:
            self._mark_failed(job.id, exc, cancel_event)
        finally:
            await self._finalize(job.id, runtime)

    async def resume(
        self,
        job: JobRecord,
        pipeline: Any,
        cancel_event: asyncio.Event,
        runtime: ComfyMcpExecutionRuntime,
    ) -> None:
        if not job.comfy_prompt_id:
            return
        try:
            await runtime.prepare(job)
            await self._collect(
                job,
                pipeline,
                runtime.client_factory(),
                cancel_event,
            )
        except asyncio.CancelledError:
            self._mark_cancelled(job.id)
        except Exception as exc:
            self._mark_failed(job.id, exc, cancel_event)
        finally:
            await self._finalize(job.id, runtime)

    async def cancel(self, runtime: ComfyMcpExecutionRuntime) -> None:
        # The wait loop observes the job event and cancels only this MCP prompt.
        del runtime

    async def _collect(
        self,
        job: JobRecord,
        pipeline: Any,
        client: ComfyMcpClient,
        cancel_event: asyncio.Event,
    ) -> None:
        prompt_id = str(job.comfy_prompt_id)
        status = await client.wait_for_completion(
            prompt_id,
            cancel_event=cancel_event,
        )
        if cancel_event.is_set():
            raise asyncio.CancelledError
        downloaded = await client.fetch_outputs(prompt_id)
        if not downloaded:
            raise RuntimeError("Comfy MCP job completed without output files")

        history = self._history_from_status(status)
        expected = pipeline.map_history_outputs(history, job=job)
        if not expected:
            raise RuntimeError("Comfy MCP job completed without mapped outputs")

        by_ref = {
            self._ref_key_from_url(item.source_url): item for item in downloaded
        }
        saved: dict[str, Path] = {}
        for key, ref in expected.items():
            ref_key = (ref.filename, ref.subfolder, ref.type)
            item = by_ref.get(ref_key)
            if item is None:
                raise RuntimeError(
                    f"Comfy MCP did not download mapped output {key}: {ref.filename}"
                )
            saved[key] = store.save_output_file(
                job.id,
                key,
                item.filename,
                item.data,
                project_id=job.project_id,
            )

        job = store.load_job(job.id) or job
        try:
            pipeline.postprocess_job_outputs(job, saved)
        except Exception:
            logger.exception("postprocess failed for Comfy MCP job %s", job.id)
        job.status = JobStatus.succeeded
        job.error = None
        job.outputs = store.build_output_slots(
            job.id,
            saved,
            labels=pipeline.output_labels,
        )
        job.input_previews = store.input_preview_urls(job.id)
        store.save_job(job)

    @staticmethod
    def _ref_key_from_url(url: str) -> tuple[str, str, str]:
        query = parse_qs(urlparse(url).query)
        return (
            (query.get("filename") or [""])[0],
            (query.get("subfolder") or [""])[0],
            (query.get("type") or ["output"])[0],
        )

    @classmethod
    def _history_from_status(cls, status: dict[str, Any]) -> dict[str, Any]:
        outputs: dict[str, dict[str, list[dict[str, str]]]] = {}
        by_node = status.get("outputs_by_node") or {}
        if not isinstance(by_node, dict):
            return {"outputs": outputs}
        for node_id, urls in by_node.items():
            node_output: dict[str, list[dict[str, str]]] = {}
            for url in urls if isinstance(urls, list) else []:
                filename, subfolder, output_type = cls._ref_key_from_url(str(url))
                suffix = Path(filename).suffix.lower()
                if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    media_key = "images"
                elif suffix in {".mp4", ".webm", ".mov", ".mkv"}:
                    media_key = "videos"
                elif suffix in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
                    media_key = "audio"
                else:
                    media_key = "files"
                node_output.setdefault(media_key, []).append(
                    {
                        "filename": filename,
                        "subfolder": subfolder,
                        "type": output_type,
                    }
                )
            outputs[str(node_id)] = node_output
        return {"outputs": outputs}

    @staticmethod
    def _mark_cancelled(job_id: str) -> None:
        job = store.load_job(job_id)
        if job is None:
            return
        job.status = JobStatus.cancelled
        job.error = "Comfy MCP job cancelled"
        store.save_job(job)

    @staticmethod
    def _mark_failed(
        job_id: str,
        exc: Exception,
        cancel_event: asyncio.Event,
    ) -> None:
        logger.exception("Comfy MCP job %s failed", job_id)
        job = store.load_job(job_id)
        if job is None:
            return
        job.status = JobStatus.cancelled if cancel_event.is_set() else JobStatus.failed
        job.error = str(exc)[:1000]
        store.save_job(job)

    async def _finalize(
        self,
        job_id: str,
        runtime: ComfyMcpExecutionRuntime,
    ) -> None:
        job = store.load_job(job_id)
        if job is None:
            return
        try:
            await runtime.finish(job)
        except Exception:
            logger.exception("finish_comfy failed for MCP job %s", job.id)
        try:
            from ..shot_sync import on_pipeline_job_terminal

            on_pipeline_job_terminal(job)
        except Exception:
            logger.exception("shot_sync failed for Comfy MCP job %s", job.id)
