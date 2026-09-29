import json

import pytest
from task_context_fixtures import context_case
from test_director_material_review import material_shot, tail_handoff_shot, Provider, Orchestrator
from test_tail_prompt_review import Provider as TailProvider, candidate, verdict
from app.config import settings
from app.core.projects.store import load_shot, save_shot
from app.agents.director.service import DirectorService
from app.agents.director.task_context_models import TaskPacket, TaskRequest, TaskContextState, ContextRead
from app.agents.director.task_context_builder import writer_task_context, ContextRequired
from app.agents.director.task_context_runtime import task_context_scope, current_task_context
from app.agents.director.task_context_query import read_task_context


def enable(monkeypatch, project):
    monkeypatch.setattr(settings, "director_task_context_mode", "pilot")
    monkeypatch.setattr(settings, "director_task_context_projects", [project.id])


def test_incomplete_packet_never_becomes_writer_input():
    packet = TaskPacket(project_id="p", task=TaskRequest(), authority={}, source_versions={},
        facts={}, complete=False, missing=[{"code": "CONTEXT_BUDGET_EXCEEDED", "source_key": "script:p"}])
    with pytest.raises(ContextRequired):
        writer_task_context(packet, dialogue_lines=[], reference_evidence=[])


def test_shadow_writer_builds_only_a_readonly_comparison(context_case, monkeypatch):
    from app.agents.director import context_metrics as metrics
    from app.agents.director.task_context_runtime import prepare_writer_packet
    project, shot, _ = context_case
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "director_task_context_mode", "shadow")
    shapes = []
    monkeypatch.setattr(metrics, "record_request_shape", shapes.append)
    with metrics.metrics_scope(project.id):
        assert prepare_writer_packet(project, shot.id) is None
    assert shapes[0]["path"] == "writer_context.shadow"
    assert load_shot(project.id, shot.id) == shot


@pytest.mark.asyncio
async def test_normal_writer_uses_full_task_evidence_and_inspects_every_picture(material_shot, monkeypatch):
    project, shot, _, _ = material_shot
    enable(monkeypatch, project)
    orch = Orchestrator()
    provider = Provider(orch)
    saved = await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    prompt_request = provider.text[-1][1]
    assert '"source_versions"' in prompt_request
    assert '"verified_dialogue_lines"' in prompt_request
    assert '"speaker_id": "char_1"' in prompt_request
    assert f'"shot:{shot.id}"' in prompt_request
    assert len(provider.visual) == 9
    assert saved.prompt_sections.detailed_description.count("Hello.") == 1
    assert current_task_context(project.id) is None


@pytest.mark.asyncio
async def test_tail_writer_keeps_exact_origin_and_current_revision(tail_handoff_shot, monkeypatch):
    project, shot = tail_handoff_shot
    enable(monkeypatch, project)
    provider = TailProvider([candidate(), verdict()])
    saved = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(
        shot.id, revision_request="Keep the stance; use a gentle pullback.")
    request = json.loads(provider.text[0][1])
    assert request["task_context"]["facts"]["continuity"][0]["origin"]["source_job_id"] == "job_previous"
    assert request["task_context"]["task"]["objective"] == "Keep the stance; use a gentle pullback."
    assert request["revision_request"] == "Keep the stance; use a gentle pullback."
    assert request["task_context"]["reviewed_references"][0]["picture_index"] == 1
    assert len(provider.visual) == 1 and saved.meta["material_review_pending"] is False


@pytest.mark.asyncio
async def test_preflight_budget_failure_does_not_write_review_or_retry(material_shot, monkeypatch):
    project, shot, _, _ = material_shot
    enable(monkeypatch, project)
    monkeypatch.setattr(settings, "director_num_ctx", 1000)
    orch = Orchestrator()
    provider = Provider(orch)
    before = {p: p.read_bytes() for p in settings.projects_dir.rglob("*") if p.is_file()}
    with pytest.raises(ContextRequired):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    after = {p: p.read_bytes() for p in settings.projects_dir.rglob("*") if p.is_file()}
    assert before == after
    assert provider.visual == [] and provider.text == []
    assert current_task_context(project.id) is None


@pytest.mark.asyncio
async def test_neighbor_change_during_writer_rejects_stale_context(material_shot, monkeypatch):
    project, shot, neighbor, _ = material_shot
    enable(monkeypatch, project)
    orch = Orchestrator()
    def mutate(stage, count):
        if stage == "prompt":
            save_shot(neighbor.model_copy(update={"script_beat": "The courier has now left the room."}))
    provider = Provider(orch, mutate=mutate)
    with pytest.raises(ValueError, match="CONTEXT_CHANGED"):
        await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
    assert load_shot(project.id, neighbor.id).script_beat == "The courier has now left the room."


@pytest.mark.asyncio
async def test_harness_missing_evidence_is_nonterminal_and_query_unlocks_once(material_shot, monkeypatch):
    from app.agents.director.harness_runtime import BackendTurn
    project, shot, _, files = material_shot
    enable(monkeypatch, project)
    original = files[0].read_bytes()
    files[0].unlink()
    orch = Orchestrator()
    provider = Provider(orch)
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    state = TaskContextState(project_id=project.id, request=TaskRequest(objective="Rewrite this shot only."), retrieved_versions={})
    with task_context_scope(state):
        turn = BackendTurn(project.id, "Please consider this shot.", svc, None)
        await turn.dispatch("context", {})
        async def write(call_id):
            return await turn.dispatch("tool", {"name": "write_prompt", "arguments": {"shot_id": shot.id}, "call_id": call_id})
        first = await write("first")
        assert first["code"] == "CONTEXT_REQUIRED" and first["concludes_turn"] is False
        assert turn.terminal_failure is None
        assert provider.visual == []
        assert (await write("duplicate"))["ok"] is False
        files[0].write_bytes(original)
        # Merely restoring a file or changing focus must not replay a rejected mutation.
        assert (await write("without-query"))["ok"] is False
        read_task_context(state, ContextRead(source_key=f"asset:props:{shot.refs[0].asset_id}:selected"))
        result = await write("recovered")
        assert result["ok"], result
        call_count = len(provider.text)
        assert (await write("saved-again"))["already_saved"]
        assert len(provider.text) == call_count


@pytest.mark.asyncio
async def test_pilot_scoped_retry_still_preserves_authored_fields(material_shot, monkeypatch):
    from app.agents.director.prompt_retry import record_prompt_failure, run_prompt_retry, authored_payload
    project, shot, _, _ = material_shot
    enable(monkeypatch, project)
    receipt = record_prompt_failure(shot, "Keep performance", ValueError("previous contract failure"))
    orch = Orchestrator()
    provider = Provider(orch)
    saved = await run_prompt_retry(project.id, receipt, DirectorService(plan_provider=provider, orchestrator=orch))
    assert authored_payload(saved) == authored_payload(shot)
    assert '"prompt_only_retry": true' in provider.text[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", [False, True])
async def test_both_writers_preserve_voice_mapping_and_content_version(material_shot, tail_handoff_shot, monkeypatch, tail):
    from app.core.projects.models import ShotVoiceRef
    from app.core.schemas import LibraryAsset
    from app.core.library.store import _write_asset, asset_dir
    project, shot = tail_handoff_shot if tail else material_shot[:2]
    enable(monkeypatch, project)
    asset = LibraryAsset(id="voi_context", kind="voices", name="Narrator", pipeline_id="external",
        job_id="fixture", created_at="2026-09-26", files={"reference": "voice.wav"}, project_id=project.id)
    _write_asset(asset)
    (asset_dir("voices", asset.id) / "voice.wav").write_bytes(b"voice content")
    shot = shot.model_copy(update={"voice_refs": [ShotVoiceRef(asset_id=asset.id, audio_index=1, speaker="Narrator")]})
    save_shot(shot)
    orch = Orchestrator()
    if tail:
        draft = candidate()
        draft["prompt_sections"]["overall_soundscape"] += " Voice from <Audio 1>."
        provider = TailProvider([draft, verdict()])
    else:
        class Voiced(Provider):
            async def complete(self, system, user, **kwargs):
                raw = await super().complete(system, user, **kwargs)
                result = json.loads(raw)
                if "prompt_sections" in result:
                    result["prompt_sections"]["overall_soundscape"] += " Voice from <Audio 1>."
                return json.dumps(result)
        provider = Voiced(orch)
    await DirectorService(plan_provider=provider, orchestrator=orch).write_prompts_after_layout(shot.id)
    text = provider.text[0 if tail else -1][1]
    assert '"speaker": "Narrator"' in text and '"audio_index": 1' in text
    assert '"content_sha256"' in text and f"asset:voices:{asset.id}:reference" in text
