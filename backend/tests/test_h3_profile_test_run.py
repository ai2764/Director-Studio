"""Integration coverage for isolated H3 workflow-profile test jobs."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.core.jobs.store import load_job, save_job
from app.core.library.store import (
    asset_dir,
    create_external_asset,
    write_asset,
)
from app.core.schemas import JobStatus, LibraryAsset
from app.main import create_app
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from app.workflow_profiles.h3 import (
    H3ProfileStore,
    ProfileChangedError,
    ProfileStateError,
    load_job_profile_snapshot,
)
from app.workflow_profiles.h3.inspector import inspect_h3_workflow


def _picture_png() -> bytes:
    image = Image.effect_noise((256, 256), 80).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _import_ready_profile(store: H3ProfileStore) -> str:
    graph = json.loads(
        (settings.workflows_dir / "h3_ref2va.api.json").read_text(encoding="utf-8")
    )
    graph["999"] = graph.pop("92")
    import_id = store.create_import(graph)
    mapping = inspect_h3_workflow(graph).mapping
    assert mapping is not None
    assert mapping.saver_node_id == "999"
    store.save_import_mapping(import_id, mapping)
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    store.record_validation_success(
        import_id,
        workflow_sha256=workflow_sha256,
        mapping_sha256=mapping_sha256,
        report={"valid": True, "issues": [], "fixed_dependencies": []},
        comfy_payload={"valid": True, "error_count": 0, "warnings": []},
    )
    return import_id


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


def test_mapped_test_video_records_same_identity_and_allows_activation(
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
    video = settings.jobs_dir / job.id / "outputs" / "video.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mapped-video")
    H3Ref2VaPipeline().postprocess_job_outputs(job, {"video": video})
    job.status = JobStatus.succeeded
    save_job(job)

    profile = store.activate_import(import_id)

    record = json.loads(
        (store.import_workflow_path(import_id).parent / "test.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "succeeded"
    assert record["contract_version"] == 1
    assert record["workflow_sha256"] == job.params["h3_profile_test_workflow_sha256"]
    assert record["mapping_sha256"] == job.params["h3_profile_test_mapping_sha256"]
    assert record["job_id"] == job.id
    assert profile.status == "active"


def test_test_result_is_not_recorded_without_a_downloaded_mapped_video(
    test_env: Path,
) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    from app.core.jobs.store import create_job

    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="missing video",
        params={
            "h3_profile_test": True,
            "h3_profile_import_id": import_id,
            "h3_profile_test_workflow_sha256": workflow_sha256,
            "h3_profile_test_mapping_sha256": mapping_sha256,
        },
        seed=42,
        fixed_seed=True,
    )

    H3Ref2VaPipeline().postprocess_job_outputs(job, {})

    assert not (store.import_workflow_path(import_id).parent / "test.json").exists()


def test_test_result_for_old_hash_cannot_activate(test_env: Path) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    store.record_test_success(
        import_id,
        workflow_sha256=workflow_sha256,
        mapping_sha256=mapping_sha256,
        job_id="job_old_hash",
    )
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


def test_activation_rechecks_successful_comfy_validation(test_env: Path) -> None:
    store = H3ProfileStore()
    import_id = _import_ready_profile(store)
    workflow_sha256, mapping_sha256 = store.import_identity(import_id)
    validation_path = store.import_workflow_path(import_id).parent / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["comfy"] = {"valid": False, "error_count": 1}
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    store.record_test_success(
        import_id,
        workflow_sha256=workflow_sha256,
        mapping_sha256=mapping_sha256,
        job_id="job_invalid_comfy",
    )

    with pytest.raises(ProfileStateError, match="validation"):
        store.activate_import(import_id)
