"""Execution adapter for non-Comfy pipelines such as ChatGPT Bridge."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from weakref import WeakKeyDictionary

from ....pipelines.base import ExternalPipeline, ExternalPipelineResult
from ...schemas import JobRecord, JobStatus
from .. import store

logger = logging.getLogger("director_studio.jobs.execution.external")


class ExternalExecutionAdapter:
    id = "external"
    interrupt_on_cancel = False

    def __init__(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._lane_locks: WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
        ] = WeakKeyDictionary()
        self._sleep = sleep or asyncio.sleep

    def can_replay(self, pipeline: ExternalPipeline) -> bool:
        return bool(pipeline.replay_after_restart)

    async def run(
        self,
        job: JobRecord,
        pipeline: ExternalPipeline,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
        runtime: object | None = None,
    ) -> None:
        del runtime
        lane = pipeline.execution_lane
        if lane:
            loop = asyncio.get_running_loop()
            locks = self._lane_locks.setdefault(loop, {})
            lock = locks.setdefault(lane, asyncio.Lock())
            async with lock:
                try:
                    await self._run_now(job, pipeline, inputs, cancel_event)
                finally:
                    cooldown = max(
                        0.0,
                        float(pipeline.execution_lane_cooldown_sec),
                    )
                    if cooldown > 0:
                        await self._sleep(cooldown)
            return
        await self._run_now(job, pipeline, inputs, cancel_event)

    async def _run_now(
        self,
        job: JobRecord,
        pipeline: ExternalPipeline,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
    ) -> None:
        try:
            job.status = JobStatus.uploading
            store.save_job(job)
            if cancel_event.is_set():
                raise asyncio.CancelledError

            job.status = JobStatus.running
            store.save_job(job)
            result: ExternalPipelineResult = await pipeline.run_external(
                job,
                inputs=inputs,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise asyncio.CancelledError
            if not result.outputs:
                raise RuntimeError("External pipeline finished without output files")

            saved: dict[str, Path] = {}
            for key, (filename, data) in result.outputs.items():
                saved[key] = store.save_output_file(
                    job.id,
                    key,
                    filename,
                    data,
                    project_id=job.project_id,
                )

            job = store.load_job(job.id) or job
            job.params = {**job.params, **result.params_update}
            try:
                pipeline.postprocess_job_outputs(job, saved)
            except Exception:
                logger.exception(
                    "postprocess_job_outputs failed for external job %s (%s)",
                    job.id,
                    job.pipeline_id,
                )
            job.status = JobStatus.succeeded
            job.error = None
            job.outputs = store.build_output_slots(
                job.id,
                saved,
                labels=pipeline.output_labels,
            )
            job.input_previews = store.input_preview_urls(job.id)
            store.save_job(job)
        except asyncio.CancelledError:
            job = store.load_job(job.id) or job
            job.status = JobStatus.cancelled
            job.error = "External job cancelled"
            store.save_job(job)
        except Exception as exc:
            logger.exception("External job %s failed", job.id)
            job = store.load_job(job.id) or job
            job.status = (
                JobStatus.cancelled if cancel_event.is_set() else JobStatus.failed
            )
            job.error = str(exc)[:1000]
            store.save_job(job)
        finally:
            await self._finalize(job.id)

    async def _finalize(self, job_id: str) -> None:
        job = store.load_job(job_id)
        if job is None:
            return
        try:
            from ..shot_sync import on_pipeline_job_terminal

            on_pipeline_job_terminal(job)
        except Exception:
            logger.exception(
                "shot_sync failed for external job %s (%s)",
                job.id,
                job.pipeline_id,
            )
