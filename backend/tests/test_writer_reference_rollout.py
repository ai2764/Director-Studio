"""Main-compatible defaults and bounded recovery of the opt-in flow."""
import json

import pytest

from app.config import settings
from app.agents.director.material_review import capture_references, observe_references_cached
from test_director_material_review import material_shot


class Vision:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.image_calls = 0
        self.text_calls = 0

    async def complete_with_images(self, *args, **kwargs):
        self.image_calls += 1
        return json.dumps(self.answers.pop(0))

    async def complete(self, *args, **kwargs):
        self.text_calls += 1
        return json.dumps(self.answers.pop(0))


def valid():
    return dict(readable=True, description='A brown sofa.', concerns=[])


def malformed():
    return {**valid(), 'facts': [dict(attribute='sofa', value='brown', visibility='observed')]}


@pytest.mark.parametrize('reuse', [False, True])
@pytest.mark.parametrize('bad', [dict(unexpected='no visual evidence'),
    dict(readable=False, description='Cannot inspect this image.', concerns=[]), malformed()])
@pytest.mark.asyncio
async def test_new_request_can_recover_after_failed_visual_output(material_shot, monkeypatch, bad, reuse):
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', reuse)
    monkeypatch.setattr(settings, 'director_visual_facts_cache', reuse)
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    with pytest.raises(ValueError):
        await observe_references_cached(Vision(bad, bad, bad), project.id, records[:1], images[:1], lambda: None)
    provider = Vision(valid(), valid(), valid())
    result = await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert result[0]['description'] == 'A brown sofa.'
    assert provider.image_calls == 1
    assert provider.text_calls <= 2


@pytest.mark.asyncio
async def test_failed_pending_repair_gets_only_one_fresh_attempt_and_shared_text_budget(material_shot, monkeypatch):
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', True)
    monkeypatch.setattr(settings, 'director_visual_facts_cache', True)
    project, shot, _, _ = material_shot
    records, images, _ = capture_references(shot)
    bad_patch = {'repairs': [{'path': 'facts[0].evidence', 'value': 'an invented red sofa'}]}
    with pytest.raises(ValueError):
        await observe_references_cached(Vision(malformed(), bad_patch, bad_patch), project.id, records[:1], images[:1], lambda: None)
    provider = Vision(bad_patch, bad_patch, malformed())
    with pytest.raises(ValueError):
        await observe_references_cached(provider, project.id, records[:1], images[:1], lambda: None)
    assert provider.image_calls == 1
    assert provider.text_calls == 2


def test_new_flow_does_not_change_main_defaults():
    assert settings.director_writer_reference_reuse is False
    assert settings.director_visual_facts_cache is False


@pytest.mark.asyncio
async def test_default_observation_keeps_main_skill_prompt_and_vision_api(monkeypatch):
    from types import SimpleNamespace
    from app.agents.director import llm_plan_provider as module
    calls, composed = [], []
    class Client:
        # Main's default path requires chat, not a new structured-output API.
        async def chat(self, model, prompt, **kwargs):
            calls.append((model, prompt, kwargs))
            return 'original-format response'
    def skill(task, *, guides=(), writer_only=False):
        composed.append((task, tuple(guides), writer_only))
        return 'MAIN_SKILL_PROMPT'
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', False)
    monkeypatch.setattr(settings, 'director_compact_vision_prompt', False)
    monkeypatch.setattr(settings, 'director_vision_model', '')
    monkeypatch.setattr(settings, 'director_vision_base_url', '')
    monkeypatch.setattr(module, 'with_director_skill', skill)
    provider = SimpleNamespace(client=Client(), model_status=lambda: {'model': 'main-model'})
    adapter = module.DirectorLLMPlanProvider(provider=provider)
    result = await adapter.complete_with_images('Inspect exactly one reference image.', 'Reference record', images=['pixels'])
    assert result == 'original-format response'
    assert composed == [('Inspect exactly one reference image.\n\nReference record', (), True)]
    assert calls == [('main-model', 'MAIN_SKILL_PROMPT', {'images': ['pixels'], 'require_vision': True})]


def test_default_source_validation_keeps_main_ambiguity_rejection(monkeypatch):
    from app.agents.director.material_review import ReferenceObservation
    from app.agents.director.reference_facts import validate_observation_sources
    monkeypatch.setattr(settings, 'director_writer_reference_reuse', False)
    raw = {**valid(), 'facts': [dict(attribute='sofa', value='brown', visibility='observed',
            evidence='Visible sofa.', source_id='wrong', source_quote='brown sofa')]}
    sources = [dict(id=key, kind='library_metadata', text='brown sofa') for key in ('notes', 'description')]
    with pytest.raises(ValueError, match='source'):
        validate_observation_sources(ReferenceObservation.model_validate(raw), sources)
