"""Scene multi-angle pipeline: H3-oriented defaults and graph hygiene."""

from __future__ import annotations

import json

from app.pipelines.scene.workflow import (
    DEFAULT_ANGLES,
    DEFAULT_PREPEND,
    NODE_LIGHTNING,
    NODE_NEGATIVE,
    NODE_PROMPT_LIST,
    NODE_SAMPLER,
    NODE_SAVE,
    VISUAL_WORKFLOW_FILENAME,
    WORKFLOW_FILENAME,
    build_scene_prompt,
    parse_angle_lines,
    visual_workflow_path,
    workflow_path,
)


def test_default_angles_cover_original_quality_view_set():
    lines = parse_angle_lines(DEFAULT_ANGLES)
    assert len(lines) == 7
    joined = "\n".join(lines).lower()
    assert "horizontal: 270" in lines[0]
    assert "horizontal: 180" in lines[1]
    assert "horizontal: 90" in lines[2]
    assert "horizontal: 315" in lines[3]
    assert "horizontal: 45" in lines[4]
    assert "bird's eye" in lines[5]
    assert "vertical: -30" in lines[6]


def test_build_scene_prompt_uses_quality_canvas_and_scene_latent():
    prompt, seed, used, stems = build_scene_prompt(
        scene_image_name="plate.png",
        scene_name="Audition_Room",
        seed=11,
        job_id="job_scene1",
    )
    assert seed == 11
    assert used == parse_angle_lines(DEFAULT_ANGLES)
    assert stems[0].startswith("Audition_Room_01_")
    assert "left_side" in stems[0]
    pl = prompt[NODE_PROMPT_LIST]["inputs"]
    assert DEFAULT_PREPEND in pl["prepend_text"]
    assert "only change" in pl["prepend_text"].lower() or "only change the camera" in pl["prepend_text"].lower()
    assert prompt[NODE_SAMPLER]["inputs"]["latent_image"] == ["105", 0]
    assert prompt["105"]["class_type"] == "VAEEncode"
    assert prompt["105"]["inputs"]["pixels"] == ["107", 0]
    assert prompt["107"]["class_type"] == "ImageScale"
    assert prompt["107"]["inputs"] == {
        "image": ["41", 0],
        "upscale_method": "lanczos",
        "width": 1728,
        "height": 960,
        "crop": "center",
    }
    assert prompt[NODE_NEGATIVE]["class_type"] == "ConditioningZeroOut"
    assert prompt[NODE_LIGHTNING]["inputs"]["strength_model"] == 1.0
    assert prompt[NODE_SAVE]["inputs"]["filename_prefix"].startswith("director-studio/")
    assert prompt[NODE_SAMPLER]["inputs"]["seed"] == 11


def test_explicit_prepend_overrides_default():
    prompt, _, _, _ = build_scene_prompt(
        scene_image_name="plate.png",
        prepend_text="night interior, tungsten practicals",
    )
    assert prompt[NODE_PROMPT_LIST]["inputs"]["prepend_text"] == "night interior, tungsten practicals"


def test_scene_api_and_visual_workflows_exist():
    assert workflow_path().name == WORKFLOW_FILENAME
    assert workflow_path().is_file()
    visual = json.loads(visual_workflow_path().read_text(encoding="utf-8"))
    assert visual_workflow_path().name == VISUAL_WORKFLOW_FILENAME
    types = {n["type"] for n in visual["nodes"]}
    assert "LoadImage" in types
    assert "TextEncodeQwenImageEditPlus" in types
    assert "ConditioningZeroOut" in types
    assert "ImageScale" in types
    assert "VAEEncode" in types
    assert "EmptySD3LatentImage" not in types
    assert "SaveImage" in types
    assert "MarkdownNote" in types
    groups = [g["title"] for g in visual["groups"]]
    assert any(t.startswith("1 ·") for t in groups)
    assert any("Output" in t for t in groups)
