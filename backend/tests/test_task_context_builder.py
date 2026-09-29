import hashlib
import json

import pytest
from PIL import Image

from task_context_fixtures import context_case
from test_director_material_review import material_shot, tail_handoff_shot
from app.config import settings
from app.core.projects.models import Shot
from app.core.projects.store import save_project, save_shot, load_shot
from app.agents.director.task_context_models import TaskRequest
from app.agents.director.task_context_snapshot import capture_task_snapshot, assert_packet_current, ContextChanged
from app.agents.director.task_context_builder import build_task_packet


def packet_for(project, shot, **kwargs):
    return build_task_packet(capture_task_snapshot(project.id),
        TaskRequest(kind="shot_prompt", target_shot_id=shot.id, objective="Keep dialogue; change framing."),
        authority={"allowed_mutations": ["write_prompt"]}, max_chars=kwargs.pop("max_chars", 100000), **kwargs)


def test_prompt_packet_preserves_full_target_and_current_request(context_case):
    project, target, _ = context_case
    target = target.model_copy(update={"script_beat": "beat-" * 400})
    save_shot(target)
    packet = packet_for(project, target)
    assert packet.facts["target"]["script_beat"] == target.script_beat
    assert packet.task.objective == "Keep dialogue; change framing."
    assert packet.complete
    assert packet.facts["dialogue_sources"] == []


def test_required_script_tail_is_not_silently_truncated(context_case):
    project, target, _ = context_case
    project = project.model_copy(update={"script_text": "x" * 6000 + "FINAL_REQUIREMENT"})
    save_project(project)
    packet = packet_for(project, target, max_chars=1000)
    assert not packet.complete
    assert any(item["code"] == "CONTEXT_BUDGET_EXCEEDED" for item in packet.missing)
    assert "x" * 6000 not in packet.model_dump_json()
    full = packet_for(project, target)
    assert "FINAL_REQUIREMENT" in full.model_dump_json()


def test_many_irrelevant_shots_do_not_expand_target_details(context_case):
    project, target, neighbor = context_case
    ids = []
    for n in range(500):
        other = Shot(id=f"sht_extra_{n}", project_id=project.id, scene_id="remote", title=target.title,
                     script_beat="IRRELEVANT_DETAIL" * 30, duration_s=6)
        save_shot(other)
        ids.append(other.id)
    save_project(project.model_copy(update={"shot_ids": [*ids, neighbor.id, target.id]}))
    packet = packet_for(project, target)
    assert packet.facts["target"]["id"] == target.id
    assert "IRRELEVANT_DETAIL" not in packet.model_dump_json()
    assert len(packet.model_dump_json()) < 15000
    assert any(c["source_key"] == "catalog:shots" and c["count"] == 502 for c in packet.available_context)


def test_derived_review_writes_do_not_invalidate_authored_dependencies(context_case):
    project, target, _ = context_case
    packet = packet_for(project, target)
    save_shot(target.model_copy(update={"meta": {"material_review_pending": True}}))
    assert_packet_current(packet)
    updated = load_shot(project.id, target.id)
    save_shot(updated.model_copy(update={"camera_motion": "Crane upward"}))
    with pytest.raises(ContextChanged):
        assert_packet_current(packet)


def test_same_asset_id_new_bytes_invalidates_snapshot(material_shot):
    project, shot, _, files = material_shot
    packet = packet_for(project, shot)
    assert packet.complete
    ref = packet.facts["references"][0]
    assert ref["picture_index"] == 1
    assert ref["content_sha256"]
    Image.new("RGB", (400, 400), "red").save(files[0])
    with pytest.raises(ContextChanged):
        assert_packet_current(packet)


def test_tail_source_identity_is_not_replaced_by_latest_job(tail_handoff_shot):
    project, shot = tail_handoff_shot
    packet = packet_for(project, shot)
    text = json.dumps(packet.facts["continuity"])
    assert "job_previous" in text
    assert "sht_previous" in text
    assert "source_generation" in text


def test_snapshot_and_builder_never_write_business_files(context_case):
    project, target, _ = context_case
    def hashes():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in settings.projects_dir.rglob("*") if p.is_file()}
    before = hashes()
    packet_for(project, target)
    assert hashes() == before


def test_unstable_reads_fail_instead_of_returning_mixed_snapshot(context_case, monkeypatch):
    from app.agents.director import task_context_snapshot as module
    project, target, _ = context_case
    original = module.load_project
    calls = 0
    def moving(project_id):
        nonlocal calls
        result = original(project_id)
        calls += 1
        return result.model_copy(update={"script_text": str(calls)})
    monkeypatch.setattr(module, "load_project", moving)
    with pytest.raises(ContextChanged):
        capture_task_snapshot(project.id)
    assert calls <= 6


def test_global_requirements_survive_old_summary_and_script_revision(context_case):
    from app.agents.director.brief import remember_directing_request
    from app.agents.director.context_io import load_agent_context, save_agent_context
    project, target, _ = context_case
    remember_directing_request(project.id, "All background speakers use British English.")
    context = load_agent_context(project.id)
    save_agent_context(project.id, context.model_copy(update={"shot_summaries": [
        {"id": target.id, "script_beat": "OBSOLETE_CACHED_BEAT"}]}))
    save_project(project.model_copy(update={"script_text": project.script_text + " Changed ending."}))
    packet = packet_for(project, target)
    assert packet.facts["requirements"][0]["text"] == "All background speakers use British English."
    assert packet.facts["requirements"][0]["script_current"] is False
    assert "OBSOLETE_CACHED_BEAT" not in packet.model_dump_json()


def test_verified_dialogue_and_voice_audio_content_are_dependencies(context_case, tmp_path):
    from app.core.projects.models import ShotVoiceRef
    from app.core.schemas import LibraryAsset
    from app.core.library.store import _write_asset, asset_dir
    project, target, _ = context_case
    asset = LibraryAsset(id="voi_test", kind="voices", name="Guide voice", pipeline_id="external",
        job_id="fixture", created_at="2026-09-26", files={"audio": "voice.wav"}, project_id=project.id)
    _write_asset(asset)
    path = asset_dir("voices", asset.id) / "voice.wav"
    path.write_bytes(b"fixture-audio-v1")
    source = tmp_path / "source.wav"
    source.write_bytes(b"source-audio")
    target = target.model_copy(update={"voice_refs": [ShotVoiceRef(asset_id=asset.id,
        file_key="audio", audio_index=1, speaker="Guide")], "source_audio_path": str(source)})
    save_shot(target)
    packet = packet_for(project, target)
    assert packet.complete
    assert packet.facts["voice_refs"][0]["speaker"] == "Guide"
    assert packet.facts["voice_refs"][0]["content_sha256"] == hashlib.sha256(b"fixture-audio-v1").hexdigest()
    assert packet.facts["source_audio"]["content_sha256"] == hashlib.sha256(b"source-audio").hexdigest()
    path.write_bytes(b"fixture-audio-v2")
    with pytest.raises(ContextChanged):
        assert_packet_current(packet)


def test_cached_dialogue_keeps_source_and_speaker_after_camera_edit(material_shot):
    project, shot, _, _ = material_shot
    lines = [line.model_dump(mode="json") for line in shot.dialogue_lines]
    project = project.model_copy(update={"script_text": "watchmaker: Hello."})
    save_project(project)
    lines[0]["source"] = {"kind": "script", "source_hash": hashlib.sha256(project.script_text.encode()).hexdigest(),
        "scene_id": shot.scene_id, "quote": project.script_text, "occurrence": 0}
    shot = shot.model_copy(update={"dialogue_lines": None, "camera_motion": "Slow push-in",
        "meta": {"dialogue_grounding": {"script_beat": shot.script_beat, "lines": lines}}})
    save_shot(shot)
    packet = packet_for(project, shot)
    assert packet.facts["dialogue_sources"][0]["speaker_id"] == "char_1"
    assert packet.facts["dialogue_sources"][0]["text"] == "Hello."
    assert packet.facts["dialogue_sources"][0]["source"] == lines[0]["source"]


def test_reference_change_during_capture_retries_to_one_consistent_pack(material_shot, monkeypatch):
    from app.agents.director import task_context_snapshot as module
    project, shot, _, files = material_shot
    original = module.capture_references
    changed = False
    def capture(current):
        nonlocal changed
        result = original(current)
        if not changed:
            changed = True
            Image.effect_noise((480, 480), 60).convert("RGB").save(files[0])
        return result
    monkeypatch.setattr(module, "capture_references", capture)
    packet = packet_for(project, shot)
    assert packet.facts["references"][0]["content_sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest()
