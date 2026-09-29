"""Authored dialogue must leave the chat boundary with durable attribution."""
import hashlib
import json

import pytest

from app.agents.director.service import DirectorService, _script_hash
from app.agents.director.tool_handlers.project import handle_project_tool
from app.core.projects.dialogue import DialogueLine, verify_dialogue_sources
from app.core.projects.models import Shot
from app.core.projects.store import create_project, load_project, load_shot, save_project, save_shot


@pytest.fixture
def board():
    project = create_project("Attribution", 'host: Old question.\nkira: Old answer.')
    shot = Shot(id="old", project_id=project.id, scene_id="studio", title="Old",
                script_beat="Old beat", duration_s=8)
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    return project, shot


async def call_tool(project, svc, name, args, message):
    return await handle_project_tool(name=name, args=args, project_id=project.id,
        project=project, svc=svc, actions=[], notes=[], result_payloads=[],
        user_feedback=message, user_message_id="message-new", requested_minimum_duration_s=0,
        refresh_shots=lambda: [], storyboard_snapshot=lambda shots: {"shots": [s.model_dump() for s in shots]},
        script_locked_message="locked")


def draft(project, dialogue, beat):
    return dict(expected_script_hash=_script_hash(project.script_text), expected_last_shot_id="old",
        shot=dict(scene_id="studio", title="New", script_beat=beat, duration_s=8,
                  shot_type="close-up", camera_angle="eye-level", camera_motion="static",
                  composition="A conversation", dialogue=dialogue))


class Grounder:
    def __init__(self, source, speakers, texts):
        self.source, self.speakers, self.texts = source, speakers, texts
        self.calls = 0

    async def complete(self, system, user, **kwargs):
        self.calls += 1
        request = json.loads(user)
        assert request["script"] == self.source
        return json.dumps({"dialogue_lines": [dict(line_id=f"line_{i}", speaker_id=speaker,
            speaker_name=speaker, text=text, language="zh", source=dict(kind="script",
            source_hash=hashlib.sha256(self.source.encode()).hexdigest(), scene_id="studio",
            quote=f"{speaker}: {text}", occurrence=0))
            for i, (speaker, text) in enumerate(zip(self.speakers, self.texts))]})


@pytest.mark.asyncio
async def test_append_resolves_new_user_dialogue_before_saving(board):
    project, old = board
    texts = ["谢谢啦，你一个小姑娘能照顾我什么？", "我会的可多啦！", "mia会的，我都会"]
    message = "host: " + texts[0] + "\nkira: " + texts[1] + "\nkira: " + texts[2]
    provider = Grounder(message, ["host", "kira", "kira"], texts)
    svc = DirectorService(plan_provider=provider, orchestrator=object())
    await call_tool(project, svc, "append_shot", draft(project, texts, message), message)
    saved_project = load_project(project.id)
    saved = load_shot(project.id, saved_project.shot_ids[-1])
    assert saved.dialogue_lines is not None
    assert [l.speaker_id for l in saved.dialogue_lines] == ["host", "kira", "kira"]
    assert saved.dialogue == texts
    verify_dialogue_sources(saved_project, saved, saved.dialogue_lines)
    evidence = saved.meta["dialogue_authoring"]
    assert evidence["user_message_id"] == "message-new"
    assert evidence["user_message"] == message
    assert evidence["source_kind"] == "user_message"
    assert saved_project.script_text == project.script_text
    assert load_shot(project.id, old.id) == old


@pytest.mark.asyncio
async def test_authorized_creative_dialogue_can_use_the_new_draft(board):
    project, _ = board
    source = "Visitor: An unexpected ending."
    provider = Grounder(source, ["Visitor"], ["An unexpected ending."])
    svc = DirectorService(plan_provider=provider, orchestrator=object())
    await call_tool(project, svc, "append_shot", draft(project, provider.texts, source), "Invent a final exchange.")
    saved = load_shot(project.id, load_project(project.id).shot_ids[-1])
    assert saved.dialogue_lines[0].text == "An unexpected ending."
    assert saved.meta["dialogue_authoring"]["source_kind"] == "authored_beat"


@pytest.mark.asyncio
async def test_revise_can_resolve_legacy_attribution_without_changing_words(board):
    project, old = board
    old.dialogue = ["New answer."]
    save_shot(old)
    source = "kira: New answer."
    svc = DirectorService(plan_provider=Grounder(source, ["kira"], old.dialogue), orchestrator=object())
    await call_tool(project, svc, "revise_shot", {"shot_id": old.id, "dialogue": old.dialogue}, source)
    saved = load_shot(project.id, old.id)
    assert saved.dialogue_lines[0].speaker_id == "kira"
    assert saved.dialogue == old.dialogue
    verify_dialogue_sources(project, saved, saved.dialogue_lines)


@pytest.mark.asyncio
async def test_unresolved_append_does_not_save_partial_shot(board):
    project, _ = board
    class Ambiguous:
        async def complete(self, *args, **kwargs):
            return json.dumps({"issues": [{"code": "dialogue_source_unresolved", "action": "Identify the speaker."}]})
    svc = DirectorService(plan_provider=Ambiguous(), orchestrator=object())
    with pytest.raises(ValueError, match="dialogue_source_unresolved"):
        await call_tool(project, svc, "append_shot", draft(project, ["Yes."], "Someone says Yes."), "Add a reply.")
    assert load_project(project.id).shot_ids == ["old"]


@pytest.mark.parametrize("name,valid", [("KIRA", True), ("Other", False)])
def test_source_identity_case_does_not_change_speaker(board, name, valid):
    project, shot = board
    shot.dialogue = ["Old answer."]
    line = DialogueLine(line_id="l1", speaker_id="char_kira", speaker_name=name, text="Old answer.",
        source=dict(kind="script", source_hash=hashlib.sha256(project.script_text.encode()).hexdigest(),
                    scene_id="studio", quote="kira: Old answer.", occurrence=0))
    if valid:
        verify_dialogue_sources(project, shot, [line])
    else:
        with pytest.raises(ValueError, match="dialogue_source_speaker_mismatch"):
            verify_dialogue_sources(project, shot, [line])


@pytest.mark.asyncio
async def test_attribution_does_not_overwrite_concurrent_revision(board):
    project, old = board
    source = "kira: New answer."
    class Concurrent(Grounder):
        async def complete(self, *args, **kwargs):
            changed = load_shot(project.id, old.id)
            changed.title = "User changed this meanwhile"
            save_shot(changed)
            return await super().complete(*args, **kwargs)
    svc = DirectorService(plan_provider=Concurrent(source, ["kira"], ["New answer."]), orchestrator=object())
    with pytest.raises(ValueError, match="changed during dialogue attribution"):
        await call_tool(project, svc, "revise_shot", {"shot_id": old.id, "dialogue": ["New answer."]}, source)
    saved = load_shot(project.id, old.id)
    assert saved.title == "User changed this meanwhile"
    assert saved.dialogue == []


@pytest.mark.asyncio
async def test_existing_speaker_ids_are_supplied_to_grounding(board):
    from app.core.projects.dialogue import apply_dialogue_update
    project, old = board
    old = apply_dialogue_update(old, {"dialogue_lines": [dict(line_id="old_line", speaker_id="char_kira",
        speaker_name="kira", text="Old answer.", source=dict(kind="shot_revision", source_hash="pending",
        scene_id="studio", quote="kira: Old answer."))]})
    save_shot(old)
    source = "kira: New answer."
    class ReusesIdentity(Grounder):
        async def complete(self, system, user, **kwargs):
            request = json.loads(user)
            assert request["known_speakers"] == [{"speaker_id": "char_kira", "speaker_name": "kira"}]
            result = json.loads(await super().complete(system, user, **kwargs))
            result["dialogue_lines"][0]["speaker_id"] = request["known_speakers"][0]["speaker_id"]
            return json.dumps(result)
    svc = DirectorService(plan_provider=ReusesIdentity(source, ["kira"], ["New answer."]), orchestrator=object())
    await call_tool(project, svc, "append_shot", draft(project, ["New answer."], source), source)
    saved = load_shot(project.id, load_project(project.id).shot_ids[-1])
    assert saved.dialogue_lines[0].speaker_id == "char_kira"


def test_later_raw_edit_drops_old_authoring_evidence(board):
    from app.core.projects.dialogue import apply_dialogue_update
    _, shot = board
    shot.meta["dialogue_authoring"] = {"user_message_id": "old-message"}
    changed = apply_dialogue_update(shot, {"dialogue": ["Different words."]})
    assert "dialogue_authoring" not in changed.meta


@pytest.mark.asyncio
async def test_append_with_structured_lines_preserves_server_provenance(board):
    project, _ = board
    request = draft(project, ["Hello."], "Visitor: Hello.")
    request["shot"]["dialogue_lines"] = [dict(line_id="l1", speaker_id="visitor", speaker_name="Visitor",
        text="Hello.", source=dict(kind="shot_revision", source_hash="pending", scene_id="studio",
                                  quote="Visitor: Hello."))]
    svc = DirectorService(plan_provider=None, orchestrator=object())
    await call_tool(project, svc, "append_shot", request, "Have the visitor greet us.")
    saved = load_shot(project.id, load_project(project.id).shot_ids[-1])
    assert saved.meta["dialogue_authoring"]["user_message_id"] == "message-new"
    assert saved.meta["dialogue_authoring"]["revision_hash"] == saved.dialogue_lines[0].source.source_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"dialogue_lines": None}, {"scene_id": "new-scene"}])
async def test_identical_dialogue_round_trip_keeps_valid_attribution(board, change):
    from app.core.projects.dialogue import apply_dialogue_update
    project, old = board
    old = apply_dialogue_update(old, {"dialogue_lines": [dict(line_id="l1", speaker_id="visitor",
        speaker_name="Visitor", text="Hello.", source=dict(kind="shot_revision", source_hash="pending",
        scene_id="studio", quote="Visitor: Hello."))]})
    save_shot(old)
    svc = DirectorService(plan_provider=None, orchestrator=object())
    await call_tool(project, svc, "revise_shot", {"shot_id": old.id, "dialogue": old.dialogue, **change}, "Keep the words.")
    saved = load_shot(project.id, old.id)
    assert saved.dialogue_lines is not None
    assert saved.dialogue_lines[0].speaker_id == "visitor"
    verify_dialogue_sources(project, saved, saved.dialogue_lines)
    if "scene_id" in change:
        assert saved.meta["dialogue_authoring"]["user_message_id"] == "message-new"


def test_mentioning_a_name_in_spoken_words_is_not_a_speaker_cue(board):
    project, shot = board
    project.script_text = "Mia: Hello Kira."
    shot.dialogue = ["Hello Kira."]
    line = DialogueLine(line_id="l1", speaker_id="char_kira", speaker_name="KIRA", text="Hello Kira.",
        source=dict(kind="script", source_hash=hashlib.sha256(project.script_text.encode()).hexdigest(),
                    scene_id="studio", quote=project.script_text, occurrence=0))
    with pytest.raises(ValueError, match="dialogue_source_speaker_mismatch"):
        verify_dialogue_sources(project, shot, [line])


def test_postposed_speaker_cue_is_supported(board):
    project, shot = board
    project.script_text = '"Hello.", says kira.'
    shot.dialogue = ["Hello."]
    line = DialogueLine(line_id="l1", speaker_id="char_kira", speaker_name="KIRA", text="Hello.",
        source=dict(kind="script", source_hash=hashlib.sha256(project.script_text.encode()).hexdigest(),
                    scene_id="studio", quote=project.script_text, occurrence=0))
    verify_dialogue_sources(project, shot, [line])


@pytest.mark.asyncio
async def test_script_change_during_attribution_rejects_old_evidence(board):
    project, old = board
    class ChangesScript(Grounder):
        async def complete(self, *args, **kwargs):
            changed = load_project(project.id)
            changed.script_text = "A different script."
            save_project(changed)
            return await super().complete(*args, **kwargs)
    svc = DirectorService(plan_provider=ChangesScript(project.script_text, ["kira"], ["Old answer."]),
                          orchestrator=object())
    with pytest.raises(ValueError, match="script changed during dialogue attribution"):
        await call_tool(project, svc, "revise_shot", {"shot_id": old.id, "dialogue": ["Old answer."]}, "Use the script.")
    assert load_shot(project.id, old.id) == old


@pytest.mark.asyncio
async def test_final_save_rechecks_revision_under_store_lock(board, monkeypatch):
    from app.core.projects import dialogue as dialogue_module
    project, old = board
    apply = dialogue_module.apply_dialogue_update
    def concurrent_edit(shot, updates):
        result = apply(shot, updates)
        latest = load_shot(project.id, old.id)
        latest.title = "Changed after the first hash check"
        save_shot(latest)
        return result
    monkeypatch.setattr(dialogue_module, "apply_dialogue_update", concurrent_edit)
    source = "kira: New answer."
    svc = DirectorService(plan_provider=Grounder(source, ["kira"], ["New answer."]), orchestrator=object())
    with pytest.raises(ValueError, match="changed during dialogue attribution"):
        await call_tool(project, svc, "revise_shot", {"shot_id": old.id, "dialogue": ["New answer."]}, source)
    saved = load_shot(project.id, old.id)
    assert saved.title == "Changed after the first hash check"
    assert saved.dialogue == []
