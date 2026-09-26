"""Recover legacy source evidence through the actual scoped retry entry point."""
import hashlib
import json

import pytest

from app.agents.director.dialogue_grounding import ground_dialogue
from app.agents.director.prompt_retry import authored_payload, record_prompt_failure, run_prompt_retry
from app.agents.director.service import DirectorService
from app.core.projects.chat_history import append_chat_message
from app.core.projects.dialogue import verify_dialogue_sources
from app.core.projects.models import PromptSections
from app.core.projects.store import load_shot, save_shot
from test_director_material_review import material_shot, Orchestrator, Provider, sections


WORDS = ["谢谢啦，你一个小姑娘能照顾我什么？", "我会的可多啦！", "mia会的，我都会"]
MESSAGE = "男主说：" + WORDS[0] + "kira急忙说，" + WORDS[1] + "之后想了一下，低头轻声害羞说，" + WORDS[2]


class SourceReview:
    async def complete_bounded(self, system, user, **kwargs):
        assert "Review recovered dialogue attribution" in system
        candidates = json.loads(user)["lines"]
        assert [line["speaker_id"] for line in candidates] == ["male_lead", "char_kira", "char_kira"]
        return json.dumps({"valid": True, "issues": []})


def response(source, scene):
    return {"dialogue_lines": [dict(line_id=f"l{i}", speaker_id=speaker_id, speaker_name=name,
        text=text, language="zh", source=dict(kind="script", scene_id=scene,
            source_hash=hashlib.sha256(source.encode()).hexdigest(), quote=quote, occurrence=0))
        for i, (speaker_id, name, text, quote) in enumerate([
            ("male_lead", "男主", WORDS[0], "男主说：" + WORDS[0]),
            ("char_kira", "kira", WORDS[1], "kira急忙说，" + WORDS[1]),
            ("char_kira", "kira", WORDS[2], MESSAGE[MESSAGE.index("kira"):]),
        ])]}


def legacy_shot(material_shot):
    project, shot, neighbor, _ = material_shot
    shot = shot.model_copy(update={"dialogue": WORDS, "dialogue_lines": None,
        "script_beat": MESSAGE, "prompt_sections": PromptSections(), "meta": {}})
    save_shot(shot)
    return project, shot, neighbor


@pytest.mark.asyncio
async def test_scoped_retry_recovers_legacy_chat_source_and_preserves_authored_state(material_shot):
    project, shot, neighbor = legacy_shot(material_shot)
    message = append_chat_message(project.id, role="user", content=MESSAGE)
    receipt = record_prompt_failure(shot, "", ValueError("dialogue_source_unresolved"))
    orch = Orchestrator()
    class RecoveryProvider(SourceReview, Provider):
        async def complete(self, system, user, **kwargs):
            if "Extract the current shot's dialogue attribution" in system:
                request = json.loads(user)
                assert request["script"] == MESSAGE
                return json.dumps(response(request["script"], shot.scene_id))
            if "reference review decision" in system.lower():
                return await super().complete(system, user, **kwargs)
            payload = sections()
            payload["detailed_description"] = "0-6 seconds: {{speech:l0}} {{speech:l1}} {{speech:l2}}"
            return json.dumps({"prompt_sections": payload})
    saved = await run_prompt_retry(project.id, receipt,
        DirectorService(plan_provider=RecoveryProvider(orch), orchestrator=orch))
    assert authored_payload(saved) == authored_payload(shot)
    assert load_shot(project.id, neighbor.id) == neighbor
    lines = saved.meta["prompt_dialogue_contract"]["lines"]
    assert [l["speaker_id"] for l in lines] == ["male_lead", "char_kira", "char_kira"]
    assert all(l["source"]["kind"] == "user_message" for l in lines)
    assert all(l["source"]["source_id"] == message.id for l in lines)
    assert all(saved.prompt_sections.detailed_description.count(text) == 1 for text in WORDS)
    replay = await run_prompt_retry(project.id, receipt, None)
    assert replay == saved


@pytest.mark.asyncio
async def test_recovered_source_is_invalid_after_shot_beat_changes(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    class Grounder(SourceReview):
        async def complete(self, system, user, **kwargs):
            request = json.loads(user)
            return json.dumps(response(request["script"], shot.scene_id))
    lines = await ground_dialogue(project, shot, Grounder())
    verify_dialogue_sources(project, shot, lines)
    changed = shot.model_copy(update={"script_beat": "Different speaker direction"})
    with pytest.raises(ValueError, match="dialogue_source_stale"):
        verify_dialogue_sources(project, changed, lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["assistant_only", "conflicting_users"])
async def test_legacy_recovery_does_not_guess_untrusted_or_ambiguous_history(material_shot, case):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="assistant" if case == "assistant_only" else "user", content=MESSAGE)
    if case == "conflicting_users":
        append_chat_message(project.id, role="user", content=MESSAGE.replace("kira急忙说，", "kira急忙说："))
    class NoGuess:
        async def complete(self, *args, **kwargs):
            raise AssertionError("No source means no model inference or blind retry")
    with pytest.raises(ValueError, match="dialogue_source_unresolved"):
        await ground_dialogue(project, shot, NoGuess())
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_chat_evidence_cannot_be_rebound_to_another_message(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    unrelated = append_chat_message(project.id, role="user", content="Different words.")
    class Grounder(SourceReview):
        async def complete(self, system, user, **kwargs):
            return json.dumps(response(json.loads(user)["script"], shot.scene_id))
    lines = await ground_dialogue(project, shot, Grounder())
    forged = [line.model_copy(update={"source": line.source.model_copy(update={"source_id": unrelated.id})}) for line in lines]
    with pytest.raises(ValueError, match="dialogue_source_stale"):
        verify_dialogue_sources(project, shot, forged)


@pytest.mark.asyncio
async def test_script_source_matching_keeps_whitespace_compatibility(material_shot):
    project, shot, _, _ = material_shot
    project = project.model_copy(update={"script_text": "Visitor: Hello   there."})
    shot = shot.model_copy(update={"dialogue": ["Hello there."], "dialogue_lines": None})
    class Grounder(SourceReview):
        async def complete(self, system, user, **kwargs):
            request = json.loads(user)
            return json.dumps({"dialogue_lines": [dict(line_id="line", speaker_id="visitor", speaker_name="Visitor",
                text="Hello there.", source=dict(kind="script", source_hash=request["script_hash"],
                    scene_id=shot.scene_id, quote="Visitor: Hello   there.", occurrence=0))]})
    lines = await ground_dialogue(project, shot, Grounder())
    assert lines[0].source.kind == "script"


@pytest.mark.asyncio
async def test_unique_source_occurrence_is_derived_not_guessed_by_model(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    class Grounder(SourceReview):
        async def complete(self, system, user, **kwargs):
            result = response(MESSAGE, shot.scene_id)
            for index, line in enumerate(result["dialogue_lines"]):
                line["source"]["occurrence"] = index
            return json.dumps(result)
    lines = await ground_dialogue(project, shot, Grounder())
    assert [line.source.occurrence for line in lines] == [0, 0, 0]


@pytest.mark.asyncio
async def test_unrelated_historical_words_are_not_a_shot_source(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    shot = shot.model_copy(update={"dialogue": ["Yes."], "script_beat": "Kira answers the host: Yes."})
    append_chat_message(project.id, role="user", content="Mia: Yes.")
    class NoGuess:
        async def complete(self, *args, **kwargs):
            raise AssertionError("Unlinked history must not reach inference")
    with pytest.raises(ValueError, match="dialogue_source_unresolved"):
        await ground_dialogue(project, shot, NoGuess())


@pytest.mark.asyncio
async def test_linked_chat_quote_keeps_context_for_implicit_speaker(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    shot = shot.model_copy(update={"script_beat": MESSAGE.replace("kira急忙说，", 'kira急忙说："') + '"'})
    append_chat_message(project.id, role="user", content=MESSAGE)
    class Grounder(SourceReview):
        async def complete(self, system, user, **kwargs):
            result = response(MESSAGE, shot.scene_id)
            result["dialogue_lines"][2]["source"]["quote"] = MESSAGE[MESSAGE.index("之后"):]
            return json.dumps(result)
    lines = await ground_dialogue(project, shot, Grounder())
    assert lines[2].speaker_id == "char_kira"
    assert lines[2].source.quote == MESSAGE
    verify_dialogue_sources(project, shot, lines)


@pytest.mark.asyncio
async def test_contextual_evidence_must_pass_semantic_attribution_review(material_shot):
    project, shot, _ = legacy_shot(material_shot)
    append_chat_message(project.id, role="user", content=MESSAGE)
    class WrongSpeaker:
        async def complete(self, system, user, **kwargs):
            result = response(MESSAGE, shot.scene_id)
            result["dialogue_lines"][2].update(speaker_id="male_lead", speaker_name="男主")
            return json.dumps(result)
        async def complete_bounded(self, system, user, **kwargs):
            candidates = json.loads(user)["lines"]
            assert candidates[2]["speaker_id"] == "male_lead"
            return json.dumps({"valid": False, "issues": [{"code": "dialogue_speaker_unresolved",
                "line_id": "l2", "evidence": "The source continues kira's reply, not the host's.",
                "action": "Restore the source speaker char_kira."}]})
    with pytest.raises(ValueError, match="dialogue_speaker_unresolved"):
        await ground_dialogue(project, shot, WrongSpeaker())
