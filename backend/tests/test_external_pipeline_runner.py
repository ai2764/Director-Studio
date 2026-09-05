from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.core.jobs import runner, store
from app.core.jobs.execution_adapters.external import ExternalExecutionAdapter
from app.core.schemas import JobStatus
from app.pipelines.base import ExternalPipeline, ExternalPipelineResult
from app.pipelines.gpt_actor.pipeline import GptActorPipeline
from app.pipelines.gpt_ref_frame.pipeline import GptRefFramePipeline


PNG = b"\x89PNG\r\n\x1a\nexternal-result"


class FakeExternalPipeline(ExternalPipeline):
    id = "fake_external"
    asset_kind = "layouts"
    display_name = "Fake external"
    replay_after_restart = False

    @property
    def output_labels(self):
        return {"layout": "Layout"}

    async def run_external(self, job, *, inputs, cancel_event):
        return ExternalPipelineResult(
            outputs={"layout": ("result.png", PNG)},
            params_update={"external_request_id": "request_public"},
        )


@pytest.mark.asyncio
async def test_external_job_persists_output_without_comfy_or_vram(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    pipeline = FakeExternalPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)
    monkeypatch.setattr(
        runner,
        "ComfyClient",
        lambda: (_ for _ in ()).throw(AssertionError("ComfyClient must not be created")),
    )

    class ForbiddenOrchestrator:
        def __getattr__(self, name):
            raise AssertionError(f"VRAM orchestrator must not be called: {name}")

    monkeypatch.setattr(runner, "get_orchestrator", lambda: ForbiddenOrchestrator())
    job = store.create_job(
        pipeline_id=pipeline.id,
        asset_kind="layouts",
        name="external-test",
    )

    await runner.start_pipeline_job(
        job,
        images={"ref_0": ("source.png", PNG)},
    )
    final = await runner.await_pipeline_job(job.id)

    assert final is not None
    assert final.status == JobStatus.succeeded
    assert final.params["external_request_id"] == "request_public"
    assert final.outputs["layout"].filename == "layout.png"
    assert (store.job_dir(job.id) / "outputs" / "layout.png").read_bytes() == PNG


@pytest.mark.asyncio
async def test_chatgpt_bridge_jobs_wait_for_the_previous_request_to_finish(
    tmp_path, monkeypatch
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    monkeypatch.setattr(settings, "gpt_bridge_job_cooldown_sec", 0)

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    class BlockingActorPipeline(GptActorPipeline):
        async def run_external(self, job, *, inputs, cancel_event):
            first_started.set()
            await release_first.wait()
            return ExternalPipelineResult(outputs={"master": ("actor.png", PNG)})

    class ObservedRefFramePipeline(GptRefFramePipeline):
        async def run_external(self, job, *, inputs, cancel_event):
            second_started.set()
            return ExternalPipelineResult(outputs={"layout": ("layout.png", PNG)})

    pipelines = {
        "gpt_actor": BlockingActorPipeline(),
        "gpt_ref_frame": ObservedRefFramePipeline(),
    }
    monkeypatch.setattr(runner, "get_pipeline", pipelines.__getitem__)

    actor_job = store.create_job(
        pipeline_id="gpt_actor",
        asset_kind="actors",
        name="serialized-actor",
    )
    ref_job = store.create_job(
        pipeline_id="gpt_ref_frame",
        asset_kind="layouts",
        name="serialized-ref",
    )

    await runner.start_pipeline_job(actor_job)
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await runner.start_pipeline_job(ref_job)

    try:
        await asyncio.wait_for(second_started.wait(), timeout=0.05)
        second_started_before_release = True
    except TimeoutError:
        second_started_before_release = False
    finally:
        release_first.set()

    await asyncio.gather(
        runner.await_pipeline_job(actor_job.id),
        runner.await_pipeline_job(ref_job.id),
    )

    assert second_started_before_release is False
    assert second_started.is_set()


@pytest.mark.asyncio
async def test_execution_lane_holds_next_job_during_post_job_cooldown(
    tmp_path, monkeypatch
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    cooldown_started = asyncio.Event()
    release_cooldown = asyncio.Event()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        cooldown_started.set()
        await release_cooldown.wait()

    class CooledPipeline(FakeExternalPipeline):
        execution_lane = "paced_bridge"
        execution_lane_cooldown_sec = 15.0

        async def run_external(self, job, *, inputs, cancel_event):
            if job.name == "first":
                first_started.set()
            else:
                second_started.set()
            return ExternalPipelineResult(outputs={"layout": ("result.png", PNG)})

    adapter = ExternalExecutionAdapter(sleep=fake_sleep)
    pipeline = CooledPipeline()
    first = store.create_job(
        pipeline_id=pipeline.id,
        asset_kind="layouts",
        name="first",
    )
    second = store.create_job(
        pipeline_id=pipeline.id,
        asset_kind="layouts",
        name="second",
    )

    first_task = asyncio.create_task(
        adapter.run(first, pipeline, {}, asyncio.Event())
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.wait_for(cooldown_started.wait(), timeout=1)
    second_task = asyncio.create_task(
        adapter.run(second, pipeline, {}, asyncio.Event())
    )

    try:
        await asyncio.wait_for(second_started.wait(), timeout=0.05)
        second_started_during_cooldown = True
    except TimeoutError:
        second_started_during_cooldown = False
    finally:
        release_cooldown.set()

    await asyncio.gather(first_task, second_task)

    assert second_started_during_cooldown is False
    assert second_started.is_set()
    assert slept == [15.0, 15.0]


@pytest.mark.asyncio
async def test_cancel_external_job_never_interrupts_comfy(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    pipeline = FakeExternalPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)

    class ForbiddenComfyClient:
        async def interrupt(self):
            raise AssertionError("external cancellation must not interrupt Comfy")

    monkeypatch.setattr(runner, "ComfyClient", ForbiddenComfyClient)
    job = store.create_job(
        pipeline_id=pipeline.id,
        asset_kind="layouts",
        name="external-cancel",
    )
    job.status = JobStatus.running
    store.save_job(job)
    event = asyncio.Event()
    runner._cancel_events[job.id] = event

    cancelled = await runner.cancel_job(job.id)

    assert event.is_set()
    assert cancelled is not None
    assert cancelled.status == JobStatus.cancelled


@pytest.mark.asyncio
async def test_external_runner_records_cancelled_error(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)

    class CancellingPipeline(FakeExternalPipeline):
        async def run_external(self, job, *, inputs, cancel_event):
            raise asyncio.CancelledError

    pipeline = CancellingPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)
    job = store.create_job(
        pipeline_id=pipeline.id,
        asset_kind="layouts",
        name="external-cancelled-error",
    )

    await runner._run_job(job.id, {}, asyncio.Event())

    final = store.load_job(job.id)
    assert final is not None
    assert final.status == JobStatus.cancelled


@pytest.mark.asyncio
async def test_recovery_fails_nonreplayable_external_job_without_execution(
    tmp_projects_dir, monkeypatch
):
    jobs_root = tmp_projects_dir.parent / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    pipeline = FakeExternalPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)
    job = store.create_job(
        pipeline_id=pipeline.id,
        asset_kind="layouts",
        name="external-interrupted",
        project_id="prj_recovery",
    )
    job.status = JobStatus.running
    store.save_job(job)

    async def forbidden_start(*args, **kwargs):
        raise AssertionError("an uncertain external generation must not be replayed")

    monkeypatch.setattr(runner, "start_pipeline_job", forbidden_start)

    recovered = await runner.recover_interrupted_jobs()
    final = store.load_job(job.id)

    assert recovered == []
    assert final is not None
    assert final.status == JobStatus.failed
    assert "interrupted" in (final.error or "").lower()
    assert "generate again" in (final.error or "").lower()
