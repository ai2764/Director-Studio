"""Preserve usable silent-shot prompts during narrow feedback edits."""
import json

import pytest

from app.config import settings
from app.agents.director.service import DirectorService
from app.core.projects.models import PromptSections
from app.core.projects.store import load_shot, save_shot
from test_director_material_review import material_shot, Orchestrator, Provider, sections


def silent_shot(material_shot):
    project, shot, _, _ = material_shot
    values = sections()
    values['detailed_description'] = '0-6 seconds: The watchmaker examines the gear.'
    values['overall_soundscape'] = 'Original quiet room tone.'
    shot = shot.model_copy(update={'dialogue': [], 'dialogue_lines': [], 'meta': {},
                                   'prompt_sections': PromptSections(**values)})
    save_shot(shot)
    return project, shot


@pytest.mark.parametrize('enabled', [False, True])
def test_unchanged_revision_preserves_prompt_only_in_optin_flow(material_shot, monkeypatch, enabled):
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', enabled)
    project, shot = silent_shot(material_shot)
    shot = shot.model_copy(update={'h3_job_id': 'job_keep_existing'})
    save_shot(shot)
    svc = DirectorService(plan_provider=Provider(Orchestrator()), orchestrator=Orchestrator())
    svc.revise_shot(project.id, {'shot_id': shot.id, 'script_beat': '  '+shot.script_beat+'  ', 'duration_s': shot.duration_s})
    saved = load_shot(project.id, shot.id)
    if enabled:
        assert saved == shot
    else:
        assert not saved.prompt_sections.summary
        assert saved.h3_job_id is None


def test_real_revision_still_invalidates_prompt_and_checks_stale_hash(material_shot, monkeypatch):
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', True)
    project, shot = silent_shot(material_shot)
    svc = DirectorService(plan_provider=Provider(Orchestrator()), orchestrator=Orchestrator())
    with pytest.raises(ValueError, match='changed'):
        svc.revise_shot(project.id, {'shot_id': shot.id, 'script_beat': shot.script_beat}, expected_shot_hash='stale')
    assert load_shot(project.id, shot.id) == shot
    svc.revise_shot(project.id, {'shot_id': shot.id, 'duration_s': 8})
    assert load_shot(project.id, shot.id).duration_s == 8
    assert not load_shot(project.id, shot.id).prompt_sections.summary


@pytest.mark.asyncio
async def test_noop_tool_receipt_does_not_claim_prompt_was_cleared(material_shot, monkeypatch):
    from app.agents.director.chat import _run_tools
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', True)
    project, shot = silent_shot(material_shot)
    orch = Orchestrator()
    svc = DirectorService(plan_provider=Provider(orch), orchestrator=orch)
    payloads, actions = [], []
    notes, _ = await _run_tools(project_id=project.id,
        tools=[{'name': 'revise_shot', 'args': {'shot_id': shot.id, 'script_beat': shot.script_beat}}],
        svc=svc, actions=actions, result_payloads=payloads, user_feedback='Keep the shot unchanged; correct only the prompt soundscape.')
    assert load_shot(project.id, shot.id) == shot
    assert payloads[-1]['changed'] is False
    assert 'preserved' in ' '.join(notes)
    assert 'cleared' not in ' '.join(notes)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_silent_revision_receives_previous_prompt_with_optin_only(material_shot, monkeypatch, enabled):
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', enabled)
    project, shot = silent_shot(material_shot)
    orch = Orchestrator();provider = Provider(orch);original = provider.complete;seen=[]
    expected = shot.prompt_sections.model_dump();expected['overall_soundscape'] = 'Only fabric rustles during the current action.'
    async def complete(system,user,*,guides=()):
        if 'reference review decision' in system.lower():
            return await original(system,user,guides=guides)
        seen.append((system,user))
        return json.dumps(expected)
    provider.complete=complete
    result = await DirectorService(plan_provider=provider,orchestrator=orch).write_prompts_after_layout(
        shot.id,revision_request='Correct only the soundscape; preserve all authored fields and other sections.')
    assert len(seen)==1
    assert ('Existing prompt, if present' in seen[0][1]) is enabled
    if enabled:
        assert 'Original quiet room tone.' in seen[0][1]
        assert 'do not move actions assigned to another Shot' in seen[0][0]
    assert result.prompt_sections.summary == shot.prompt_sections.summary
    assert result.script_beat == shot.script_beat
    assert result.duration_s == shot.duration_s
