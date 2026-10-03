from app.core.h3.prompt import validate_h3_prompt, compose_h3_prompt
from app.core.projects.models import PromptSections
import pytest


def test_reference_delimiter_recovery_keeps_unsubmitted_reference_checks():
    from app.core.h3.prompt import normalize_reference_tag_delimiters
    sections = PromptSections(
        subject_definitions="Picture 1 controls the room; Picture 2 controls the actor.",
        summary="A greeting.", retention_analysis="Keep <Picture 1> stable.",
        detailed_description="0-3 seconds: The actor waves.",
        overall_soundscape="Audio 1 controls delivery.", non_diegetic_music="None.",
    )
    normalized = normalize_reference_tag_delimiters(sections)
    assert normalized.retention_analysis == sections.retention_analysis
    with pytest.raises(ValueError, match="unsubmitted Picture"):
        validate_h3_prompt(normalized.as_ordered_text(), [], audio_count=1,
                           required_picture_indices=[1], submitted_picture_indices=[1])


def test_reports_dialogue_audio_and_picture_errors_together():
    sections = PromptSections(
        subject_definitions="Person.", summary="A greeting.", retention_analysis="Same face.",
        detailed_description="0-5 seconds: Waves.", overall_soundscape="Room tone.",
        non_diegetic_music="No music.",
    )
    with pytest.raises(ValueError) as error:
        validate_h3_prompt(sections.as_ordered_text(), ["Hello."], audio_count=1,
                           required_picture_indices=[1, 2], submitted_picture_indices=[1, 2])
    assert "dialogue does not match" in str(error.value)
    assert "Audio 1" in str(error.value)
    assert "Picture 1" in str(error.value)
    assert "Picture 2" in str(error.value)


def test_order_and_dialogue():
    sections = PromptSections(
        subject_definitions="A",
        summary="B",
        retention_analysis="C",
        detailed_description="<Subject 1> (S1): <d>[Chinese] 几点？</d>",
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


def test_allows_picture_references_inside_timed_action_descriptions():
    sections = PromptSections(
        subject_definitions=(
            "<Picture 1> defines the rocket design. "
            "<Picture 2> defines the launch composition."
        ),
        summary="A rocket launches into the sky.",
        retention_analysis="Retain both references throughout the clip.",
        detailed_description=(
            "0.0-1.5s: The rocket stands in the composition established by "
            "<Picture 2> as its engines ignite. 1.5-3.3s: The rocket lifts "
            "while retaining the silhouette defined by <Picture 1>."
        ),
        overall_soundscape="A rising engine roar.",
        non_diegetic_music="No music.",
    )

    validate_h3_prompt(
        compose_h3_prompt(sections),
        [],
        required_picture_indices=[1, 2],
        submitted_picture_indices=[1, 2],
    )


def test_requires_every_ordinary_picture_binding_with_accurate_error():
    from app.core.h3.prompt import validate_required_picture_bindings

    prompt = "<Picture 5> establishes the empty doorway."

    with pytest.raises(
        ValueError, match="missing required Picture binding: <Picture 6>"
    ):
        validate_required_picture_bindings(prompt, [5, 6])


def test_requires_every_selected_layout_picture_binding():
    from app.core.h3.prompt import validate_required_picture_bindings

    with pytest.raises(
        ValueError, match="missing selected Layout binding: <Picture 6>"
    ):
        validate_required_picture_bindings(
            "<Picture 5> establishes the empty doorway.",
            [5, 6],
            binding_label="selected Layout",
        )


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


def test_pipeline_enforces_required_layout_picture_binding():
    from app.core.schemas import JobRecord, JobStatus
    from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline

    prompt = compose_h3_prompt(
        PromptSections(
            subject_definitions="The doorway remains coherent.",
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

    with pytest.raises(
        ValueError,
        match="missing selected Layout binding: <Picture 1>",
    ):
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
