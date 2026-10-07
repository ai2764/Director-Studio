"""Profile v3 test runs require a real context upload and keep output selection."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.core.jobs.store import list_jobs, load_job, save_job
from app.core.library.store import create_external_asset
from app.core.projects.video_context import VideoContextError
from app.main import create_app
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from app.workflow_profiles.h3 import H3ContextVideoInput, H3ProfileStore
from app.workflow_profiles.h3.inspector import inspect_h3_workflow


def _picture_png() -> bytes:
    image = Image.effect_noise((64, 64), 40).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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


def _validated_import(store: H3ProfileStore, *, context_video: bool) -> str:
    graph = json.loads(
        (settings.workflows_dir / "h3_ref2va.api.json").read_text(encoding="utf-8")
    )
    graph["999"] = graph.pop("92")
    if context_video:
        graph["50"] = {
            "class_type": "LoadVideo",
            "inputs": {"file": "leftover.mp4"},
        }
        graph["122"]["inputs"]["context_pixels"] = ["50", 0]
    import_id = store.create_import(graph)
    store.save_import_output(import_id, "999")
    mapping = inspect_h3_workflow(graph, output_node_id="999").mapping
    assert mapping is not None
    assert mapping.output.node_id == "999"
    if context_video:
        mapping = mapping.model_copy(
            update={
                "context_video": H3ContextVideoInput(node_id="50", input_name="file")
            }
        )
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


def test_validation_endpoint_fills_the_video_boundary(test_env, monkeypatch):
    from app.api import h3_workflow_profiles as api
    captured = []
    class Client:
        async def validate_workflow(self, graph):
            captured.append(graph)
            return {"valid": True, "error_count": 0, "warnings": []}
        async def aclose(self):
            pass
    monkeypatch.setattr(api, "ComfyMcpClient", Client)
    import_id = _validated_import(H3ProfileStore(), context_video=True)
    with TestClient(create_app()) as client:
        result = client.post(f"/api/workflow-profiles/h3/imports/{import_id}/validate")
    assert result.status_code == 200, result.text
    assert captured[0]["50"]["inputs"]["file"] == "contract-context.mp4"


@pytest.mark.parametrize("audio_window", [0, 24])
def test_profile_test_records_uploaded_motion_audio_settings(test_env, monkeypatch, audio_window):
    from app.pipelines.h3_ref2va.video_context import attach_video_context
    graph = json.loads((settings.workflows_dir / "h3_ref2va.api.json").read_text(encoding="utf8"))
    graph = attach_video_context(graph, uploaded_video="old.mp4", delivered_frames=56,
                                context_frames=39, audio_context_frames=audio_window, carry_audio=True)
    store = H3ProfileStore()
    import_id = store.create_import(graph)
    store.save_import_output(import_id, "92")
    mapping = inspect_h3_workflow(graph, output_node_id="92").mapping
    load_id = next(key for key, node in graph.items() if node["class_type"] == "LoadVideo")
    mapping = mapping.model_copy(update={"context_video": H3ContextVideoInput(node_id=load_id, input_name="file")})
    store.save_import_mapping(import_id, mapping)
    workflow_hash, mapping_hash = store.import_identity(import_id)
    store.record_validation_success(import_id, workflow_sha256=workflow_hash, mapping_sha256=mapping_hash,
                                    report={"valid": True}, comfy_payload={"valid": True})
    picture = create_external_asset(kind="actors", name="Actor", image_bytes=_picture_png(), image_filename="actor.png")
    monkeypatch.setattr("app.core.projects.video_context.load_video_context_upload", lambda *args: (
        {"filename": "source.mp4", "upload_id": "vup_test", "sha256": "source-hash", "media": {"has_audio": True}}, b"video"))
    async def start(job, *, images):
        return job
    monkeypatch.setattr("app.api.h3_workflow_profiles.start_pipeline_job", start)
    with TestClient(create_app()) as client:
        response = client.post(f"/api/workflow-profiles/h3/imports/{import_id}/test", json={
            "picture_asset_id": picture.id, "context_project_id": "prj_test", "context_upload_id": "vup_test"})
    assert response.status_code == 202, response.text
    source = load_job(response.json()["job_id"]).params["video_context_source"]
    assert source["carry_audio"] is True
    assert source["context_frames"] == 39
    assert source["audio_context_frames"] == audio_window


def test_context_mapping_requires_an_upload_and_records_contract_three(
    test_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = H3ProfileStore()
    import_id = _validated_import(store, context_video=True)
    picture = create_external_asset(
        kind="actors",
        name="Test actor",
        image_bytes=_picture_png(),
        image_filename="actor.png",
    )
    with TestClient(create_app()) as client:
        missing = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={"picture_asset_id": picture.id},
        )
    assert missing.status_code == 422
    assert missing.json()["code"] == "context_video_required"
    assert list_jobs() == []
    assert store.load_import_output(import_id) == "999"

    def load_upload(project_id: str, upload_id: str):
        assert project_id == "prj_context"
        assert upload_id == "vup_context"
        return (
            {
                "filename": "clip.mp4",
                "upload_id": upload_id,
                "sha256": "abc",
                "media": {"fps": 24, "width": 864, "height": 480},
            },
            b"mp4-bytes",
        )

    captured: dict = {}

    async def capture_start(job, *, images=None):
        captured["images"] = images
        captured["job"] = job
        H3Ref2VaPipeline().prepare_job_submission(job)
        save_job(job)
        return job

    monkeypatch.setattr(
        "app.core.projects.video_context.load_video_context_upload",
        load_upload,
    )
    monkeypatch.setattr(
        "app.api.h3_workflow_profiles.start_pipeline_job",
        capture_start,
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={
                "picture_asset_id": picture.id,
                "context_project_id": "prj_context",
                "context_upload_id": "vup_context",
            },
        )

    assert response.status_code == 202, response.text
    job = load_job(response.json()["job_id"])
    assert job is not None
    assert job.project_id is None
    assert "project_id" not in job.params
    assert job.params["image_keys"] == ["picture_1"]
    assert job.params["audio_keys"] == []
    assert job.params["context_video_key"] == "context_video"
    assert job.params["h3_contract_version"] == 3
    assert job.params["video_context_source"]["mode"] == "external_upload"
    assert job.params["video_context_source"]["carry_audio"] is False
    assert job.params["video_context_source"]["upload_id"] == "vup_context"
    filename, data = captured["images"]["context_video"]
    assert filename == "clip.mp4"
    assert data == b"mp4-bytes"
    assert "picture_1" in captured["images"]
    assert store.load_import_output(import_id) == "999"


def test_context_upload_is_rejected_without_a_mapping(test_env: Path):
    store = H3ProfileStore()
    import_id = _validated_import(store, context_video=False)
    picture = create_external_asset(
        kind="actors",
        name="Test actor",
        image_bytes=_picture_png(),
        image_filename="actor.png",
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={
                "picture_asset_id": picture.id,
                "context_project_id": "prj_context",
                "context_upload_id": "vup_context",
            },
        )
    assert response.status_code == 422
    assert response.json()["code"] == "context_video_unsupported"
    assert list_jobs() == []


def test_missing_context_upload_file_stays_a_client_error(
    test_env: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = H3ProfileStore()
    import_id = _validated_import(store, context_video=True)
    picture = create_external_asset(
        kind="actors",
        name="Test actor",
        image_bytes=_picture_png(),
        image_filename="actor.png",
    )

    def missing_upload(_project_id: str, _upload_id: str):
        raise VideoContextError("Context video upload was not found")

    monkeypatch.setattr(
        "app.core.projects.video_context.load_video_context_upload",
        missing_upload,
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/api/workflow-profiles/h3/imports/{import_id}/test",
            json={
                "picture_asset_id": picture.id,
                "context_project_id": "prj_context",
                "context_upload_id": "vup_missing",
            },
        )
    assert response.status_code == 422
    assert response.json()["code"] == "context_video_required"
    assert "not found" in response.json()["message"]
    assert list_jobs() == []
