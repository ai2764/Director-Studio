import pytest
from app.agents.director.chat_orchestrator import _requested_minimum_duration_s


@pytest.mark.parametrize("text, expected", [
    ("Create a 2–3 minute historical experience film.", 120),
    ("Total runtime 2:30–2:50", 150),
    ("做一个2-3分钟的影片", 120),
    ("At least 90 seconds", 90),
    ("Shot 2: 3 minutes later they arrive.", 0),
    ("Mia waits 2–3 minutes for the ship.", 0),
    ("她等了2-3分钟才上船。", 0),
    ("Target duration: 2–3 minutes", 120),
])
def test_extracts_authored_total_duration(text, expected):
    assert _requested_minimum_duration_s(text) == expected


def test_directing_request_survives_context_rebuild_and_unrelated_followup():
    from app.core.projects.store import create_project
    from app.agents.director.service import _build_context
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.brief import remember_directing_request, directing_requests, minimum_duration
    project = create_project("Film", "A visit to a ship.")
    remember_directing_request(project.id, "Create a 2–3 minute film using Tao's handheld VCR.")
    save_agent_context(project.id, _build_context(project, [], phase="planned"))
    remember_directing_request(project.id, "Keep both face references.")
    assert minimum_duration(project) == 120
    assert "handheld VCR" in directing_requests(project)[0]
    remember_directing_request(project.id, "Revise to a 1–2 minute film.")
    assert minimum_duration(project) == 60


def test_repair_key_invalidates_when_directing_request_changes():
    from app.core.projects.store import create_project
    from app.core.projects.models import Shot
    from app.agents.director.brief import remember_directing_request
    from app.agents.director.prompt_repair import repair_key
    project = create_project("Film", "Ship visit")
    shot = Shot(id="sht_1", project_id=project.id, scene_id="scene_1", title="Boarding",
                script_beat="They board the ship.", duration_s=5)
    old = repair_key(project, shot, "pixels", "", "model")
    remember_directing_request(project.id, "Mia wears the beige skirt suit throughout.")
    assert repair_key(project, shot, "pixels", "", "model") != old


@pytest.mark.parametrize("duration", [16, float("nan"), float("inf")])
def test_revise_rejects_unsupported_duration_before_any_write(duration):
    from app.core.projects.store import create_project, save_project, save_shot, load_shot
    from app.core.projects.models import Shot
    from app.agents.director.service import DirectorService
    from app.agents.director.planner import ShotRevisionSubmission
    project = create_project("Film", "A short shot.")
    shot = Shot(id="sht_duration", project_id=project.id, scene_id="scene_1", title="Boarding",
                script_beat="They board.", duration_s=5, h3_job_id="job_preserved")
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    with pytest.raises(ValueError):
        DirectorService(plan_provider=None).revise_shot(project.id, ShotRevisionSubmission(shot_id=shot.id, duration_s=duration))
    assert load_shot(project.id, shot.id) == shot
