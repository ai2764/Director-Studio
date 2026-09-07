from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ...integrations.comfy_mcp import ComfyMcpClient
from ...integrations.minimax_h3 import MiniMaxH3Client
from ...pipelines.base import ExternalPipeline
from ...pipelines.registry import get_pipeline
from ..comfy import ComfyClient, ComfyError
from ..paths import find_job_dir
from ..schemas import JobRecord, JobStatus
from ..vram import get_orchestrator
from . import store
from .execution import ExecutionAdapterRegistry
from .execution_adapters.comfy import ComfyExecutionAdapter, ComfyExecutionRuntime
from .execution_adapters.comfy_mcp import (
    ComfyMcpExecutionAdapter,
    ComfyMcpExecutionRuntime,
)
from .execution_adapters.external import ExternalExecutionAdapter
from .execution_adapters.h3_api import H3ApiExecutionAdapter, H3ApiExecutionRuntime

logger = logging.getLogger("director_studio.jobs")

_tasks: dict[str, asyncio.Task[None]] = {}
_cancel_events: dict[str, asyncio.Event] = {}
_comfy_execution_adapter = ComfyExecutionAdapter()
_comfy_mcp_execution_adapter = ComfyMcpExecutionAdapter()
_external_execution_adapter = ExternalExecutionAdapter()
_h3_api_execution_adapter = H3ApiExecutionAdapter()
_comfy_mcp_client = ComfyMcpClient()
_execution_adapters = ExecutionAdapterRegistry(
    [
        _comfy_execution_adapter,
        _comfy_mcp_execution_adapter,
        _external_execution_adapter,
        _h3_api_execution_adapter,
    ]
)


def _comfy_runtime() -> ComfyExecutionRuntime:
    return ComfyExecutionRuntime(
        client_factory=ComfyClient,
        prepare=prepare_comfy,
        finish=finish_comfy,
        update_phase=update_generation_phase,
        save_completed_outputs=_save_completed_outputs,
    )


def _h3_api_runtime() -> H3ApiExecutionRuntime:
    return H3ApiExecutionRuntime(client_factory=MiniMaxH3Client)


def _comfy_mcp_runtime() -> ComfyMcpExecutionRuntime:
    return ComfyMcpExecutionRuntime(
        client_factory=lambda: _comfy_mcp_client,
        prepare=prepare_comfy,
        finish=finish_comfy,
    )


def _runtime_for(adapter: Any) -> Any:
    if adapter.id == "comfy":
        return _comfy_runtime()
    if adapter.id == "comfy_mcp":
        return _comfy_mcp_runtime()
    if adapter.id == "h3_api":
        return _h3_api_runtime()
    return None


async def close_execution_runtimes() -> None:
    """Release persistent protocol clients owned by the job runner."""
    await _comfy_mcp_client.aclose()


def _submission_id(adapter: Any, job: JobRecord) -> str | None:
    getter = getattr(adapter, "submission_id", None)
    if callable(getter):
        return getter(job)
    return job.comfy_prompt_id


def _can_replay(adapter: Any, pipeline: Any, job: JobRecord) -> bool:
    checker = getattr(adapter, "can_replay_job", None)
    if callable(checker):
        return bool(checker(job))
    return bool(adapter.can_replay(pipeline))


# All Comfy pipeline jobs use exclusive VRAM (unload LLM before queue).
# Includes h3_ref2va (video), ref_frame (reference still), actor, scene, …
EXCLUSIVE_PIPELINES: frozenset[str] | None = None  # None = all pipelines
LOCAL_COMFY_ADAPTER_IDS = frozenset({"comfy", "comfy_mcp"})


def _uses_exclusive_vram(pipeline_id: str) -> bool:
    if EXCLUSIVE_PIPELINES is None:
        return True
    return pipeline_id in EXCLUSIVE_PIPELINES


async def prepare_comfy(job: JobRecord) -> None:
    """Claim exclusive GPU / unload Ollama before Comfy upload/queue (incl. video)."""
    if not _uses_exclusive_vram(job.pipeline_id):
        return
    logger.info(
        "prepare_comfy: unload LLM before pipeline=%s job=%s", job.pipeline_id, job.id
    )
    await get_orchestrator().before_comfy_job(job.pipeline_id)


async def finish_comfy(job: JobRecord) -> None:
    """Clear Comfy GPU ownership after a terminal job status."""
    if not _uses_exclusive_vram(job.pipeline_id):
        return
    await get_orchestrator().after_comfy_job(job.pipeline_id, job.status.value)


async def update_generation_phase(job_id: str, status: str, phase: str) -> None:
    await get_orchestrator().update_generation(
        job_id,
        status=status,
        phase=phase,
    )


async def _reserve_local_generation(
    job: JobRecord, pipeline: Any, adapter: Any
) -> bool:
    if adapter.id not in LOCAL_COMFY_ADAPTER_IDS:
        return False
    phase = "queued" if job.status == JobStatus.queued else "generating"
    await get_orchestrator().reserve_generation(
        job_id=job.id,
        pipeline_id=job.pipeline_id,
        kind=pipeline.generation_kind,
        status=job.status.value,
        phase=phase,
        queued_at=job.created_at,
    )
    return True


async def start_pipeline_job(
    job: JobRecord,
    *,
    images: dict[str, tuple[str, bytes]] | None = None,
) -> JobRecord:
    """
    Persist input images and run the job's pipeline in the background.

    `images` maps logical input names (e.g. "actor", "wardrobe") to (filename, bytes).
    """
    images = images or {}
    for kind, (filename, data) in images.items():
        store.save_input_file(job.id, kind, filename, data, project_id=job.project_id)

    pipeline = get_pipeline(job.pipeline_id)
    prepare_submission = getattr(pipeline, "prepare_job_submission", None)
    if callable(prepare_submission):
        try:
            prepare_submission(job)
        except Exception as exc:
            _fail_job_preparation(job, exc)
            raise
    store.save_job(job)
    labels = (
        pipeline.labels_for_job(job)
        if hasattr(pipeline, "labels_for_job")
        else pipeline.output_labels
    )
    job = store.enrich_job_urls(job, labels=labels)
    store.save_job(job)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    reserved = await _reserve_local_generation(job, pipeline, adapter)

    cancel = asyncio.Event()
    _cancel_events[job.id] = cancel
    try:
        task = asyncio.create_task(_run_job(job.id, images, cancel))
    except Exception:
        _cancel_events.pop(job.id, None)
        if reserved:
            await get_orchestrator().release_generation(job.id)
        raise
    _tasks[job.id] = task
    task.add_done_callback(lambda _t, jid=job.id: _tasks.pop(jid, None))
    return job


async def await_pipeline_job(job_id: str) -> JobRecord | None:
    """Wait for this process's task, then return its latest durable record."""
    task = _tasks.get(job_id)
    if task is not None:
        await asyncio.shield(task)
    return store.load_job(job_id)


async def resume_pipeline_job(job: JobRecord) -> JobRecord:
    """Resume result collection for a task already accepted by its provider."""
    pipeline = get_pipeline(job.pipeline_id)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    if not _submission_id(adapter, job):
        raise ValueError(f"job {job.id} has no submitted provider task to resume")
    if job.id in _tasks:
        return job

    pipeline = get_pipeline(job.pipeline_id)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    reserved = await _reserve_local_generation(job, pipeline, adapter)

    cancel = asyncio.Event()
    _cancel_events[job.id] = cancel
    try:
        task = asyncio.create_task(_resume_job(job.id, cancel))
    except Exception:
        _cancel_events.pop(job.id, None)
        if reserved:
            await get_orchestrator().release_generation(job.id)
        raise
    _tasks[job.id] = task
    task.add_done_callback(lambda _t, jid=job.id: _tasks.pop(jid, None))
    return job


async def recover_interrupted_jobs() -> list[str]:
    """Recover interrupted local and external provider jobs safely.

    Uvicorn reloads and process restarts discard the in-memory asyncio tasks.
    Submitted task IDs resume waiting/downloading. Pre-submit jobs replay only
    when their adapter can prove replay is safe.
    """
    recoverable = {JobStatus.queued, JobStatus.uploading, JobStatus.running}
    candidates = sorted(
        store.list_jobs(limit=10_000),
        key=_recovery_sort_key,
    )
    recovered: list[str] = []

    for job in candidates:
        if job.status not in recoverable or job.id in _tasks:
            continue
        try:
            if await _recover_interrupted_job(job):
                recovered.append(job.id)
        except Exception as exc:
            logger.exception("Could not recover job %s (%s)", job.id, job.pipeline_id)
            try:
                _fail_job_preparation(job, exc)
            except Exception:
                logger.exception("Could not persist recovery failure for %s", job.id)
    return recovered


def _fail_job_preparation(job: JobRecord, exc: Exception) -> None:
    job.status = JobStatus.failed
    job.error = f"Job preparation failed: {exc}"
    store.save_job(job)
    try:
        from .shot_sync import on_pipeline_job_terminal

        on_pipeline_job_terminal(job)
    except Exception:
        logger.exception("shot_sync failed for preparation failure %s", job.id)


async def _recover_interrupted_job(job: JobRecord) -> bool:
    pipeline = get_pipeline(job.pipeline_id)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    if _submission_id(adapter, job):
        await resume_pipeline_job(job)
        logger.info("resumed submitted job %s (%s)", job.id, job.pipeline_id)
        return True

    if not _can_replay(adapter, pipeline, job):
        job.status = JobStatus.failed
        job.error = (
            "Provider generation was interrupted and was not replayed because "
            "submission may already have occurred. Ask the user to generate again."
        )
        store.save_job(job)
        try:
            from .shot_sync import on_pipeline_job_terminal

            on_pipeline_job_terminal(job)
        except Exception:
            logger.exception("shot_sync failed for interrupted external job %s", job.id)
        return False

    directory = find_job_dir(job.id)
    images: dict[str, tuple[str, bytes]] = {}
    if directory is not None:
        inputs = directory / "inputs"
        if inputs.is_dir():
            for path in sorted(inputs.iterdir()):
                if path.is_file():
                    images[path.stem] = (path.name, path.read_bytes())

    job.status = JobStatus.queued
    job.error = None
    store.save_job(job)
    await start_pipeline_job(job, images=images or None)
    logger.info("recovered interrupted job %s (%s)", job.id, job.pipeline_id)
    return True


def _recovery_sort_key(job: JobRecord) -> tuple[bool, str]:
    """Resume submitted provider work before considering safe replays."""
    try:
        pipeline = get_pipeline(job.pipeline_id)
        adapter = _execution_adapters.resolve(pipeline, job=job)
        submitted = bool(_submission_id(adapter, job))
    except (KeyError, ValueError):
        submitted = bool(job.comfy_prompt_id or job.external_task_id)
    return (not submitted, job.created_at)


async def _save_completed_outputs(
    job_id: str,
    *,
    pipeline: Any,
    client: ComfyClient,
    history: dict[str, Any],
) -> JobRecord:
    """Map, download, postprocess, and persist one completed Comfy prompt."""
    job = store.load_job(job_id)
    if job is None:
        raise ComfyError(f"job disappeared while completing: {job_id}")
    mapped = pipeline.map_history_outputs(history, job=job)
    if not mapped:
        raise ComfyError("Job finished but no expected outputs found")

    saved: dict[str, Path] = {}
    for key, ref in mapped.items():
        data = await client.download_image(
            ref.filename,
            subfolder=ref.subfolder,
            folder_type=ref.type,
        )
        path = store.save_output_file(job_id, key, ref.filename, data)
        saved[key] = path

    job = store.load_job(job_id) or job
    try:
        pipeline.postprocess_job_outputs(job, saved)
    except Exception:
        logger.exception(
            "postprocess_job_outputs failed for %s (%s)",
            job_id,
            job.pipeline_id,
        )

    labels = (
        pipeline.labels_for_job(job)
        if hasattr(pipeline, "labels_for_job")
        else pipeline.output_labels
    )
    job.status = JobStatus.succeeded
    job.error = None
    job.outputs = store.build_output_slots(job_id, saved, labels=labels)
    job.input_previews = store.input_preview_urls(job_id)
    store.save_job(job)
    logger.info(
        "Job %s (%s) succeeded with %s outputs",
        job_id,
        job.pipeline_id,
        list(saved.keys()),
    )
    return job


async def _resume_job(job_id: str, cancel: asyncio.Event) -> None:
    """Resume a submitted job through its declared execution adapter."""
    job = store.load_job(job_id)
    if job is None:
        return
    pipeline = get_pipeline(job.pipeline_id)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    try:
        await adapter.resume(job, pipeline, cancel, _runtime_for(adapter))
    finally:
        if adapter.id in LOCAL_COMFY_ADAPTER_IDS:
            await get_orchestrator().release_generation(job_id)
        _cancel_events.pop(job_id, None)


async def cancel_job(job_id: str) -> JobRecord | None:
    job = store.load_job(job_id)
    if not job:
        return None
    ev = _cancel_events.get(job_id)
    if ev:
        ev.set()
    pipeline = get_pipeline(job.pipeline_id)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    if job.status in (JobStatus.running, JobStatus.uploading, JobStatus.queued):
        previous_status = job.status
        if adapter.interrupt_on_cancel:
            await adapter.cancel(_runtime_for(adapter))
        job.status = JobStatus.cancelled
        if adapter.id == "h3_api":
            if job.external_task_id:
                job.error = (
                    "Polling cancelled locally; MiniMax task "
                    f"{job.external_task_id} may continue and incur cost"
                )
            elif previous_status == JobStatus.running:
                job.error = (
                    "Cancelled locally while the MiniMax create request was in flight; "
                    "submission outcome is uncertain and the remote task may continue "
                    "and incur cost. Do not resubmit until the provider task list is checked."
                )
            else:
                job.error = "MiniMax H3 API job cancelled locally before submission"
        else:
            job.error = "Cancelled by user"
        store.save_job(job)
        if adapter.id in LOCAL_COMFY_ADAPTER_IDS and job_id not in _tasks:
            await get_orchestrator().release_generation(job_id)
    if job.status == JobStatus.cancelled:
        try:
            from .shot_sync import on_pipeline_job_terminal

            on_pipeline_job_terminal(job)
        except Exception:
            logger.exception(
                "shot_sync failed while cancelling job %s (%s)",
                job.id,
                job.pipeline_id,
            )
    labels = (
        pipeline.labels_for_job(job)
        if hasattr(pipeline, "labels_for_job")
        else pipeline.output_labels
    )
    return store.enrich_job_urls(job, labels=labels)


async def _run_job(
    job_id: str,
    images: dict[str, tuple[str, bytes]],
    cancel: asyncio.Event,
) -> None:
    job = store.load_job(job_id)
    if job is None:
        return
    pipeline = get_pipeline(job.pipeline_id)
    adapter = _execution_adapters.resolve(pipeline, job=job)
    runtime = _runtime_for(adapter)
    try:
        await adapter.run(job, pipeline, images, cancel, runtime)
    finally:
        if adapter.id in LOCAL_COMFY_ADAPTER_IDS:
            await get_orchestrator().release_generation(job_id)
        _cancel_events.pop(job_id, None)


async def _run_external_job(
    job: JobRecord,
    pipeline: ExternalPipeline,
    images: dict[str, tuple[str, bytes]],
    cancel: asyncio.Event,
) -> None:
    """Compatibility wrapper around the external execution adapter."""
    try:
        await _external_execution_adapter.run(job, pipeline, images, cancel)
    finally:
        _cancel_events.pop(job.id, None)
