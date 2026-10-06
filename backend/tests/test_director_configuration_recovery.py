"""Recover explicit authoring mistakes without changing the story in a reviewer."""
import json

import pytest
from jsonschema import Draft202012Validator

from app.agents.director.harness_runtime import BackendTurn
from app.agents.director.material_review import review_references
from app.core.projects.models import LayoutReference, Shot, ShotVoiceRef, AgentContext
from app.core.projects.store import create_project, load_shot, save_project, save_shot


def project_and_shot():
    project = create_project("Silent ending", "Hold the ending pose.")
    shot = Shot(id="sht_hold", project_id=project.id, scene_id="studio",
                title="Hold", script_beat="Hold the ending pose.", duration_s=13)
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    return project, shot


def test_multiple_conflicts_must_all_be_revised_before_retry(tmp_projects_dir):
    from app.agents.director.configuration_recovery import ConfigurationRecovery
    _, shot = project_and_shot()
    shot = shot.model_copy(update={"voice_refs": [ShotVoiceRef(asset_id="voice_tao", audio_index=1)]})
    recovery = ConfigurationRecovery()
    assert recovery.record(shot, {"issues": [{"field": field} for field in ("duration_s", "voice_matches")]})
    partial = shot.model_copy(update={"duration_s": 3})
    assert recovery.before_write(partial) is not None
    assert recovery.before_write(partial.model_copy(update={"voice_refs": []})) is None


def test_video_only_project_does_not_offer_layout_acceptance(tmp_projects_dir):
    project, shot = project_and_shot()
    save_shot(shot.model_copy(update={"h3_job_id": "job_video"}))
    turn = BackendTurn(project.id, "Keep the video", None, None)
    assert "accept_ref_frame" not in {
        tool["function"]["name"] for tool in turn.context()["tools"]}


def test_layout_acceptance_schema_uses_only_materialized_layout_ids(tmp_projects_dir):
    project, shot = project_and_shot()
    save_shot(shot.model_copy(update={"layout_refs": [
        LayoutReference(id="lref_ready", asset_id="lay_ready"),
        LayoutReference(id="lref_pending", job_id="job_pending"),
    ]}))
    turn = BackendTurn(project.id, "Keep it", None, None)
    schema = next(tool["function"]["parameters"] for tool in turn.context()["tools"]
                  if tool["function"]["name"] == "accept_ref_frame")
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors({"shot_id": shot.id, "layout_ref_id": "lref_ready"}))
    for invalid in ("job_video", "lref_pending", "lref_invented"):
        assert list(validator.iter_errors({"shot_id": shot.id, "layout_ref_id": invalid}))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,direction", [
    ("duration_s", "Add a three-second ending."),
    ("voice_matches", "This ending must have no audio references."),
])
async def test_review_reports_configuration_conflict_without_authoring(
    tmp_projects_dir, monkeypatch, field, direction,
):
    project, shot = project_and_shot()
    if field == "voice_matches":
        shot = shot.model_copy(update={"voice_refs": [ShotVoiceRef(asset_id="voice_tao", audio_index=1)]})
        save_shot(shot)
    monkeypatch.setattr("app.agents.director.material_review.observe_references_cached",
                        _empty_observations)

    class Provider:
        async def complete_with_images(self, *args, **kwargs):
            raise AssertionError("cached observations expected")

        async def complete(self, system, user, **kwargs):
            data = json.loads(user)
            assert data["intent"]["current_request"] == direction
            assert data["shot"]["audio_bindings"]["voice_refs"] == [
                ref.model_dump(mode="json") for ref in shot.voice_refs]
            return json.dumps({"rewrite_prompt": True, "reason": "Saved parameters conflict.",
                "blocking_question": None, "configuration_issues": [
                    {"field": field, "requirement": direction, "reason": "Apply the explicit request."}]})

    with pytest.raises(ValueError) as caught:
        await review_references(Provider(), project, shot, [], [], "signature", lambda: None,
                                revision_request=direction)
    assert getattr(caught.value, "code", None) == "SHOT_CONFIGURATION_CONFLICT"
    assert caught.value.issues[0]["field"] == field
    assert load_shot(project.id, shot.id) == shot


async def _empty_observations(*args, **kwargs):
    return []


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["harness", "legacy"])
async def test_real_authoring_tool_repairs_duration_and_voice_in_same_turn(
    tmp_projects_dir, monkeypatch, runtime,
):
    from app.config import settings
    from app.agents.director.chat import handle_chat
    from app.agents.director.context_io import save_agent_context
    from app.agents.director.service import DirectorService, _script_hash
    from app.core.prompt_errors import ShotConfigurationConflict

    project, shot = project_and_shot()
    shot = shot.model_copy(update={"voice_refs": [ShotVoiceRef(asset_id="voice_tao", audio_index=1)]})
    neighbor = shot.model_copy(update={"id": "sht_neighbor"}, deep=True)
    save_shot(shot)
    save_shot(neighbor)
    save_project(project.model_copy(update={"shot_ids": [shot.id, neighbor.id]}))
    save_agent_context(project.id, AgentContext(project_id=project.id,
        script_hash=_script_hash(project.script_text), shot_summaries=[{"id": shot.id}, {"id": neighbor.id}]))
    direction = "Make this ending three seconds and remove its voice references."

    class Service(DirectorService):
        attempts = 0

        async def write_prompts_after_layout(self, shot_id, **kwargs):
            self.attempts += 1
            current = load_shot(project.id, shot_id)
            if self.attempts == 1:
                raise ShotConfigurationConflict([
                    {"field": field, "requirement": direction, "reason": "Apply the explicit request."}
                    for field in ("duration_s", "voice_matches")])
            assert current.duration_s == 3
            assert current.voice_refs == []
            return current

    service = Service(plan_provider=object())
    tools = [
        {"name": "write_prompt", "arguments": {"shot_id": shot.id}},
        {"name": "revise_shot", "arguments": {"shot_id": shot.id, "duration_s": 3, "voice_matches": []}},
        {"name": "write_prompt", "arguments": {"shot_id": shot.id}},
    ]
    if runtime == "harness":
        turn = BackendTurn(project.id, direction, service, None)
        for index, tool in enumerate(tools):
            result = await turn.tool({**tool, "call_id": f"call-{index}"})
            assert result["ok"] is (index != 0), result
        assert turn.terminal_failure is None
    else:
        monkeypatch.setattr(settings, "director_agent_runtime", "legacy")
        calls = []

        async def chat_fn(system, user, **kwargs):
            index = len(calls)
            calls.append(kwargs)
            return {"content": "Done." if index == 3 else "", "thinking": "",
                    "tool_calls": [tools[index]] if index < 3 else []}

        result = await handle_chat(project_id=project.id, message=direction, svc=service, chat_fn=chat_fn)
        assert len(calls) == 4
        assert result.failure_message == ""
        assert result.reply == "Done."
        assert "SHOT_CONFIGURATION_CONFLICT" in json.dumps(calls[1]["messages"])
    saved = load_shot(project.id, shot.id)
    assert saved.duration_s == 3 and saved.voice_refs == []
    assert saved.script_beat == shot.script_beat and saved.video_context == shot.video_context
    assert saved.refs == shot.refs
    assert load_shot(project.id, neighbor.id) == neighbor
    assert service.attempts == 2


@pytest.mark.asyncio
async def test_nonverbal_audio_is_not_removed_without_an_explicit_conflict(tmp_projects_dir, monkeypatch):
    project, shot = project_and_shot()
    shot = shot.model_copy(update={"voice_refs": [ShotVoiceRef(asset_id="voice_tao", audio_index=1)]})
    save_shot(shot)
    monkeypatch.setattr("app.agents.director.material_review.observe_references_cached", _empty_observations)

    class Provider:
        async def complete_with_images(self, *args, **kwargs):
            raise AssertionError("cached observations expected")

        async def complete(self, *args, **kwargs):
            return json.dumps({"rewrite_prompt": True, "reason": "A vocal laugh needs the voice reference.",
                               "blocking_question": None})

    result = await review_references(Provider(), project, shot, [], [], "signature", lambda: None,
                                    revision_request="Tao laughs without saying words.")
    assert result["decision"]["configuration_issues"] == []
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_review_cannot_invent_user_authority_for_configuration_repair(tmp_projects_dir, monkeypatch):
    project, shot = project_and_shot()
    monkeypatch.setattr("app.agents.director.material_review.observe_references_cached", _empty_observations)

    class Provider:
        calls = 0

        async def complete_with_images(self, *args, **kwargs):
            raise AssertionError("cached observations expected")

        async def complete(self, *args, **kwargs):
            self.calls += 1
            return json.dumps({"rewrite_prompt": True, "reason": "Prefer three seconds.",
                "blocking_question": None, "configuration_issues": [
                    {"field": "duration_s", "requirement": "Make it three seconds.", "reason": "Better pacing."}]})

    provider = Provider()
    with pytest.raises(ValueError) as caught:
        await review_references(provider, project, shot, [], [], "signature", lambda: None,
                                revision_request="Write its prompt.")
    assert getattr(caught.value, "code", None) != "SHOT_CONFIGURATION_CONFLICT"
    assert provider.calls == 2
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_harness_can_repair_configuration_once_but_cannot_blindly_retry(
    tmp_projects_dir, monkeypatch,
):
    project, shot = project_and_shot()
    calls = []

    async def run_tools(**kwargs):
        calls.append(kwargs["tools"][0]["name"])
        kwargs["result_payloads"].append({
            "ok": False, "code": "SHOT_CONFIGURATION_CONFLICT", "shot_id": shot.id,
            "issues": [{"field": "duration_s", "requirement": "Three seconds.",
                        "reason": "Saved duration is thirteen seconds."}],
            "error": "Correct the saved duration using revise_shot.",
            "retryable": True, "concludes_turn": False,
        })
        return [], set()

    monkeypatch.setattr("app.agents.director.chat._run_tools", run_tools)
    turn = BackendTurn(project.id, "Add a three-second ending.", None, None)
    first = await turn.tool({"name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "first"})
    assert first["retryable"] is True
    assert turn.terminal_failure is None
    assert "revise_shot" in {tool["function"]["name"] for tool in turn.context()["tools"]}
    repeat = await turn.tool({"name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "blind"})
    assert repeat["ok"] is False
    assert "revise_shot" in repeat["error"]
    assert calls == ["write_prompt"]
    for index, selector in enumerate(({"shot_index": 1}, {"title": shot.title}, {})):
        alternative = await turn.tool({"name": "write_prompt", "arguments": selector,
                                       "call_id": f"alternative-{index}"})
        assert "revise_shot" in alternative["error"]
    assert calls == ["write_prompt"]
    # An unrelated store write must not open a retry.
    save_shot(shot.model_copy(update={"title": "Changed title"}))
    turn.expected_state = turn.snapshot()[2]
    await turn.tool({"name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "unrelated"})
    assert calls == ["write_prompt"]
    save_shot(shot.model_copy(update={"duration_s": 3}))
    turn.expected_state = turn.snapshot()[2]
    final = await turn.tool({"name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "second"})
    assert calls == ["write_prompt", "write_prompt"]
    assert final["retryable"] is False
    assert turn.terminal_failure
    assert turn.context()["tools"] == []


@pytest.mark.asyncio
async def test_material_editor_handoff_does_not_grant_authoring_permission(tmp_projects_dir):
    from app.agents.director.service import DirectorService
    from app.core.prompt_errors import ShotConfigurationConflict
    project, shot = project_and_shot()
    direction = (f'Shot 01 references changed for “Hold” ({shot.id}). '
                 'Review every current Picture reference and decide the next step. '
                 'Saved reference delta: {"added":[],"removed":[],"reordered":[]}. '
                 'User note: Make this ending three seconds.')

    class Service(DirectorService):
        async def write_prompts_after_layout(self, *args, **kwargs):
            raise ShotConfigurationConflict([{"field": "duration_s", "requirement": "three seconds",
                                              "reason": "Saved duration is thirteen seconds."}])

    turn = BackendTurn(project.id, direction, Service(plan_provider=object()), None)
    result = await turn.tool({"name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": "scoped"})
    assert result["retryable"] is False
    assert result["concludes_turn"] is True
    assert turn.terminal_failure
    assert load_shot(project.id, shot.id) == shot
