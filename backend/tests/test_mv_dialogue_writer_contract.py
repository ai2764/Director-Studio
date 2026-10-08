"""MV lyric repair must use the same protocol that its writer can compile."""
import json

import pytest

from test_director_dialogue_attribution import authored_shot, Orchestrator, writer_sections
from test_dialogue_metadata_preflight import MetadataProvider, resolution, without_language
from app.agents.director.dialogue_preflight import prepare_dialogue, parse_dialogue_draft, dialogue_contract_current
from app.agents.director.prompt_repair import repair_request
from app.agents.director.service import DirectorService
from app.core.h3.dialogue_binding import compile_dialogue_draft
from app.core.projects.models import ProjectMode
from app.core.projects.store import save_project


def mv_project(project):
    project = project.model_copy(update={"mode": ProjectMode.mv})
    save_project(project)
    return project


@pytest.mark.asyncio
async def test_mv_authored_lyrics_resolve_language_without_changing_source(authored_shot):
    project, shot = authored_shot
    project = mv_project(project)
    shot = without_language(shot)
    lines = await prepare_dialogue(project, shot, MetadataProvider(resolution()))
    assert lines[0].language == "English"
    assert lines[0].model_dump(exclude={"language"}) == shot.dialogue_lines[0].model_dump(exclude={"language"})


def test_flat_six_sections_with_speech_tokens_are_compiled(authored_shot):
    _, shot = authored_shot
    sections = writer_sections()
    sections["detailed_description"] = "Camera pushes in as the visitor sings {{speech:l1}}."
    draft = compile_dialogue_draft(parse_dialogue_draft(json.dumps(sections)), shot.dialogue_lines)
    assert "<d>[English] Hello.</d>" in draft.prompt_sections.detailed_description
    assert "Camera pushes in" in draft.prompt_sections.detailed_description


@pytest.mark.asyncio
async def test_mv_writer_compiles_and_certifies_flat_lyric_draft(authored_shot):
    project, shot = authored_shot
    project = mv_project(project)
    class Writer:
        async def complete(self, system, user, **kwargs):
            assert "Place {{speech:line_id}}" in system
            sections = writer_sections()
            sections["detailed_description"] = "Camera pushes in as the visitor sings {{speech:l1}}."
            return json.dumps(sections)
    updated = await DirectorService(plan_provider=Writer(), orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert "{{speech" not in updated.prompt_sections.as_ordered_text()
    assert "<d>[English] Hello.</d>" in updated.prompt_sections.detailed_description
    assert dialogue_contract_current(project, updated)
    changed = updated.model_copy(update={"prompt_sections": updated.prompt_sections.model_copy(
        update={"detailed_description": "No lyrics."})})
    assert not dialogue_contract_current(project, changed)
    assert updated.dialogue_lines == shot.dialogue_lines


def test_legacy_repair_does_not_request_uncompilable_speech_tokens():
    request = repair_request("legacy shot", {"error": "language tag missing", "rejected_candidate": "{}"},
                             dialogue_bindings=False)
    assert "{{speech:" not in request
    assert "<d>[Language]" in request


@pytest.mark.asyncio
async def test_legacy_mv_without_attribution_keeps_its_existing_protocol(authored_shot):
    project, shot = authored_shot
    project = mv_project(project)
    legacy = shot.model_copy(update={"dialogue_lines": None})
    provider = MetadataProvider({})
    assert await prepare_dialogue(project, legacy, provider) is None
    assert not provider.requests
