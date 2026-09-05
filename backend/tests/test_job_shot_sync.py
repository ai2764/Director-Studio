"""Regression: ref_frame / h3_ref2va job terminal → shot state sync (C1 / C2)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.jobs import runner, store
from app.core.jobs.shot_sync import on_pipeline_job_terminal
from app.core.library.store import load_asset
from app.core.projects import LayoutReference, LayoutReviewStatus
from app.core.projects.layouts import LayoutProvider
from app.core.projects.models import RefRole, Shot, ShotRef, ShotStatus
from app.core.projects.store import create_project, load_shot, save_shot
from app.core.schemas import ComfyImageRef, JobRecord, JobStatus, OutputSlot


def _make_shot(project_id: str, **updates) -> Shot:
    shot = Shot(
        id="sht_sync_test01",
        project_id=project_id,
        scene_id="sc01",
        title="Cafe open",
        script_beat="Wide of actor at table",
        duration_s=8.0,
        status=ShotStatus.ref_frame_pending,
    )
    if updates:
        shot = shot.model_copy(update=updates)
    save_shot(shot)
    return shot


def _succeeded_ref_frame_job(
    tmp_path,
    *,
    job_id: str = "job_ff_sync1",
    shot_id: str,
    project_id: str,
    layout_ref_id: str | None = None,
) -> JobRecord:
    jdir = tmp_path / "jobs" / job_id
    (jdir / "outputs").mkdir(parents=True)
    layout_file = jdir / "outputs" / "layout.png"
    layout_file.write_bytes(b"fake-png-bytes")

    return JobRecord(
        id=job_id,
        pipeline_id="ref_frame",
        asset_kind="layouts",
        status=JobStatus.succeeded,
        name="layout:Cafe open",
        notes="Wide of actor at table",
        params={
            "description": "Wide of actor at table",
            "shot_id": shot_id,
            "project_id": project_id,
            "source_asset_ids": ["act_1"],
            **({"layout_ref_id": layout_ref_id} if layout_ref_id else {}),
        },
        seed=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        outputs={
            "layout": OutputSlot(
                key="layout",
                label="layout",
                path=str(layout_file),
            ),
        },
    )


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    lib = tmp_path / "library"
    projects = tmp_path / "projects"
    jobs.mkdir()
    lib.mkdir()
    projects.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", lib)
    monkeypatch.setattr(settings, "projects_dir", projects)
    return tmp_path


def test_ref_frame_succeeded_promotes_layout_to_qc_and_shot_to_needs_review(isolated_data):
    """A generated reference frame owns QC on its Layout, not on the Shot."""
    project = create_project("Sync C1", "INT. CAFE")
    job_id = "job_ff_sync1"
    shot = _make_shot(
        project.id,
        status=ShotStatus.ref_frame_pending,
        ref_frame_job_id=job_id,
    )

    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == ShotStatus.needs_review
    assert updated.layout_review_status == "pending_review"
    assert updated.layout_asset_id is not None
    assert updated.layout_asset_id.startswith("lay_")
    assert updated.ref_frame_job_id == job_id
    assert updated.refs[0].role == RefRole.layout_ref_frame
    assert updated.refs[0].asset_id == updated.layout_asset_id
    assert updated.refs[0].picture_index == 1

    asset = load_asset("layouts", updated.layout_asset_id)
    assert asset is not None
    assert asset.meta.get("review_status") == "pending_review"
    assert asset.meta.get("source_shot_id") == shot.id

    reloaded_job = store.load_job(job_id)
    assert reloaded_job is not None
    assert reloaded_job.library_asset_id == updated.layout_asset_id


def test_ref_frame_succeeded_idempotent(isolated_data):
    project = create_project("Sync C1 idemp", "script")
    job_id = "job_ff_idemp"
    shot = _make_shot(
        project.id,
        ref_frame_job_id=job_id,
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)
    first = load_shot(project.id, shot.id)
    on_pipeline_job_terminal(store.load_job(job_id) or job)
    second = load_shot(project.id, shot.id)

    assert first is not None and second is not None
    assert first.layout_asset_id == second.layout_asset_id
    assert second.status == ShotStatus.needs_review


def test_gpt_ref_frame_succeeded_uses_existing_layout_qc_flow(isolated_data):
    project = create_project("Sync GPT Layout", "script")
    job_id = "job_gpt_layout_sync"
    layout_ref_id = "lref_gpt_sync"
    shot = _make_shot(
        project.id,
        layout_refs=[
            LayoutReference(
                id=layout_ref_id,
                provider=LayoutProvider.gpt,
                job_id=job_id,
                purpose="newcomer enters",
            )
        ],
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
        layout_ref_id=layout_ref_id,
    ).model_copy(
        update={
            "pipeline_id": "gpt_ref_frame",
            "params": {
                "provider": "gpt",
                "generation_prompt": "Image1 controls the set. Return one image.",
                "shot_id": shot.id,
                "project_id": project.id,
                "layout_ref_id": layout_ref_id,
                "layout_source_refs": [],
            },
        }
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    layout = updated.layout_refs[0]
    assert layout.provider == LayoutProvider.gpt
    assert layout.job_status == JobStatus.succeeded
    assert layout.review_status == LayoutReviewStatus.pending_review
    assert layout.selected_for_h3 is True
    assert layout.asset_id is not None
    assert updated.status == ShotStatus.needs_review


def test_ref_frame_sync_updates_only_matching_layout_reference(isolated_data):
    project = create_project("Sync sibling Layouts", "script")
    first_job_id = "job_ff_sibling_first"
    second_job_id = "job_ff_sibling_second"
    shot = _make_shot(
        project.id,
        layout_refs=[
            LayoutReference(
                id="lref_before",
                job_id=first_job_id,
                purpose="before entry",
            ),
            LayoutReference(
                id="lref_after",
                job_id=second_job_id,
                purpose="after entry",
            ),
        ],
        ref_frame_job_id=first_job_id,
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=second_job_id,
        shot_id=shot.id,
        project_id=project.id,
        layout_ref_id="lref_after",
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert len(updated.layout_refs) == 2
    assert updated.layout_refs[0].id == "lref_before"
    assert updated.layout_refs[0].asset_id is None
    assert updated.layout_refs[0].review_status is None
    assert updated.layout_refs[1].id == "lref_after"
    assert updated.layout_refs[1].asset_id is not None
    assert updated.layout_refs[1].review_status == LayoutReviewStatus.pending_review
    assert updated.ref_frame_job_id == second_job_id
    assert updated.status == ShotStatus.needs_review


def test_new_layout_completion_becomes_the_only_current_h3_layout(isolated_data):
    """A completed reshoot replaces the current composition without deleting history."""
    project = create_project("Sync current Layout", "script")
    job_id = "job_ff_new_current"
    shot = _make_shot(
        project.id,
        layout_asset_id="lay_previous",
        layout_refs=[
            LayoutReference(
                id="lref_previous",
                asset_id="lay_previous",
                job_status=JobStatus.succeeded,
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
                purpose="old composition",
            ),
            LayoutReference(
                id="lref_replacement",
                job_id=job_id,
                job_status=JobStatus.running,
                purpose="new composition",
            ),
        ],
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_previous",
                picture_index=1,
            )
        ],
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
        layout_ref_id="lref_replacement",
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert len(updated.layout_refs) == 2
    previous, replacement = updated.layout_refs
    assert previous.asset_id == "lay_previous"
    assert previous.selected_for_h3 is False
    assert replacement.asset_id == updated.layout_asset_id
    assert replacement.selected_for_h3 is True
    assert [ref.asset_id for ref in updated.refs] == [replacement.asset_id]


def test_append_layout_completion_preserves_existing_active_layout(isolated_data):
    """A requested second state joins the active pack instead of replacing it."""
    project = create_project("Append Layout", "Mia is joined by the Agent.")
    job_id = "job_ff_append_state"
    shot = _make_shot(
        project.id,
        layout_asset_id="lay_mia_alone",
        layout_refs=[
            LayoutReference(
                id="lref_mia_alone",
                asset_id="lay_mia_alone",
                job_status=JobStatus.succeeded,
                review_status=LayoutReviewStatus.usable,
                selected_for_h3=True,
                purpose="Mia alone",
            ),
            LayoutReference(
                id="lref_two_people",
                job_id=job_id,
                job_status=JobStatus.running,
                purpose="Mia and Agent together",
                activation_mode="append",
            ),
        ],
        refs=[
            ShotRef(
                role=RefRole.layout_ref_frame,
                asset_id="lay_mia_alone",
                picture_index=1,
            )
        ],
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
        layout_ref_id="lref_two_people",
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert [layout.selected_for_h3 for layout in updated.layout_refs] == [True, True]
    assert [ref.asset_id for ref in updated.refs] == [
        "lay_mia_alone",
        updated.layout_refs[1].asset_id,
    ]


def test_ref_frame_succeeded_does_not_steal_picture_1(isolated_data):
    project = create_project("Sync keep order", "script")
    job_id = "job_ff_keep_pic"
    shot = _make_shot(
        project.id,
        status=ShotStatus.ref_frame_pending,
        ref_frame_job_id=job_id,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_keep1", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_keep1", picture_index=2),
        ],
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
    )
    store.save_job(job)

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    by_role = {ref.role: ref for ref in updated.refs}
    assert by_role[RefRole.actor].picture_index == 1
    assert by_role[RefRole.scene].picture_index == 2
    assert by_role[RefRole.layout_ref_frame].picture_index == 3
    assert by_role[RefRole.layout_ref_frame].asset_id == updated.layout_asset_id


def test_ref_frame_failed_does_not_promote(isolated_data):
    project = create_project("Sync fail", "script")
    job_id = "job_ff_fail"
    shot = _make_shot(project.id, ref_frame_job_id=job_id)
    job = JobRecord(
        id=job_id,
        pipeline_id="ref_frame",
        asset_kind="layouts",
        status=JobStatus.failed,
        name="layout",
        params={"shot_id": shot.id, "project_id": project.id},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        error="boom",
    )
    on_pipeline_job_terminal(job)
    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == ShotStatus.ref_frame_pending
    assert updated.layout_asset_id is None


@pytest.mark.parametrize("status", [JobStatus.failed, JobStatus.cancelled])
def test_ref_frame_terminal_failure_marks_only_matching_layout_reference(
    isolated_data, status
):
    """A failed sibling is terminal without hiding another Layout still in flight."""
    project = create_project("Sync terminal Layout", "script")
    failed_job_id = "job_ff_failed_layout"
    active_job_id = "job_ff_active_layout"
    shot = _make_shot(
        project.id,
        layout_refs=[
            LayoutReference(
                id="lref_failed",
                job_id=failed_job_id,
                purpose="failed angle",
            ),
            LayoutReference(
                id="lref_active",
                job_id=active_job_id,
                purpose="active angle",
            ),
        ],
        ref_frame_job_id=failed_job_id,
    )
    job = JobRecord(
        id=failed_job_id,
        pipeline_id="ref_frame",
        asset_kind="layouts",
        status=status,
        name="layout",
        params={
            "shot_id": shot.id,
            "project_id": project.id,
            "layout_ref_id": "lref_failed",
        },
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        error="worker stopped",
    )

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    failed, active = updated.layout_refs
    assert failed.asset_id is None
    assert failed.job_status == status
    assert failed.job_error == "worker stopped"
    assert active.asset_id is None
    assert active.job_status is None
    assert updated.status == ShotStatus.ref_frame_pending


def test_ref_frame_library_save_failure_marks_exact_layout_terminal(
    isolated_data,
    monkeypatch,
):
    project = create_project("Sync library failure", "script")
    job_id = "job_ff_library_failure"
    source_refs = [
        {
            "role": RefRole.scene,
            "asset_id": "scn_retry",
            "file_key": "wide",
            "notes": "preserve for retry",
        }
    ]
    shot = _make_shot(
        project.id,
        layout_refs=[
            LayoutReference(
                id="lref_library_failure",
                job_id=job_id,
                job_status=JobStatus.running,
                purpose="door reveal",
                state_description="The open doorway reveals the hall.",
                time_hint="after the latch turns",
                source_refs=source_refs,
                selected_for_h3=True,
            ),
            LayoutReference(
                id="lref_sibling",
                job_id="job_ff_sibling",
                purpose="opening hold",
            ),
        ],
        ref_frame_job_id=job_id,
    )
    job = _succeeded_ref_frame_job(
        isolated_data,
        job_id=job_id,
        shot_id=shot.id,
        project_id=project.id,
        layout_ref_id="lref_library_failure",
    )

    class FailingPipeline:
        def save_to_library(self, job, *, name=None, notes=None):
            raise RuntimeError("asset store is read-only")

    monkeypatch.setattr(
        "app.core.jobs.shot_sync.get_pipeline",
        lambda _pipeline_id: FailingPipeline(),
    )

    on_pipeline_job_terminal(job)

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    failed, sibling = updated.layout_refs
    assert failed.id == "lref_library_failure"
    assert failed.job_status == JobStatus.failed
    assert "asset store is read-only" in failed.job_error
    assert failed.selected_for_h3 is False
    assert failed.purpose == "door reveal"
    assert failed.state_description == "The open doorway reveals the hall."
    assert failed.time_hint == "after the latch turns"
    assert [source.model_dump() for source in failed.source_refs] == source_refs
    assert sibling == shot.layout_refs[1]


@pytest.mark.parametrize(
    "job_status,shot_status",
    [
        (JobStatus.succeeded, ShotStatus.succeeded),
        (JobStatus.failed, ShotStatus.failed),
        (JobStatus.cancelled, ShotStatus.failed),
    ],
)
def test_h3_terminal_updates_shot(isolated_data, job_status, shot_status):
    """C2 partial: h3_ref2va terminal maps onto shot when h3_job_id matches."""
    project = create_project("Sync H3", "script")
    job_id = f"job_h3_{job_status.value}"
    shot = _make_shot(
        project.id,
        status=ShotStatus.queued,
        h3_job_id=job_id,
        ref_frame_job_id=None,
    )
    job = JobRecord(
        id=job_id,
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=job_status,
        name="h3",
        params={"shot_id": shot.id, "project_id": project.id},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    on_pipeline_job_terminal(job)
    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == shot_status
    assert updated.h3_job_id == job_id


@pytest.mark.asyncio
async def test_cancel_h3_job_immediately_releases_shot_for_resubmit(isolated_data):
    project = create_project("Cancel H3", "script")
    job = store.create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="h3:cancel",
        params={
            "h3_provider": "local",
            "shot_id": "sht_cancel_h3",
            "project_id": project.id,
        },
        project_id=project.id,
    )
    shot = _make_shot(
        project.id,
        id="sht_cancel_h3",
        status=ShotStatus.queued,
        h3_job_id=job.id,
        ref_frame_job_id=None,
    )

    cancelled = await runner.cancel_job(job.id)

    assert cancelled is not None
    assert cancelled.status == JobStatus.cancelled
    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == ShotStatus.failed


def test_h3_ignores_mismatched_job_id(isolated_data):
    project = create_project("Sync H3 mismatch", "script")
    shot = _make_shot(
        project.id,
        status=ShotStatus.queued,
        h3_job_id="job_h3_current",
        ref_frame_job_id=None,
    )
    job = JobRecord(
        id="job_h3_stale",
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=JobStatus.succeeded,
        name="h3",
        params={"shot_id": shot.id, "project_id": project.id},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    on_pipeline_job_terminal(job)
    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == ShotStatus.queued
    assert updated.h3_job_id == "job_h3_current"


@pytest.mark.asyncio
async def test_run_job_success_triggers_ref_frame_shot_sync(isolated_data, monkeypatch):
    """Full runner path: _run_job success invokes shot sync (C1 end-to-end)."""
    project = create_project("Runner C1", "script")
    job = store.create_job(
        pipeline_id="ref_frame",
        asset_kind="layouts",
        name="layout:runner",
        params={
            "description": "blocking",
            "shot_id": "sht_runner_c1",
            "project_id": project.id,
            "source_asset_ids": [],
        },
    )
    shot = _make_shot(
        project.id,
        id="sht_runner_c1",
        ref_frame_job_id=job.id,
        status=ShotStatus.ref_frame_pending,
    )

    class FakePipeline:
        id = "ref_frame"
        execution_adapter_id = "comfy"
        output_labels = {"layout": "Layout"}

        def build_prompt(self, job, *, uploaded_images):
            return {"1": {}}, 42

        def map_history_outputs(self, history, *, job=None):
            return {
                "layout": ComfyImageRef(
                    filename="layout.png", subfolder="", type="output"
                ),
            }

        def save_to_library(self, job, *, name=None, notes=None):
            from app.pipelines import get_pipeline as real_get

            return real_get("ref_frame").save_to_library(job, name=name, notes=notes)

    class RecordingOrch:
        async def before_comfy_job(self, pipeline_id: str) -> None:
            pass

        async def after_comfy_job(self, pipeline_id: str, terminal_status: str) -> None:
            pass

        async def update_generation(self, job_id: str, *, status: str, phase: str) -> None:
            pass

        async def release_generation(self, job_id: str) -> None:
            pass

    fake_client = SimpleNamespace(
        upload_image=AsyncMock(return_value="up.png"),
        queue_prompt=AsyncMock(return_value="prompt-1"),
        wait_for_completion=AsyncMock(return_value={"outputs": {}}),
        download_image=AsyncMock(return_value=b"png-from-comfy"),
    )

    monkeypatch.setattr(runner, "get_pipeline", lambda _pid: FakePipeline())
    monkeypatch.setattr(runner, "ComfyClient", lambda: fake_client)
    monkeypatch.setattr(runner, "get_orchestrator", lambda: RecordingOrch())

    cancel = asyncio.Event()
    await runner._run_job(job.id, {}, cancel)

    final_job = store.load_job(job.id)
    assert final_job is not None
    assert final_job.status == JobStatus.succeeded

    updated = load_shot(project.id, shot.id)
    assert updated is not None
    assert updated.status == ShotStatus.needs_review
    assert updated.layout_review_status == "pending_review"
    assert updated.layout_asset_id is not None
    assert updated.ref_frame_job_id == job.id
