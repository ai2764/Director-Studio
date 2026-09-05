"""Execution adapter for the official MiniMax H3 asynchronous API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from ....integrations.minimax_h3 import MiniMaxH3Client
from ...schemas import JobRecord, JobStatus
from .. import store

logger = logging.getLogger("director_studio.jobs.execution.h3_api")


@dataclass(frozen=True)
class H3ApiExecutionRuntime:
    client_factory: Callable[[], MiniMaxH3Client]


class H3ApiExecutionAdapter:
    id = "h3_api"
    interrupt_on_cancel = False

    def can_replay(self, pipeline: Any) -> bool:
        del pipeline
        # If the process died after POST succeeded but before task_id was
        # persisted, replay could create a second paid generation.
        return False

    @staticmethod
    def can_replay_job(job: JobRecord) -> bool:
        return job.status in {JobStatus.queued, JobStatus.uploading}

    @staticmethod
    def submission_id(job: JobRecord) -> str | None:
        return job.external_task_id

    async def run(
        self,
        job: JobRecord,
        pipeline: Any,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
        runtime: H3ApiExecutionRuntime,
    ) -> None:
        client = runtime.client_factory()
        try:
            job.status = JobStatus.uploading
            store.save_job(job)
            payload = pipeline.build_api_payload(job, inputs=inputs)
            if cancel_event.is_set():
                raise asyncio.CancelledError

            job.status = JobStatus.running
            store.save_job(job)
            task_id = await client.create_video(payload)
            job = store.load_job(job.id) or job
            job.external_task_id = task_id
            store.save_job(job)
            await self._collect(job, pipeline, client, cancel_event)
        except asyncio.CancelledError:
            self._mark_cancelled(job.id)
        except Exception as exc:
            if cancel_event.is_set():
                self._mark_cancelled(
                    job.id,
                    submission_uncertain=(
                        job.status == JobStatus.running and not job.external_task_id
                    ),
                )
            else:
                self._mark_failed(job.id, exc, cancel_event)
        finally:
            self._finalize(job.id)

    async def resume(
        self,
        job: JobRecord,
        pipeline: Any,
        cancel_event: asyncio.Event,
        runtime: H3ApiExecutionRuntime,
    ) -> None:
        if not job.external_task_id:
            return
        try:
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
            self._finalize(job.id)

    async def cancel(self, runtime: H3ApiExecutionRuntime) -> None:
        # Local cancellation stops polling. The provider does not need the
        # Comfy interrupt path, and remote cancellation can be added separately.
        del runtime

    async def _collect(
        self,
        job: JobRecord,
        pipeline: Any,
        client: MiniMaxH3Client,
        cancel_event: asyncio.Event,
    ) -> None:
        result = await client.wait_for_video(
            str(job.external_task_id),
            cancel_event=cancel_event,
        )
        if cancel_event.is_set():
            raise asyncio.CancelledError
        video = await client.download_video(result.download_url)
        if cancel_event.is_set():
            raise asyncio.CancelledError
        saved = {
            "video": store.save_output_file(
                job.id,
                "video",
                "video.mp4",
                video,
                project_id=job.project_id,
            )
        }
        job = store.load_job(job.id) or job
        job.params = {**job.params, "minimax_task": result.task}
        try:
            pipeline.postprocess_job_outputs(job, saved)
        except Exception:
            logger.exception("postprocess failed for MiniMax H3 job %s", job.id)
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
    def _mark_cancelled(
        job_id: str,
        *,
        submission_uncertain: bool = False,
    ) -> None:
        job = store.load_job(job_id)
        if job is None:
            return
        job.status = JobStatus.cancelled
        if submission_uncertain and not job.external_task_id:
            job.error = (
                "Cancelled locally while the MiniMax create request was in flight; "
                "submission outcome is uncertain and the remote task may continue "
                "and incur cost. Do not resubmit until the provider task list is checked."
            )
        elif job.external_task_id:
            job.error = (
                "Polling cancelled locally; MiniMax task "
                f"{job.external_task_id} may continue and incur cost"
            )
        else:
            job.error = "MiniMax H3 API job cancelled locally before submission"
        store.save_job(job)

    @staticmethod
    def _mark_failed(
        job_id: str,
        exc: Exception,
        cancel_event: asyncio.Event,
    ) -> None:
        logger.exception("MiniMax H3 API job %s failed", job_id)
        job = store.load_job(job_id)
        if job is None:
            return
        job.status = JobStatus.cancelled if cancel_event.is_set() else JobStatus.failed
        job.error = str(exc)[:1000]
        store.save_job(job)

    @staticmethod
    def _finalize(job_id: str) -> None:
        job = store.load_job(job_id)
        if job is None:
            return
        try:
            from ..shot_sync import on_pipeline_job_terminal

            on_pipeline_job_terminal(job)
        except Exception:
            logger.exception("shot_sync failed for MiniMax H3 API job %s", job.id)
