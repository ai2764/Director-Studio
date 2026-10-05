"""Previous-shot continuation inherits the selected artifact's actual size."""
import pytest
from fastapi import HTTPException

from app.core.projects.models import ShotVideoContext
from app.core.projects.store import load_shot, save_shot
from app.core.projects.video_context import configure_video_context, resolve_video_context, VideoContextError
from test_video_context_sources import _board, _succeed, _media


def _configured(monkeypatch, tmp_path):
    project, first, second = _board(monkeypatch, tmp_path)
    original = _succeed(project.id, first.id)
    replacement = _succeed(project.id, first.id, b"new-video")
    save_shot(first.model_copy(update={"h3_job_id": replacement.id}))
    monkeypatch.setattr("app.core.projects.video_context.probe_video",
                        lambda path: _media(width=480, height=864) if original.id in str(path)
                        else _media(width=704, height=1280))
    configure_video_context(project.id, second.id, ShotVideoContext(
        mode="previous_shot", source_job_id=original.id))
    return project, load_shot(project.id, second.id), original


def test_status_resolves_pinned_artifact_size_not_current_job(monkeypatch, tmp_path):
    from app.api import projects as api
    _project, shot, original = _configured(monkeypatch, tmp_path)
    status = api.get_shot_video_context(shot.id)
    assert status["resolution"] == {"width": 480, "height": 864}
    assert status["source_job_id"] == original.id
    assert str(tmp_path) not in str(status)


def test_resolve_rejects_same_aspect_but_different_size(monkeypatch, tmp_path):
    _project, shot, _original = _configured(monkeypatch, tmp_path)
    before = load_shot(shot.project_id, shot.id)
    with pytest.raises(VideoContextError, match="resolution"):
        resolve_video_context(shot, width=960, height=1728)
    assert load_shot(shot.project_id, shot.id) == before


@pytest.mark.asyncio
async def test_submit_rejects_resolution_override_before_writer_or_new_job(monkeypatch, tmp_path):
    from app.api import projects as api
    from app.core.jobs.store import list_jobs
    project, shot, _original = _configured(monkeypatch, tmp_path)
    before = {job.id for job in list_jobs(project_id=project.id)}
    class Service:
        async def write_prompts_after_layout(self, *args, **kwargs):
            raise AssertionError("resolution must be checked before writing")
    with pytest.raises(HTTPException) as error:
        await api.submit_shot_endpoint(shot.id, svc=Service(),
            options=api.H3SubmitOptions(width=960, height=1728))
    assert error.value.status_code == 400
    assert "480" in error.value.detail and "864" in error.value.detail
    assert {job.id for job in list_jobs(project_id=project.id)} == before


def test_off_status_has_no_resolution_constraint(monkeypatch, tmp_path):
    from app.api import projects as api
    _project, shot, _original = _configured(monkeypatch, tmp_path)
    configure_video_context(shot.project_id, shot.id, ShotVideoContext(mode="off"))
    status = api.get_shot_video_context(shot.id)
    assert status["resolution"] is None
    assert status["mode"] == "off"
