"""The writer selects speech positions; source records supply every spoken byte."""
import json

import pytest

from app.core.h3 import dialogue_binding as binding
from app.core.projects.dialogue import DialogueContractError, DialogueLine
from app.core.projects.models import PromptSections


def line(id="l1", speaker="p1", text="Olá… atenção: vem!", language="Português"):
    return DialogueLine(line_id=id, speaker_id=speaker, speaker_name="Visitor", text=text,
        language=language, source=dict(kind="script", source_hash="x", scene_id="s",
                                      quote=f"Visitor: {text}"))


def draft(detail, uses=None, **fields):
    return binding.DialoguePromptDraft(prompt_sections=PromptSections(
        subject_definitions="Two visitors.", summary="An encounter.",
        retention_analysis="Keep the room.", detailed_description=detail,
        overall_soundscape="Room tone.", non_diegetic_music="N/A"),
        dialogue_uses=uses or [], **fields)


def compile_draft(candidate, lines):
    # A missing serializer is a behavioral failure, not an import/collection error.
    compiler = getattr(binding, "compile_dialogue_draft", None)
    assert callable(compiler), "A shared source-driven dialogue compiler is required"
    return compiler(candidate, lines)


def test_placeholders_preserve_creative_prose_and_exact_source_words():
    candidate = draft("Camera arcs. Softly: {{speech:l1}} Then hold the empty door.")
    result = compile_draft(candidate, [line()])
    assert result.prompt_sections.detailed_description == (
        "Camera arcs. Softly: (p1: Visitor) <d>[Português] Olá… atenção: vem!</d> "
        "Then hold the empty door.")
    assert result.dialogue_uses[0].line_ids == ["l1"]
    assert result.dialogue_uses[0].speaker_id == "p1"
    assert result.dialogue_uses[0].block_indexes == [0]
    assert candidate.prompt_sections.detailed_description.startswith("Camera arcs. Softly: {{speech:")
    assert compile_draft(result, [line()]) == result


def test_repeated_identical_words_preserve_distinct_speakers_and_occurrences():
    result = compile_draft(draft("{{speech:l1}} A pause. {{speech:l2}}"),
                           [line(), line("l2", "p2")])
    assert [use.speaker_id for use in result.dialogue_uses] == ["p1", "p2"]
    assert result.prompt_sections.detailed_description.count("<d>[Português] Olá… atenção: vem!</d>") == 2


@pytest.mark.parametrize("detail,code", [
    ("{{speech:other}}", "dialogue_unknown_line"),
    ("{{speech:l1}} {{speech:l1}}", "dialogue_coverage_mismatch"),
    ("{{speech:l2}} {{speech:l1}}", "dialogue_coverage_mismatch"),
    ("{{speech:l1}}", "dialogue_coverage_mismatch"),
    ("{{speech:l1", "dialogue_placeholder_invalid"),
    ("<d>[Português] Other.</d> {{speech:l1}}", "dialogue_mixed_format"),
])
def test_invalid_placeholder_coverage_is_actionable(detail, code):
    with pytest.raises(DialogueContractError, match=code) as error:
        compile_draft(draft(detail), [line(), line("l2", "p2")])
    assert error.value.issues[0].expected
    assert error.value.issues[0].actual
    assert error.value.issues[0].action


def test_no_source_language_is_not_guessed():
    with pytest.raises(DialogueContractError, match="dialogue_language_missing"):
        compile_draft(draft("{{speech:l1}}"), [line(language="")])


def test_unicode_character_spans_preserve_speech_across_cuts_and_metadata_roundtrip():
    source = line(text="こんにちは世界", language="日本語")
    result = compile_draft(draft("{{speech:l1:0:5}} Cut to the listener. {{speech:l1:5:7}}"), [source])
    assert "<d>[日本語] こんにちは</d> Cut to the listener. (p1: Visitor) <d>[日本語] 世界</d>" in result.prompt_sections.detailed_description
    assert result.dialogue_uses[0].source_spans == [(0, 5), (5, 7)]
    restored = binding.DialoguePromptDraft.model_validate_json(result.model_dump_json())
    binding.validate_dialogue_uses(restored, [source])


@pytest.mark.parametrize("detail", [
    "{{speech:l1:0:3}} {{speech:l1:2:7}}",
    "{{speech:l1:0:3}} {{speech:l1:4:7}}",
    "{{speech:l1:0:8}}", "{{speech:l1:0:0}}",
])
def test_explicit_spans_cannot_drop_duplicate_or_invent_authored_characters(detail):
    with pytest.raises(DialogueContractError, match="dialogue_span_invalid"):
        compile_draft(draft(detail), [line(text="Bonjour", language="French")])


def test_supplied_wrong_speaker_metadata_is_not_silently_overwritten():
    candidate = draft("{{speech:l1}}", [dict(line_ids=["l1"], speaker_id="p2", block_indexes=[0])])
    with pytest.raises(DialogueContractError, match="dialogue_speaker_mismatch"):
        compile_draft(candidate, [line()])


def test_legacy_valid_blocks_remain_supported_and_wrong_words_still_fail():
    uses = [dict(line_ids=["l1"], speaker_id="p1", block_indexes=[0])]
    good = compile_draft(draft("Whisper <d>[Português] Olá… atenção: vem!</d>", uses), [line()])
    assert good.prompt_sections.detailed_description.startswith("Whisper (p1: Visitor)")
    with pytest.raises(DialogueContractError, match="dialogue_block_mismatch"):
        compile_draft(draft("Whisper <d>[Português] Wrong.</d>", uses), [line()])


def test_malformed_legacy_block_reports_the_bad_field_and_actual_content():
    candidate = draft("Whisper <d>Português: Olá… atenção: vem!</d>",
                      [dict(line_ids=["l1"], speaker_id="p1", block_indexes=[0])])
    with pytest.raises(DialogueContractError) as error:
        binding.validate_dialogue_uses(candidate, [line()])
    issue = error.value.issues[0]
    assert issue.expected
    assert "Português: Olá" in issue.actual
    assert "detailed_description" in str(error.value)


def test_parse_placeholder_envelope_without_manual_attribution():
    from app.agents.director.dialogue_preflight import parse_dialogue_draft
    raw = json.dumps({"prompt_sections": draft("{{speech:l1}}").prompt_sections.model_dump()})
    parsed = parse_dialogue_draft(raw)
    result = compile_draft(parsed, [line()])
    assert result.dialogue_uses[0].speaker_id == "p1"


@pytest.mark.parametrize("language,text,offset", [
    ("日本語", "こんにちは世界", 5), ("Thai", "สวัสดีครับ", 6), ("French", "Bonjour", 3),
])
def test_compiled_cut_spans_pass_final_h3_contract_for_any_language(language, text, offset):
    from app.core.h3.prompt import validate_h3_prompt
    result = compile_draft(draft(f"{{{{speech:l1:0:{offset}}}}} Cut. {{{{speech:l1:{offset}:{len(text)}}}}}"),
                           [line(text=text, language=language)])
    validate_h3_prompt(result.prompt_sections.as_ordered_text(), [text])


@pytest.mark.parametrize("detail", [
    "<d>[French] Bon</d> Cut. <d>[French] jour</d>",
    "<d>[French] Bonjour</d>",
])
def test_raw_legacy_cut_blocks_still_match_full_source_without_trusted_bypass(detail):
    from app.core.h3.prompt import validate_h3_prompt
    validate_h3_prompt(draft(detail).prompt_sections.as_ordered_text(), ["Bonjour"])


@pytest.mark.parametrize("detail", [
    "<d>[French] jour</d> <d>[French] Bon</d>",
    "<d>[French] Bon</d> <d>[French] Bon</d> <d>[French] jour</d>",
    "<d>[French] Bon</d>", "<d>[French] Bon jour</d>",
])
def test_raw_cut_matching_does_not_accept_changed_reordered_or_repeated_words(detail):
    from app.core.h3.prompt import validate_h3_prompt
    with pytest.raises(ValueError, match="does not match"):
        validate_h3_prompt(draft(detail).prompt_sections.as_ordered_text(), ["Bonjour"])


def test_legacy_persistence_omits_absent_new_span_field():
    from app.agents.director.dialogue_preflight import prompt_dialogue_record
    from app.core.projects.models import Project, Shot
    project = Project(id="p", name="Test", script_text="", created_at="", updated_at="")
    candidate = draft("<d>[Português] Olá… atenção: vem!</d>",
                      [dict(line_ids=["l1"], speaker_id="p1", block_indexes=[0])])
    shot = Shot(id="s", project_id="p", scene_id="s", title="Test", script_beat="A greeting.",
                duration_s=6, dialogue=[line().text], prompt_sections=candidate.prompt_sections)
    record = prompt_dialogue_record(project, shot, [line()], candidate)
    assert record["uses"] == [dict(line_ids=["l1"], speaker_id="p1", block_indexes=[0])]


def test_persisted_explicit_spans_must_match_each_block_not_only_aggregate_words():
    candidate = draft("<d>[English] Hello</d> Cut. <d>[English] world</d>", [dict(
        line_ids=["l1"], speaker_id="p1", block_indexes=[0, 1], source_spans=[(0, 3), (3, 11)])])
    with pytest.raises(DialogueContractError, match="dialogue_block_mismatch"):
        binding.validate_dialogue_uses(candidate, [line(text="Hello world", language="English")])


def test_clear_wrong_speaker_prose_finding_survives_placeholder_compilation():
    candidate = draft("The other person says {{speech:l1}}", dialogue_conflicts=[dict(
        line_id="l1", actual_speaker_id="p2", quote="The other person says {{speech:l1}}", confidence="clear")])
    with pytest.raises(DialogueContractError, match="dialogue_prose_conflict"):
        compile_draft(candidate, [line()])


def test_source_reference_in_another_section_cannot_leak_into_final_h3():
    candidate = draft("{{speech:l1}}")
    candidate.prompt_sections.summary = "{{speech:l1}}"
    with pytest.raises(DialogueContractError, match="dialogue_placeholder_misplaced"):
        compile_draft(candidate, [line()])
