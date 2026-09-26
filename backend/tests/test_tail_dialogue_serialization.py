"""Source serialization crosses the actual tail review and storage boundary."""
import json

import pytest

from test_tail_prompt_review import Provider, candidate, verdict, tail_handoff_shot
from test_director_material_review import Orchestrator
from app.agents.director.service import DirectorService
from app.core.projects.dialogue import apply_dialogue_update
from app.core.projects.store import load_shot, save_shot


def with_dialogue(shot):
    return apply_dialogue_update(shot, {"dialogue_lines": [dict(
        line_id="line-1", speaker_id="visitor", speaker_name="Visitor", text="Bonjour.",
        language="French", source=dict(kind="script", source_hash="x", scene_id=shot.scene_id,
                                       quote="Visitor: Bonjour."))]})


@pytest.mark.asyncio
async def test_tail_reference_compiles_before_review_and_persists_real_attribution(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = with_dialogue(shot)
    save_shot(shot)
    response = candidate()
    response["prompt_sections"]["detailed_description"] += " She quietly says {{speech:line-1}}"
    provider = Provider([response, verdict(), response, verdict()])
    result = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "(visitor: Visitor) <d>[French] Bonjour.</d>" in result.prompt_sections.detailed_description
    assert len(provider.text) == 2
    audited = json.loads(provider.text[1][1])["candidate_prompt"]["detailed_description"]
    assert "<d>[French] Bonjour.</d>" in audited and "{{speech:" not in audited
    stored = load_shot(project.id, shot.id)
    assert stored.meta["prompt_dialogue_contract"]["uses"][0]["speaker_id"] == "visitor"
    assert stored.prompt_sections == result.prompt_sections


@pytest.mark.asyncio
async def test_malformed_legacy_tail_block_fails_without_changing_saved_prompt(tail_handoff_shot):
    project, shot = tail_handoff_shot
    shot = with_dialogue(shot)
    save_shot(shot)
    response = candidate()
    response["prompt_sections"]["detailed_description"] += " She says <d>French Bonjour.</d>"
    response["dialogue_uses"] = [dict(line_ids=["line-1"], speaker_id="visitor", block_indexes=[0])]
    provider = Provider([response, verdict(), response, verdict()])
    with pytest.raises(ValueError, match="dialogue_block_invalid.*detailed_description.*French Bonjour"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert load_shot(project.id, shot.id).prompt_sections == shot.prompt_sections
