import json

import pytest

from app.agents.director.asset_catalog import _inventory
from app.agents.director.chat_context import project_context_blob
from app.core.library.store import write_asset
from app.core.projects.store import create_project
from app.core.schemas import LibraryAsset


def layout(project_id, asset_id, *, external=True, review_status=None):
    return write_asset(LibraryAsset(
        id=asset_id, kind="layouts", name="Mia and puppet identity group",
        notes="Group portrait establishes actor and puppet identities; disregard its stage.",
        pipeline_id="external" if external else "qwen21_layout", job_id="",
        created_at="2026-10-03T00:00:00Z", files={"master": "group.png"},
        project_id=project_id,
        meta={"external": external, "review_status": review_status},
    ))


def test_imported_layout_discoverable_in_director_context_but_not_casting_pool():
    project = create_project("Layout discovery", "Generate a new lake scene.")
    asset = layout(project.id, "lay_imported_identity")
    context = json.loads(project_context_blob(project, []))
    item = next((a for a in context["library_inventory"] if a["id"] == asset.id), None)
    assert item is not None, "Imported reference image must expose an exact handle to Agent"
    assert item["kind"] == "layouts"
    assert item["file_keys"] == ["master"]
    assert "identities" in item["notes"]
    assert not any(a["id"] == asset.id for a in _inventory(project.id))


def test_context_does_not_expand_to_foreign_generated_or_rejected_layouts():
    project = create_project("Layout discovery", "Generate a new lake scene.")
    foreign = create_project("Other project", "Unrelated")
    layout(project.id, "lay_rejected", review_status="reject")
    layout(project.id, "lay_generated", external=False)
    layout(foreign.id, "lay_foreign")
    visible = json.loads(project_context_blob(project, []))["library_inventory"]
    assert not {"lay_rejected", "lay_generated", "lay_foreign"}.intersection(a["id"] for a in visible)


def append_request(project, asset_id, file_key="master"):
    from app.agents.director.asset_catalog import _script_hash
    return {
        "expected_script_hash": _script_hash(project.script_text),
        "expected_last_shot_id": None,
        "shot": {
            "scene_id": "lake", "title": "Paper light", "script_beat": "Mia holds a light.",
            "shot_type": "medium", "camera_angle": "eye level", "camera_motion": "locked-off",
            "composition": "Mia beside puppets", "duration_s": 8,
            "asset_matches": [{"role": "other", "asset_id": asset_id, "file_key": file_key}],
        },
    }


def test_append_preserves_explicit_imported_layout_identity_reference():
    from app.agents.director.service import DirectorService
    from app.core.projects.store import load_shot
    project = create_project("Paper light", "Mia holds a light beside the lake.")
    layout(project.id, "lay_identity_group")
    svc = DirectorService(plan_provider=None, orchestrator=object())
    shot = svc.append_shot(project.id, append_request(project, "lay_identity_group"))
    saved = load_shot(project.id, shot.id)
    assert [(r.role.value, r.asset_id, r.file_key) for r in saved.refs] == [
        ("other", "lay_identity_group", "master")
    ]
    assert not any(a["id"] == "lay_identity_group" for a in _inventory(project.id))


@pytest.mark.parametrize("invalid", ["foreign", "generated", "rejected", "file_key"])
def test_append_rejects_inaccessible_or_invalid_layout_reference(invalid):
    from app.agents.director.service import DirectorService
    from app.core.projects.store import list_shots
    project = create_project("Paper light", "Mia holds a light beside the lake.")
    owner = create_project("Other", "Other").id if invalid == "foreign" else project.id
    layout(owner, "lay_invalid", external=invalid != "generated",
           review_status="reject" if invalid == "rejected" else None)
    svc = DirectorService(plan_provider=None, orchestrator=object())
    key = "missing" if invalid == "file_key" else "master"
    with pytest.raises(ValueError, match="unknown library asset|invalid file_key"):
        svc.append_shot(project.id, append_request(project, "lay_invalid", key))
    assert list_shots(project.id) == []


@pytest.mark.asyncio
async def test_save_storyboard_can_bind_the_same_imported_identity_reference(tmp_projects_dir):
    from app.agents.director.service import DirectorService
    from test_harness_grounding_contract import ValidationProvider, Orchestrator
    project = create_project("Imported reference storyboard", "Mia holds a light.")
    asset = layout(project.id, "lay_storyboard_identity")
    request = append_request(project, asset.id)
    svc = DirectorService(plan_provider=ValidationProvider(), orchestrator=Orchestrator())
    result = await svc.save_storyboard(project.id, [request["shot"]], request["expected_script_hash"])
    assert result[0].refs[0].asset_id == asset.id
    assert result[0].blocked_reasons == []
    assert result[0].meta["asset_binding_policy"] == "explicit"


def test_storyboard_tools_do_not_offer_reserved_generated_layout_roles():
    from app.agents.director.tool_schema import director_tool_schemas
    project = create_project("Schema", "A guitarist walks.")
    for tool in director_tool_schemas(project):
        if tool["function"]["name"] in {"save_storyboard", "append_shot"}:
            role = tool["function"]["parameters"]["$defs"]["AssetMatchDraft"]["properties"]["role"]
            assert "layout_ref_frame" not in role["enum"]
            assert "layout" not in role["enum"]
            assert "other" in role["enum"]


def test_patch_refs_can_edit_an_imported_identity_reference():
    from app.agents.director.service import DirectorService
    project = create_project("Imported edit", "Mia holds a light.")
    asset = layout(project.id, "lay_patch_identity")
    svc = DirectorService(plan_provider=None, orchestrator=object())
    shot = svc.append_shot(project.id, append_request(project, asset.id))
    result = svc.patch_shot_refs(project.id, [{
        "shot_id": shot.id,
        "refs": [{"role": "other", "asset_id": asset.id, "file_key": "master",
                  "picture_index": 1, "notes": "Preserve the microphone."}],
    }])
    assert result[0].refs[0].notes == "Preserve the microphone."
    assert result[0].script_beat == shot.script_beat
