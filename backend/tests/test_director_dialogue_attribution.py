"""Attribution is data, not a character-name keyword policy."""
import pytest

from app.core.projects.models import Shot


def line_payload(line_id="l1", speaker_id="char_1", text="Hello."):
    return dict(line_id=line_id, speaker_id=speaker_id, speaker_name="Visitor",
                text=text, language="English", source=dict(kind="script",
                source_hash="a" * 64, scene_id="sc1", quote="Visitor: Hello.", occurrence=0))


def shot_payload(**changes):
    return dict(id="s1", project_id="p1", scene_id="sc1", title="Greeting",
                script_beat="A greeting.", duration_s=6, **changes)


def test_structured_lines_survive_shot_round_trip():
    shot = Shot(**shot_payload(dialogue=["Hello.", "Hello."],
                dialogue_lines=[line_payload(), line_payload("l2", "char_2")]))
    assert shot.model_dump()["dialogue_lines"][1]["speaker_id"] == "char_2"
    assert Shot.model_validate_json(shot.model_dump_json()) == shot


def test_disagreeing_projection_is_rejected():
    with pytest.raises(ValueError, match="dialogue_projection_mismatch"):
        Shot(**shot_payload(dialogue=["Goodbye."], dialogue_lines=[line_payload()]))


def test_duplicate_line_ids_are_rejected_but_repeated_words_are_not():
    with pytest.raises(ValueError, match="duplicate_line_id"):
        Shot(**shot_payload(dialogue=["Hello.", "Hello."],
             dialogue_lines=[line_payload(), line_payload()]))


def test_structured_text_preserves_punctuation_and_unicode():
    text = "等等… 不，不。"
    shot = Shot(**shot_payload(dialogue=[text], dialogue_lines=[line_payload(text=text)]))
    assert shot.model_dump()["dialogue_lines"][0]["text"] == text


def test_legacy_labelled_projection_remains_readable():
    shot = Shot(**shot_payload(dialogue=['VISITOR: "Hello."'], dialogue_lines=[line_payload()]))
    assert shot.dialogue == ['VISITOR: "Hello."']
    assert shot.model_dump()["dialogue_lines"][0]["text"] == "Hello."


def test_literal_colon_is_not_misread_as_a_speaker_label():
    text = "Attention: the door is closing."
    shot = Shot(**shot_payload(dialogue=[text], dialogue_lines=[line_payload(text=text)]))
    assert shot.dialogue_lines[0].text == text


def test_scene_only_edit_restamps_authored_attribution():
    from app.core.projects.dialogue import apply_dialogue_update, verify_dialogue_sources
    from app.core.projects.models import Project
    shot = apply_dialogue_update(Shot(**shot_payload(dialogue=["Hello."])),
                                 {"dialogue_lines": [line_payload()]})
    changed = apply_dialogue_update(shot, {"scene_id": "new-scene"})
    project = Project(id="p1", name="Test", script_text="", created_at="", updated_at="")
    verify_dialogue_sources(project, changed, changed.dialogue_lines)


@pytest.mark.parametrize("quotes,occurrences", [
    (["Pat: Yes.", "Pat: Yes."], [1, 0]),
    (["Pat: Yes.", "Pat: Yes.\n"], [0, 0]),
])
def test_grounding_rejects_reversed_or_reused_actual_source_occurrences(quotes, occurrences):
    import hashlib
    from app.core.projects.dialogue import DialogueLine, verify_dialogue_sources
    from app.core.projects.models import Project
    script = "Pat: Yes.\nPat: Yes."
    project = Project(id="p1", name="Test", script_text=script, created_at="", updated_at="")
    shot = Shot(**shot_payload(dialogue=["Yes.", "Yes."]))
    lines = [DialogueLine(line_id=f"l{i}", speaker_id=f"pat{i}", speaker_name="Pat", text="Yes.",
        source=dict(kind="script", scene_id="sc1", source_hash=hashlib.sha256(script.encode()).hexdigest(),
                    quote=q, occurrence=o)) for i, (q, o) in enumerate(zip(quotes, occurrences))]
    with pytest.raises(ValueError, match="dialogue_source"):
        verify_dialogue_sources(project, shot, lines)


def test_grounding_accepts_ordered_same_name_same_words_with_different_quotes():
    import hashlib
    from app.core.projects.dialogue import DialogueLine, verify_dialogue_sources
    from app.core.projects.models import Project
    script = "Scene one. Pat: Yes.\nScene two. Pat: Yes."
    project = Project(id="p1", name="Test", script_text=script, created_at="", updated_at="")
    shot = Shot(**shot_payload(dialogue=["Yes.", "Yes."]))
    lines = [DialogueLine(line_id=f"l{i}", speaker_id=f"pat{i}", speaker_name="Pat", text="Yes.",
        source=dict(kind="script", scene_id="sc1", source_hash=hashlib.sha256(script.encode()).hexdigest(),
                    quote=q, occurrence=0)) for i, q in enumerate(["Scene one. Pat: Yes.", "Scene two. Pat: Yes."])]
    verify_dialogue_sources(project, shot, lines)


def test_legacy_load_and_resolved_silence_are_distinct():
    assert Shot(**shot_payload()).model_dump()["dialogue_lines"] is None
    assert Shot(**shot_payload(dialogue_lines=[])).model_dump()["dialogue_lines"] == []


def test_legacy_edit_invalidates_attribution_but_identical_save_does_not():
    from app.core.projects.dialogue import apply_dialogue_update
    original = Shot(**shot_payload(dialogue=["Hello."], dialogue_lines=[line_payload()],
                   meta={"prompt_dialogue_signature": "old", "other": 42}))
    assert apply_dialogue_update(original, {"dialogue": ["Hello."]}) == original
    changed = apply_dialogue_update(original, {"dialogue": ["Goodbye."]})
    assert changed.dialogue_lines is None
    assert not changed.meta.get("prompt_dialogue_signature")
    assert changed.meta["other"] == 42


def test_structured_revision_changes_projection_and_has_server_source():
    from app.core.projects.dialogue import apply_dialogue_update, verify_dialogue_sources
    from app.core.projects.models import Project
    original = Shot(**shot_payload(dialogue=["Hello."]))
    changed = apply_dialogue_update(original, {"dialogue_lines": [line_payload(text="Goodbye.")]})
    assert changed.dialogue == ["Goodbye."]
    assert changed.dialogue_lines[0].source.kind == "shot_revision"
    project = Project(id="p1", name="Test", script_text="Visitor: Hello.", created_at="", updated_at="")
    verify_dialogue_sources(project, changed, changed.dialogue_lines)
    forged = changed.model_copy(update={"dialogue_lines": [changed.dialogue_lines[0].model_copy(
        update={"speaker_id": "intruder"})]})
    with pytest.raises(ValueError, match="dialogue_source_stale"):
        verify_dialogue_sources(project, forged, forged.dialogue_lines)


def test_disagreeing_dual_patch_performs_no_mutation():
    from app.core.projects.dialogue import apply_dialogue_update
    shot = Shot(**shot_payload(dialogue=["Hello."]))
    with pytest.raises(ValueError, match="dialogue_projection_mismatch"):
        apply_dialogue_update(shot, {"dialogue": ["Other."], "dialogue_lines": [line_payload()]})
    assert shot.dialogue == ["Hello."] and shot.dialogue_lines is None


@pytest.mark.asyncio
async def test_grounding_verifies_actual_script_and_does_not_write(tmp_path, monkeypatch):
    import hashlib
    import json
    from app.config import settings
    from app.agents.director.dialogue_grounding import ground_dialogue
    from app.core.projects.models import Project
    monkeypatch.setattr(settings, "projects_dir", tmp_path)
    script = "Visitor: Hello."
    project = Project(id="p1", name="Test", script_text=script, created_at="", updated_at="")
    shot = Shot(**shot_payload(dialogue=["Hello."]))
    payload = line_payload()
    payload["source"]["source_hash"] = hashlib.sha256(script.encode()).hexdigest()
    class Provider:
        async def complete(self, system, user, **kwargs):
            assert script in user
            return json.dumps({"dialogue_lines": [payload]})
    lines = await ground_dialogue(project, shot, Provider())
    assert lines[0].speaker_id == "char_1"
    assert not list((tmp_path / "p1").rglob("*.json"))
    payload["source"]["quote"] = "Another person: Goodbye."
    with pytest.raises(ValueError, match="dialogue_source"):
        await ground_dialogue(project, shot, Provider())


@pytest.fixture
def authored_shot(tmp_path, monkeypatch):
    from app.config import settings
    from app.core.projects.store import create_project, save_project, save_shot
    from app.core.projects.dialogue import apply_dialogue_update
    project = create_project("Greeting", "Visitor: Hello.")
    shot = Shot(**{**shot_payload(dialogue=["Hello."]), "project_id": project.id})
    shot = apply_dialogue_update(shot, {"dialogue_lines": [line_payload()]})
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    return project, shot


def writer_sections():
    return dict(subject_definitions="A visitor by the door.", summary="A greeting.",
        retention_analysis="Preserve the room.", detailed_description="0–6 seconds: <d>[English] Hello.</d>",
        overall_soundscape="Room tone.", non_diegetic_music="N/A")


def certify_test_shot(project, shot, name="Speaker"):
    """Seed an already-attributed prompt for cold-path transport tests."""
    from app.core.projects.dialogue import apply_dialogue_update
    from app.core.h3.dialogue_binding import DialoguePromptDraft, annotate_speakers
    from app.agents.director.dialogue_preflight import prompt_dialogue_record
    shot = apply_dialogue_update(shot, {"dialogue_lines": [
        {**line_payload(text=text, line_id=f"l{i + 1}"), "speaker_name": name}
        for i, text in enumerate(shot.dialogue)]})
    draft = DialoguePromptDraft(prompt_sections=shot.prompt_sections, dialogue_uses=[
        {"line_ids": [f"l{i + 1}"], "speaker_id": "char_1", "block_indexes": [i]}
        for i in range(len(shot.dialogue))])
    shot = shot.model_copy(update={"prompt_sections": annotate_speakers(draft, shot.dialogue_lines)})
    shot = shot.model_copy(update={"meta": {**shot.meta,
        "prompt_dialogue_contract": prompt_dialogue_record(project, shot, shot.dialogue_lines, draft)}})
    from test_reference_facts import certify_reference_test_shot
    return certify_reference_test_shot(project, shot)


class Orchestrator:
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def llm_session(self, **kwargs):
        yield self
    async def ensure_llm_ready(self):
        pass


@pytest.mark.asyncio
async def test_writer_repairs_wrong_speaker_and_keeps_directing_request(authored_shot):
    import json
    from app.agents.director.service import DirectorService
    from app.agents.director.brief import remember_directing_request
    from app.core.projects.store import load_shot
    project, shot = authored_shot
    requirement = "Except Tao and Mia, every speaking character uses British English. Preserve their established accents."
    remember_directing_request(project.id, requirement)
    class Provider:
        requests = []
        async def complete(self, system, user, **kwargs):
            self.requests.append(user)
            return json.dumps({"prompt_sections": writer_sections(), "dialogue_uses": [
                {"line_ids": ["l1"], "speaker_id": "wrong" if len(self.requests) == 1 else "char_1", "block_indexes": [0]}]})
    provider = Provider()
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "char_1" in updated.prompt_sections.detailed_description
    assert len(provider.requests) == 2
    assert "dialogue_speaker_mismatch" in provider.requests[1]
    assert requirement in provider.requests[0]
    assert load_shot(project.id, shot.id).meta["prompt_dialogue_contract"]["lines"][0]["speaker_id"] == "char_1"


@pytest.mark.asyncio
async def test_failed_attribution_repair_does_not_replace_saved_prompt(authored_shot):
    import json
    from app.agents.director.service import DirectorService
    from app.core.projects.store import load_shot
    project, shot = authored_shot
    class Provider:
        calls = 0
        async def complete(self, *args, **kwargs):
            self.calls += 1
            return json.dumps({"prompt_sections": writer_sections(), "dialogue_uses": [
                {"line_ids": ["l1"], "speaker_id": "wrong", "block_indexes": [0]}]})
    provider = Provider()
    with pytest.raises(ValueError, match="dialogue_speaker_mismatch"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert provider.calls == 2
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
