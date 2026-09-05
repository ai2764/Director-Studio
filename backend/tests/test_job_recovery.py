from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from app.core.jobs import runner, store
from app.core.schemas import ComfyImageRef, JobStatus
from app.config import settings


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_replays_saved_inputs(
    tmp_projects_dir, monkeypatch
):
    jobs_dir = tmp_projects_dir.parent / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_dir)
    job = store.create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="layout:recover me",
        project_id="prj_recovery",
    )
    store.save_input_file(
        job.id,
        "ref_0",
        "source.png",
        b"saved image bytes",
        project_id=job.project_id,
    )
    job.status = JobStatus.uploading
    store.save_job(job)

    recovered: list[tuple[str, dict[str, tuple[str, bytes]]]] = []

    async def fake_start(current, *, images=None):
        recovered.append((current.id, images or {}))
        return current

    monkeypatch.setattr(runner, "start_pipeline_job", fake_start)

    ids = await runner.recover_interrupted_jobs()

    assert ids == [job.id]
    assert recovered == [
        (job.id, {"ref_0": ("ref_0.png", b"saved image bytes")})
    ]
    assert store.load_job(job.id).status == JobStatus.queued


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_resumes_submitted_job_without_requeue(
    tmp_projects_dir, monkeypatch
):
    jobs_dir = tmp_projects_dir.parent / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_dir)
    job = store.create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="layout:already submitted",
        project_id="prj_recovery",
    )
    job.status = JobStatus.running
    job.comfy_prompt_id = "prompt_already_on_comfy"
    store.save_job(job)

    starts: list[str] = []
    resumes: list[str] = []

    async def fake_start(current, *, images=None):
        starts.append(current.id)
        return current

    async def fake_resume(current):
        resumes.append(current.id)
        return current

    monkeypatch.setattr(runner, "start_pipeline_job", fake_start)
    monkeypatch.setattr(runner, "resume_pipeline_job", fake_resume)

    assert await runner.recover_interrupted_jobs() == [job.id]
    assert starts == []
    assert resumes == [job.id]


@pytest.mark.asyncio
async def test_resume_job_collects_existing_prompt_without_resubmitting(
    tmp_projects_dir, monkeypatch
):
    jobs_dir = tmp_projects_dir.parent / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_dir)
    job = store.create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="layout:collect me",
    )
    job.status = JobStatus.running
    job.comfy_prompt_id = "prompt-existing"
    store.save_job(job)

    class FakePipeline:
        id = "ref_frame"
        execution_adapter_id = "comfy"
        output_labels = {"layout": "Layout"}

        def map_history_outputs(self, history, *, job=None):
            return {
                "layout": ComfyImageRef(
                    filename="layout.png", subfolder="", type="output"
                )
            }

        def postprocess_job_outputs(self, job, saved):
            return None

    fake_client = SimpleNamespace(
        wait_for_completion=AsyncMock(return_value={"outputs": {}}),
        download_image=AsyncMock(return_value=b"layout bytes"),
    )

    class FakeOrchestrator:
        def __init__(self):
            self.phases = []
            self.released = []

        async def before_comfy_job(self, pipeline_id):
            return None

        async def after_comfy_job(self, pipeline_id, status):
            return None

        async def update_generation(self, job_id, *, status, phase):
            self.phases.append((job_id, status, phase))

        async def release_generation(self, job_id):
            self.released.append(job_id)

    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: FakePipeline())
    monkeypatch.setattr(runner, "ComfyClient", lambda: fake_client)
    fake_orchestrator = FakeOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: fake_orchestrator)

    await runner._resume_job(job.id, asyncio.Event())

    final = store.load_job(job.id)
    assert final.status == JobStatus.succeeded
    fake_client.wait_for_completion.assert_awaited_once_with(
        "prompt-existing", cancel_event=ANY
    )
    assert fake_orchestrator.phases == [
        (job.id, "running", "generating"),
        (job.id, "running", "saving"),
    ]
    assert fake_orchestrator.released == [job.id]
    assert not hasattr(fake_client, "queue_prompt")
