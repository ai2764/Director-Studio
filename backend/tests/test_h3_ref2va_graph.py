"""Unit tests for pure MiniMaxH3ReferenceToVideo graph fill."""

from __future__ import annotations

import json

import pytest

from app.core.jobs.store import create_job, save_input_file
from app.core.schemas import JobRecord, JobStatus
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline
from app.pipelines.h3_ref2va.schemas import H3Ref2VaJobResponse
from app.pipelines.h3_ref2va.workflow import (
    fill_ref2va_graph,
    load_base_prompt,
    map_history_outputs,
    minimal_graph,
)


VALID_PROMPT = (
    "subject_definitions:\nA\n"
    "summary:\nB\n"
    "retention_analysis:\nC\n"
    "detailed_description:\nD\n"
    "overall_soundscape:\nE\n"
    "non_diegetic_music:\nF"
)


def _base_job(**overrides):
    job = {
        "prompt": VALID_PROMPT,
        "dialogue": [],
        "images": ["p1.png", "p2.png"],
        "frames": 294,
        "seed": 7,
        "width": 864,
        "height": 480,
        "output_prefix": "director-studio/h3/sht1",
    }
    job.update(overrides)
    return job


def test_fill_injects_boundary_values_without_overriding_workflow_sampling():
    base = minimal_graph()
    base["14"]["inputs"] = {
        "scheduler": "workflow_scheduler",
        "steps": 27,
        "denoise": 0.75,
    }
    base["13"]["inputs"]["sampler_name"] = "workflow_sampler"

    g = fill_ref2va_graph(base, _base_job())
    h3 = g["10"]["inputs"]
    assert g["14"]["inputs"] == {
        "scheduler": "workflow_scheduler",
        "steps": 27,
        "denoise": 0.75,
    }
    assert g["13"]["inputs"]["sampler_name"] == "workflow_sampler"
    assert h3["ref_image_size"] == "match"
    assert h3["prompt"] == VALID_PROMPT
    assert h3["length"] == 294
    assert h3["width"] == 864
    assert h3["height"] == 480
    ref_keys = [k for k in h3 if k.startswith("ref_images.")]
    assert len(ref_keys) == 2
    assert h3["ref_images.ref_image_0"][0] in g
    assert g[h3["ref_images.ref_image_0"][0]]["class_type"] == "LoadImage"
    assert g[h3["ref_images.ref_image_0"][0]]["inputs"]["image"] == "p1.png"
    assert g[h3["ref_images.ref_image_1"][0]]["inputs"]["image"] == "p2.png"
    assert g["11"]["inputs"]["noise_seed"] == 7
    assert g["19"]["inputs"]["filename_prefix"] == "director-studio/h3/sht1"
    # pure ref2va — no I2V first/last frame sockets
    for node in g.values():
        assert node.get("class_type") != "MiniMaxH3ImageToVideo"
        inputs = node.get("inputs") or {}
        assert "ref_frame" not in inputs
        assert "last_frame" not in inputs


def test_fill_rejects_more_than_nine_images():
    with pytest.raises(ValueError, match="9 images"):
        fill_ref2va_graph(
            minimal_graph(),
            _base_job(images=[f"{i}.png" for i in range(10)]),
        )


@pytest.mark.parametrize("frames", [4, 6, 74, 363])
def test_fill_rejects_frames_outside_supported_grid(frames):
    with pytest.raises(ValueError, match="frame count"):
        fill_ref2va_graph(minimal_graph(), _base_job(frames=frames))


def test_fill_passes_custom_resolution_to_h3_node():
    graph = fill_ref2va_graph(
        minimal_graph(),
        _base_job(width=1280, height=704),
    )

    assert graph["10"]["inputs"]["width"] == 1280
    assert graph["10"]["inputs"]["height"] == 704


def test_fill_rejects_negative_seed():
    with pytest.raises(ValueError, match="seed"):
        fill_ref2va_graph(minimal_graph(), _base_job(seed=-1))


def test_fill_accepts_maximum_comfy_seed_without_stage_two_reservation():
    graph = fill_ref2va_graph(minimal_graph(), _base_job(seed=2**64 - 1))
    assert graph["11"]["inputs"]["noise_seed"] == 2**64 - 1


def test_fill_validates_prompt_when_dialogue_provided():
    with pytest.raises(ValueError, match="section order|dialogue"):
        fill_ref2va_graph(
            minimal_graph(),
            _base_job(prompt="not a valid prompt", dialogue=["hello"]),
        )


def test_fill_wires_audios_and_preserves_unicode():
    prompt = (
        "subject_definitions:\n<Audio 1> defines the speaker voice.\n"
        "summary:\nB\n你去地铁站？\n"
        "retention_analysis:\nC\n"
        "detailed_description:\nD\n"
        "overall_soundscape:\nE\n"
        "non_diegetic_music:\nF"
    )
    g = fill_ref2va_graph(
        minimal_graph(),
        _base_job(
            prompt=prompt,
            dialogue=["你去地铁站？"],
            images=["a.png"],
            audios=["voice.wav"],
        ),
    )
    h3 = g["10"]["inputs"]
    assert len([k for k in h3 if k.startswith("ref_images.")]) == 1
    assert len([k for k in h3 if k.startswith("ref_audios.")]) == 1
    assert "你去地铁站？" in json.dumps(g, ensure_ascii=False)


def test_pipeline_resolves_logical_audio_keys_to_uploaded_names():
    job = JobRecord(
        id="job_voice",
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=JobStatus.queued,
        name="voice test",
        params={
            "prompt": VALID_PROMPT.replace("A", "<Audio 1> defines Mia.", 1),
            "dialogue": [],
            "frames": 294,
            "image_keys": ["ref_1"],
            "audio_keys": ["voice_audio_1"],
        },
        created_at="2026-08-25T00:00:00+00:00",
        updated_at="2026-08-25T00:00:00+00:00",
    )

    graph, _seed = H3Ref2VaPipeline().build_prompt(
        job,
        uploaded_images={
            "ref_1": "uploaded-picture.png",
            "voice_audio_1": "uploaded-voice.wav",
        },
    )

    load_images = [
        node for node in graph.values() if node.get("class_type") == "LoadImage"
    ]
    load_audios = [
        node for node in graph.values() if node.get("class_type") == "LoadAudio"
    ]
    assert [node["inputs"]["image"] for node in load_images] == ["uploaded-picture.png"]
    assert [node["inputs"]["audio"] for node in load_audios] == ["uploaded-voice.wav"]


def test_official_workflow_rejects_private_native_audio_lock():
    with pytest.raises(ValueError, match="not part of the official"):
        fill_ref2va_graph(
            load_base_prompt(),
            _base_job(native_audio="shot01.wav"),
        )


def test_pipeline_registered():
    from app.pipelines import get_pipeline

    pipe = get_pipeline("h3_ref2va")
    assert pipe.id == "h3_ref2va"
    assert pipe.asset_kind == "productions"


def test_production_graph_is_official_single_stage_ref2va():
    graph = fill_ref2va_graph(load_base_prompt(), _base_job(frames=73))

    assert graph["127"]["inputs"]["unet_name"] == (
        "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    )
    assert graph["128"]["inputs"]["clip_name"] == (
        "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    )
    assert graph["136"]["inputs"]["width"] == 864
    assert graph["136"]["inputs"]["height"] == 480
    assert graph["123"]["inputs"]["sampler_name"] == "res_multistep"
    assert graph["124"]["inputs"] == {
        "model": ["127", 0],
        "scheduler": "simple",
        "steps": 20,
        "denoise": 1.0,
    }
    assert graph["129"]["inputs"]["noise_seed"] == 7
    assert graph["92"]["class_type"] == "SaveVideo"
    assert graph["92"]["inputs"]["filename_prefix"] == "director-studio/h3/sht1"
    assert len([n for n in graph.values() if n.get("class_type") == "SaveVideo"]) == 1
    assert not any(n.get("class_type") == "LoraLoaderModelOnly" for n in graph.values())


def test_history_maps_official_save_video_to_single_video_output():
    history = {
        "outputs": {
            "92": {"videos": [{"filename": "official.mp4", "type": "output"}]},
        }
    }

    mapped = map_history_outputs(history)

    assert mapped["video"].filename == "official.mp4"
    assert "video_raw" not in mapped


def test_pipeline_exposes_only_official_video_output_slot():
    pipe = H3Ref2VaPipeline()
    assert pipe.output_labels == {"video": "H3 Ref2AV Video"}
    assert pipe.meta_defaults()["output_slots"] == [
        {"key": "video", "label": "H3 Ref2AV Video"}
    ]
    defaults = pipe.meta_defaults()["defaults"]
    assert "steps" not in defaults
    assert "scheduler" not in defaults
    assert "sampler" not in defaults
    assert "ref_image_size" not in defaults


def test_h3_job_response_exposes_json_identity():
    job = JobRecord(
        id="job_json",
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=JobStatus.queued,
        name="json shot",
        params={
            "prompt": VALID_PROMPT,
            "dialogue": [],
            "frames": 141,
            "image_keys": ["ref_0"],
            "json_shot_id": "shot_001",
            "json_storyboard_revision": 3,
        },
        created_at="2026-08-26T00:00:00+00:00",
        updated_at="2026-08-26T00:00:00+00:00",
        project_id="prj_json",
    )
    response = H3Ref2VaJobResponse.from_job(job)
    assert response.project_id == "prj_json"
    assert response.json_shot_id == "shot_001"
    assert response.json_storyboard_revision == 3


def test_save_input_file_preserves_wav_suffix(tmp_path, monkeypatch):
    from app.config import settings

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_dir)
    job = create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="audio snapshot",
    )
    dest = save_input_file(job.id, "ref_audio_0", "voice.wav", b"RIFF-audio")
    assert dest.name == "ref_audio_0.wav"
    assert dest.suffix == ".wav"
    assert dest.read_bytes() == b"RIFF-audio"


def test_fill_requires_unique_boundary_nodes():
    graph = minimal_graph()
    graph["99"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}}

    with pytest.raises(RuntimeError, match="exactly one RandomNoise"):
        fill_ref2va_graph(graph, _base_job())
