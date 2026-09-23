"""Persistent managed-run plan constraints independent of HTTP."""

from __future__ import annotations

import pytest

from app.core.managed_runs.models import RunStep
from app.core.jobs.store import create_job
from app.core.managed_runs.store import (
    _save_run,
    activate_run,
    bind_job,
    create_draft,
    finish_stop,
    load_run,
    request_stop,
    run_selected,
)
from app.core.projects.models import Shot, ShotMusicSegment
from app.core.projects.store import create_project, save_project, save_shot


def _project_with_two_shots():
    project = create_project("Connected scene", "Two shots")
    first = Shot(id="sht_first", project_id=project.id, scene_id="scene_1",
                 title="First", script_beat="The hatch closes.", duration_s=5)
    second = Shot(id="sht_second", project_id=project.id, scene_id="scene_1",
                  title="Second", script_beat="The hatch opens again.", duration_s=5)
    save_shot(first)
    save_shot(second)
    save_project(project.model_copy(update={"shot_ids": [first.id, second.id]}))
    return project, first, second


def test_draft_requires_current_project_shot_order() -> None:
    project, first, second = _project_with_two_shots()

    with pytest.raises(ValueError, match="project order"):
        create_draft(project.id, [RunStep(shot_id=second.id), RunStep(shot_id=first.id)])


def test_tail_source_must_precede_target_and_have_reason() -> None:
    project, first, second = _project_with_two_shots()

    with pytest.raises(ValueError, match="earlier Shot"):
        create_draft(project.id, [
            RunStep(shot_id=first.id, tail_from_shot_id=second.id, tail_reason="Reverse"),
            RunStep(shot_id=second.id),
        ])
    with pytest.raises(ValueError, match="reason"):
        create_draft(project.id, [
            RunStep(shot_id=first.id),
            RunStep(shot_id=second.id, tail_from_shot_id=first.id),
        ])


def test_only_one_active_run_and_saved_resolution_is_stable() -> None:
    project, first, second = _project_with_two_shots()
    steps = [RunStep(shot_id=first.id), RunStep(shot_id=second.id)]
    first_run = create_draft(project.id, steps)
    second_run = create_draft(project.id, steps)

    active = activate_run(project.id, first_run.run_id, "portrait-768")

    assert active.resolution_preset == "portrait-768"
    assert load_run(project.id, first_run.run_id).resolution_preset == "portrait-768"
    with pytest.raises(ValueError, match="Another managed run"):
        activate_run(project.id, second_run.run_id, "portrait-1080")


def test_changed_shot_brief_cannot_activate_old_plan() -> None:
    project, first, second = _project_with_two_shots()
    draft = create_draft(project.id, [RunStep(shot_id=first.id), RunStep(shot_id=second.id)])
    save_shot(second.model_copy(update={"script_beat": "A completely different action."}))

    with pytest.raises(ValueError, match="changed"):
        activate_run(project.id, draft.run_id, "landscape-480")


def test_fresh_draft_defaults_selection_to_every_planned_shot() -> None:
    project, first, second = _project_with_two_shots()

    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )

    assert draft.selected_shot_ids == [first.id, second.id]
    assert draft.pending_shot_ids == []


def test_run_selected_can_rerun_a_completed_shot() -> None:
    project, first, second = _project_with_two_shots()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    run = _save_run(
        draft.model_copy(
            update={
                "state": "completed",
                "resolution_preset": "landscape-480",
                "completed_job_ids": {first.id: "job_old"},
            }
        )
    )

    resumed = run_selected(project.id, run.run_id, [first.id], None)

    assert resumed.state == "active"
    assert resumed.pending_shot_ids == [first.id]
    assert resumed.completed_job_ids[first.id] == "job_old"


@pytest.mark.parametrize("state", ["paused", "stopped", "completed"])
def test_run_selected_reactivates_every_inactive_terminal_state(state) -> None:
    project, first, second = _project_with_two_shots()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    inactive = _save_run(
        draft.model_copy(
            update={
                "state": state,
                "resolution_preset": "landscape-480",
                "pending_event_id": None,
            }
        )
    )

    resumed = run_selected(project.id, inactive.run_id, [second.id], None)

    assert resumed.state == "active"
    assert resumed.pending_shot_ids == [second.id]
    assert resumed.current_index == 1
    assert resumed.pending_event_id.startswith("selection:")


def test_run_selected_rejects_empty_selection_and_resolution_change() -> None:
    project, first, second = _project_with_two_shots()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )

    with pytest.raises(ValueError, match="Select at least one"):
        run_selected(project.id, draft.run_id, [], "landscape-480")

    active = run_selected(
        project.id, draft.run_id, [first.id], "landscape-480"
    )
    request_stop(project.id, active.run_id)
    finish_stop(project.id, active.run_id)
    with pytest.raises(ValueError, match="cannot change"):
        run_selected(project.id, active.run_id, [second.id], "portrait-480")


def test_selection_with_only_missing_dependencies_persists_warning_without_starting() -> None:
    project, first, second = _project_with_two_shots()
    draft = create_draft(
        project.id,
        [
            RunStep(shot_id=first.id),
            RunStep(
                shot_id=second.id,
                tail_from_shot_id=first.id,
                tail_reason="Continue the door",
            ),
        ],
    )

    updated = run_selected(
        project.id, draft.run_id, [second.id], "landscape-480"
    )

    assert updated.state == "paused"
    assert updated.selected_shot_ids == [second.id]
    assert updated.pending_shot_ids == []
    assert second.id in updated.skipped_shots
    assert updated.current_index == len(updated.steps)
    assert updated.pending_event_id is None


def test_stop_preserves_pending_queue_and_clears_cancelled_binding() -> None:
    project, first, second = _project_with_two_shots()
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    running = run_selected(
        project.id,
        draft.run_id,
        [first.id, second.id],
        "landscape-480",
    )
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="managed first",
        project_id=project.id,
        params={"shot_id": first.id},
    )
    bind_job(project.id, running.run_id, first.id, job.id)

    request_stop(project.id, running.run_id)
    stopped = finish_stop(project.id, running.run_id)

    assert stopped.state == "stopped"
    assert stopped.current_job_id is None
    assert stopped.pending_event_id is None
    assert stopped.pending_shot_ids == [first.id, second.id]


def test_music_segment_change_invalidates_saved_plan() -> None:
    project, first, second = _project_with_two_shots()
    segment = ShotMusicSegment(
        core_start_s=1,
        core_end_s=4,
        submit_start_s=0,
        submit_end_s=5,
    )
    second = second.model_copy(update={"music_segment": segment})
    save_shot(second)
    draft = create_draft(
        project.id,
        [RunStep(shot_id=first.id), RunStep(shot_id=second.id)],
    )
    changed = segment.model_copy(update={"core_start_s": 2})
    save_shot(second.model_copy(update={"music_segment": changed}))

    with pytest.raises(ValueError, match="changed"):
        run_selected(project.id, draft.run_id, [second.id], "landscape-480")
