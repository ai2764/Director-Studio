"""First shots and continuations snapshot different, explicit tested profiles."""
import hashlib
import json
from pathlib import Path

import pytest

from app.config import settings
from app.core.jobs.store import create_job, save_job, job_dir
from app.pipelines.h3_ref2va.workflow import fill_profile_graph
from app.workflow_profiles.h3 import H3ProfileStore, H3WorkflowProfile, ProfileStorageError, load_job_profile_snapshot
from app.workflow_profiles.h3.inspector import inspect_h3_workflow
from test_h3_profile_runtime import _job_params


def install(store, profile_id, filename):
    graph = json.loads((Path(settings.workflows_dir) / filename).read_text(encoding="utf-8"))
    mapping = inspect_h3_workflow(graph, output_node_id="92").mapping
    version = 3 if mapping.context_video else 2
    encoded = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    profile = H3WorkflowProfile(id=profile_id, mapping=mapping, contract_version=version,
        workflow_sha256=hashlib.sha256(encoded).hexdigest(), status="tested")
    evidence = dict(contract_version=version, workflow_sha256=profile.workflow_sha256,
                    mapping_sha256=store.mapping_sha256(mapping))
    store.install_profile(profile, graph,
        validation_record={**evidence, "valid": True, "report": {"valid": True}, "comfy": {"valid": True}},
        test_record={**evidence, "status": "succeeded", "job_id": "fixture-test-" + profile_id})


@pytest.fixture
def routes(monkeypatch):
    store = H3ProfileStore()
    install(store, "motion-4", "h3_ref2va_turbo4_video_context.api.json")
    install(store, "standalone-4", "h3_ref2va_fast4.api.json")
    store.select_profile("motion-4")
    monkeypatch.setattr(settings, "h3_standalone_profile_id", "standalone-4")
    return store


@pytest.mark.parametrize("with_context", [False, True])
def test_submission_routes_and_fills_actual_graph_without_changing_selected_profile(routes, with_context):
    params = _job_params(**({"context_video_key": "context_video", "context_video": "source.mp4"} if with_context else {}))
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="routing", params=params)
    profile = routes.snapshot_for_job(job)
    save_job(job)
    assert profile.profile_id == ("motion-4" if with_context else "standalone-4")
    graph = fill_profile_graph(profile, params)
    assert graph["124"]["inputs"]["steps"] == 4
    assert routes.resolve_active().profile_id == "motion-4"
    if with_context:
        assert graph["142"]["inputs"]["file"] == "source.mp4"
        assert graph["144"]["inputs"]["context_length"] == "5"
    else:
        assert not any(n["class_type"] == "MiniMaxH3MotionContext" for n in graph.values())


def test_existing_snapshot_is_immutable_after_route_changes(routes, monkeypatch):
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="snapshot", params={})
    original = routes.snapshot_for_job(job)
    save_job(job)
    monkeypatch.setattr(settings, "h3_standalone_profile_id", "")
    assert routes.snapshot_for_job(job) == original
    assert load_job_profile_snapshot(job.id).profile_id == "standalone-4"


@pytest.mark.parametrize("standalone", ["", "missing-profile", "motion-4"])
def test_bad_standalone_route_fails_before_snapshot_or_inference(routes, monkeypatch, standalone):
    monkeypatch.setattr(settings, "h3_standalone_profile_id", standalone)
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="invalid", params={})
    with pytest.raises(ProfileStorageError):
        routes.snapshot_for_job(job)
    assert not (job_dir(job.id) / "workflow_profile").exists()
    assert "h3_profile_id" not in job.params


def test_changed_standalone_profile_does_not_fall_back_silently(routes):
    routes.workflow_path("standalone-4").write_text("{}", encoding="utf-8")
    with pytest.raises(ProfileStorageError):
        routes.resolve_for_submission(has_context=False)
    assert routes.resolve_for_submission(has_context=True).profile_id == "motion-4"
