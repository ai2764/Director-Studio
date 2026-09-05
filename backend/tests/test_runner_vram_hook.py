"""VRAM orchestrator hooks on the job runner (before/after Comfy)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.jobs import runner, store
from app.core.schemas import ComfyImageRef, JobRecord, JobStatus


class RecordingOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def reserve_generation(self, **kwargs) -> None:
        self.calls.append(
            (
                "reserve",
                kwargs["job_id"],
                kwargs["pipeline_id"],
                kwargs["kind"],
                kwargs["status"],
                kwargs["phase"],
            )
        )

    async def update_generation(self, job_id: str, *, status: str, phase: str) -> None:
        self.calls.append(("phase", job_id, status, phase))

    async def release_generation(self, job_id: str) -> None:
        self.calls.append(("release", job_id))

    async def before_comfy_job(self, pipeline_id: str) -> None:
        self.calls.append(("before", pipeline_id))

    async def after_comfy_job(self, pipeline_id: str, terminal_status: str) -> None:
        self.calls.append(("after", pipeline_id, terminal_status))


def _job(*, pipeline_id: str = "ref_frame", status: JobStatus = JobStatus.queued) -> JobRecord:
    return JobRecord(
        id="job_vram_test",
        pipeline_id=pipeline_id,
        asset_kind="layouts",
        status=status,
        name="vram-hook-test",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_prepare_and_finish_comfy_call_orchestrator(monkeypatch):
    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    job = _job(pipeline_id="ref_frame")
    await runner.prepare_comfy(job)
    job.status = JobStatus.succeeded
    await runner.finish_comfy(job)

    assert orch.calls == [
        ("before", "ref_frame"),
        ("after", "ref_frame", "succeeded"),
    ]


@pytest.mark.asyncio
async def test_prepare_comfy_all_pipelines(monkeypatch):
    """Exclusive policy applies to every Comfy pipeline job, not only H3/ref_frame."""
    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    for pid in ("ref_frame", "h3_ref2va", "actor", "scene"):
        await runner.prepare_comfy(_job(pipeline_id=pid))
        await runner.finish_comfy(_job(pipeline_id=pid, status=JobStatus.failed))

    assert ("before", "h3_ref2va") in orch.calls
    assert ("after", "h3_ref2va", "failed") in orch.calls
    assert ("before", "actor") in orch.calls
    assert ("before", "scene") in orch.calls


@pytest.mark.asyncio
async def test_start_pipeline_job_reserves_before_background_task(monkeypatch, tmp_path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    class FakePipeline:
        id = "ref_frame"
        execution_adapter_id = "comfy"
        generation_kind = "image"
        output_labels = {"layout": "Layout"}

    async def fake_run(job_id, images, cancel):
        orch.calls.append(("run", job_id))

    monkeypatch.setattr(runner, "get_pipeline", lambda _pid: FakePipeline())
    monkeypatch.setattr(runner, "_run_job", fake_run)
    job = store.create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="reserve-before-run",
        params={},
    )

    await runner.start_pipeline_job(job)
    await runner.await_pipeline_job(job.id)

    assert orch.calls[:2] == [
        ("reserve", job.id, "ref_frame", "image", "queued", "queued"),
        ("run", job.id),
    ]


@pytest.mark.asyncio
async def test_start_comfy_mcp_job_reserves_before_background_task(monkeypatch, tmp_path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    class FakePipeline:
        id = "actor"
        execution_adapter_id = "comfy_mcp"
        generation_kind = "image"
        output_labels = {"actor": "Actor"}

    async def fake_run(job_id, images, cancel):
        orch.calls.append(("run", job_id))

    monkeypatch.setattr(runner, "get_pipeline", lambda _pid: FakePipeline())
    monkeypatch.setattr(runner, "_run_job", fake_run)
    job = store.create_job(
        pipeline_id="actor",
        asset_kind="actors",
        name="reserve-mcp-before-run",
        params={},
    )

    await runner.start_pipeline_job(job)
    await runner.await_pipeline_job(job.id)

    assert orch.calls[:2] == [
        ("reserve", job.id, "actor", "image", "queued", "queued"),
        ("run", job.id),
    ]


@pytest.mark.asyncio
async def test_run_comfy_mcp_job_releases_generation_reservation(monkeypatch, tmp_path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    class FakePipeline:
        id = "actor"
        execution_adapter_id = "comfy_mcp"

    class FakeAdapter:
        id = "comfy_mcp"

        async def run(self, job, pipeline, images, cancel, runtime):
            orch.calls.append(("adapter_run", job.id))

    job = store.create_job(
        pipeline_id="actor",
        asset_kind="actors",
        name="release-mcp-after-run",
        params={},
    )
    monkeypatch.setattr(runner, "get_pipeline", lambda _pid: FakePipeline())
    monkeypatch.setattr(
        runner._execution_adapters,
        "resolve",
        lambda pipeline, job=None: FakeAdapter(),
    )

    await runner._run_job(job.id, {}, asyncio.Event())

    assert orch.calls == [
        ("adapter_run", job.id),
        ("release", job.id),
    ]


@pytest.mark.asyncio
async def test_run_job_calls_before_then_after_on_success(monkeypatch, tmp_path):
    """Full _run_job path: before_comfy_job before Comfy work, after with terminal status."""
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)

    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    job = store.create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="hook-success",
        params={"description": "test"},
    )

    class FakePipeline:
        id = "ref_frame"
        execution_adapter_id = "comfy"
        output_labels = {"layout": "Layout"}

        def build_prompt(self, job, *, uploaded_images):
            return {"1": {}}, 42

        def map_history_outputs(self, history, *, job=None):
            return {
                "layout": ComfyImageRef(filename="out.png", subfolder="", type="output"),
            }

    fake_client = SimpleNamespace(
        upload_image=AsyncMock(return_value="up.png"),
        queue_prompt=AsyncMock(return_value="prompt-1"),
        wait_for_completion=AsyncMock(return_value={"outputs": {}}),
        download_image=AsyncMock(return_value=b"png-bytes"),
    )

    monkeypatch.setattr(runner, "get_pipeline", lambda _pid: FakePipeline())
    monkeypatch.setattr(runner, "ComfyClient", lambda: fake_client)

    cancel = asyncio.Event()
    await runner._run_job(job.id, {}, cancel)

    final = store.load_job(job.id)
    assert final is not None
    assert final.status == JobStatus.succeeded
    assert orch.calls[0] == ("before", "ref_frame")
    assert orch.calls[1:4] == [
        ("phase", job.id, "uploading", "uploading"),
        ("phase", job.id, "running", "generating"),
        ("phase", job.id, "running", "saving"),
    ]
    assert orch.calls[-2:] == [
        ("after", "ref_frame", "succeeded"),
        ("release", job.id),
    ]


@pytest.mark.asyncio
async def test_run_job_after_comfy_on_failure(monkeypatch, tmp_path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)

    orch = RecordingOrchestrator()
    monkeypatch.setattr(runner, "get_orchestrator", lambda: orch)

    job = store.create_job(
        pipeline_id="h3_ref2va",
        asset_kind="shots",
        name="hook-fail",
        params={},
    )

    class BoomPipeline:
        id = "h3_ref2va"
        execution_adapter_id = "comfy"
        output_labels = {}

        def build_prompt(self, job, *, uploaded_images):
            raise RuntimeError("graph boom")

        def map_history_outputs(self, history, *, job=None):
            return {}

    monkeypatch.setattr(runner, "get_pipeline", lambda _pid: BoomPipeline())
    monkeypatch.setattr(runner, "ComfyClient", lambda: SimpleNamespace())

    cancel = asyncio.Event()
    await runner._run_job(job.id, {}, cancel)

    final = store.load_job(job.id)
    assert final is not None
    assert final.status == JobStatus.failed
    assert orch.calls == [
        ("before", "h3_ref2va"),
        ("phase", job.id, "uploading", "uploading"),
        ("after", "h3_ref2va", "failed"),
        ("release", job.id),
    ]
