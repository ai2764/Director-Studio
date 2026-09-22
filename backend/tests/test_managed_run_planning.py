"""Persistent managed-run plan constraints independent of HTTP."""

from __future__ import annotations

import pytest

from app.core.managed_runs.models import RunStep
from app.core.managed_runs.store import activate_run, create_draft, load_run
from app.core.projects.models import Shot
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
