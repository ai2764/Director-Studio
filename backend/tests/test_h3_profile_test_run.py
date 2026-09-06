"""Integration coverage for isolated H3 workflow-profile test jobs."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.core.jobs.execution_adapters.comfy_mcp import (
    ComfyMcpExecutionAdapter,
    ComfyMcpExecutionRuntime,
)
from app.core.jobs.store import (
    build_output_slots,
    create_job,
    load_job,
    save_job,
)
from app.core.library.store import (
    asset_dir,
    create_external_asset,
    write_asset,
)
from app.core.schemas import JobStatus, LibraryAsset
from app.integrations.comfy_mcp import McpOutputFile
from app.main import create_app
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from app.workflow_profiles.h3 import (
    H3ProfileStore,
    ProfileChangedError,
    ProfileStateError,
    ProfileStorageError,
    load_job_profile_snapshot,
)
from app.workflow_profiles.h3.inspector import inspect_h3_workflow


def _picture_png() -> bytes:
    image = Image.effect_noise((256, 256), 80).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_test_route_preparation_race_persists_failed_job(
    test_env, actor_picture, monkeypatch
):
    from app.api import h3_workflow_profiles as api
    from app.core.jobs.store import list_jobs

    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    resolve_picture = api._resolve_picture_asset

    def change_import(asset_id):
        graph = store.load_import_workflow(import_id)
        graph["136"]["inputs"]["width"] = 777
        store.import_workflow_path(import_id).write_text(json.dumps(graph))
        return resolve_picture(asset_id)

    monkeypatch.setattr(api, "_resolve_picture_asset", change_import)
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={"picture_asset_id": actor_picture.id},
        )
    assert response.status_code == 409
    jobs = list_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.failed
    assert "changed" in jobs[0].error.lower()
    assert store.resolve_active().source == "builtin"


def _import_ready_profile(store: H3ProfileStore) -> str:
    graph = json.loads(
        (settings.workflows_dir / "h3_ref2va.api.json").read_text(encoding="utf-8")
    )
    graph["999"] = graph.pop("92")
    import_id = store.create_import(graph)
    store.save_import_output(import_id, "999")
    mapping = inspect_h3_workflow(graph, output_node_id="999").mapping
    assert mapping is not None
    assert mapping.saver_node_id == "999"
    store.save_import_mapping(import_id, mapping)
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    mapping = store.load_import_mapping(import_id)
    assert mapping is not None
    store.record_validation_success(
        import_id,
        workflow_sha256=workflow_sha256,
        mapping_sha256=mapping_sha256,
        report={"valid": True, "issues": [], "fixed_dependencies": []},
        comfy_payload={"valid": True, "error_count": 0, "warnings": []},
    )
    return import_id


def _profile_test_job(store: H3ProfileStore, import_id: str):
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    mapping = store.load_import_mapping(import_id)
    assert mapping is not None
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="profile adapter test",
        params={
            "h3_provider": "local",
            "h3_profile_test": True,
            "h3_profile_import_id": import_id,
            "h3_profile_test_workflow_sha256": workflow_sha256,
            "h3_profile_test_mapping_sha256": mapping_sha256,
            "h3_profile_test_boundary_sha256": store.boundary_sha256(mapping),
        },
        seed=42,
        fixed_seed=True,
    )
    H3Ref2VaPipeline().prepare_job_submission(job)
    job.status = JobStatus.running
    job.comfy_prompt_id = "prompt_profile_test"
    save_job(job)
    return job


def _durable_success_job(store: H3ProfileStore, import_id: str):
    job = _profile_test_job(store, import_id)
    video = settings.jobs_dir / job.id / "outputs" / "video.mp4"
    video.write_bytes(b"mapped-video")
    job.outputs = build_output_slots(job.id, {"video": video})
    job.status = JobStatus.succeeded
    save_job(job)
    return job


def _record_durable_success(store: H3ProfileStore, import_id: str):
    job = _durable_success_job(store, import_id)
    store.record_test_success(
        import_id,
        workflow_sha256=job.params["h3_profile_test_workflow_sha256"],
        mapping_sha256=job.params["h3_profile_test_mapping_sha256"],
        job_id=job.id,
    )
    return job


@pytest.mark.parametrize(
    "kind", ["hardlink"] if os.name == "nt" else ["symlink", "hardlink"]
)
def test_linked_test_job_metadata_cannot_authorize_activation(
    test_env: Path, kind: str
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _durable_success_job(store, import_id)
    job_path = settings.jobs_dir / job.id / "job.json"
    outside_path = test_env.parent / f"outside-{kind}-job.json"
    outside_path.write_bytes(job_path.read_bytes())
    job_path.unlink()
    if kind == "hardlink":
        os.link(outside_path, job_path)
    else:
        job_path.symlink_to(outside_path)

    with pytest.raises(ProfileStorageError, match="storage path"):
        store.record_test_success(
            import_id,
            workflow_sha256=job.params["h3_profile_test_workflow_sha256"],
            mapping_sha256=job.params["h3_profile_test_mapping_sha256"],
            job_id=job.id,
        )


class _CompletedTestClient:
    def __init__(
        self,
        cancel_event: asyncio.Event | None = None,
        *,
        phase: str = "",
        outputs_by_node: dict[str, list[str]] | None = None,
    ):
        self.cancel_event = cancel_event
        self.phase = phase
        self.outputs_by_node = outputs_by_node or {
            "999": ["http://comfy/view?filename=test.mp4&subfolder=&type=output"]
        }

    async def wait_for_completion(self, prompt_id, *, cancel_event):
        assert prompt_id == "prompt_profile_test"
        return {
            "status": "completed",
            "outputs_by_node": self.outputs_by_node,
        }

    async def fetch_outputs(self, prompt_id):
        assert prompt_id == "prompt_profile_test"
        if self.phase == "during":
            assert self.cancel_event is not None
            self.cancel_event.set()
            await asyncio.sleep(0)
        elif self.phase == "after":
            await asyncio.sleep(0)
            assert self.cancel_event is not None
            self.cancel_event.set()
        return [
            McpOutputFile(
                filename=f"downloaded-{index}.mp4",
                source_url=url,
                data=f"mapped-video-{index}".encode(),
            )
            for index, url in enumerate(
                url for urls in self.outputs_by_node.values() for url in urls
            )
        ]


def _runtime(client) -> ComfyMcpExecutionRuntime:
    async def no_op(_job):
        return None

    return ComfyMcpExecutionRuntime(
        client_factory=lambda: client,
        prepare=no_op,
        finish=no_op,
    )


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(settings, "jobs_dir", data_root / "jobs")
    monkeypatch.setattr(settings, "library_root", data_root / "library")
    monkeypatch.setattr(settings, "projects_dir", data_root / "projects")
    monkeypatch.setattr(
        settings, "workflow_profiles_dir", data_root / "workflow_profiles"
    )
    monkeypatch.setattr(settings, "h3_provider", "local")
    return data_root


@pytest.fixture
def actor_picture(test_env: Path):
    return create_external_asset(
        kind="actors",
        name="Test actor",
        image_bytes=_picture_png(),
        image_filename="actor.png",
    )


@pytest.fixture
def voice_asset(test_env: Path):
    asset = LibraryAsset(
        id="voi_123456789abc",
        kind="voices",
        name="Test voice",
        pipeline_id="external",
        job_id="",
        created_at="2026-09-05T00:00:00Z",
        files={"reference": "reference.wav"},
        meta={"h3_ready": True, "duration_s": 2.0},
    )
    directory = asset_dir("voices", asset.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reference.wav").write_bytes(b"RIFF-test-voice")
    return write_asset(asset)


def test_test_run_creates_isolated_56_frame_local_h3_job(
    test_env: Path,
    actor_picture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)

    async def capture_start(job, *, images=None):
        assert images is not None
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
        return job

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.start_pipeline_job", capture_start
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={"picture_asset_id": actor_picture.id, "audio_asset_id": None},
        )

    assert response.status_code == 202, response.text
    assert response.json()["job_url"].endswith(
        f"/api/h3-ref2va/jobs/{response.json()['job_id']}"
    )
    job = load_job(response.json()["job_id"])
    assert job is not None
    assert job.params["frames"] == 56
    assert job.params["width"] == 864
    assert job.params["height"] == 480
    assert job.params["h3_provider"] == "local"
    assert job.params["h3_profile_import_id"] == import_id
    assert job.params["h3_profile_test"] is True
    assert job.params["image_keys"] == ["picture_1"]
    assert job.params["audio_keys"] == []
    assert "<Picture 1>" in job.params["prompt"]
    assert "slow camera push" in job.params["prompt"].lower()
    assert "normal ambient audio" in job.params["prompt"].lower()
    assert job.seed == 42
    assert job.fixed_seed is True
    assert job.project_id is None
    assert job.library_asset_id is None
    assert "shot_id" not in job.params

    snapshot = load_job_profile_snapshot(job.id)
    assert snapshot.profile_id == import_id
    assert snapshot.mapping.saver_node_id == "999"
    assert job.params["h3_profile_sha256"] == snapshot.workflow_sha256
    assert job.params["h3_profile_test_workflow_sha256"] == snapshot.workflow_sha256
    assert job.params["h3_profile_test_mapping_sha256"] == store.mapping_sha256(
        snapshot.mapping
    )


def test_test_run_resolves_optional_voice_asset(
    test_env: Path,
    actor_picture,
    voice_asset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_id = _import_ready_profile(H3ProfileStore())
    captured_inputs = {}

    async def capture_start(job, *, images=None):
        captured_inputs.update(images or {})
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
        return job

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.start_pipeline_job", capture_start
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={
                "picture_asset_id": actor_picture.id,
                "audio_asset_id": voice_asset.id,
            },
        )

    assert response.status_code == 202, response.text
    job = load_job(response.json()["job_id"])
    assert job is not None
    assert job.params["audio_keys"] == ["audio_1"]
    assert "<Audio 1>" in job.params["prompt"]
    assert captured_inputs["audio_1"] == ("reference.wav", b"RIFF-test-voice")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["during", "after"])
async def test_comfy_mcp_cancellation_while_fetching_never_records_test_evidence(
    test_env: Path,
    phase: str,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _profile_test_job(store, import_id)
    cancel_event = asyncio.Event()
    client = _CompletedTestClient(cancel_event, phase=phase)

    await ComfyMcpExecutionAdapter().resume(
        job,
        H3Ref2VaPipeline(),
        cancel_event,
        _runtime(client),
    )

    terminal = load_job(job.id)
    assert terminal is not None
    assert terminal.status == JobStatus.cancelled
    assert not (store.import_workflow_path(import_id).parent / "test.json").exists()


@pytest.mark.asyncio
async def test_failure_persisting_final_success_never_records_test_evidence(
    test_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.jobs.execution_adapters import comfy_mcp

    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _profile_test_job(store, import_id)
    real_save_job = comfy_mcp.store.save_job

    def fail_final_success(candidate):
        if candidate.status == JobStatus.succeeded:
            raise OSError("final job persistence failed")
        real_save_job(candidate)

    monkeypatch.setattr(comfy_mcp.store, "save_job", fail_final_success)

    await ComfyMcpExecutionAdapter().resume(
        job,
        H3Ref2VaPipeline(),
        asyncio.Event(),
        _runtime(_CompletedTestClient()),
    )

    terminal = load_job(job.id)
    assert terminal is not None
    assert terminal.status == JobStatus.failed
    assert not (store.import_workflow_path(import_id).parent / "test.json").exists()


@pytest.mark.parametrize(
    "status",
    [None, JobStatus.running, JobStatus.failed, JobStatus.cancelled],
    ids=["missing", "running", "failed", "cancelled"],
)
def test_activation_rejects_evidence_for_non_succeeded_job(
    test_env: Path,
    status: JobStatus | None,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    mapping = store.load_import_mapping(import_id)
    assert mapping is not None
    if status is None:
        job_id = "job_missing_test_evidence"
    else:
        job = _profile_test_job(store, import_id)
        video = settings.jobs_dir / job.id / "outputs" / "video.mp4"
        video.write_bytes(b"mapped-video")
        job.outputs = build_output_slots(job.id, {"video": video})
        job.status = status
        save_job(job)
        job_id = job.id
    evidence_path = store.import_workflow_path(import_id).parent / "test.json"
    evidence_path.write_text(
        json.dumps(
            {
                "status": "succeeded",
                "contract_version": 2,
                "workflow_sha256": workflow_sha256,
                "mapping_sha256": mapping_sha256,
                "boundary_sha256": store.boundary_sha256(mapping),
                "job_id": job_id,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileStateError, match="test job"):
        store.activate_import(import_id)


@pytest.mark.asyncio
async def test_mapped_test_video_records_same_identity_and_allows_activation(
    test_env: Path,
    actor_picture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)

    async def capture_start(job, *, images=None):
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
        return job

    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.start_pipeline_job", capture_start
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={"picture_asset_id": actor_picture.id},
        )

    job = load_job(response.json()["job_id"])
    assert job is not None
    job.status = JobStatus.running
    job.comfy_prompt_id = "prompt_profile_test"
    save_job(job)
    await ComfyMcpExecutionAdapter().resume(
        job,
        H3Ref2VaPipeline(),
        asyncio.Event(),
        _runtime(_CompletedTestClient()),
    )

    profile = store.activate_import(import_id)

    record = json.loads(
        (store.import_workflow_path(import_id).parent / "test.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "succeeded"
    assert record["contract_version"] == 2
    assert record["workflow_sha256"] == job.params["h3_profile_test_workflow_sha256"]
    assert record["boundary_sha256"] == job.params["h3_profile_test_boundary_sha256"]
    assert record["artifact_index"] == 0
    assert record["job_id"] == job.id
    assert profile.status == "active"


@pytest.mark.asyncio
async def test_profile_test_keeps_all_videos_from_confirmed_output_node_only(
    test_env: Path,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _profile_test_job(store, import_id)
    other = "http://comfy/view?filename=wrong.mp4&subfolder=&type=output"
    first = "http://comfy/view?filename=first.mp4&subfolder=&type=output"
    second = "http://comfy/view?filename=second.mp4&subfolder=&type=output"

    await ComfyMcpExecutionAdapter().resume(
        job,
        H3Ref2VaPipeline(),
        asyncio.Event(),
        _runtime(
            _CompletedTestClient(
                outputs_by_node={"777": [other], "999": [first, second]}
            )
        ),
    )

    terminal = load_job(job.id)
    assert terminal is not None
    assert terminal.status == JobStatus.succeeded
    assert list(terminal.outputs) == ["video_candidate_0", "video_candidate_1"]
    pending = json.loads(
        (store.import_workflow_path(import_id).parent / "test.json").read_text()
    )
    assert pending["status"] == "awaiting_selection"
    assert [item["artifact_index"] for item in pending["candidates"]] == [0, 1]


@pytest.mark.asyncio
async def test_selecting_observed_test_video_does_not_rerun_and_allows_activation(
    test_env: Path,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _profile_test_job(store, import_id)
    first = "http://comfy/view?filename=first.mp4&subfolder=&type=output"
    second = "http://comfy/view?filename=second.mp4&subfolder=&type=output"
    await ComfyMcpExecutionAdapter().resume(
        job,
        H3Ref2VaPipeline(),
        asyncio.Event(),
        _runtime(_CompletedTestClient(outputs_by_node={"999": [first, second]})),
    )

    with TestClient(create_app()) as client:
        response = client.put(
            f"/api/workflow-profiles/h3/imports/{import_id}/test-output",
            json={"artifact_index": 1},
        )

    assert response.status_code == 200, response.text
    assert response.json()["artifact_index"] == 1
    mapping = store.load_import_mapping(import_id)
    assert mapping is not None
    assert mapping.output.artifact_index == 1
    assert store.import_lifecycle(import_id)["status"] == "tested"
    assert store.activate_import(import_id).mapping.output.artifact_index == 1


def test_test_result_is_not_recorded_without_a_downloaded_mapped_video(
    test_env: Path,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _profile_test_job(store, import_id)
    job.status = JobStatus.succeeded
    save_job(job)

    with pytest.raises(ProfileStateError, match="mapped video"):
        H3Ref2VaPipeline().on_job_succeeded(job)

    assert not (store.import_workflow_path(import_id).parent / "test.json").exists()


def test_test_result_for_old_hash_cannot_activate(test_env: Path) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    _record_durable_success(store, import_id)
    workflow_path = store.import_workflow_path(import_id)
    graph = json.loads(workflow_path.read_text(encoding="utf-8"))
    graph["136"]["inputs"]["prompt"] = "changed after the test"
    workflow_path.write_text(json.dumps(graph), encoding="utf-8")
    new_workflow_sha256, current_mapping_sha256 = store.import_identity(import_id)
    store.record_validation_success(
        import_id,
        workflow_sha256=new_workflow_sha256,
        mapping_sha256=current_mapping_sha256,
        report={"valid": True},
        comfy_payload={"valid": True},
    )

    with pytest.raises(ProfileChangedError, match="successful test"):
        store.activate_import(import_id)


def test_completion_rechecks_import_identity_after_durable_job_verification(
    test_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    job = _durable_success_job(store, import_id)
    original_verify = store._require_successful_test_job

    def verify_then_change_import(**kwargs):
        verified = original_verify(**kwargs)
        workflow_path = store.import_workflow_path(import_id)
        graph = json.loads(workflow_path.read_text(encoding="utf-8"))
        graph["136"]["inputs"]["prompt"] = "changed during completion"
        workflow_path.write_text(json.dumps(graph), encoding="utf-8")
        return verified

    monkeypatch.setattr(
        store, "_require_successful_test_job", verify_then_change_import
    )

    with pytest.raises(ProfileChangedError, match="test execution"):
        store.record_test_success(
            import_id,
            workflow_sha256=job.params["h3_profile_test_workflow_sha256"],
            mapping_sha256=job.params["h3_profile_test_mapping_sha256"],
            job_id=job.id,
        )

    assert not (store.import_workflow_path(import_id).parent / "test.json").exists()


def test_activation_rechecks_import_identity_after_job_verification(
    test_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    _record_durable_success(store, import_id)
    original_verify = store._require_successful_test_job

    def verify_then_change_import(**kwargs):
        verified = original_verify(**kwargs)
        workflow_path = store.import_workflow_path(import_id)
        graph = json.loads(workflow_path.read_text(encoding="utf-8"))
        graph["136"]["inputs"]["prompt"] = "changed during activation"
        workflow_path.write_text(json.dumps(graph), encoding="utf-8")
        return verified

    monkeypatch.setattr(
        store, "_require_successful_test_job", verify_then_change_import
    )

    with pytest.raises(ProfileChangedError, match="activation"):
        store.activate_import(import_id)


def test_activation_rechecks_import_identity_immediately_before_selection(
    test_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    _record_durable_success(store, import_id)
    original_install = store.install_profile

    def install_then_change_import(*args, **kwargs):
        original_install(*args, **kwargs)
        workflow_path = store.import_workflow_path(import_id)
        graph = json.loads(workflow_path.read_text(encoding="utf-8"))
        graph["136"]["inputs"]["prompt"] = "changed before activation pointer"
        workflow_path.write_text(json.dumps(graph), encoding="utf-8")

    monkeypatch.setattr(store, "install_profile", install_then_change_import)

    with pytest.raises(ProfileChangedError, match="activation"):
        store.activate_import(import_id)

    assert store.resolve_active().profile_id == "builtin-official-h3"


def test_activation_rechecks_successful_comfy_validation(test_env: Path) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    _record_durable_success(store, import_id)
    validation_path = store.import_workflow_path(import_id).parent / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["comfy"] = {"valid": False, "error_count": 1}
    validation_path.write_text(json.dumps(validation), encoding="utf-8")

    with pytest.raises(ProfileStateError, match="validation"):
        store.activate_import(import_id)
