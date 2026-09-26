import json
import pytest

from test_director_dialogue_attribution import authored_shot, line_payload, writer_sections, Orchestrator
from app.core.projects.dialogue import apply_dialogue_update
from app.core.projects.store import load_shot, save_shot
from app.agents.director.dialogue_preflight import dialogue_contract_current
from app.agents.director.service import DirectorService


class Provider:
    async def complete(self, system, user, **kwargs):
        return json.dumps({"prompt_sections": writer_sections(), "dialogue_uses": [
            {"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]})


def test_speaker_only_authored_revision_invalidates_managed_fingerprint(authored_shot):
    from app.core.managed_runs.store import _fingerprint
    project, shot = authored_shot
    before = _fingerprint(project.id)
    changed = apply_dialogue_update(shot, {"dialogue_lines": [line_payload(speaker_id="another")]})
    save_shot(changed)
    assert _fingerprint(project.id) != before


@pytest.mark.asyncio
async def test_derived_prompt_contract_does_not_stale_managed_plan(authored_shot):
    from app.core.managed_runs.store import _fingerprint
    project, shot = authored_shot
    before = _fingerprint(project.id)
    updated = await DirectorService(plan_provider=Provider(), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert dialogue_contract_current(project, updated)
    assert _fingerprint(project.id) == before


@pytest.mark.asyncio
async def test_prompt_bytes_and_current_requirements_are_part_of_contract(authored_shot):
    from app.agents.director.brief import remember_directing_request
    project, shot = authored_shot
    updated = await DirectorService(plan_provider=Provider(), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert dialogue_contract_current(project, updated)
    edited = updated.model_copy(update={"prompt_sections": updated.prompt_sections.model_copy(update={"summary": "Changed."})})
    assert not dialogue_contract_current(project, edited)
    remember_directing_request(project.id, "Other speakers use British English.")
    assert not dialogue_contract_current(project, updated)


@pytest.mark.asyncio
async def test_midflight_speaker_edit_survives_old_writer_response(authored_shot):
    import asyncio
    project, shot = authored_shot
    entered, release = asyncio.Event(), asyncio.Event()
    class PausedProvider(Provider):
        async def complete(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await super().complete(*args, **kwargs)
    task = asyncio.create_task(DirectorService(plan_provider=PausedProvider(), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id))
    await asyncio.wait_for(entered.wait(), 2)
    changed = apply_dialogue_update(shot, {"dialogue_lines": [line_payload(speaker_id="new-speaker")]})
    save_shot(changed)
    release.set()
    with pytest.raises(ValueError, match="changed"):
        await task
    assert load_shot(project.id, shot.id) == changed


def test_atomic_compare_save_rejects_stale_source(authored_shot):
    from app.core.projects.store import save_shot_if_current
    project, shot = authored_shot
    changed = apply_dialogue_update(shot, {"dialogue_lines": [line_payload(speaker_id="new-speaker")]})
    save_shot(changed)
    def check():
        if load_shot(project.id, shot.id) != shot:
            raise ValueError("changed")
    with pytest.raises(ValueError, match="changed"):
        save_shot_if_current(shot, check_current=check)
    assert load_shot(project.id, shot.id) == changed


@pytest.mark.asyncio
async def test_submission_rejects_uncertified_prompt_even_if_refresh_returns_it(authored_shot, monkeypatch):
    from app.api.projects import submit_shot_endpoint
    from app.core.projects.models import PromptSections, ShotStatus
    from fastapi import HTTPException
    project, shot = authored_shot
    shot = shot.model_copy(update={"status": ShotStatus.approved, "prompt_sections": PromptSections(**writer_sections())})
    save_shot(shot)
    class Refresher:
        calls = 0
        async def write_prompts_after_layout(self, id):
            self.calls += 1
            return shot
    svc = Refresher()
    with pytest.raises(HTTPException) as error:
        await submit_shot_endpoint(shot.id, svc=svc, options=None)
    assert svc.calls == 1
    assert "dialogue_contract_stale" in str(error.value.detail)
