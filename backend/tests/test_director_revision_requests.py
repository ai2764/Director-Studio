"""A revise_shot / append_shot request becomes a directing request for later reviewers.

Found in the real e2e: "make shot 1 exactly 5 seconds" was applied by revise_shot but never
recorded, so material review only saw the original "each about 4 seconds" planning request,
reported the 5 s as SHOT_CONFIGURATION_CONFLICT and the agent reverted the user's edit.
"""
import pytest

from test_director_append_shot import board  # noqa: F401  (fixture)

from app.agents.director.brief import directing_requests, remember_directing_request
from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.service import DirectorService
from app.core.projects.models import Shot
from app.core.projects.store import create_project, load_project, load_shot, save_project, save_shot


@pytest.mark.asyncio
async def test_revise_shot_records_the_revision_request(tmp_projects_dir):
    project = create_project("Rain", "Mia walks in the rain.")
    shot = Shot(id="sht_one", project_id=project.id, scene_id="SC1", title="Walk",
                script_beat="Mia walks in the rain.", duration_s=4)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    remember_directing_request(project.id, "Plan 2 shots, each about 4 seconds.")
    direction = "Make shot 1 exactly 5 seconds with a slow push-in."
    turn = BackendTurn(project.id, direction, DirectorService(plan_provider=object()), None)
    result = await turn.tool({"name": "revise_shot", "call_id": "c1",
                              "arguments": {"shot_id": shot.id, "duration_s": 5,
                                            "camera_motion": "slow push-in"}})
    assert result["ok"], result
    assert load_shot(project.id, shot.id).duration_s == 5
    requests = directing_requests(load_project(project.id))
    assert requests[-1] == direction
    assert requests[0].startswith("Plan 2 shots")


@pytest.mark.asyncio
async def test_append_shots_records_the_batch_request(board):
    from test_director_append_shot import payload

    project, _svc = board
    remember_directing_request(project.id, "Plan 4 shots, each about 8 seconds.")
    request = payload(project)
    draft = request.pop("shot")
    args = {**request, "shots": [{**draft, "title": "Arrival"}, {**draft, "title": "Empty shore"}]}
    direction = "Add two 7 second shore shots at the end."
    turn = BackendTurn(project.id, direction, DirectorService(plan_provider=object()), None)
    result = await turn.tool({"name": "append_shots", "call_id": "c1", "arguments": args})
    assert result["ok"], result
    assert len(load_project(project.id).shot_ids) == 6
    assert directing_requests(load_project(project.id))[-1] == direction
