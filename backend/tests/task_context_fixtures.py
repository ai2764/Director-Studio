import pytest

from app.core.projects.models import Shot
from app.core.projects.store import create_project, save_project, save_shot


@pytest.fixture
def context_case(tmp_projects_dir):
    project = create_project("Context fixture", "A courier waits. A guide arrives.")
    target = Shot(id="sht_target", project_id=project.id, scene_id="scene_a",
                  title="Meeting", script_beat="The guide approaches.", duration_s=6)
    neighbor = Shot(id="sht_neighbor", project_id=project.id, scene_id="scene_a",
                    title="Arrival", script_beat="A courier holds a parcel.", duration_s=6)
    save_shot(neighbor)
    save_shot(target)
    project = project.model_copy(update={"shot_ids": [neighbor.id, target.id]})
    save_project(project)
    return project, target, neighbor
