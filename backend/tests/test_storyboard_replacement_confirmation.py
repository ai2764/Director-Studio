import json

import pytest
from app.agents.director.chat import _project_context_blob, _run_tools, handle_chat
from app.agents.director.service import _script_hash
from app.agents.director.tool_handlers.project import _load_storyboard_replacement
from app.core.projects.models import Shot
from app.core.projects.store import create_project, list_shots, save_project, save_shot

CAMERA_DRAFT = {
    "shot_type": "medium shot",
    "camera_angle": "eye level",
    "camera_motion": "locked-off",
    "composition": "the subject remains readable in the center of frame",
}


def _existing_project():
    project = create_project("Replacement confirmation", "A robot crosses the plaza.")
    old = Shot(
        id="sht_existing_confirmation",
        project_id=project.id,
        scene_id="sc_old",
        title="Existing shot",
        script_beat="The existing shot must remain until confirmation.",
        duration_s=5.0,
    )
    save_shot(old)
    save_project(project.model_copy(update={"shot_ids": [old.id]}))
    return project, old


def _replacement_submission(project):
    return {
        "expected_script_hash": _script_hash(project.script_text),
        "shots": [
            {
                **CAMERA_DRAFT,
                "scene_id": "sc_new",
                "title": "Replacement shot",
                "script_beat": "The robot launches from the plaza in one continuous shot.",
                "duration_s": 10.0,
                "dialogue": [],
                "asset_matches": [],
                "voice_matches": [],
            }
        ],
    }


class RecordingService:
    def __init__(self, returned_shots):
        self.returned_shots = returned_shots
        self.calls = []

    async def preview_storyboard(self, *args, **kwargs):
        return self.returned_shots

    async def save_storyboard(
        self,
        project_id,
        drafts,
        expected_script_hash,
        *,
        user_feedback,
        requested_minimum_duration_s,
    ):
        self.calls.append(
            {
                "project_id": project_id,
                "drafts": drafts,
                "expected_script_hash": expected_script_hash,
                "user_feedback": user_feedback,
                "requested_minimum_duration_s": requested_minimum_duration_s,
            }
        )
        return self.returned_shots


@pytest.mark.asyncio
async def test_existing_storyboard_save_becomes_a_confirmation_proposal(
    tmp_projects_dir,
):
    project, old = _existing_project()
    service = RecordingService([old])
    actions = []
    payloads = []

    await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "save_storyboard",
                "args": _replacement_submission(project),
            }
        ],
        svc=service,
        actions=actions,
        result_payloads=payloads,
        user_feedback="Replace the current storyboard with one shot.",
    )

    assert service.calls == []
    assert list_shots(project.id) == [old]
    assert len(actions) == 1
    assert actions[0].startswith("propose_storyboard_replacement:")
    assert payloads[-1]["confirmation_required"] is True
    assert payloads[-1]["concludes_turn"] is True
    assert "清除" in payloads[-1]["reply"]
    assert "重写" in payloads[-1]["reply"]
    context = json.loads(_project_context_blob(project, [old], focused=True))
    assert context["pending_storyboard_replacement"] == {
        "proposal_id": actions[0].split(":", 1)[1],
        "shot_count": 1,
        "status": "awaiting_explicit_destructive_confirmation",
        "changes": {"removed_shot_ids": [], "removed_dialogue": []},
        "last_error": None,
    }


@pytest.mark.asyncio
async def test_storyboard_replacement_requires_explicit_later_confirmation(
    tmp_projects_dir,
):
    project, old = _existing_project()
    service = RecordingService([old])

    await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "save_storyboard",
                "args": _replacement_submission(project),
            }
        ],
        svc=service,
        actions=[],
        user_feedback="Replace the current storyboard with one shot.",
    )

    for message in ("Replace the current storyboard with one shot.", "ok"):
        actions = []
        notes, _ = await _run_tools(
            project_id=project.id,
            tools=[{"name": "confirm_storyboard_replacement", "args": {"proposal_id": _load_storyboard_replacement(project.id)["id"]}}],
            svc=service,
            actions=actions,
            user_feedback=message,
        )
        assert actions == []
        assert service.calls == []
        assert "明确" in notes[-1]

    actions = []
    payloads = []
    await _run_tools(
        project_id=project.id,
        tools=[{"name": "confirm_storyboard_replacement", "args": {"proposal_id": _load_storyboard_replacement(project.id)["id"]}}],
        svc=service,
        actions=actions,
        result_payloads=payloads,
        user_feedback="确认清除并重写全部 shots",
    )

    assert actions == ["save_storyboard"]
    assert len(service.calls) == 1
    assert service.calls[0]["project_id"] == project.id
    assert service.calls[0]["drafts"][0].title == "Replacement shot"
    assert "Replace the current storyboard" in service.calls[0]["user_feedback"]
    assert payloads[-1]["storyboard"]["shots"][0]["id"] == old.id


@pytest.mark.asyncio
async def test_native_chat_ends_with_the_destructive_replacement_warning(
    tmp_projects_dir,
):
    project, old = _existing_project()
    service = RecordingService([old])
    model_calls = 0

    async def chat_fn(system, user, **kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            raise AssertionError("the confirmation warning must conclude this turn")
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "save_storyboard",
                    "arguments": _replacement_submission(project),
                }
            ],
        }

    result = await handle_chat(
        project_id=project.id,
        message="Make the whole storyboard one shot and save it.",
        svc=service,
        chat_fn=chat_fn,
    )

    assert model_calls == 1
    assert service.calls == []
    assert list_shots(project.id) == [old]
    assert "清除" in result.reply
    assert "确认清除并重写全部 shots" in result.reply


@pytest.mark.asyncio
async def test_harness_backend_returns_a_terminal_confirmation_warning(
    tmp_projects_dir,
):
    from app.agents.director.harness_runtime import BackendTurn

    project, old = _existing_project()
    service = RecordingService([old])
    turn = BackendTurn(
        project.id,
        "Make the whole storyboard one shot and save it.",
        service,
        None,
    )
    await turn.dispatch("context", {})

    result = await turn.dispatch(
        "tool",
        {
            "name": "save_storyboard",
            "arguments": _replacement_submission(project),
            "call_id": "replace-1",
        },
    )

    assert result["ok"] is True
    assert result["confirmation_required"] is True
    assert result["concludes_turn"] is True
    assert "清除" in result["reply"]
    assert service.calls == []
    assert list_shots(project.id) == [old]


@pytest.mark.asyncio
async def test_storyboard_change_invalidates_the_pending_replacement(
    tmp_projects_dir,
):
    project, old = _existing_project()
    service = RecordingService([old])

    await _run_tools(
        project_id=project.id,
        tools=[
            {
                "name": "save_storyboard",
                "args": _replacement_submission(project),
            }
        ],
        svc=service,
        actions=[],
        user_feedback="Replace the current storyboard with one shot.",
    )
    intervening = Shot(
        id="sht_intervening_change",
        project_id=project.id,
        scene_id="sc_changed",
        title="Intervening shot",
        script_beat="A manual edit happens after the warning.",
        duration_s=4.0,
    )
    save_shot(intervening)
    save_project(
        project.model_copy(update={"shot_ids": [old.id, intervening.id]})
    )

    actions = []
    notes, _ = await _run_tools(
        project_id=project.id,
        tools=[{"name": "confirm_storyboard_replacement", "args": {"proposal_id": _load_storyboard_replacement(project.id)["id"]}}],
        svc=service,
        actions=actions,
        user_feedback="确认清除并重写全部 shots",
    )

    assert service.calls == []
    assert actions == []
    assert "changed after the replacement warning" in notes[-1]
    assert list_shots(project.id) == [old, intervening]
