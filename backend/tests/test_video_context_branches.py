"""Non-adjacent shot dependencies must survive chat, storage and submission."""
import hashlib

import pytest

from app.core.projects.models import ShotVideoContext
from app.core.projects.store import load_project, load_shot, replace_project_shots, save_project, save_shot
from app.core.projects.video_context import (
    VideoContextError, configure_video_context, submission_video_context,
    video_context_prompt_is_stale, video_context_prompt_signature,
)
from test_video_context_sources import _board, _media, _shot, _succeed
from test_video_context_tools import _call


def _branch_board(monkeypatch, tmp_path, count):
    project, first, second = _board(monkeypatch, tmp_path)
    shots = [first, second] + [_shot(project.id, f"sht_{i}") for i in range(3, count + 1)]
    jobs = []
    for i, shot in enumerate(shots, 1):
        job = _succeed(project.id, shot.id, f"clip-{i}".encode())
        save_shot(shot.model_copy(update={"h3_job_id": job.id}))
        jobs.append(job)
    save_project(project.model_copy(update={"shot_ids": [shot.id for shot in shots]}))
    monkeypatch.setattr("app.core.projects.video_context.probe_video", lambda _: _media())
    return load_project(project.id), shots, jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("sources", [
    [None, 1, 2, None, None, 2],
    [None, None, 2, 3, 2],
    [None, 1, None, 1],
], ids=["chain-break-return-6-to-2", "3-to-2-4-to-3-5-to-2", "1-2-break-4-to-1"])
async def test_branch_trajectories_use_the_selected_video(monkeypatch, tmp_path, sources):
    from app.api.projects import get_shot_video_context
    from app.agents.director.writer_context import video_context_writer_view

    project, shots, jobs = _branch_board(monkeypatch, tmp_path, len(sources))
    monkeypatch.setattr("app.agents.director.writer_context._png_from_video",
                        lambda path: path.read_bytes())
    for i, source_number in enumerate(sources):
        args = {"shot_id": shots[i].id, "mode": "off" if source_number is None else "previous_shot"}
        if source_number is not None:
            args["source_shot_id"] = shots[source_number - 1].id
        _, results, _, _ = await _call("configure_video_context", project.id, args, "Set this shot's context")
        assert results[-1]["ok"], results[-1]["blocked_reasons"]
        target = load_shot(project.id, shots[i].id)
        staged = submission_video_context(target, width=864, height=480, delivered_frames=56)
        if source_number is None:
            assert target.video_context.source_shot_id is None
            assert staged is None
            assert video_context_writer_view(target) is None
            continue
        source = shots[source_number - 1]
        source_job = jobs[source_number - 1]
        expected_bytes = f"clip-{source_number}".encode()
        assert target.video_context.source_shot_id == source.id
        status = get_shot_video_context(target.id)
        assert status["source_job_id"] == source_job.id
        assert status["blocked_reasons"] == []
        assert staged["data"] == expected_bytes
        provenance = staged["params"]["video_context_source"]
        assert provenance["source_shot_id"] == source.id
        assert provenance["source_job_id"] == source_job.id
        assert provenance["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
        view = video_context_writer_view(target)
        assert view["source_job_id"] == source_job.id
        assert view["tail_frame_png"] == expected_bytes


@pytest.mark.asyncio
async def test_agent_tool_rejects_an_unknown_source_without_falling_back(monkeypatch, tmp_path):
    project, shots, _ = _branch_board(monkeypatch, tmp_path, 4)
    _, results, actions, _ = await _call("configure_video_context", project.id,
        {"shot_id": shots[3].id, "mode": "previous_shot", "source_shot_id": "sht_foreign"},
        "Use the selected source")
    assert results[-1]["ok"] is False
    assert results[-1]["blocked_reasons"]
    assert actions == []
    assert load_shot(project.id, shots[3].id).video_context is None


def test_reorder_keeps_an_earlier_dependency_across_unrelated_shots(monkeypatch, tmp_path):
    project, shots, _ = _branch_board(monkeypatch, tmp_path, 4)
    configure_video_context(project.id, shots[3].id, ShotVideoContext(
        mode="previous_shot", source_shot_id=shots[0].id))
    reordered = [load_shot(project.id, shots[i].id) for i in [0, 2, 1, 3]]
    replace_project_shots(project.id, reordered)
    assert load_project(project.id).shot_ids == [shot.id for shot in reordered]
    assert load_shot(project.id, shots[3].id).video_context.source_shot_id == shots[0].id
    with pytest.raises(VideoContextError):
        replace_project_shots(project.id, reordered[::-1])


def test_return_dependency_tracks_its_source_and_not_the_adjacent_shot(monkeypatch, tmp_path):
    project, shots, _ = _branch_board(monkeypatch, tmp_path, 4)
    configure_video_context(project.id, shots[3].id, ShotVideoContext(
        mode="previous_shot", source_shot_id=shots[0].id))
    target = load_shot(project.id, shots[3].id)
    target = target.model_copy(update={"meta": {
        "prompt_video_context_signature": video_context_prompt_signature(target)}})
    unrelated = _succeed(project.id, shots[2].id, b"unrelated-rerun")
    save_shot(shots[2].model_copy(update={"h3_job_id": unrelated.id}))
    assert not video_context_prompt_is_stale(target)
    rerun = _succeed(project.id, shots[0].id, b"selected-source-rerun")
    save_shot(shots[0].model_copy(update={"h3_job_id": rerun.id}))
    assert video_context_prompt_is_stale(target)
    assert submission_video_context(target, width=864, height=480)["data"] == b"selected-source-rerun"


@pytest.mark.parametrize("source_index", [2, 3])
def test_self_and_future_sources_are_rejected(monkeypatch, tmp_path, source_index):
    project, shots, _ = _branch_board(monkeypatch, tmp_path, 4)
    with pytest.raises(VideoContextError):
        configure_video_context(project.id, shots[2].id, ShotVideoContext(
            mode="previous_shot", source_shot_id=shots[source_index].id))
    assert load_shot(project.id, shots[2].id).video_context is None
