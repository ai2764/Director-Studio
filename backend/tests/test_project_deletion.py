"""Only synthetic projects under pytest's isolated runtime are deleted."""
import pytest
from app.config import settings
from app.core.projects.store import create_project, project_dir, load_project
from app.core.projects.deletion import delete_project
from app.core.jobs.store import create_job


def test_permanent_delete_removes_only_confirmed_project():
    project = create_project("Disposable test project", "")
    other = create_project("Keep test project", "")
    artifact = project_dir(project.id) / "shots" / "fixture.txt"
    artifact.write_text("fixture")
    shared = settings.library_root / "shared.txt"
    shared.write_text("shared fixture")
    delete_project(project.id, project.name)
    assert not project_dir(project.id).exists()
    assert load_project(other.id) is not None
    assert shared.read_text() == "shared fixture"


def test_wrong_confirmation_does_not_delete():
    project = create_project("Disposable test project", "")
    with pytest.raises(ValueError, match="exactly"):
        delete_project(project.id, "wrong")
    assert load_project(project.id) is not None


def test_active_generation_blocks_delete():
    project = create_project("Disposable test project", "")
    create_job(pipeline_id="h3_ref2va",asset_kind="productions",name="fixture",project_id=project.id)
    with pytest.raises(ValueError, match="generation"):
        delete_project(project.id, project.name)
    assert load_project(project.id) is not None


@pytest.mark.parametrize("project_id", ["../", "prj_../outside", "prj_a/../../", "C:/Users"])
def test_delete_rejects_unsafe_project_id(project_id):
    with pytest.raises(ValueError, match="Invalid project ID"):
        delete_project(project_id, "anything")


@pytest.mark.asyncio
async def test_endpoint_blocks_active_agent():
    from app.api.projects import delete_project_endpoint, DeleteProjectBody
    from app.core.projects.chat_sessions import director_chat_sessions
    from fastapi import HTTPException
    project = create_project("Disposable test project", "")
    session = await director_chat_sessions.reserve(project.id)
    try:
        with pytest.raises(HTTPException) as error:
            await delete_project_endpoint(project.id,DeleteProjectBody(confirmation=project.name))
        assert error.value.status_code == 409
        assert load_project(project.id) is not None
    finally:
        await director_chat_sessions.finish(project.id, session.session_id)


def test_workspace_refinement_blocks_delete(monkeypatch):
    import app.shot_workspace.timings as timings
    project = create_project("Disposable test project", "")
    monkeypatch.setattr(timings,"_active_operations",{project.id:1})
    with pytest.raises(ValueError, match="assistant"):
        delete_project(project.id,project.name)
    assert load_project(project.id) is not None
