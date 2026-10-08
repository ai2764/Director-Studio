from app.config import settings
from app.workflow_profiles.h3.store import H3ProfileStore


def test_experiment_can_select_fast_template_without_changing_primary(monkeypatch):
    monkeypatch.setattr(settings, "h3_builtin_workflow", "h3_ref2va_fast4.api.json", raising=False)
    fast = H3ProfileStore().resolve_builtin()
    scheduler = next(n for n in fast.workflow.values() if n["class_type"] == "BasicScheduler")
    assert scheduler["inputs"]["steps"] == 4
    assert fast.source == "builtin"
    monkeypatch.setattr(settings, "h3_builtin_workflow", "h3_ref2va.api.json")
    normal = H3ProfileStore().resolve_builtin()
    scheduler = next(n for n in normal.workflow.values() if n["class_type"] == "BasicScheduler")
    assert scheduler["inputs"]["steps"] == 8
    assert fast.workflow_sha256 != normal.workflow_sha256


def test_fast_template_snapshot_is_frozen_after_default_changes(monkeypatch, tmp_path):
    from app.core.jobs.store import create_job
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(settings, "workflow_profiles_dir", tmp_path / "profiles")
    monkeypatch.setattr(settings, "h3_builtin_workflow", "h3_ref2va_fast4.api.json")
    job = create_job(pipeline_id="h3_ref2va", asset_kind="productions", name="snapshot")
    store = H3ProfileStore()
    snapshot = store.snapshot_for_job(job)
    monkeypatch.setattr(settings, "h3_builtin_workflow", "h3_ref2va.api.json")
    frozen = store.load_job_snapshot(job.id)
    assert frozen.workflow_sha256 == snapshot.workflow_sha256
    scheduler = next(n for n in frozen.workflow.values() if n["class_type"] == "BasicScheduler")
    assert scheduler["inputs"]["steps"] == 4
