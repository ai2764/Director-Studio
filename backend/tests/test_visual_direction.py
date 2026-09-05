from __future__ import annotations

import json
from io import BytesIO

import pytest
from PIL import Image

from app.agents.director.visual_direction import (
    _analysis_prompt,
    analyze_ref_frame,
    compile_visual_prompt,
    parse_visual_brief,
)
from app.core.projects.models import Shot


def _brief_payload() -> dict:
    return {
        "shot_type": "wide shot",
        "camera": "eye level, slight left perspective",
        "scene_lock": [
            "preserve the black casting-room door",
            "preserve the bench and hallway perspective",
        ],
        "characters": [
            {
                "reference_image": "Image2",
                "frame_position": "left third beside the black door",
                "body_angle": "right-facing three-quarter",
                "head_direction": "looking at the CASTING TODAY sign",
                "pose": "walking with left foot advancing",
                "interaction": "none",
                "identity_lock": ["same face", "short tousled black hair"],
                "wardrobe_lock": [
                    "dark navy cropped work jacket",
                    "cream crew-neck T-shirt",
                    "khaki trousers",
                    "off-white sneakers",
                ],
            }
        ],
        "forbidden": ["frontal passport pose", "duplicate door handles"],
    }


def _two_actor_brief_payload() -> dict:
    payload = _brief_payload()
    second = dict(payload["characters"][0])
    second.update(
        {
            "reference_image": "Image3",
            "frame_position": "right side across the table",
            "body_angle": "left-facing three-quarter",
            "head_direction": "looking at the woman from Image2",
            "pose": "seated upright",
            "identity_lock": ["same male face", "short neat black hair"],
            "wardrobe_lock": ["black suit jacket", "black shirt", "black trousers"],
        }
    )
    payload["characters"] = [payload["characters"][0], second]
    return payload


def _shot() -> Shot:
    return Shot(
        id="sht_visual",
        project_id="prj_visual",
        scene_id="sc01",
        title="Corridor walk-in",
        script_beat="The actor walks toward the door and reads the sign.",
        shot_type="medium wide",
        camera_angle="eye level from the corridor's left side",
        camera_motion="slow push from wide to medium",
        composition="actor on the left third, door on the right third",
        duration_s=6.0,
    )


def test_analysis_prompt_uses_planned_camera_brief_as_authoritative_direction():
    prompt = _analysis_prompt(
        _shot(),
        ["Image1 = scene corridor master", "Image2 = actor qian master"],
    )

    assert "PLANNED SHOT TYPE: medium wide" in prompt
    assert "PLANNED CAMERA ANGLE: eye level from the corridor's left side" in prompt
    assert "PLANNED CAMERA MOTION: slow push from wide to medium" in prompt
    assert "PLANNED COMPOSITION: actor on the left third, door on the right third" in prompt
    assert "Treat this planned camera brief as authoritative" in prompt


def test_parse_visual_brief_accepts_fenced_json():
    payload = json.dumps(_brief_payload())
    brief = parse_visual_brief(f"```json\n{payload}\n```")
    assert brief.shot_type == "wide shot"
    assert brief.characters[0].reference_image == "Image2"
    assert brief.characters[0].wardrobe_lock[-1] == "off-white sneakers"


def test_parse_visual_brief_rejects_missing_wardrobe_lock():
    payload = _brief_payload()
    payload["characters"][0]["wardrobe_lock"] = []
    with pytest.raises(ValueError, match="wardrobe_lock"):
        parse_visual_brief(json.dumps(payload))


def test_compile_visual_prompt_is_focused_and_contains_visual_constraints():
    brief = parse_visual_brief(json.dumps(_brief_payload()))
    prompt = compile_visual_prompt(
        _shot(),
        brief,
        captions=[
            "Image1 = scene corridor master",
            "Image2 = actor qian master",
        ],
    )
    assert "Corridor walk-in" in prompt
    assert "The actor walks toward the door" in prompt
    assert "eye level, slight left perspective" in prompt
    assert "left third beside the black door" in prompt
    assert "dark navy cropped work jacket" in prompt
    assert "duplicate door handles" in prompt
    assert "Image2 = actor qian master" in prompt
    assert "STORY / SCRIPT CONTEXT" not in prompt
    assert "AUDITIONS CANCELLED" not in prompt


def test_compile_visual_prompt_returns_agent_generation_prompt_verbatim():
    payload = _brief_payload()
    payload["generation_prompt"] = (
        "A rain-dark cafe table fills the foreground. Use Image1 for the exact "
        "cafe architecture and lighting. Seat the person from Image2 at frame left, "
        "preserving her face and navy jacket, as she watches the opposite chair."
    )
    brief = parse_visual_brief(json.dumps(payload))

    prompt = compile_visual_prompt(
        _shot(),
        brief,
        captions=[
            "Image1 SCENE rainy cafe",
            "Image2 ACTOR qian master",
        ],
    )

    assert prompt == payload["generation_prompt"]


def test_compile_visual_prompt_falls_back_when_agent_prompt_omits_reference():
    payload = _brief_payload()
    payload["generation_prompt"] = (
        "Use Image1 for a wide corridor shot with the actor beside the door."
    )
    brief = parse_visual_brief(json.dumps(payload))

    prompt = compile_visual_prompt(
        _shot(),
        brief,
        captions=[
            "Image1 SCENE corridor master",
            "Image2 ACTOR qian master",
        ],
    )

    assert prompt != payload["generation_prompt"]
    assert "REFERENCE MAP" in prompt
    assert "Image2 ACTOR qian master" in prompt


def test_compile_visual_prompt_rejects_character_reference_to_scene_image():
    brief = parse_visual_brief(json.dumps(_brief_payload()))
    brief.characters[0].reference_image = "Image1"
    with pytest.raises(ValueError, match="CHARACTER reference"):
        compile_visual_prompt(
            _shot(),
            brief,
            captions=[
                "Image1 SCENE environment — composite character INTO this set",
                "Image2 CHARACTER qian master",
            ],
        )


def test_compile_visual_prompt_requires_one_distinct_character_per_actor_image():
    brief = parse_visual_brief(json.dumps(_brief_payload()))
    with pytest.raises(ValueError, match="every attached CHARACTER reference"):
        compile_visual_prompt(
            _shot(),
            brief,
            captions=[
                "Image1 SCENE environment — composite character INTO this set",
                "Image2 CHARACTER qian master",
                "Image3 CHARACTER mia master",
            ],
        )


def test_compile_visual_prompt_rejects_unknown_character_reference():
    brief = parse_visual_brief(json.dumps(_brief_payload()))
    brief.characters[0].reference_image = "Image9"
    with pytest.raises(ValueError, match="attached CHARACTER reference"):
        compile_visual_prompt(
            _shot(),
            brief,
            captions=[
                "Image1 SCENE corridor master",
                "Image2 CHARACTER qian master",
            ],
        )


def _image_bytes(width: int, height: int, color: str) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_analyze_ref_frame_sends_ordered_1024px_images_and_returns_trace():
    calls: list[dict] = []

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append(
                {"model": model, "prompt": prompt, **kwargs}
            )
            return json.dumps(_brief_payload())

    result = await analyze_ref_frame(
        _shot(),
        images={
            "ref_0": ("scene.png", _image_bytes(1600, 900, "navy")),
            "ref_1": ("actor.png", _image_bytes(800, 1200, "beige")),
        },
        captions=[
            "Image1 SCENE corridor master",
            "Image2 ACTOR qian master",
        ],
        model="qwen3.6:27b",
        ollama=_VisionClient(),
    )

    assert len(calls) == 1
    assert calls[0]["model"] == "qwen3.6:27b"
    assert calls[0]["require_vision"] is True
    assert calls[0]["format"] == result.brief.model_json_schema()
    assert len(calls[0]["images"]) == 2
    assert calls[0]["images"] == result.vision_images_b64
    assert result.selected_refs == [
        {"image": "Image1", "file_key": "ref_0", "caption": "Image1 SCENE corridor master"},
        {"image": "Image2", "file_key": "ref_1", "caption": "Image2 ACTOR qian master"},
    ]
    assert result.vision_input_captions[-1] == "Image2 ACTOR qian master"
    assert "dark navy cropped work jacket" in result.compiled_prompt

    decoded = __import__("base64").b64decode(calls[0]["images"][0])
    with Image.open(BytesIO(decoded)) as image:
        assert max(image.size) == 1024


@pytest.mark.asyncio
async def test_analyze_scene_only_pack_accepts_empty_characters():
    payload = _brief_payload()
    payload["characters"] = []
    payload["generation_prompt"] = (
        "Use Image1 for the exact empty corridor architecture and lighting."
    )
    calls: list[str] = []

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append(prompt)
            return json.dumps(payload)

    result = await analyze_ref_frame(
        _shot(),
        images={"ref_0": ("scene.png", _image_bytes(640, 384, "navy"))},
        captions=["Image1 SCENE empty corridor"],
        model="qwen-test",
        ollama=_VisionClient(),
    )

    assert result.brief.characters == []
    assert result.compiled_prompt == payload["generation_prompt"]
    assert '"characters":[]' in calls[0]


@pytest.mark.asyncio
async def test_analyze_actor_pack_still_requires_matching_character_entry():
    payload = _brief_payload()
    payload["characters"] = []
    payload["generation_prompt"] = "Preserve the person from Image1."

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            return json.dumps(payload)

    with pytest.raises(ValueError, match="every attached CHARACTER reference"):
        await analyze_ref_frame(
            _shot(),
            images={"ref_0": ("actor.png", _image_bytes(384, 640, "beige"))},
            captions=["Image1 ACTOR Lu master"],
            model="qwen-test",
            ollama=_VisionClient(),
        )


@pytest.mark.asyncio
async def test_analyze_ref_frame_loads_director_skill_before_vision_call(
    tmp_path, monkeypatch
):
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: director\ndescription: Use when directing H3 Ref2AV.\n---\n\n"
        "DIRECTOR CONTRACT VISION_RULES\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DS_DIRECTOR_SKILL_PATH", str(skill_path))
    calls: list[dict] = []

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append({"model": model, "prompt": prompt, **kwargs})
            return json.dumps(_brief_payload())

    await analyze_ref_frame(
        _shot(),
        images={
            "ref_0": ("scene.png", _image_bytes(640, 384, "navy")),
            "ref_1": ("actor.png", _image_bytes(384, 640, "beige")),
        },
        captions=["Image1 SCENE corridor", "Image2 ACTOR qian"],
        model="qwen-test",
        ollama=_VisionClient(),
    )

    assert "DIRECTOR CONTRACT VISION_RULES" in calls[0]["prompt"]
    assert calls[0]["prompt"].index("DIRECTOR CONTRACT") < calls[0]["prompt"].index(
        "You are the visual director"
    )


@pytest.mark.asyncio
async def test_analyze_ref_frame_uses_failed_result_for_review_only():
    calls: list[dict] = []
    payload = _brief_payload()
    payload["generation_prompt"] = (
        "Use Image1 for the rainy cafe. Preserve the person from Image2 exactly, "
        "with her fingertips visibly hovering above the folder."
    )

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append({"model": model, "prompt": prompt, **kwargs})
            return json.dumps(payload)

    result = await analyze_ref_frame(
        _shot(),
        images={
            "ref_0": ("scene.png", _image_bytes(640, 384, "navy")),
            "ref_1": ("actor.png", _image_bytes(384, 640, "beige")),
        },
        captions=["Image1 SCENE rainy cafe", "Image2 ACTOR qian master"],
        review_image=("failed.png", _image_bytes(640, 384, "red")),
        feedback="The fingertips touch the folder; leave a visible air gap.",
        model="qwen3.6:27b",
        ollama=_VisionClient(),
    )

    assert len(calls[0]["images"]) == 3
    assert "PreviousResult" in calls[0]["prompt"]
    assert "visible air gap" in calls[0]["prompt"]
    assert len(result.selected_refs) == 2
    assert result.vision_input_captions == [
        "Image1 SCENE rainy cafe",
        "Image2 ACTOR qian master",
    ]
    assert result.vision_images_b64 == calls[0]["images"][:2]
    assert result.review_image_used is True
    assert result.review_feedback == "The fingertips touch the folder; leave a visible air gap."


@pytest.mark.asyncio
async def test_analyze_ref_frame_repairs_missing_image_token_without_legacy_fallback():
    payload = _two_actor_brief_payload()
    payload["generation_prompt"] = (
        "Use Image1 for the rainy cafe. Seat the woman from Image2 at frame left. "
        "Seat the man opposite her at frame right, looking toward her."
    )
    repaired = (
        "Use Image1 for the rainy cafe. Seat the woman from Image2 at frame left. "
        "Seat the man from Image3 opposite her at frame right, looking toward her."
    )
    responses = [json.dumps(payload), json.dumps({"generation_prompt": repaired})]
    calls: list[dict] = []

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append({"model": model, "prompt": prompt, **kwargs})
            return responses.pop(0)

    result = await analyze_ref_frame(
        _shot(),
        images={
            "ref_0": ("scene.png", _image_bytes(640, 384, "navy")),
            "ref_1": ("woman.png", _image_bytes(384, 640, "beige")),
            "ref_2": ("man.png", _image_bytes(384, 640, "black")),
        },
        captions=[
            "Image1 SCENE rainy cafe",
            "Image2 ACTOR Lin Ya",
            "Image3 ACTOR Chen Mo",
        ],
        model="qwen3.6:27b",
        ollama=_VisionClient(),
    )

    assert len(calls) == 2
    assert "Image3" in calls[1]["prompt"]
    assert result.compiled_prompt == repaired
    assert result.brief.generation_prompt == repaired


@pytest.mark.asyncio
async def test_analyze_ref_frame_falls_back_when_prompt_repair_still_omits_reference():
    payload = _two_actor_brief_payload()
    payload["generation_prompt"] = "Use Image1 for the cafe and seat Image2 at the table."
    responses = [
        json.dumps(payload),
        json.dumps(
            {"generation_prompt": "Use Image1 for the cafe and seat Image2 at the table."}
        ),
    ]
    calls: list[dict] = []

    class _VisionClient:
        async def chat(self, model, prompt, **kwargs):
            calls.append({"model": model, "prompt": prompt, **kwargs})
            return responses.pop(0)

    result = await analyze_ref_frame(
        _shot(),
        images={
            "ref_0": ("scene.png", _image_bytes(640, 384, "navy")),
            "ref_1": ("woman.png", _image_bytes(384, 640, "beige")),
            "ref_2": ("man.png", _image_bytes(384, 640, "black")),
        },
        captions=[
            "Image1 SCENE rainy cafe",
            "Image2 ACTOR Lin Ya",
            "Image3 ACTOR Chen Mo",
        ],
        model="qwen3.6:27b",
        ollama=_VisionClient(),
    )

    assert len(calls) == 2
    assert "REFERENCE MAP" in result.compiled_prompt
    assert "Image3 ACTOR Chen Mo" in result.compiled_prompt


@pytest.mark.asyncio
async def test_analyze_ref_frame_rejects_invalid_model_output():
    class _BadVisionClient:
        async def chat(self, *args, **kwargs):
            return "I cannot produce JSON"

    with pytest.raises(ValueError, match="JSON object"):
        await analyze_ref_frame(
            _shot(),
            images={
                "ref_0": ("scene.png", _image_bytes(64, 64, "white")),
                "ref_1": ("actor.png", _image_bytes(64, 64, "black")),
            },
            captions=["Image1 SCENE", "Image2 ACTOR"],
            model="qwen3.6:27b",
            ollama=_BadVisionClient(),
        )
