"""Prop prep pipeline: one uploaded photo → isolated H3 master still."""

from __future__ import annotations

import json

import pytest

from app.core.schemas import ComfyImageRef, JobRecord, JobStatus
from app.pipelines.prop.workflow import (
    NODE_DESCRIPTION,
    NODE_EMPTY_LATENT,
    NODE_NEGATIVE,
    NODE_LIGHTNING,
    NODE_MULTI_ANGLE,
    NODE_REF_IMAGE_1,
    NODE_SAMPLER,
    NODE_SAVE,
    NODE_SCENE_ENCODE,
    PROP_HEIGHT,
    PROP_WIDTH,
    build_prop_prompt,
    compile_prop_instruction,
    map_history_outputs,
)


def test_prop_pipeline_registered():
    from app.pipelines import get_pipeline
    from app.pipelines.prop.workflow import visual_workflow_path, workflow_path

    pipe = get_pipeline("prop")
    assert pipe.id == "prop"
    assert pipe.asset_kind == "props"
    assert pipe.output_labels["master"]
    assert pipe.library_input_keys() == ["prop"]
    assert workflow_path().is_file()
    visual = json.loads(visual_workflow_path().read_text(encoding="utf-8"))
    types = {n["type"] for n in visual["nodes"]}
    assert "LoadImage" in types
    assert "TextEncodeQwenImageEditPlus" in types
    assert "EmptySD3LatentImage" in types
    assert "SaveImage" in types
    assert "MarkdownNote" in types


def test_compile_prop_instruction_requests_one_isolated_view_for_the_final_sheet():
    text = compile_prop_instruction(name="Deodorant can", notes="silver cap, red logo")
    assert "Deodorant can" in text
    assert "silver cap" in text
    lower = text.lower()
    assert "isolated product-reference image" in lower
    assert "do not create a sheet" in lower
    assert "neutral" in lower or "studio" in lower
    assert "same object" in lower
    assert "different version" in lower


def test_build_prop_prompt_uses_empty_latent_and_square_canvas():
    prompt, seed = build_prop_prompt(
        image_name="prop.png",
        name="Folding knife",
        notes="black handle",
        seed=7,
        output_prefix="director-studio/job_prop/master",
        job_id="job_prop",
    )
    assert seed == 7
    pos = prompt[NODE_DESCRIPTION]["inputs"]["prompt"]
    assert "Folding knife" in pos
    assert "hero three-quarter view" in pos.lower()
    assert "front view" in prompt["20"]["inputs"]["prompt"].lower()
    assert "side or rear view" in prompt["21"]["inputs"]["prompt"].lower()
    assert prompt[NODE_REF_IMAGE_1]["inputs"]["image"] == "prop.png"
    assert prompt[NODE_SAMPLER]["inputs"]["latent_image"] == [NODE_EMPTY_LATENT, 0]
    assert prompt[NODE_SAMPLER]["inputs"]["denoise"] == pytest.approx(1.0)
    assert prompt[NODE_MULTI_ANGLE]["inputs"]["lora_name"] == "qwen-image-edit-2511-multiple-angles-lora.safetensors"
    assert prompt[NODE_MULTI_ANGLE]["inputs"]["strength_model"] == pytest.approx(1.0)
    assert prompt[NODE_LIGHTNING]["inputs"]["lora_name"] == "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
    assert prompt[NODE_LIGHTNING]["inputs"]["strength_model"] == pytest.approx(1.0)
    assert prompt[NODE_SAMPLER]["inputs"]["model"] == ["5", 0]
    assert prompt[NODE_SAMPLER]["inputs"]["steps"] == 4
    assert prompt[NODE_SAMPLER]["inputs"]["cfg"] == pytest.approx(1.0)
    for sampler_id in (NODE_SAMPLER, "23", "24"):
        assert prompt[sampler_id]["inputs"]["steps"] == 4
        assert prompt[sampler_id]["inputs"]["cfg"] == pytest.approx(1.0)
    assert prompt[NODE_EMPTY_LATENT]["inputs"]["width"] == PROP_WIDTH
    assert prompt[NODE_EMPTY_LATENT]["inputs"]["height"] == PROP_HEIGHT
    assert NODE_SCENE_ENCODE not in prompt
    assert prompt[NODE_SAVE]["inputs"]["images"] == ["31", 0]
    assert prompt[NODE_SAVE]["inputs"]["filename_prefix"].endswith("master")
    neg = prompt[NODE_NEGATIVE]["inputs"]["prompt"].lower()
    assert "inconsistent object versions" in neg


def test_build_prop_prompt_requires_image():
    with pytest.raises(ValueError, match="prop"):
        build_prop_prompt(image_name="", name="Can")


def test_map_history_outputs_uses_prop_reference_sheet_key():
    history = {
        "outputs": {
            NODE_SAVE: {
                "images": [
                    {"filename": "master_00001_.png", "subfolder": "", "type": "output"}
                ]
            }
        }
    }
    mapped = map_history_outputs(history)
    assert set(mapped) == {"master"}
    assert isinstance(mapped["master"], ComfyImageRef)
    assert mapped["master"].filename == "master_00001_.png"


def test_generate_prop_requires_name_and_image(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(settings, "library_root", tmp_path / "library")
    (tmp_path / "projects").mkdir()
    (tmp_path / "jobs").mkdir()
    (tmp_path / "library").mkdir()

    client = TestClient(create_app())
    missing_image = client.post("/api/props/generate", data={"name": "Can"})
    assert missing_image.status_code == 400
    assert "prop_image" in missing_image.text

    missing_name = client.post(
        "/api/props/generate",
        data={"name": "  "},
        files={"prop_image": ("can.png", b"fake-png-bytes" * 40, "image/png")},
    )
    assert missing_name.status_code == 400


def test_pipeline_build_prompt_reads_uploaded_prop():
    from app.pipelines import get_pipeline

    pipe = get_pipeline("prop")
    job = JobRecord(
        id="job_p1",
        pipeline_id="prop",
        asset_kind="props",
        status=JobStatus.queued,
        name="Spray can",
        notes="blue body",
        params={},
        seed=3,
        created_at="t",
        updated_at="t",
    )
    graph, seed = pipe.build_prompt(job, uploaded_images={"prop": "upload.png"})
    assert seed == 3
    assert graph[NODE_REF_IMAGE_1]["inputs"]["image"] == "upload.png"
    assert "Spray can" in graph[NODE_DESCRIPTION]["inputs"]["prompt"]
