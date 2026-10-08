import json

import pytest

from app.agents.director.brief import directing_requests, remember_directing_request
from app.agents.director.context_io import save_agent_context
from app.agents.director.planner import AssetMatchDraft, ShotRefsPatchSubmission
from app.agents.director.reference_facts import reference_sources
from app.agents.director.service import _build_context
from app.core.projects.chat_history import append_chat_message
from app.core.projects.store import create_project, load_project


@pytest.mark.asyncio
async def test_reference_purpose_survives_set_script_and_context_rebuild():
    from app.agents.director.chat import _project_context_blob, _run_tools
    from app.agents.director.service import DirectorService

    project = create_project("Reference intent", "A probe approaches a station.")
    purpose = "Use station_side for surface material only; docking geometry comes from station_plan."
    message = append_chat_message(project.id, role="user", content=purpose)
    remember_directing_request(project.id, purpose)
    original = reference_sources(project, {})[0]
    await _run_tools(
        project_id=project.id,
        tools=[{"name": "set_script", "args": {"script": "The probe enters the docking bay."}}],
        svc=DirectorService(plan_provider=None), actions=[], user_feedback="Expand the arrival beat.",
    )
    updated = load_project(project.id)
    save_agent_context(project.id, _build_context(updated, [], phase="planned"))

    context = json.loads(_project_context_blob(updated, []))
    assert purpose in context["directing_requests"]
    source = next(item for item in reference_sources(updated, {}) if item["text"] == purpose)
    assert source["id"] == original["id"] == message.id
    assert source["source_message_id"] == message.id
    assert source["kind"] == "user_directing_request"
    assert source["script_hash"] == original["script_hash"]
    assert source["script_current"] is False


def test_replacement_removal_and_repeated_original_intent_remain_chronological():
    project = create_project("Reference intent", "A probe docks.")
    requests = [
        "Use station_side for surface material only.",
        "Replace station_side with station_weathered for material.",
        "Remove the material reference; use the script description.",
        "Use station_side for surface material only.",
    ]
    ids = []
    for text in requests:
        message = append_chat_message(project.id, role="user", content=text)
        ids.append(message.id)
        remember_directing_request(project.id, text)
        remember_directing_request(project.id, text)  # A retry is not a new user request.
    assert directing_requests(project) == requests
    assert [source["source_message_id"] for source in reference_sources(project, {})] == ids


def test_song_map_is_not_saved_as_a_directing_request() -> None:
    project = create_project("Music intent", "")
    authored = "Keep the camera fixed."
    expanded = authored + "\n\nComplete saved song segmentation: 2 timestamped units.\nAll saved song segments:\n[]"
    append_chat_message(project.id, role="user", content=authored)
    remember_directing_request(project.id, expanded)
    assert directing_requests(project) == [authored]
    assert reference_sources(project, {})[0]["source_message_id"] is not None

    from app.agents.director.context_io import load_agent_context, save_agent_context
    context = load_agent_context(project.id)
    record = context.extra["directing_brief"]
    record["sources"][0]["text"] = expanded  # Existing project data from an older run.
    save_agent_context(project.id, context)
    assert directing_requests(project) == [authored]

    remember_directing_request(project.id, "Change the angle.\n\nSelected song segment IDs: [\"seg_b\"]")
    assert directing_requests(project)[-1] == "Change the angle."


def test_legacy_request_keeps_original_script_provenance_after_update():
    from app.core.projects.models import AgentContext
    from app.core.projects.store import save_project

    project = create_project("Legacy intent", "New script")
    save_agent_context(project.id, AgentContext(project_id=project.id, script_hash="old", extra={
        "directing_brief": {"script_hash": "original-script-hash", "messages": ["Keep the tilted horizon."]}
    }))
    remember_directing_request(project.id, "Use a wider view.")
    save_project(project)
    first = reference_sources(project, {})[0]
    assert first["text"] == "Keep the tilted horizon."
    assert first["script_hash"] == "original-script-hash"
    assert first["script_current"] is False
    assert first["source_message_id"] is None


def test_reference_patch_carries_free_text_purpose_and_distinguishes_omission_from_clear():
    base = {"role": "scene", "asset_id": "station_side", "file_key": "reference", "picture_index": 1}
    submitted = ShotRefsPatchSubmission.model_validate({"updates": [{"shot_id": "sht_probe", "refs": [
        {**base, "notes": "Material only; source msg_probe. Do not copy the camera angle."}
    ]}]})
    ref = submitted.updates[0].refs[0]
    assert ref.model_dump(exclude_unset=True)["notes"] == "Material only; source msg_probe. Do not copy the camera angle."
    assert "notes" not in AssetMatchDraft.model_validate(base).model_fields_set
    assert AssetMatchDraft.model_validate({**base, "notes": ""}).model_dump(exclude_unset=True)["notes"] == ""
