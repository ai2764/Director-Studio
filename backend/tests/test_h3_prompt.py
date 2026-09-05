from app.core.h3.prompt import validate_h3_prompt, compose_h3_prompt
from app.core.projects.models import PromptSections
import pytest


def test_order_and_dialogue():
    sections = PromptSections(
        subject_definitions="A",
        summary="B\n<Subject 1> (S1): <d>[Chinese] 几点？</d>",
        retention_analysis="C",
        detailed_description="D",
        overall_soundscape="E",
        non_diegetic_music="F",
    )
    text = compose_h3_prompt(sections)
    validate_h3_prompt(text, ["几点？"])
    with pytest.raises(ValueError, match="section order"):
        validate_h3_prompt(text.replace("summary:", "x:"), ["几点？"])


def test_audio_tags_must_reference_submitted_audio_but_may_repeat():
    sections = PromptSections(
        subject_definitions="<Audio 1> defines Mia. <Audio 2> defines Daniel.",
        summary="B",
        retention_analysis="C",
        detailed_description="Mia says hello.",
        overall_soundscape="E",
        non_diegetic_music="F",
    )
    text = compose_h3_prompt(sections)
    validate_h3_prompt(text, [], audio_count=2)
    validate_h3_prompt(
        text.replace("overall_soundscape:\nE", "overall_soundscape:\n<Audio 1> stays consistent."),
        [],
        audio_count=2,
    )

    with pytest.raises(ValueError, match="Audio 2"):
        validate_h3_prompt(text.replace("<Audio 2>", "Daniel"), [], audio_count=2)
    with pytest.raises(ValueError, match="Audio 3"):
        validate_h3_prompt(text + " <Audio 3>", [], audio_count=2)


def test_requires_every_selected_layout_picture_binding():
    from app.core.h3.prompt import validate_required_picture_bindings

    prompt = "<Picture 5> establishes the empty doorway."

    with pytest.raises(
        ValueError, match="missing selected Layout binding: <Picture 6>"
    ):
        validate_required_picture_bindings(prompt, [5, 6])


def test_required_picture_bindings_deduplicate_indices():
    from app.core.h3.prompt import validate_required_picture_bindings

    validate_required_picture_bindings(
        "<Picture 5> controls the composition.",
        [5, 5],
    )


def test_rejects_picture_tag_not_in_submitted_picture_set():
    from app.core.h3.prompt import validate_required_picture_bindings

    with pytest.raises(
        ValueError,
        match=r"unsubmitted Picture.*<Picture 3>",
    ):
        validate_required_picture_bindings(
            "<Picture 1> controls identity. <Picture 3> controls geography.",
            [],
            submitted_picture_indices=[1, 2],
        )


@pytest.mark.parametrize(
    "text",
    [
        "Use <Picture 5> from 0-3s, then switch to <Picture 6>.",
        "At 3 seconds switch to <Picture 6>.",
        "At 3 seconds, <Picture 6> activates.",
        "At 3 seconds, <Picture 6> takes over.",
        "<Picture 5> is shown from 0–3 seconds.",
        "<Picture 5> is used during 0-3 seconds.",
        "<Picture 6> switches at 3 seconds.",
        "Switch from <Picture 5> to <Picture 6> at 3 seconds.",
    ],
)
def test_rejects_time_addressable_picture_claims(text):
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    with pytest.raises(ValueError, match="Pictures condition the whole clip"):
        validate_no_time_addressable_pictures(text)


@pytest.mark.parametrize(
    "text",
    [
        (
            "<Picture 4> depicts the closed empty doorway, confirming that "
            "the opening state (0–3 s) contains only one person."
        ),
        (
            "<Picture 4> excludes Kai from this reference so that the first "
            "three seconds remain single-occupant."
        ),
        (
            "<Picture 4> establishes the doorway geometry. This Layout keeps "
            "Kai absent during the first three seconds."
        ),
    ],
)
def test_rejects_semantic_time_assignment_to_picture_or_layout(text):
    """A Picture cannot be evidence for a timed state, even in natural prose."""
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    with pytest.raises(ValueError, match="Pictures condition the whole clip"):
        validate_no_time_addressable_pictures(text)


def test_rejects_semantic_time_assignment_later_in_picture_binding_paragraph():
    """A long binding paragraph cannot hide a timed claim several sentences later."""
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    binding = (
        "<Picture 4> (lay_d08b2ee0c505, Layout): Composition and blocking "
        "reference. It locks the static wide framing, Mei's seated position, "
        "the table scale, eyeline height, and room geometry. It depicts the "
        "closed empty doorway behind Mei, confirming that the opening state "
        "(0–3 s) contains only one person. Kai is deliberately excluded from "
        "this reference so that the first three seconds remain single-occupant."
    )

    with pytest.raises(ValueError, match="Pictures condition the whole clip"):
        validate_no_time_addressable_pictures(binding)


@pytest.mark.parametrize(
    "text",
    [
        "<Picture 4> keeps Kai absent from 0-3 seconds.",
        "<Picture 4> ensures Kai is absent during 0-3 seconds.",
        "<Picture 4> confirms an empty doorway for 0-3 seconds.",
        (
            "<Picture 4> establishes the doorway geometry for the whole clip. "
            "This Layout guarantees Kai remains absent from 0-3 seconds."
        ),
        (
            "<Picture 4> establishes the doorway geometry for the whole clip. "
            "This reference establishes an empty-door state during the first "
            "three seconds."
        ),
    ],
)
def test_rejects_reference_subject_controlling_timed_state(text):
    """Reference subjects cannot keep/ensure/confirm a state for a time window."""
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    with pytest.raises(ValueError, match="Pictures condition the whole clip"):
        validate_no_time_addressable_pictures(text)


@pytest.mark.parametrize(
    "text",
    [
        "<Picture 4> controls the empty-door state for 0-3 seconds.",
        "<Picture 4> locks Kai out of frame during the first three seconds.",
        (
            "This scene uses <Picture 4>. This Layout defines an empty-door "
            "state for 0-3 seconds."
        ),
        "During the first three seconds, <Picture 4> keeps Kai absent.",
        "For 0-3 seconds, <Picture 4> depicts an empty doorway.",
    ],
)
def test_rejects_explicit_reference_timed_state_in_any_sentence_order(text):
    """Controller, state assignment, and time window may occur in any order."""
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    with pytest.raises(ValueError, match="Pictures condition the whole clip"):
        validate_no_time_addressable_pictures(text)


@pytest.mark.parametrize(
    "text",
    [
        "<Picture 4> shows the empty doorway until 3 seconds.",
        "This Layout represents Kai's absence before 3 seconds.",
        "<Picture 4> is the empty-door reference after 3 seconds.",
    ],
)
def test_rejects_any_explicit_reference_with_a_timing_window(text):
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    with pytest.raises(ValueError, match="Pictures condition the whole clip"):
        validate_no_time_addressable_pictures(text)


def test_allows_timed_pronoun_action_without_explicit_reference_controller():
    """Do not infer that an unrelated sentence's `it` means the Picture."""
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    validate_no_time_addressable_pictures(
        "<Picture 4> establishes the room. A wall clock appears behind Mei; "
        "it keeps ticking for 0-3 seconds."
    )


@pytest.mark.parametrize(
    "text",
    [
        (
            "<Picture 4> establishes the room while Kai enters during the "
            "first three seconds."
        ),
        (
            "<Picture 4> establishes the room; Kai enters during the first "
            "three seconds."
        ),
    ],
)
def test_allows_independent_timed_action_in_while_or_semicolon_clause(text):
    """Timing in an independent clause does not time-address the Picture."""
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    validate_no_time_addressable_pictures(text)


@pytest.mark.parametrize(
    "text",
    [
        "<Picture 4> shows the room while Kai waits until 3 seconds.",
        "<Picture 4> represents the room; Kai exits after 3 seconds.",
    ],
)
def test_allows_until_before_after_in_independent_action_clause(text):
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    validate_no_time_addressable_pictures(text)


def test_allows_action_timing_separate_from_picture_grounding():
    from app.core.h3.prompt import validate_no_time_addressable_pictures

    validate_no_time_addressable_pictures(
        "<Picture 5> establishes the doorway geometry. "
        "From 0-3 seconds, Chen walks through the doorway."
    )
    validate_no_time_addressable_pictures(
        "<Picture 5> establishes the doorway geometry. "
        "At 3 seconds, Chen walks through the doorway."
    )
    validate_no_time_addressable_pictures(
        "<Picture 5> establishes the doorway geometry and the empty doorway "
        "shown in the reference. From 0–3 seconds, Kai remains out of frame."
    )
    validate_no_time_addressable_pictures(
        "<Picture 5> establishes whole-clip doorway geometry. "
        "Action timeline: during the first three seconds, Kai remains out of frame."
    )


def _tail_transition_sections(detailed_description: str) -> PromptSections:
    return PromptSections(
        subject_definitions="<Picture 1> grounds the inherited vortex geometry.",
        summary="The inherited image state gives way to Mia.",
        retention_analysis="Retain coherent motion and Mia's identity.",
        detailed_description=detailed_description,
        overall_soundscape="A soft atmospheric swell.",
        non_diegetic_music="No music.",
    )


def _tail_transition_layouts() -> list[dict[str, object]]:
    return [
        {
            "asset_id": "lay_tail",
            "picture_index": 1,
            "origin_kind": "clip_tail_frame",
            "visible_transition_required": True,
        }
    ]


def test_allows_visible_tail_frame_handoff_in_first_action_interval():
    from app.core.h3.prompt import validate_tail_frame_transition_prompt

    validate_tail_frame_transition_prompt(
        _tail_transition_sections(
            "0–0.8 seconds: The inherited vortex continues, then dissolves open, "
            "revealing Mia's face. 0.8–5 seconds: Mia looks into camera."
        ),
        _tail_transition_layouts(),
    )


@pytest.mark.parametrize(
    "detailed_description",
    [
        "0–0.8 seconds: Hard cut to Mia's face. 0.8–5 seconds: She watches.",
        "0–0.8 seconds: Use the vortex as palette only. Mia watches.",
        "0–0.8 seconds: Use the vortex for style only. Mia watches.",
        "0–0.8 seconds: The inherited vortex must not manifest. Mia watches.",
        "0–0.8 seconds: The inherited vortex must not be visible. Mia watches.",
        "0.5–1.2 seconds: The inherited vortex dissolves to reveal Mia.",
        "0–0.8 seconds: Mia is already in close-up and looks into camera.",
    ],
)
def test_rejects_missing_or_neutralized_tail_frame_handoff(detailed_description):
    from app.core.h3.prompt import validate_tail_frame_transition_prompt

    with pytest.raises(ValueError, match="tail-frame transition"):
        validate_tail_frame_transition_prompt(
            _tail_transition_sections(detailed_description),
            _tail_transition_layouts(),
        )


@pytest.mark.parametrize(
    ("subject_definitions", "error"),
    [
        (
            "At 3 seconds switch to <Picture 1>.",
            "Pictures condition the whole clip",
        ),
        (
            "At 3 seconds, <Picture 1> activates.",
            "Pictures condition the whole clip",
        ),
        (
            "<Picture 1> is shown from 0-5 seconds.",
            "Pictures condition the whole clip",
        ),
        (
            "Switch from <Picture 1> to <Picture 2> at 3 seconds.",
            "Pictures condition the whole clip",
        ),
        (
            "The doorway remains coherent.",
            "missing selected Layout binding: <Picture 1>",
        ),
    ],
)
def test_pipeline_enforces_layout_picture_prompt_contract(
    subject_definitions,
    error,
):
    from app.core.schemas import JobRecord, JobStatus
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline

    prompt = compose_h3_prompt(
        PromptSections(
            subject_definitions=subject_definitions,
            summary="One coherent doorway scene.",
            retention_analysis="Retain geography.",
            detailed_description="From 0-5 seconds, Chen enters.",
            overall_soundscape="Room tone.",
            non_diegetic_music="No music.",
        )
    )
    job = JobRecord(
        id="job_layout_contract",
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=JobStatus.queued,
        name="layout contract",
        params={
            "prompt": prompt,
            "dialogue": [],
            "frames": 90,
            "image_keys": ["ref_0"],
            "layout_picture_indices": [1],
        },
        created_at="2026-08-25T00:00:00+00:00",
        updated_at="2026-08-25T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match=error):
        H3Ref2VaPipeline().build_prompt(
            job,
            uploaded_images={"ref_0": "layout.png"},
        )


def test_pipeline_rejects_picture_tag_beyond_actual_uploaded_image_order():
    from app.core.schemas import JobRecord, JobStatus
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline

    prompt = compose_h3_prompt(
        PromptSections(
            subject_definitions=(
                "<Picture 1> controls identity. <Picture 2> controls geography."
            ),
            summary="One coherent room scene.",
            retention_analysis="Retain identity and geography.",
            detailed_description="From 0-5 seconds, Chen enters.",
            overall_soundscape="Room tone.",
            non_diegetic_music="No music.",
        )
    )
    job = JobRecord(
        id="job_unsubmitted_picture",
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=JobStatus.queued,
        name="unsubmitted Picture",
        params={
            "prompt": prompt,
            "dialogue": [],
            "frames": 90,
            "image_keys": ["ref_0"],
            "layout_picture_indices": [],
        },
        created_at="2026-08-25T00:00:00+00:00",
        updated_at="2026-08-25T00:00:00+00:00",
    )

    with pytest.raises(ValueError, match=r"unsubmitted Picture.*<Picture 2>"):
        H3Ref2VaPipeline().build_prompt(
            job,
            uploaded_images={"ref_0": "actor.png"},
        )
