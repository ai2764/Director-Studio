"""Video-context graph length, custom mapping, and media normalization."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.projects.video_context import VideoContextError
from app.pipelines.h3_ref2va.video_context import (
    attach_video_context,
    context_generation_frames,
    context_runtime_options,
    prepare_context_bytes,
)
from app.pipelines.h3_ref2va.workflow import fill_profile_graph, load_base_prompt
from app.workflow_profiles.h3 import (
    H3BoundaryMapping,
    H3ContextVideoInput,
    H3InputMapping,
    H3OutputSelection,
    H3ProfileStore,
    ResolvedH3Profile,
    contract_version_for,
)
from app.workflow_profiles.h3.validator import validate_h3_contract

VALID_PROMPT = (
    "subject_definitions:\nA\nsummary:\nB\nretention_analysis:\nC\n"
    "detailed_description:\nD\noverall_soundscape:\nE\nnon_diegetic_music:\nF"
)


def _shaped_graph() -> dict:
    return {
        "119": {"class_type": "VAELoader", "inputs": {"vae_name": "video.safetensors"}},
        "120": {"class_type": "VAELoader", "inputs": {"vae_name": "audio.safetensors"}},
        "125": {"class_type": "SamplerCustomAdvanced", "inputs": {"latent_image": ["136", 1], "guider": ["126", 0]}},
        "126": {"class_type": "BasicGuider", "inputs": {"conditioning": ["136", 0], "model": ["134", 0]}},
        "121": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["125", 0], "vae": ["120", 0]}},
        "122": {"class_type": "VAEDecode", "inputs": {"samples": ["125", 0], "vae": ["119", 0]}},
        "130": {"class_type": "CreateVideo", "inputs": {"images": ["122", 0], "audio": ["121", 0], "fps": 24.0}},
        "92": {"class_type": "SaveVideo", "inputs": {"video": ["130", 0], "filename_prefix": "video/H3"}},
        "136": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["128", 0],
                "vae": ["119", 0],
                "audio_vae": ["120", 0],
                "prompt": "old",
                "width": 864,
                "height": 480,
                "length": 56,
                "ref_audios.ref_audio_0": ["40", 0],
            },
        },
        "40": {"class_type": "LoadAudio", "inputs": {"audio": "song.wav"}},
    }


def _builtin(graph: dict) -> ResolvedH3Profile:
    return ResolvedH3Profile(
        profile_id="builtin-official-h3",
        workflow=graph,
        mapping=H3BoundaryMapping(
            inputs=H3InputMapping(
                h3_node_id="136",
                prompt_input="prompt",
                width_input="width",
                height_input="height",
                frames_input="length",
                picture_input_pattern="ref_images.ref_image_{index}",
                audio_input_pattern="ref_audios.ref_audio_{index}",
                seed_node_id=None,
                seed_input=None,
            ),
            output=H3OutputSelection(node_id="92"),
        ),
        workflow_sha256="a" * 64,
        source="builtin",
    )


def _job(**extra):
    payload = {
        "prompt": VALID_PROMPT,
        "images": ["picture.png"],
        "audios": ["song.wav"],
        "frames": 124,
        "width": 864,
        "height": 480,
        "seed": 7,
    }
    payload.update(extra)
    return payload


def _node(graph: dict, class_type: str) -> tuple[str, dict]:
    found = [
        (node_id, node)
        for node_id, node in graph.items()
        if node.get("class_type") == class_type
    ]
    assert len(found) == 1
    return found[0]


def test_context_generation_frames_and_limit():
    assert context_generation_frames(124, 22, max_frames=362) == 158
    with pytest.raises(ValueError, match="video_context_length_exceeds_limit"):
        context_generation_frames(362, 22, max_frames=362)


def test_attach_samples_overlap_then_delivers_original_length():
    source = load_base_prompt()
    original = json.loads(json.dumps(source))
    segment = {
        "core_start_s": 12.0,
        "core_end_s": 17.2,
        "submit_start_s": 11.5,
        "submit_end_s": 17.4,
    }
    graph = attach_video_context(
        source,
        uploaded_video="context_video.mp4",
        delivered_frames=124,
        context_frames=22,
        audio_context_frames=24,
        carry_audio=False,
    )

    assert source == original
    assert segment == {
        "core_start_s": 12.0,
        "core_end_s": 17.2,
        "submit_start_s": 11.5,
        "submit_end_s": 17.4,
    }
    assert graph["136"]["inputs"]["length"] == 158
    load_id, load = _node(graph, "LoadVideo")
    components_id, components = _node(graph, "GetVideoComponents")
    motion_id, motion = _node(graph, "MiniMaxH3MotionContext")
    trim_id, trim = _node(graph, "MiniMaxH3MotionContextTrim")
    batch_id, batch = _node(graph, "ImageFromBatch")
    audio_id, audio = _node(graph, "TrimAudioDuration")
    assert {load_id, components_id, motion_id, trim_id, batch_id, audio_id}.isdisjoint({"901", "903"})
    assert load["inputs"]["file"] == "context_video.mp4"
    assert components["inputs"]["video"] == [load_id, 0]
    assert motion["inputs"]["context_frames"] == [components_id, 0]
    assert motion["inputs"]["context_length"] == "22"
    assert "context_audio" not in motion["inputs"]
    assert "context_latent" not in motion["inputs"]
    assert graph["126"]["inputs"]["conditioning"] == [motion_id, 0]
    assert trim["inputs"]["trim_frames"] == [motion_id, 1]
    assert trim["inputs"]["images"][0] == "122"
    assert batch["inputs"]["length"] == 124
    assert 158 - 22 - batch["inputs"]["length"] == 12
    assert audio["inputs"]["duration"] == 124 / 24
    assert graph["130"]["inputs"]["images"] == [batch_id, 0]
    assert graph["130"]["inputs"]["audio"] == [audio_id, 0]
    assert graph["92"]["inputs"]["video"] == ["130", 0]


def test_carry_audio_wires_context_audio_without_removing_reference_audio():
    params = _job(
        context_video="context.mp4",
        video_context_source={"context_frames": 22, "audio_context_frames": 24, "carry_audio": True},
        music_segment={"submit_start_s": 1.0, "submit_end_s": 6.2},
    )
    segment = dict(params["music_segment"])
    graph = fill_profile_graph(_builtin(_shaped_graph()), params)

    assert params["music_segment"] == segment
    motion = _node(graph, "MiniMaxH3MotionContext")[1]
    assert motion["inputs"]["context_audio"][1] == 1
    assert motion["inputs"]["audio_vae"] == ["120", 0]
    assert motion["inputs"]["audio_context_length"] == 24
    assert graph["136"]["inputs"]["ref_audios.ref_audio_0"][0] in graph
    assert graph[graph["136"]["inputs"]["ref_audios.ref_audio_0"][0]]["class_type"] == "LoadAudio"
    assert graph[graph["136"]["inputs"]["ref_audios.ref_audio_0"][0]]["inputs"]["audio"] == "song.wav"


def test_graph_without_context_gains_no_nodes():
    source = _shaped_graph()
    graph = fill_profile_graph(_builtin(source), _job())
    assert set(graph) >= set(source)
    assert not any(node.get("class_type") == "LoadVideo" for node in graph.values())
    assert not any(node.get("class_type") == "MiniMaxH3MotionContext" for node in graph.values())


def test_missing_motion_role_blocks_the_graph():
    with pytest.raises(ValueError, match="exactly one MiniMaxH3ReferenceToVideo"):
        attach_video_context(
            {"1": {"class_type": "MiniMaxH3VideoExtendPatched", "inputs": {}}},
            uploaded_video="clip.mp4",
            delivered_frames=124,
            context_frames=22,
            audio_context_frames=24,
            carry_audio=False,
        )
    with pytest.raises(ValueError, match="BasicGuider"):
        attach_video_context(
            {
                "136": {
                    "class_type": "MiniMaxH3ReferenceToVideo",
                    "inputs": {"length": 124, "vae": ["119", 0]},
                }
            },
            uploaded_video="clip.mp4",
            delivered_frames=124,
            context_frames=22,
            audio_context_frames=24,
            carry_audio=False,
        )


def test_v2_mapping_hash_omits_an_absent_context_video():
    mapping = H3BoundaryMapping(
        inputs=H3InputMapping(
            h3_node_id="136",
            prompt_input="prompt",
            width_input="width",
            height_input="height",
            frames_input="length",
            picture_input_pattern="ref_images.ref_image_{index}",
            audio_input_pattern="ref_audios.ref_audio_{index}",
            seed_node_id="129",
            seed_input="noise_seed",
        ),
        output=H3OutputSelection(node_id="92"),
    )
    dumped = mapping.model_dump(mode="json")
    assert "context_video" not in dumped
    assert contract_version_for(mapping) == 2
    restored = H3BoundaryMapping.model_validate(dumped)
    assert H3ProfileStore.mapping_sha256(mapping) == H3ProfileStore.mapping_sha256(restored)

    video = mapping.model_copy(update={
        "context_video": H3ContextVideoInput(node_id="50", input_name="file"),
    })
    assert "context_video" in video.model_dump(mode="json")
    assert contract_version_for(video) == 3
    assert H3ProfileStore.mapping_sha256(video) != H3ProfileStore.mapping_sha256(mapping)


def test_custom_mapping_writes_only_the_uploaded_filename():
    graph = _shaped_graph()
    graph["50"] = {"class_type": "LoadVideo", "inputs": {"file": "leftover.mp4"}}
    graph["130"]["inputs"]["images"] = ["50", 0]
    profile = ResolvedH3Profile(
        profile_id="custom-video",
        workflow=graph,
        mapping=_builtin(graph).mapping.model_copy(update={
            "context_video": H3ContextVideoInput(node_id="50", input_name="file"),
        }),
        workflow_sha256="b" * 64,
        source="custom",
    )
    filled = fill_profile_graph(profile, _job(context_video="uploaded-context.mp4", frames=56))
    assert filled["50"]["inputs"]["file"] == "uploaded-context.mp4"
    assert filled["136"]["inputs"]["length"] == 56
    assert not any(node.get("class_type") == "MiniMaxH3MotionContext" for node in filled.values())
    assert graph["50"]["inputs"]["file"] == "leftover.mp4"


def test_mapped_motion_context_preserves_window_and_delivery_length():
    original = attach_video_context(_shaped_graph(), uploaded_video="old.mp4",
        delivered_frames=56, context_frames=39, audio_context_frames=24, carry_audio=True)
    load_id, _ = _node(original, "LoadVideo")
    profile = replace(_builtin(original), source="custom", mapping=_builtin(original).mapping.model_copy(update={
        "context_video": H3ContextVideoInput(node_id=load_id, input_name="file"),
    }))
    graph = fill_profile_graph(profile, _job(context_video="new.mp4",frames=124))
    assert graph["136"]["inputs"]["length"] == 175
    motion = _node(graph, "MiniMaxH3MotionContext")[1]
    assert motion["inputs"]["context_length"] == "39"
    assert motion["inputs"]["audio_context_length"] == 24
    assert "context_audio" in motion["inputs"]
    assert _node(graph, "ImageFromBatch")[1]["inputs"]["length"] == 124
    assert _node(graph, "TrimAudioDuration")[1]["inputs"]["duration"] == 124 / 24
    assert _node(original, "ImageFromBatch")[1]["inputs"]["length"] == 56


@pytest.mark.parametrize("carry_audio", [False, True])
def test_imported_vhs_video_conditions_samples_and_preserves_loader_settings(carry_audio):
    from types import SimpleNamespace
    from app.workflow_profiles.h3.inspector import inspect_h3_workflow

    original = attach_video_context(load_base_prompt(), uploaded_video="old.mp4",
        delivered_frames=56, context_frames=39, audio_context_frames=24, carry_audio=carry_audio)
    load_id, _ = _node(original, "LoadVideo")
    components_id, _ = _node(original, "GetVideoComponents")
    original[load_id] = {"class_type": "VHS_LoadVideo", "inputs": {
        "video": "", "force_rate": 24, "force_size": "Disabled", "frame_load_cap": 0,
        "skip_first_frames": 0, "select_every_nth": 1}}
    del original[components_id]
    motion = _node(original, "MiniMaxH3MotionContext")[1]
    motion["inputs"]["context_frames"] = [load_id, 0]
    if carry_audio:
        motion["inputs"]["context_audio"] = [load_id, 2]
    mapping = inspect_h3_workflow(original, output_node_id="92").mapping
    profile = replace(_builtin(original), source="custom", mapping=mapping)
    assert validate_h3_contract(original, mapping).valid
    options = context_runtime_options(SimpleNamespace(context_frames=None, carry_audio=None, audio_context_frames=None), profile)
    assert options == {"context_frames": 39, "audio_context_frames": 24 if carry_audio else 0, "carry_audio": carry_audio}
    graph = fill_profile_graph(profile, _job(context_video="new.mp4", frames=124))
    assert graph[load_id]["inputs"] == {**original[load_id]["inputs"], "video": "new.mp4"}
    assert graph["136"]["inputs"]["length"] == 175
    assert _node(graph, "ImageFromBatch")[1]["inputs"]["length"] == 124
    assert original[load_id]["inputs"]["video"] == ""


def test_motion_context_cannot_silently_use_an_unmapped_uploaded_file():
    from types import SimpleNamespace
    graph = attach_video_context(load_base_prompt(), uploaded_video="old.mp4",
        delivered_frames=56, context_frames=22, audio_context_frames=24, carry_audio=False)
    profile = replace(_builtin(graph), source="custom")
    with pytest.raises(ValueError, match="no context video file mapping"):
        context_runtime_options(SimpleNamespace(context_frames=None, carry_audio=None, audio_context_frames=None), profile)


def test_unmapped_motion_context_cannot_run_with_its_old_workflow_video():
    graph = attach_video_context(load_base_prompt(), uploaded_video="old.mp4",
        delivered_frames=56, context_frames=22, audio_context_frames=24, carry_audio=False)
    profile = replace(_builtin(graph), source="custom")
    verdict = validate_h3_contract(graph, profile.mapping)
    assert not verdict.valid
    with pytest.raises(ValueError, match="context video file mapping"):
        fill_profile_graph(profile, _job())


@pytest.mark.parametrize("loader_override", [
    {"frame_load_cap": 22}, {"skip_first_frames": 22}, {"select_every_nth": 2}, {"force_rate": 30},
    {"vae": ["119", 0]}, {"meta_batch": ["5000", 0]},
])
def test_vhs_continuation_rejects_loader_settings_that_change_the_source_tail(loader_override):
    graph = attach_video_context(load_base_prompt(), uploaded_video="old.mp4",
        delivered_frames=56, context_frames=22, audio_context_frames=24, carry_audio=False)
    load_id, _ = _node(graph, "LoadVideo")
    components_id, _ = _node(graph, "GetVideoComponents")
    graph[load_id] = {"class_type": "VHS_LoadVideo", "inputs": {
        "video": "old.mp4", "force_rate": 24, "frame_load_cap": 0,
        "skip_first_frames": 0, "select_every_nth": 1, **loader_override}}
    if "meta_batch" in loader_override:
        graph["5000"] = {"class_type": "VHS_BatchManager", "inputs": {"frames_per_batch": 22}}
    del graph[components_id]
    _node(graph, "MiniMaxH3MotionContext")[1]["inputs"]["context_frames"] = [load_id, 0]
    profile = replace(_builtin(graph), source="custom", mapping=_builtin(graph).mapping.model_copy(update={
        "context_video": H3ContextVideoInput(node_id=load_id, input_name="video")}))
    assert not validate_h3_contract(graph, profile.mapping).valid


@pytest.mark.parametrize("bypass", ["conditioning", "video_decode", "audio_decode"])
def test_imported_motion_context_rejects_bypassed_sampling_chain(bypass):
    graph = attach_video_context(load_base_prompt(), uploaded_video="old.mp4",
        delivered_frames=56, context_frames=22, audio_context_frames=24, carry_audio=False)
    h3_id, _ = _node(graph, "MiniMaxH3ReferenceToVideo")
    if bypass == "conditioning":
        _node(graph, "BasicGuider")[1]["inputs"]["conditioning"] = [h3_id, 0]
    else:
        decode_class = "VAEDecode" if bypass == "video_decode" else "VAEDecodeAudio"
        _node(graph, decode_class)[1]["inputs"]["samples"] = [h3_id, 1]
    load_id, _ = _node(graph, "LoadVideo")
    profile = replace(_builtin(graph), source="custom", mapping=_builtin(graph).mapping.model_copy(update={
        "context_video": H3ContextVideoInput(node_id=load_id, input_name="file")}))
    verdict = validate_h3_contract(graph, profile.mapping)
    assert not verdict.valid
    with pytest.raises(ValueError, match="continuation sampling chain"):
        fill_profile_graph(profile, _job(context_video="actual.mp4"))


def test_custom_without_a_video_or_mapping_is_rejected():
    graph = _shaped_graph()
    graph["50"] = {"class_type": "LoadVideo", "inputs": {"file": "leftover.mp4"}}
    mapped = ResolvedH3Profile(
        profile_id="custom-video",
        workflow=graph,
        mapping=_builtin(graph).mapping.model_copy(update={
            "context_video": H3ContextVideoInput(node_id="50", input_name="file"),
        }),
        workflow_sha256="b" * 64,
        source="custom",
    )
    with pytest.raises(ValueError, match="context video source is required"):
        fill_profile_graph(mapped, _job(frames=56))
    assert graph["50"]["inputs"]["file"] == "leftover.mp4"

    with pytest.raises(ValueError, match="no context video file mapping"):
        fill_profile_graph(replace(_builtin(graph), source="custom"), _job(context_video="clip.mp4"))


def test_context_video_mapping_must_be_a_reachable_file_input():
    graph = _shaped_graph()
    graph["50"] = {"class_type": "LoadVideo", "inputs": {"file": "sample.mp4"}}
    disconnected = _builtin(graph).mapping.model_copy(update={
        "context_video": H3ContextVideoInput(node_id="50", input_name="file"),
    })
    report = validate_h3_contract(graph, disconnected)
    assert report.valid is False
    assert any(issue.code == "unreachable_mapping" for issue in report.issues)

    graph["51"] = {"class_type": "LoadVideo", "inputs": {"file": ["50", 0]}}
    graph["130"]["inputs"]["images"] = ["51", 0]
    linked = _builtin(graph).mapping.model_copy(update={
        "context_video": H3ContextVideoInput(node_id="51", input_name="file"),
    })
    linked_report = validate_h3_contract(graph, linked)
    assert any(issue.code == "invalid_context_video" for issue in linked_report.issues)


def test_prepare_copies_matching_media_and_normalizes_the_rest(monkeypatch):
    calls: list[dict] = []

    def fake_transcode(data, **kwargs):
        calls.append({"data": data, **kwargs})
        return b"normalized-bytes"

    monkeypatch.setattr(
        "app.pipelines.h3_ref2va.video_context._transcode_context_video",
        fake_transcode,
    )
    copied, conversion = prepare_context_bytes(
        b"same-bytes",
        media_width=864, media_height=480, media_fps=24, media_duration_s=2,
        has_audio=True, width=864, height=480, context_frames=22, carry_audio=False,
    )
    assert copied == b"same-bytes"
    assert conversion is None
    assert calls == []

    larger, conversion = prepare_context_bytes(
        b"large",
        media_width=1728, media_height=960, media_fps=24, media_duration_s=2,
        has_audio=True, width=864, height=480, context_frames=22, carry_audio=False,
    )
    assert larger == b"normalized-bytes"
    assert conversion["from_width"] == 1728
    assert conversion["to_fps"] == 24

    faster, conversion = prepare_context_bytes(
        b"thirty",
        media_width=864, media_height=480, media_fps=30, media_duration_s=2,
        has_audio=False, width=864, height=480, context_frames=22, carry_audio=False,
    )
    assert faster == b"normalized-bytes"
    assert conversion["from_fps"] == 30
    assert calls[-1]["keep_audio"] is False

    with pytest.raises(VideoContextError, match="aspect ratio"):
        prepare_context_bytes(
            b"square",
            media_width=864, media_height=864, media_fps=24, media_duration_s=2,
            has_audio=True, width=864, height=480, context_frames=22, carry_audio=False,
        )
    with pytest.raises(VideoContextError, match="shorter than the selected window"):
        prepare_context_bytes(
            b"short",
            media_width=864, media_height=480, media_fps=24, media_duration_s=0.2,
            has_audio=True, width=864, height=480, context_frames=22, carry_audio=False,
        )
    with pytest.raises(VideoContextError, match="no audio"):
        prepare_context_bytes(
            b"silent",
            media_width=864, media_height=480, media_fps=24, media_duration_s=2,
            has_audio=False, width=864, height=480, context_frames=22, carry_audio=True,
        )
    assert len(calls) == 2


def test_window_is_validated_at_the_normalized_frame_rate():
    with pytest.raises(VideoContextError, match="shorter than the selected window"):
        prepare_context_bytes(
            b"fast-but-short", media_width=864, media_height=480,
            media_fps=30, media_duration_s=0.8, has_audio=False,
            width=864, height=480, context_frames=22, carry_audio=False,
        )


def test_fast_template_uses_verified_sampling_and_leaves_the_official_graph():
    root = Path(__file__).parents[1] / "workflows"
    official = json.loads((root / "h3_ref2va.api.json").read_text(encoding="utf-8"))
    fast = json.loads((root / "h3_ref2va_fast4.api.json").read_text(encoding="utf-8"))
    assert any(node.get("class_type") == "H3SparseAttention" for node in official.values())
    assert any(
        node.get("class_type") == "BasicScheduler" and node["inputs"]["steps"] == 8
        for node in official.values()
    )
    assert not any("Singularity" in json.dumps(node) for node in official.values())
    scheduler = next(node for node in fast.values() if node.get("class_type") == "BasicScheduler")
    unet = next(node for node in fast.values() if node.get("class_type") == "UNETLoader")
    lora = next(node for node in fast.values() if node.get("class_type") == "LoraLoaderModelOnly")
    attention = next(node for node in fast.values() if node.get("class_type") == "ModelAttentionBackend")
    assert scheduler["inputs"]["steps"] == 4
    assert scheduler["inputs"]["scheduler"] == "beta"
    assert unet["inputs"]["unet_name"] == "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors"
    assert lora["inputs"]["lora_name"] == "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
    assert attention["inputs"]["attention"] == "comfy kitchen attention"
    assert not any(node.get("class_type") == "MiniMaxH3MotionContextSaveLatent" for node in fast.values())
    assert not any(node.get("class_type") == "LoadImage" for node in fast.values())
    assert not any(node.get("class_type") == "H3SparseAttention" for node in fast.values())
