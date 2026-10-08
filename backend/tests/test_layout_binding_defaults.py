import pytest

from app.agents.director.planner import ShotRefsPatch
from app.agents.director.service import DirectorService
from app.core.library.store import write_asset
from app.core.projects.layouts import LayoutReference
from app.core.projects.models import RefRole, Shot, ShotRef
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from test_director_agent import director_dirs, _seed_layout_source_asset


@pytest.mark.parametrize("file_key", ["layout", "master"])
def test_rebinding_owned_layout_without_optional_file_key_preserves_image(director_dirs, file_key):
    project = create_project("Layout rebinding", "A traveler crosses the valley.")
    asset = _seed_layout_source_asset(
        director_dirs["library"], kind="layouts", asset_id="lay_owned",
        name="Approved valley", file_key=file_key,
    )
    write_asset(asset.model_copy(update={"project_id": project.id}))
    layout = LayoutReference(id="lref_owned", asset_id=asset.id,
                             purpose="Approved valley composition", selected_for_h3=True)
    shot = Shot(id="sht_owned", project_id=project.id, scene_id="valley",
                title="Crossing", script_beat="A traveler crosses the valley.", duration_s=8,
                layout_refs=[layout], refs=[ShotRef(role=RefRole.layout_ref_frame,
                    asset_id=asset.id, file_key=file_key, picture_index=1,
                    notes="Preserve the approved valley composition.")])
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    svc = DirectorService(plan_provider=None, orchestrator=object())

    svc.patch_shot_refs(project.id, [ShotRefsPatch(shot_id=shot.id, refs=[{
        "role": "layout", "asset_id": asset.id, "picture_index": 1,
    }])])

    saved = load_shot(project.id, shot.id)
    assert saved.refs == shot.refs
    assert saved.layout_refs == shot.layout_refs
