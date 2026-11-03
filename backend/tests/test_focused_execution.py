import json

import pytest

from app.config import settings
from app.core.projects.models import Shot, ShotVideoContext
from app.core.projects.store import create_project, save_shot, save_project
from test_director_material_review import material_shot, Orchestrator, Provider


def test_context_preserves_global_sources_numbering_and_dependency():
    from app.agents.director.execution_context import focused_writer_context, execution_scope_signature
    project = create_project("Film", script_text="Global requirement outside nearby shots.")
    shots = [Shot(id=f"s{i}", project_id=project.id, scene_id="scene", title=f"Shot {i}", script_beat=f"Action {i}", duration_s=6) for i in range(5)]
    for shot in shots:
        save_shot(shot)
    project.shot_ids = [s.id for s in shots]
    save_project(project)
    shots[4].video_context = ShotVideoContext(mode="previous_shot", source_shot_id="s0")
    context = {"script": project.script_text, "shot_summaries": [{"id": s.id, "text": s.script_beat} for s in shots],
               "extra": {"directing_brief": {"messages": ["Keep Mia throughout."]}}}
    signature = execution_scope_signature(context, project)
    context["extra"]["execution_scope_certificate"] = {"signature": signature, "target_shot_id": shots[4].id,
                                                     "dependencies": ["s0"], "complete": True}
    result = focused_writer_context(context, project, shots[4])
    assert {s["id"] for s in result["shot_summaries"]} == {"s0", "s3", "s4"}
    assert result["script"] == context["script"]
    assert result["extra"] == context["extra"]
    assert len(result["execution_scope"]["shot_order"]) == 5
    assert context["shot_summaries"] != result["shot_summaries"]


def test_context_unknown_dependency_retains_complete_context():
    from app.agents.director.execution_context import focused_writer_context
    project = create_project("Film", script_text="Background.")
    shot = Shot(id="s1", project_id=project.id, scene_id="scene", title="One", script_beat="Wait.", duration_s=6, meta={"continuity_dependencies": ["missing"]})
    project.shot_ids = ["s1", "s2"]
    context = {"shot_summaries": [{"id": "s1"}, {"id": "s2"}]}
    assert focused_writer_context(context, project, shot) == context


def test_uncertified_cross_shot_request_keeps_required_authored_staging():
    from app.agents.director.execution_context import focused_writer_context
    project = create_project("Film", "Shot 6 uses the opening shot's staging.")
    project.shot_ids = [f"s{i}" for i in range(6)]
    shot = Shot(id="s5", project_id=project.id, scene_id="scene", title="Six", script_beat="Use the opening staging.", duration_s=6)
    context = {"shot_summaries": [{"id": sid, "composition": "Mia is left of Tao."} for sid in project.shot_ids]}
    assert focused_writer_context(context, project, shot) == context


@pytest.mark.asyncio
async def test_speed_only_change_reuses_review_but_still_writes_and_certifies(material_shot, monkeypatch):
    from app.core.projects.store import load_shot
    from app.agents.director.reference_facts import reference_contract_current
    monkeypatch.setattr(settings, "director_focused_execution", True, raising=False)
    project, shot, _, _ = material_shot
    shot.camera_motion = "slow pan left"
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch)
    svc = __import__("app.agents.director.service", fromlist=["DirectorService"]).DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    first_reviews = sum("reference review decision" in system.lower() for system, _ in provider.text)
    first.camera_motion = "very slow pan left"
    save_shot(first)
    from app.agents.director.execution_context import reusable_execution_review, review_execution_snapshot
    from app.agents.director.material_review import capture_references
    review = first.meta["material_review"]
    assert not first.meta.get("material_review_pending")
    assert reusable_execution_review(project, first, capture_references(first)[0], review, "") is not None, (
        review["execution_input"], review_execution_snapshot(first), review.get("handoff_signature"))
    second = await svc.write_prompts_after_layout(shot.id)
    assert sum("reference review decision" in system.lower() for system, _ in provider.text) == first_reviews
    assert len(provider.visual) == 9
    assert reference_contract_current(project, load_shot(project.id, shot.id))
    assert second.meta["material_review"]["execution_reuse"] == "camera_speed_only"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["action", "visibility", "dialogue", "free_text", "global"])
async def test_semantic_changes_keep_existing_review(material_shot, monkeypatch, change):
    monkeypatch.setattr(settings, "director_focused_execution", True, raising=False)
    project, shot, _, _ = material_shot
    shot.camera_motion = "slow pan left"
    save_shot(shot)
    orch = Orchestrator()
    provider = Provider(orch)
    from app.agents.director.service import DirectorService
    svc = DirectorService(plan_provider=provider, orchestrator=orch)
    first = await svc.write_prompts_after_layout(shot.id)
    if change == "action": first.script_beat += " Walk away."
    if change == "visibility": first.camera_motion = "pan behind the actor"
    if change == "dialogue": first.feedback = "Change the words."
    if change == "global":
        project.script_text += " Keep the red coat."
        save_project(project)
    save_shot(first)
    try:
        await svc.write_prompts_after_layout(shot.id, revision_request="Look closely at the sleeves" if change == "free_text" else "")
    except ValueError:
        pass  # A preserved semantic gate may reject this synthetic incompatible request.
    assert sum("reference review decision" in system.lower() for system, _ in provider.text) >= 2


@pytest.mark.asyncio
async def test_focused_writer_uses_task_contract_without_agent_operating_instructions(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.agents.director.llm_plan_provider import DirectorLLMPlanProvider
    from app.agents.director.writer_context import complete_writer_prompt
    from app.core.prompt_errors import PromptOutputTruncated
    monkeypatch.setattr(settings, "director_focused_execution", True, raising=False)
    skill = tmp_path / "custom.md"
    skill.write_text("Use restrained camera movement.", encoding="utf-8")
    monkeypatch.setenv("DS_DIRECTOR_SKILL_PATH", str(skill))
    calls = []
    finish = "stop"
    async def response(model, **kwargs):
        calls.append(kwargs)
        return {"content": "candidate", "finish_reason": finish}
    adapter = DirectorLLMPlanProvider(SimpleNamespace(client=SimpleNamespace(chat_response=response)), model="text")
    result = await complete_writer_prompt(adapter, "Preserve exact dialogue and Picture tags.", "CURRENT SHOT", None, allow_focused=True)
    assert result == "candidate"
    prompt = calls[0]["messages"][0]["content"]
    assert "DIRECTOR_SKILL" not in prompt
    assert "Preserve exact dialogue" in prompt
    assert "Use restrained camera movement" in prompt
    assert calls[0]["options"]["num_predict"] == settings.director_num_predict
    finish = "length"
    with pytest.raises(PromptOutputTruncated):
        await complete_writer_prompt(adapter, "contract", "shot", None, allow_focused=True)


def test_review_snapshot_tracks_dialogue_and_voice_inputs():
    from app.agents.director.execution_context import review_execution_snapshot
    from app.core.projects.models import ShotVoiceRef
    shot = Shot(id="s", project_id="p", scene_id="scene", title="One", script_beat="Wait", duration_s=6)
    before = review_execution_snapshot(shot)
    shot.voice_refs = [ShotVoiceRef(asset_id="voice", audio_index=1, speaker="Mia")]
    assert review_execution_snapshot(shot) != before


def test_focused_execution_does_not_change_other_authoring_modes(monkeypatch):
    from app.agents.director.execution_context import focused_execution_enabled
    from app.core.projects.models import ProjectMode
    project = create_project("Film", "A scene.")
    monkeypatch.setattr(settings, "director_focused_execution", True)
    assert focused_execution_enabled(project)
    project.mode = ProjectMode.mv
    assert not focused_execution_enabled(project)
    project.mode = ProjectMode.director
    project.authoring_mode = "shot_workspace"
    assert not focused_execution_enabled(project)
