"""Execution-adapter contracts for durable pipeline jobs."""

import pytest
import asyncio
from pathlib import Path

from app.core.jobs.execution import ExecutionAdapterRegistry
from app.core.jobs.execution_adapters.external import ExternalExecutionAdapter
from app.core.jobs.execution_adapters.comfy import ComfyExecutionAdapter
from app.core.jobs.execution_adapters.comfy_mcp import (
    ComfyMcpExecutionAdapter,
    ComfyMcpExecutionRuntime,
)
from app.core.jobs.store import create_job, load_job, save_job
from app.pipelines.actor.pipeline import ActorPipeline
from app.pipelines.gpt_actor.pipeline import GptActorPipeline
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from app.pipelines.prop.pipeline import PropPipeline
from app.pipelines.ref_frame.pipeline import RefFramePipeline
from app.pipelines.scene.pipeline import ScenePipeline
from app.core.schemas import JobRecord, JobStatus
from app.integrations.comfy_mcp import McpOutputFile


class FakeAdapter:
    def __init__(self, adapter_id: str):
        self.id = adapter_id


def test_pipeline_declares_execution_adapter_without_type_inference():
    assert ActorPipeline().execution_adapter_id == "comfy_mcp"
    assert ScenePipeline().execution_adapter_id == "comfy_mcp"
    assert PropPipeline().execution_adapter_id == "comfy_mcp"
    assert RefFramePipeline().execution_adapter_id == "comfy_mcp"
    assert GptActorPipeline().execution_adapter_id == "external"


def test_registry_resolves_declared_adapter_and_allows_future_h3_api():
    comfy = FakeAdapter("comfy")
    external = FakeAdapter("external")
    h3_api = FakeAdapter("h3_api")
    comfy_mcp = FakeAdapter("comfy_mcp")
    registry = ExecutionAdapterRegistry([comfy, comfy_mcp, external, h3_api])

    assert registry.resolve(ActorPipeline()) is comfy_mcp
    assert registry.resolve(GptActorPipeline()) is external

    future_pipeline = type("FutureH3ApiPipeline", (), {"execution_adapter_id": "h3_api"})()
    assert registry.resolve(future_pipeline) is h3_api


def test_h3_job_persists_provider_choice_for_adapter_routing():
    comfy = FakeAdapter("comfy")
    comfy_mcp = FakeAdapter("comfy_mcp")
    h3_api = FakeAdapter("h3_api")
    registry = ExecutionAdapterRegistry([comfy, comfy_mcp, h3_api])
    pipeline = H3Ref2VaPipeline()

    def job(provider: str) -> JobRecord:
        return JobRecord(
            id=f"job_{provider}",
            pipeline_id="h3_ref2va",
            asset_kind="productions",
            status=JobStatus.queued,
            name="h3",
            params={"h3_provider": provider},
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

    assert registry.resolve(pipeline, job=job("local")) is comfy_mcp
    assert registry.resolve(pipeline, job=job("mcp")) is comfy_mcp
    assert registry.resolve(pipeline, job=job("minimax")) is h3_api


def test_h3_pipeline_uses_mcp_adapter_when_global_provider_is_local(monkeypatch):
    from app.pipelines.h3_ref2va import pipeline as h3_pipeline_module

    monkeypatch.setattr(h3_pipeline_module.settings, "h3_provider", "local")

    assert H3Ref2VaPipeline().execution_adapter_id == "comfy_mcp"


def test_registry_rejects_duplicate_and_unknown_adapter_ids():
    with pytest.raises(ValueError, match="duplicate execution adapter"):
        ExecutionAdapterRegistry([FakeAdapter("comfy"), FakeAdapter("comfy")])

    registry = ExecutionAdapterRegistry([FakeAdapter("comfy")])
    unknown = type("UnknownPipeline", (), {"execution_adapter_id": "missing"})()
    with pytest.raises(KeyError, match="missing"):
        registry.resolve(unknown)


def test_external_adapter_exposes_recovery_and_cancel_policy():
    adapter = ExternalExecutionAdapter()

    assert adapter.id == "external"
    assert adapter.interrupt_on_cancel is False
    assert adapter.can_replay(GptActorPipeline()) is False


def test_comfy_adapter_exposes_resume_and_interrupt_policy():
    adapter = ComfyExecutionAdapter()

    assert adapter.id == "comfy"
    assert adapter.interrupt_on_cancel is True
    assert adapter.can_replay(ActorPipeline()) is True
    assert callable(adapter.run)
    assert callable(adapter.resume)
    assert callable(adapter.cancel)


class FakeMcpClient:
    def __init__(self):
        self.uploaded_inputs = None
        self.submitted_graph = None
        self.waited_prompt_ids: list[str] = []
        self.cancelled_prompt_ids: list[str] = []

    async def upload_inputs(self, job_id, inputs):
        self.uploaded_inputs = (job_id, inputs)
        return {key: f"cloud-{key}.png" for key in inputs}

    async def submit_workflow(self, graph):
        self.submitted_graph = graph
        return "prompt_mcp_123"

    async def wait_for_completion(self, prompt_id, *, cancel_event):
        self.waited_prompt_ids.append(prompt_id)
        if cancel_event.is_set():
            self.cancelled_prompt_ids.append(prompt_id)
            raise asyncio.CancelledError
        return {
            "status": "completed",
            "outputs_by_node": {
                "136": [
                    "http://comfy/view?filename=remote-video.mp4&subfolder=&type=output"
                ]
            },
        }

    async def fetch_outputs(self, prompt_id):
        assert prompt_id == "prompt_mcp_123"
        return [
            McpOutputFile(
                filename="prompt_000.mp4",
                source_url=(
                    "http://comfy/view?filename=remote-video.mp4&subfolder=&type=output"
                ),
                data=b"mcp-video",
            )
        ]


class FakeMcpPipeline:
    output_labels = {"video": "H3 Ref2AV Video"}

    def build_prompt(self, job, *, uploaded_images):
        assert uploaded_images == {"ref_0": "cloud-ref_0.png"}
        return {"136": {"class_type": "MiniMaxH3ReferenceToVideo"}}, 522

    def map_history_outputs(self, history, *, job=None):
        item = history["outputs"]["136"]["videos"][0]
        from app.core.schemas import ComfyImageRef

        return {"video": ComfyImageRef(**item)}

    def postprocess_job_outputs(self, job, outputs):
        assert job.id
        assert outputs["video"].read_bytes() == b"mcp-video"


class FakeMcpImagePipeline:
    output_labels = {"master": "Master", "threeview": "Three View"}

    def map_history_outputs(self, history, *, job=None):
        from app.core.schemas import ComfyImageRef

        return {
            "master": ComfyImageRef(**history["outputs"]["10"]["images"][0]),
            "threeview": ComfyImageRef(**history["outputs"]["20"]["images"][0]),
        }

    def postprocess_job_outputs(self, job, outputs):
        return None


def _mcp_job():
    return create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="mcp h3",
        params={"h3_provider": "mcp"},
        project_id="prj_mcp",
    )


@pytest.mark.asyncio
async def test_comfy_mcp_adapter_persists_prompt_and_downloaded_video(tmp_projects_dir):
    client = FakeMcpClient()
    phases: list[tuple[str, str]] = []

    async def prepare(job):
        phases.append(("prepare", job.id))

    async def finish(job):
        phases.append(("finish", job.status.value))

    runtime = ComfyMcpExecutionRuntime(
        client_factory=lambda: client,
        prepare=prepare,
        finish=finish,
    )
    adapter = ComfyMcpExecutionAdapter()
    job = _mcp_job()

    await adapter.run(
        job,
        FakeMcpPipeline(),
        {"ref_0": ("actor.png", b"actor")},
        asyncio.Event(),
        runtime,
    )

    saved = load_job(job.id)
    assert saved is not None
    assert saved.status == JobStatus.succeeded
    assert saved.comfy_prompt_id == "prompt_mcp_123"
    assert saved.seed == 522
    assert saved.outputs["video"].filename == "video.mp4"
    assert Path(saved.outputs["video"].path).read_bytes() == b"mcp-video"
    assert phases == [("prepare", job.id), ("finish", "succeeded")]


@pytest.mark.asyncio
async def test_comfy_mcp_adapter_maps_image_outputs_by_node_not_download_order(
    tmp_projects_dir,
):
    class ImageClient:
        async def wait_for_completion(self, prompt_id, *, cancel_event):
            return {
                "status": "completed",
                "outputs_by_node": {
                    "10": [
                        "http://comfy/view?filename=master.png&subfolder=actors&type=output"
                    ],
                    "20": [
                        "http://comfy/view?filename=threeview.png&subfolder=actors&type=output"
                    ],
                },
            }

        async def fetch_outputs(self, prompt_id):
            return [
                McpOutputFile(
                    "download_2.png",
                    "http://comfy/view?filename=threeview.png&subfolder=actors&type=output",
                    b"three-view",
                ),
                McpOutputFile(
                    "download_1.png",
                    "http://comfy/view?filename=master.png&subfolder=actors&type=output",
                    b"master",
                ),
            ]

    async def no_op(_job):
        return None

    job = _mcp_job()
    job.status = JobStatus.running
    job.comfy_prompt_id = "prompt_mcp_123"
    save_job(job)
    runtime = ComfyMcpExecutionRuntime(
        client_factory=ImageClient,
        prepare=no_op,
        finish=no_op,
    )

    await ComfyMcpExecutionAdapter().resume(
        job,
        FakeMcpImagePipeline(),
        asyncio.Event(),
        runtime,
    )

    saved = load_job(job.id)
    assert saved is not None
    assert saved.status == JobStatus.succeeded
    assert Path(saved.outputs["master"].path).read_bytes() == b"master"
    assert Path(saved.outputs["threeview"].path).read_bytes() == b"three-view"


@pytest.mark.asyncio
async def test_comfy_mcp_adapter_resumes_submitted_prompt_without_reupload(tmp_projects_dir):
    client = FakeMcpClient()

    async def no_op(_job):
        return None

    runtime = ComfyMcpExecutionRuntime(
        client_factory=lambda: client,
        prepare=no_op,
        finish=no_op,
    )
    adapter = ComfyMcpExecutionAdapter()
    job = _mcp_job()
    job.status = JobStatus.running
    job.comfy_prompt_id = "prompt_mcp_123"
    save_job(job)

    await adapter.resume(
        job,
        FakeMcpPipeline(),
        asyncio.Event(),
        runtime,
    )

    saved = load_job(job.id)
    assert saved is not None
    assert saved.status == JobStatus.succeeded
    assert client.uploaded_inputs is None
    assert client.submitted_graph is None
    assert client.waited_prompt_ids == ["prompt_mcp_123"]


@pytest.mark.asyncio
async def test_runner_closes_persistent_comfy_mcp_client(monkeypatch):
    from app.core.jobs import runner

    class ClosableClient:
        closed = False

        async def aclose(self):
            self.closed = True

    client = ClosableClient()
    monkeypatch.setattr(runner, "_comfy_mcp_client", client)

    await runner.close_execution_runtimes()

    assert client.closed is True
