"""Prevent evidence omissions and contradictory semantic approvals at publication."""
import json

import pytest

from test_tail_prompt_review import Provider, candidate, verdict, tail_handoff_shot
from test_director_material_review import Orchestrator
from app.agents.director.service import DirectorService
from app.core.projects.store import load_shot


def checks(**failures):
    return {key: {"compatible": key not in failures,
                  "evidence": failures.get(key, "Inherited view and candidate agree; no conflicting claim.")}
            for key in ("opening_alignment", "transition_path", "reference_roles", "section_consistency")}


@pytest.mark.asyncio
async def test_reviewer_receives_casting_roles_and_verified_reference_observations(tail_handoff_shot):
    _, shot = tail_handoff_shot
    provider = Provider([candidate(), verdict()])
    await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    draft = json.loads(provider.text[0][1])
    audit = json.loads(provider.text[1][1])
    assert audit["references"] == draft["references"]
    assert audit["selected_layouts"] == draft["selected_layouts"]
    assert audit["references"][0]["description"].startswith("Eye-level waist-up")


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension,evidence", [
    ("opening_alignment", "Tail is knee-up; candidate opens full-body wide with a locked camera."),
    ("reference_roles", "Identity sheet is barefoot; selected costume supplies heels; candidate asserts barefoot."),
    ("section_consistency", "Composition ends back-facing; detailed action ends facing camera."),
])
async def test_total_approval_cannot_override_a_failed_dimension(tail_handoff_shot, dimension, evidence):
    project, shot = tail_handoff_shot
    inconsistent = {**verdict(), "checks": checks(**{dimension: evidence})}
    provider = Provider([candidate(), inconsistent, candidate(), inconsistent])
    with pytest.raises(ValueError, match="Accepted prompt conflicts with checks: " + dimension):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    stored = load_shot(project.id, shot.id)
    assert stored.prompt_sections == shot.prompt_sections
    assert stored.camera_motion == shot.camera_motion


@pytest.mark.asyncio
async def test_missing_dimension_cannot_publish_an_unexamined_candidate(tail_handoff_shot):
    project, shot = tail_handoff_shot
    incomplete = {**verdict(), "checks": checks()}
    del incomplete["checks"]["reference_roles"]
    provider = Provider([candidate(), incomplete, candidate(), incomplete])
    with pytest.raises(ValueError, match="reference_roles"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections


@pytest.mark.asyncio
async def test_failed_field_cannot_hide_under_passing_dimension_summaries(tail_handoff_shot):
    project, shot = tail_handoff_shot
    inconsistent = verdict()
    inconsistent["field_checks"]["subject_definitions"] = {
        "compatible": False, "evidence": "Costume scope does not support the asserted wardrobe detail."}
    provider = Provider([candidate(), inconsistent, candidate(), inconsistent])
    with pytest.raises(ValueError, match="Accepted prompt conflicts with fields: subject_definitions"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections


@pytest.mark.asyncio
async def test_failed_neutral_observation_stops_before_drafting(tail_handoff_shot, monkeypatch):
    from app.agents.director import writer_context
    from app.config import settings
    project, shot = tail_handoff_shot
    monkeypatch.setattr(writer_context, "video_context_writer_view", lambda _shot: {
        "mode": "previous_shot", "context_frames": 22, "carry_audio": False,
        "tail_frame_png": b"\x89PNG\r\n\x1a\nsource-ending",
    })

    class VisualProvider(Provider):
        async def complete_bounded_with_images(self, system, user, **kwargs):
            return await self.complete(system, user)

    provider = VisualProvider([{"framing": "Medium crop"}])
    with pytest.raises(ValueError, match="visible_state"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert len(provider.text) == 1
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
    errors = list((settings.projects_dir / project.id / "agent" / "prompt_failures").glob("*.json"))
    assert any(json.loads(path.read_text())["attempts"][0]["stage"] == "source_ending_observation"
               for path in errors)
