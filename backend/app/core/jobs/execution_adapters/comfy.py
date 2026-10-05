"""Execution adapter for ComfyUI-backed pipelines."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ...comfy import ComfyError
from ...schemas import JobRecord, JobStatus
from .. import store

logger = logging.getLogger("director_studio.jobs.execution.comfy")


def comfy_input_name(job_id: str, kind: str, filename: str) -> str:
    """One job-scoped Comfy input name. Video keys keep their own suffix."""
    extension = Path(filename).suffix or ".png"
    return f"ds_{job_id}_{kind}{extension}"


@dataclass(frozen=True)
class ComfyExecutionRuntime:
    client_factory: Callable[[], Any]
    prepare: Callable[[JobRecord], Awaitable[None]]
    finish: Callable[[JobRecord], Awaitable[None]]
    update_phase: Callable[[str, str, str], Awaitable[None]]
    save_completed_outputs: Callable[..., Awaitable[JobRecord]]
    client_factory_for_job: Callable[[JobRecord], Any] | None = None


class ComfyExecutionAdapter:
    id = "comfy"
    interrupt_on_cancel = True

    def can_replay(self, pipeline: Any) -> bool:
        return True

    async def run(
        self,
        job: JobRecord,
        pipeline: Any,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
        runtime: ComfyExecutionRuntime,
    ) -> None:
        client = _client_for_job(runtime, job)
        try:
            await runtime.prepare(job)

            job.status = JobStatus.uploading
            store.save_job(job)
            await runtime.update_phase(job.id, job.status.value, "uploading")
            uploaded: dict[str, str] = {}
            for kind, (filename, data) in inputs.items():
                if cancel_event.is_set():
                    raise ComfyError("Job cancelled")
                uploaded[kind] = await client.upload_image(
                    data,
                    comfy_input_name(job.id, kind, filename),
                )

            prompt, resolved_seed = pipeline.build_prompt(
                job,
                uploaded_images=uploaded,
            )
            job.seed = resolved_seed
            store.save_job(job)
            if cancel_event.is_set():
                raise ComfyError("Job cancelled")

            job.status = JobStatus.running
            store.save_job(job)
            await runtime.update_phase(job.id, job.status.value, "generating")
            prompt_id = await client.queue_prompt(prompt)
            job.comfy_prompt_id = prompt_id
            store.save_job(job)

            history = await client.wait_for_completion(
                prompt_id,
                cancel_event=cancel_event,
            )
            await runtime.update_phase(job.id, job.status.value, "saving")
            job = await runtime.save_completed_outputs(
                job.id,
                pipeline=pipeline,
                client=client,
                history=history,
            )
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            job = store.load_job(job.id) or job
            if cancel_event.is_set() or "cancel" in str(exc).lower():
                job.status = JobStatus.cancelled
                job.error = str(exc)
            else:
                job.status = JobStatus.failed
                job.error = str(exc)[:1000]
            store.save_job(job)
        finally:
            await self._finalize(job.id, runtime)

    async def resume(
        self,
        job: JobRecord,
        pipeline: Any,
        cancel_event: asyncio.Event,
        runtime: ComfyExecutionRuntime,
    ) -> None:
        if not job.comfy_prompt_id:
            return
        client = _client_for_job(runtime, job)
        try:
            await runtime.prepare(job)
            await runtime.update_phase(job.id, job.status.value, "generating")
            history = await client.wait_for_completion(
                job.comfy_prompt_id,
                cancel_event=cancel_event,
            )
            await runtime.update_phase(job.id, job.status.value, "saving")
            job = await runtime.save_completed_outputs(
                job.id,
                pipeline=pipeline,
                client=client,
                history=history,
            )
        except Exception as exc:
            logger.exception("Resumed job %s failed", job.id)
            job = store.load_job(job.id) or job
            if cancel_event.is_set() or "cancel" in str(exc).lower():
                job.status = JobStatus.cancelled
                job.error = str(exc)
            else:
                job.status = JobStatus.failed
                job.error = str(exc)[:1000]
            store.save_job(job)
        finally:
            await self._finalize(job.id, runtime)

    async def cancel(
        self, runtime: ComfyExecutionRuntime, job: JobRecord | None = None
    ) -> None:
        try:
            client = _client_for_job(runtime, job) if job else runtime.client_factory()
            await client.interrupt()
        except Exception:
            return

    async def _finalize(
        self,
        job_id: str,
        runtime: ComfyExecutionRuntime,
    ) -> None:
        job = store.load_job(job_id)
        if job is None:
            return
        try:
            await runtime.finish(job)
        except Exception:
            logger.exception(
                "finish_comfy failed for job %s (%s)",
                job.id,
                job.pipeline_id,
            )
        try:
            from ..shot_sync import on_pipeline_job_terminal

            on_pipeline_job_terminal(job)
        except Exception:
            logger.exception(
                "shot_sync failed for job %s (%s)",
                job.id,
                job.pipeline_id,
            )


def _client_for_job(runtime: ComfyExecutionRuntime, job: JobRecord) -> Any:
    if runtime.client_factory_for_job is not None:
        return runtime.client_factory_for_job(job)
    return runtime.client_factory()
