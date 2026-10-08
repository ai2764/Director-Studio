import pytest
from app.core.projects.models import PromptSections
from app.core.projects.dialogue import DialogueLine, DialogueContractError
from app.core.h3.dialogue_binding import DialoguePromptDraft, DialogueUse, validate_dialogue_uses, annotate_speakers


def line(id="l1", who="p1", text="Hello."):
    return DialogueLine(line_id=id, speaker_id=who, speaker_name="Visitor", text=text,
        language="English", source=dict(kind="script", source_hash="x", scene_id="s",
                                        quote=f"Visitor: {text}", occurrence=0))


def draft(detail, uses):
    return DialoguePromptDraft(prompt_sections=PromptSections(subject_definitions="A visitor.",
        summary="A greeting.", retention_analysis="Keep the room.", detailed_description=detail,
        overall_soundscape="Room tone.", non_diegetic_music="N/A"), dialogue_uses=uses)


def use(ids=None, who="p1", blocks=None):
    return DialogueUse(line_ids=ids or ["l1"], speaker_id=who,
                       block_indexes=[0] if blocks is None else blocks)


def test_correct_words_wrong_speaker_is_rejected():
    with pytest.raises(DialogueContractError, match="dialogue_speaker_mismatch"):
        validate_dialogue_uses(draft("<d>[English] Hello.</d>", [use(who="p2")]), [line()])


@pytest.mark.parametrize("uses", [[], [use(ids=["unknown"])], [use(), use()],
    [use(blocks=[-1])], [use(blocks=[2])]])
def test_missing_unknown_duplicate_or_out_of_range_bindings_are_rejected(uses):
    with pytest.raises(ValueError):
        validate_dialogue_uses(draft("<d>[English] Hello.</d>", uses), [line()])


def test_same_speaker_lines_can_share_a_block():
    validate_dialogue_uses(draft("<d>[English] Hello. Come in.</d>", [use(["l1", "l2"])]),
        [line(), line("l2", text="Come in.")])


@pytest.mark.parametrize("words,detail", [
    ("We should leave now.", "<d>[English] We should <scenetrans></d> Cut. <d>[English] <scenetrans>leave now.</d>"),
    ("我们现在走。", "<d>[Chinese] 我们<scenetrans></d> Cut. <d>[Chinese] <scenetrans>现在走。</d>"),
])
def test_one_line_can_cross_a_cut(words, detail):
    validate_dialogue_uses(draft(detail, [use(blocks=[0, 1])]), [line(text=words)])


def test_repeated_words_keep_distinct_occurrence_and_speaker():
    candidate = draft("<d>[English] Hello.</d> Reply <d>[English] Hello.</d>",
                      [use(), use(["l2"], "p2", [1])])
    lines = [line(), line("l2", "p2")]
    validate_dialogue_uses(candidate, lines)
    annotated = annotate_speakers(candidate, lines)
    assert "(S1) <d>" in annotated.detailed_description and "(S2) <d>" in annotated.detailed_description
    assert "S1 is Visitor; S2 is Visitor" in annotated.subject_definitions
    assert annotated.detailed_description.count("<d>[English] Hello.</d>") == 2
    again = annotate_speakers(candidate.model_copy(update={"prompt_sections": annotated}), lines)
    assert annotated == again


@pytest.mark.parametrize("prose", ["The camera tracks left. A quiet whisper: ", "A wide shot; an exuberant greeting: "])
def test_annotation_preserves_creative_direction(prose):
    original = draft(prose + "<d>[English] Hello.</d>", [use()])
    result = annotate_speakers(original, [line()])
    assert result.detailed_description.startswith(prose)
    assert result.subject_definitions.startswith(original.prompt_sections.subject_definitions)
    assert result.model_dump(exclude={"detailed_description", "subject_definitions"}) == original.prompt_sections.model_dump(exclude={"detailed_description", "subject_definitions"})


def test_compiler_keeps_named_speaker_metadata_out_of_speech_cues():
    from app.core.h3.dialogue_binding import compile_dialogue_draft
    tao = line(who="tao", text="By the late 1990s, games gave her a body.").model_copy(
        update={"speaker_name": "Tao"})
    mia = line("l2", "mia", "Hi!").model_copy(update={"speaker_name": "Mia"})
    source = draft("He speaks: {{speech:l1}} She replies: {{speech:l2}}", [])
    result = compile_dialogue_draft(source, [tao, mia])
    assert result.prompt_sections.detailed_description == (
        "He speaks: (S1) <d>[English] By the late 1990s, games gave her a body.</d> "
        "She replies: (S2) <d>[English] Hi!</d>")
    assert "S1 is Tao; S2 is Mia" in result.prompt_sections.subject_definitions
    assert [use.speaker_id for use in result.dialogue_uses] == ["tao", "mia"]
    assert [use.line_ids for use in result.dialogue_uses] == [["l1"], ["l2"]]
    assert compile_dialogue_draft(result, [tao, mia]) == result


def test_recompiling_legacy_generated_name_cue_replaces_it_without_changing_words():
    from app.core.h3.dialogue_binding import compile_dialogue_draft
    tao = line(who="tao").model_copy(update={"speaker_name": "Tao"})
    source = draft("He says: (tao: Tao) <d>[English] Hello.</d>", [use(who="tao")])
    result = compile_dialogue_draft(source, [tao])
    assert result.prompt_sections.detailed_description == "He says: (S1) <d>[English] Hello.</d>"
    assert result.dialogue_uses == source.dialogue_uses


def test_recompile_preserves_direction_added_after_generated_speaker_bindings():
    from app.core.h3.dialogue_binding import compile_dialogue_draft
    source = draft("He says: {{speech:l1}}", [])
    first = compile_dialogue_draft(source, [line()])
    revised_sections = first.prompt_sections.model_copy(update={"subject_definitions":
        first.prompt_sections.subject_definitions + "\n<Picture 3> defines the burgundy games room."})
    revised = compile_dialogue_draft(first.model_copy(update={"prompt_sections": revised_sections}), [line()])
    assert "<Picture 3> defines the burgundy games room." in revised.prompt_sections.subject_definitions
    assert revised.prompt_sections.subject_definitions.count("Speaker identities:") == 1


def test_recompile_reassigns_generated_handle_when_an_earlier_speaker_is_removed():
    from app.core.h3.dialogue_binding import compile_dialogue_draft
    mia = line("l2", "mia", "Hi!").model_copy(update={"speaker_name": "Mia"})
    remaining = draft("She replies: (S2) <d>[English] Hi!</d>",
                      [use(["l2"], "mia", [0])])
    remaining = remaining.model_copy(update={"prompt_sections":
        remaining.prompt_sections.model_copy(update={"subject_definitions":
            "Two performers.\nSpeaker identities: S1 is Tao; S2 is Mia."})})
    result = compile_dialogue_draft(remaining, [mia])
    assert result.prompt_sections.detailed_description == "She replies: (S1) <d>[English] Hi!</d>"
    assert result.prompt_sections.subject_definitions == "Two performers.\nSpeaker identities: S1 is Mia."
    assert result.dialogue_uses == remaining.dialogue_uses


def test_silence_needs_no_binding_but_extra_speech_is_rejected():
    validate_dialogue_uses(draft("A silent room.", []), [])
    with pytest.raises(DialogueContractError):
        validate_dialogue_uses(draft("<d>[English] Surprise.</d>", []), [])


def test_different_people_cannot_share_one_speaker_block():
    with pytest.raises(DialogueContractError, match="dialogue_speaker_mismatch"):
        validate_dialogue_uses(draft("<d>[English] Hello. Goodbye.</d>", [use(["l1", "l2"])]),
                              [line(), line("l2", "p2", "Goodbye.")])


def test_new_authorized_speaker_is_not_rejected_by_old_name_rules():
    candidate = draft("A tracking close-up. <d>[English] Hello.</d>", [use(who="new-authorized-person")])
    validate_dialogue_uses(candidate, [line(who="new-authorized-person")])


def test_repair_that_changes_speech_structure_cannot_reuse_old_uses():
    import json
    from app.agents.director.prompt_repair import merge_repair
    previous = draft("<d>[English] Hello.</d>", [use()]).model_dump_json()
    patch = json.dumps({"prompt_sections": {"detailed_description": "Silent."}})
    assert "dialogue_uses" not in json.loads(merge_repair(patch, previous))


def test_grounded_prose_conflict_is_repairable_even_with_correct_metadata():
    quote = "The other person says <d>[English] Hello.</d>"
    candidate = draft(quote, [use()])
    from app.core.h3.dialogue_binding import DialogueConflict
    candidate = candidate.model_copy(update={"dialogue_conflicts": [DialogueConflict(
        line_id="l1", actual_speaker_id="p2", quote=quote, confidence="clear")]})
    with pytest.raises(DialogueContractError, match="dialogue_prose_conflict"):
        validate_dialogue_uses(candidate, [line()])


def test_unsupported_or_uncertain_semantic_opinion_does_not_block():
    from app.core.h3.dialogue_binding import DialogueConflict
    candidate = draft("<d>[English] Hello.</d>", [use()])
    candidate = candidate.model_copy(update={"dialogue_conflicts": [
        DialogueConflict(line_id="l1", actual_speaker_id="p2", quote="Not in the prompt", confidence="clear"),
        DialogueConflict(line_id="l1", actual_speaker_id="p2", quote="<d>[English] Hello.</d>", confidence="uncertain")]})
    validate_dialogue_uses(candidate, [line()])


def test_generalized_attribution_fixtures_and_creative_counterexamples():
    import json
    from pathlib import Path
    cases = json.loads((Path(__file__).parent / "fixtures/dialogue_attribution_cases.json").read_text(encoding="utf-8"))
    for case in cases:
        lines = [line(x["line_id"], x["speaker_id"], x["text"]) for x in case["source_lines"]]
        for variant in case["allowed_variants"] or [""]:
            detail = variant + " ".join(f"<d>[English] {x.text}</d>" for x in lines)
            candidate = draft(detail, [DialogueUse(**x) for x in case["candidate_uses"]])
            issues = []
            try:
                validate_dialogue_uses(candidate, lines)
            except DialogueContractError as error:
                issues = sorted({x.code for x in error.issues})
            assert issues == case["expected_issue_codes"], case["id"]
