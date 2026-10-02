"""Dialogue placement/counting and save-time enforcement, with neutral fixtures."""
from contextlib import asynccontextmanager

import pytest

from app.config import settings
from app.core.h3.prompt import compose_h3_prompt, validate_h3_prompt
from app.core.projects.models import (
    ProjectMode,
    ProjectMusicMaster,
    PromptSections,
    Shot,
    ShotMusicSegment,
)
from app.core.projects.store import create_project, load_shot, save_project, save_shot
from app.agents.director.service import DirectorService


def prompt(**changes):
    fields = dict(subject_definitions="A watchmaker stands by a table.",
                  summary="The watchmaker greets a visitor.",
                  retention_analysis="Keep the workshop geography.",
                  detailed_description="[Shot 1] The watchmaker (S1) says <d>[English] Hello.</d>",
                  overall_soundscape="Quiet room tone.", non_diegetic_music="N/A")
    return PromptSections(**{**fields, **changes})


def test_visible_text_is_not_an_extra_vocal_event():
    text = compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] A sign reads "Hello." The watchmaker (S1) says <d>[English] Hello.</d>'))
    validate_h3_prompt(text, ["Hello."])


def test_visible_text_in_subject_definition_is_not_spoken_duplication():
    validate_h3_prompt(compose_h3_prompt(prompt(subject_definitions=
        'A sign reading "Hello." hangs over the doorway.')), ["Hello."])


def test_short_dialogue_does_not_match_ordinary_music_prose():
    validate_h3_prompt(compose_h3_prompt(prompt(
        detailed_description='[Shot 1] (S1) says <d>[English] No</d>',
        non_diegetic_music="No music.")), ["No"])


def test_visible_text_exemption_does_not_hide_other_spoken_duplication():
    with pytest.raises(ValueError, match="summary"):
        validate_h3_prompt(compose_h3_prompt(prompt(summary=
            'A sign reading "Hello." hangs over the doorway. The person says "Hello."')), ["Hello."])


@pytest.mark.parametrize("body", ['The voice says No', 'The voice whispers "No"'])
def test_explicit_short_speech_in_soundscape_is_still_rejected(body):
    with pytest.raises(ValueError, match="overall_soundscape"):
        validate_h3_prompt(compose_h3_prompt(prompt(
            detailed_description='[Shot 1] (S1) says <d>[English] No</d>',
            overall_soundscape=body)), ["No"])


def test_scripted_repetition_is_preserved():
    text = compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] (S1) says <d>[English] Hello.</d> then repeats <d>[English] Hello.</d>'))
    validate_h3_prompt(text, ["Hello.", "Hello."])


def test_short_line_is_not_counted_inside_a_longer_line():
    text = compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] (S1) says <d>[English] Go!</d> then <d>[English] Do not Go!</d>'))
    validate_h3_prompt(text, ["Go!", "Do not Go!"])


@pytest.mark.parametrize("section", ["summary", "retention_analysis", "overall_soundscape", "non_diegetic_music"])
def test_duplicate_error_names_the_wrong_section(section):
    with pytest.raises(ValueError, match=section):
        validate_h3_prompt(compose_h3_prompt(prompt(**{section: "The voice says Hello."})), ["Hello."])


def test_reports_all_wrong_sections_in_one_repair_request():
    with pytest.raises(ValueError) as error:
        validate_h3_prompt(compose_h3_prompt(prompt(summary="Hello.", overall_soundscape="Hello.")), ["Hello."])
    assert "summary" in str(error.value) and "overall_soundscape" in str(error.value)


@pytest.mark.parametrize("detail", [
    '[Shot 1] The watchmaker says Hello.',
    '[Shot 1] (S1) says <d>Hello.</d>',
    '[Shot 1] (S1) says <d>[English] Hello.',
    '[Shot 1] (S1) says <d>[English] <d>Hello.</d></d>',
])
def test_requires_well_formed_spoken_blocks(detail):
    with pytest.raises(ValueError, match="detailed_description.*<d>"):
        validate_h3_prompt(compose_h3_prompt(prompt(detailed_description=detail)), ["Hello."])


def test_summary_alone_does_not_satisfy_dialogue():
    with pytest.raises(ValueError, match="summary"):
        validate_h3_prompt(compose_h3_prompt(prompt(summary="Hello.", detailed_description="A silent room.")), ["Hello."])


def test_unplanned_vocal_repetition_is_rejected_with_location():
    with pytest.raises(ValueError, match="detailed_description.*<d>"):
        validate_h3_prompt(compose_h3_prompt(prompt(detailed_description=
            '[Shot 1] (S1) says <d>[English] Hello.</d> then <d>[English] Hello.</d>')), ["Hello."])


def test_dialogue_may_continue_across_a_cut():
    text = compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] (S1) says <d>[English] We should <scenetrans></d> '
        '[Shot 2] At 00:02.000, the voice continues <d>[English] <scenetrans>leave now.</d>'))
    validate_h3_prompt(text, ["We should leave now."])


def test_two_script_lines_can_share_a_spoken_block():
    validate_h3_prompt(compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] (S1) says <d>[English] Hello. Come in.</d>')), ["Hello.", "Come in."])


def test_chinese_dialogue_can_continue_across_a_cut_without_invented_spaces():
    validate_h3_prompt(compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] (S1) says <d>[Chinese] 我们<scenetrans></d> '
        '[Shot 2] The voice continues <d>[Chinese] <scenetrans>现在走。</d>')), ["我们现在走。"])


def test_wrapped_dialogue_does_not_require_identical_whitespace():
    validate_h3_prompt(compose_h3_prompt(prompt(detailed_description=
        '[Shot 1] (S1) says <d>[English] We should\n leave now.</d>')), ["We should leave now."])


def test_screenplay_speaker_labels_are_not_spoken_words():
    validate_h3_prompt(
        compose_h3_prompt(
            prompt(
                detailed_description=(
                    '[Shot 1] Mia says <d>[English] Final check complete.</d> '
                    'Elsa answers <d>[English] Then let us go.</d>'
                )
            )
        ),
        ['MIA: "Final check complete."', 'ELSA: "Then let us go."'],
    )


def test_unquoted_screenplay_speaker_label_is_not_spoken_words():
    validate_h3_prompt(
        compose_h3_prompt(
            prompt(detailed_description='[Shot 1] Mia says <d>[English] Go.</d>')
        ),
        ["Mia: Go."],
    )


@pytest.mark.parametrize(
    "dialogue",
    [
        "NORA\nIf it is dying, I am bringing it in.",
        "NORA (V.O.)\nIf it is dying, I am bringing it in.",
    ],
)
def test_multiline_screenplay_speaker_cue_is_not_spoken_words(dialogue):
    validate_h3_prompt(
        compose_h3_prompt(
            prompt(
                detailed_description=(
                    '[Shot 1] Nora says <d>[English] '
                    'If it is dying, I am bringing it in.</d>'
                )
            )
        ),
        [dialogue],
    )


def test_language_tag_accidentally_saved_in_storyboard_is_not_spoken_words():
    validate_h3_prompt(
        compose_h3_prompt(
            prompt(
                detailed_description=(
                    '[Shot 1] Mia says <d>[English] Final check complete.</d>'
                )
            )
        ),
        ['[English] MIA: "Final check complete."'],
    )


@pytest.mark.parametrize("dialogue,detail", [
    (["Hello.", "Come in."], '<d>[English] Come in. Hello.</d>'),
    (["Hello."], '<d>[English] Hello. Extra words.</d>'),
])
def test_spoken_content_follows_the_script(dialogue, detail):
    with pytest.raises(ValueError, match="detailed_description.*<d>"):
        validate_h3_prompt(compose_h3_prompt(prompt(detailed_description=detail)), dialogue)


class Orchestrator:
    @asynccontextmanager
    async def llm_session(self, **kwargs):
        yield self

    async def ensure_llm_ready(self):
        pass


class Provider:
    def __init__(self, results):
        self.results = iter(results)
        self.requests = []

    async def complete(self, system, user, **kwargs):
        self.requests.append(user)
        import json
        result = next(self.results)
        if "Director internal response contract" in system:
            return json.dumps({"prompt_sections": result.model_dump(), "dialogue_uses": [
                {"line_ids": ["l1"], "speaker_id": "char_1", "block_indexes": [0]}]})
        return result.model_dump_json()


@pytest.fixture
def saved_shot(tmp_path, monkeypatch):
    for setting in ("projects_dir", "library_root", "jobs_dir"):
        monkeypatch.setattr(settings, setting, tmp_path / setting)
    project = create_project("Greeting", "A watchmaker greets a visitor.")
    shot = Shot(id="sht_greeting", project_id=project.id, scene_id="sc01", title="Greeting",
                script_beat="A watchmaker greets a visitor.", duration_s=6,
                dialogue=["Hello."], prompt_sections=prompt())
    from app.core.projects.dialogue import apply_dialogue_update
    from test_director_dialogue_attribution import line_payload
    shot = apply_dialogue_update(shot, {"dialogue_lines": [line_payload()]})
    save_shot(shot)
    save_project(project.model_copy(update={"shot_ids": [shot.id]}))
    return shot


@pytest.mark.asyncio
async def test_plain_write_repairs_wrong_section_before_saving(saved_shot):
    provider = Provider([prompt(overall_soundscape="The voice says Hello."), prompt()])
    updated = await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(saved_shot.id)
    assert updated.prompt_sections.overall_soundscape == "Quiet room tone."
    assert load_shot(saved_shot.project_id, saved_shot.id) == updated
    assert len(provider.requests) == 2
    assert "overall_soundscape" in provider.requests[1]


@pytest.mark.asyncio
async def test_plain_write_repairs_legacy_protocol_using_source_reference(saved_shot):
    malformed = prompt(
        detailed_description='[Shot 1] The watchmaker says <d>English Hello.</d>'
    )
    provider = Provider([malformed, prompt(detailed_description='[Shot 1] The watchmaker says {{speech:l1}}')])

    updated = await DirectorService(
        plan_provider=provider,
        orchestrator=Orchestrator(),
    ).write_prompts_after_layout(saved_shot.id)

    assert '<d>[English] Hello.</d>' in updated.prompt_sections.detailed_description
    assert '<d>English' not in updated.prompt_sections.detailed_description
    assert load_shot(saved_shot.project_id, saved_shot.id) == updated
    assert len(provider.requests) == 2
    assert "dialogue_block_invalid" in provider.requests[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_audio", [True, False])
async def test_mv_prompt_write_binds_planned_song_excerpt_as_audio_1(saved_shot, use_audio):
    project = create_project("unused", "unused")
    project = project.model_copy(update={
        "id": saved_shot.project_id,
        "name": "MV",
        "script_text": "Mia sings.",
        "mode": ProjectMode.mv,
        "shot_ids": [saved_shot.id],
        "music_master": ProjectMusicMaster(
            filename="song.wav",
            relative_path="music/master.wav",
            duration_s=30.0,
            content_sha256="a" * 64,
            source_format="wav",
        ),
    })
    save_project(project)
    segment = ShotMusicSegment(
        core_start_s=1.0,
        core_end_s=2.0,
        submit_start_s=0.5,
        submit_end_s=2.75,
        use_as_audio_reference=use_audio,
    )
    save_shot(saved_shot.model_copy(update={
        "duration_s": 2.25,
        "music_segment": segment,
        **({"dialogue": [], "dialogue_lines": []} if not use_audio else {}),
    }))
    provider = Provider([prompt(
        subject_definitions=(
            "A watchmaker stands by a table. "
            + ("<Audio 1> is the submitted song excerpt and performance timing." if use_audio else "")
        ),
        detailed_description=(
            "0–2.25 seconds: the watchmaker sings in sync, saying <d>[English] Hello.</d>"
            if use_audio else "0–2.25 seconds: the watchmaker silently waits."
        ),
        overall_soundscape=("<Audio 1> supplies the vocal and musical timing."
                            if use_audio else "Quiet room ambience only."),
    )])

    updated = await DirectorService(
        plan_provider=provider,
        orchestrator=Orchestrator(),
    ).write_prompts_after_layout(saved_shot.id)

    assert '"core_start_s": 1.0' in provider.requests[0]
    assert '"submit_end_s": 2.75' in provider.requests[0]
    assert updated.meta["prompt_music_signature"]
    assert ("<Audio 1>" in updated.prompt_sections.as_ordered_text()) is use_audio


@pytest.mark.asyncio
async def test_failed_repair_keeps_previously_saved_prompt(saved_shot):
    bad = prompt(overall_soundscape="The voice says Hello.")
    provider = Provider([bad, bad])
    with pytest.raises(ValueError, match="overall_soundscape"):
        await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(saved_shot.id)
    assert load_shot(saved_shot.project_id, saved_shot.id) == saved_shot
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_editorial_song_title_is_repaired_before_prompt_is_saved(saved_shot):
    from app.core.projects.store import load_project
    project = load_project(saved_shot.project_id).model_copy(update={
        "mode": ProjectMode.mv,
        "music_master": ProjectMusicMaster(filename="Anywhere Will Do (Remix).wav",
            relative_path="music/master.wav", duration_s=30,
            content_sha256="a" * 64, source_format="wav"),
    })
    save_project(project)
    shot = saved_shot.model_copy(update={
        "dialogue": [], "dialogue_lines": [],
        "music_segment": ShotMusicSegment(core_start_s=20.8, core_end_s=24.02,
            submit_start_s=20.3, submit_end_s=24.77, use_as_audio_reference=False),
    })
    save_shot(shot)
    clean = prompt(detailed_description="The camera slowly approaches the clock.",
                   overall_soundscape="Quiet room tone. No singing or speech.",
                   non_diegetic_music="None. No background music.")
    leaked = clean.model_copy(update={"non_diegetic_music":
        "None generated. Anywhere Will Do (Remix) will be overlaid in post-production."})
    provider = Provider([leaked, clean])
    updated = await DirectorService(plan_provider=provider,
        orchestrator=Orchestrator()).write_prompts_after_layout(shot.id)
    assert updated.prompt_sections.non_diegetic_music == clean.non_diegetic_music
    assert len(provider.requests) == 2
    assert "editorial" in provider.requests[1].lower()


@pytest.mark.asyncio
async def test_writer_does_not_repeat_history_or_current_request(saved_shot):
    from app.agents.director.brief import remember_directing_request
    remember_directing_request(saved_shot.project_id, "Keep the distinctive turquoise counter.")
    provider = Provider([prompt()])
    await DirectorService(plan_provider=provider, orchestrator=Orchestrator()).write_prompts_after_layout(
        saved_shot.id, revision_request="Use a steady camera for this revision.")
    assert provider.requests[0].count("Keep the distinctive turquoise counter.") == 1
    assert provider.requests[0].count("Use a steady camera for this revision.") == 1
