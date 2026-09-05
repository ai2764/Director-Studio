"""Unit tests for ref_frame layout pipeline."""

from __future__ import annotations

import pytest

from app.core.schemas import JobRecord, JobStatus, LibraryAsset, OutputSlot
from app.pipelines.ref_frame.workflow import (
    DEFAULT_CFG,
    DEFAULT_SHIFT,
    DEFAULT_STEPS,
    LAYOUT_HEIGHT,
    LAYOUT_WIDTH,
    NODE_DESCRIPTION,
    NODE_LIGHTNING_LORA,
    NODE_MODEL_SAMPLING,
    NODE_REF_IMAGE_1,
    NODE_SAMPLER,
    NODE_SAVE,
    NODE_SCENE_SCALE,
    fill_layout_graph,
    map_history_outputs,
    minimal_graph,
    workflow_file_valid,
)


def test_layout_asset_id_prefix():
    from app.core.library.store import new_asset_id

    assert new_asset_id("layouts").startswith("lay_")


def test_pipeline_registered():
    from app.pipelines import get_pipeline

    pipe = get_pipeline("ref_frame")
    assert pipe.id == "ref_frame"
    assert pipe.asset_kind == "layouts"
    assert "layout" in pipe.output_labels
    assert pipe.enabled is True
    assert workflow_file_valid() is True


def test_fill_sets_description_seed_and_refs():
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Wide shot of actor in cafe, eye level",
            "images": ["scene.jpg", "actor.png"],
            "seed": 42,
            "output_prefix": "director-studio/job1/ref_frame",
            "ref_labels": ["SCENE beach", "CHARACTER identity"],
        },
    )
    prompt = g[NODE_DESCRIPTION]["inputs"]["prompt"]
    assert prompt == "Wide shot of actor in cafe, eye level"
    assert g[NODE_SAMPLER]["inputs"]["seed"] == 42
    assert g[NODE_SAVE]["inputs"]["filename_prefix"] == "director-studio/job1/ref_frame"
    assert g[NODE_REF_IMAGE_1]["inputs"]["image"] == "scene.jpg"
    # Quality mode samples from the full-resolution first reference latent.
    from app.pipelines.ref_frame.workflow import EMPTY_LATENT_DENOISE

    latent_id = g[NODE_SAMPLER]["inputs"]["latent_image"][0]
    assert g[latent_id]["class_type"] == "VAEEncode"
    assert g[NODE_SAMPLER]["inputs"]["denoise"] == pytest.approx(EMPTY_LATENT_DENOISE)
    assert g[NODE_SAMPLER]["inputs"]["steps"] == DEFAULT_STEPS
    assert g[NODE_SAMPLER]["inputs"]["cfg"] == pytest.approx(DEFAULT_CFG)
    negative_id = g[NODE_SAMPLER]["inputs"]["negative"][0]
    assert g[negative_id]["class_type"] == "ConditioningZeroOut"
    loaders = [
        n
        for n in g.values()
        if isinstance(n, dict) and n.get("class_type") == "LoadImage"
    ]
    assert len(loaders) == 2
    images = {n["inputs"]["image"] for n in loaders}
    assert images == {"actor.png", "scene.jpg"}


def test_multi_ref_quality_graph_keeps_vl_images_and_chains_full_res_latents():
    """Losing the external ReferenceLatent chain reintroduces 1MP AREA-softened refs."""
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Seat Image2 inside Image1 without changing her wardrobe.",
            "images": ["scene.jpg", "actor.png"],
            "seed": 42,
        },
    )

    positive_inputs = g[NODE_DESCRIPTION]["inputs"]
    assert positive_inputs["image1"] == [NODE_REF_IMAGE_1, 0]
    assert "image2" in positive_inputs
    assert "vae" not in positive_inputs

    vae_encodes = {
        nid: node for nid, node in g.items() if node.get("class_type") == "VAEEncode"
    }
    reference_latents = {
        nid: node
        for nid, node in g.items()
        if node.get("class_type") == "ReferenceLatent"
    }
    zero_nodes = {
        nid: node
        for nid, node in g.items()
        if node.get("class_type") == "ConditioningZeroOut"
    }
    methods = [
        node
        for node in g.values()
        if node.get("class_type") == "FluxKontextMultiReferenceLatentMethod"
    ]

    assert len(vae_encodes) == 2
    assert len(reference_latents) == 4  # quality mode repeats each reference once
    assert len(zero_nodes) == 1
    assert methods[0]["inputs"]["reference_latents_method"] == "index_timestep_zero"

    sampler = g[NODE_SAMPLER]["inputs"]
    assert g[sampler["positive"][0]]["class_type"] == "ReferenceLatent"
    assert g[sampler["negative"][0]]["class_type"] == "ConditioningZeroOut"
    assert g[sampler["latent_image"][0]]["class_type"] == "VAEEncode"


def test_reference_frame_uses_fixed_quality_canvas_without_stretching_identity_refs():
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Place Image2 in Image1.",
            "images": ["scene.jpg", "actor.png"],
            # The reference-frame quality tier is fixed; callers cannot silently
            # reduce it through legacy width/height parameters.
            "width": 1280,
            "height": 768,
        },
    )

    scale = g[NODE_SCENE_SCALE]
    assert scale["class_type"] == "ImageScale"
    assert scale["inputs"] == {
        "image": [NODE_REF_IMAGE_1, 0],
        "upscale_method": "lanczos",
        "width": LAYOUT_WIDTH,
        "height": LAYOUT_HEIGHT,
        "crop": "center",
    }
    assert (LAYOUT_WIDTH, LAYOUT_HEIGHT) == (1728, 960)

    sampler_latent = g[NODE_SAMPLER]["inputs"]["latent_image"][0]
    assert g[sampler_latent]["inputs"]["pixels"] == [NODE_SCENE_SCALE, 0]

    actor_encode = next(
        node
        for node in g.values()
        if node.get("class_type") == "VAEEncode"
        and node["inputs"]["pixels"] == ["8", 0]
    )
    assert actor_encode["inputs"]["pixels"] == ["8", 0]


def test_portrait_reference_frame_keeps_quality_pixels_and_flips_canvas():
    """A 9:16 project must not receive the default landscape reference frame."""
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Vertical music-video performance frame.",
            "images": ["scene.jpg", "actor.png"],
            "aspect_ratio": "9:16",
        },
    )

    scale = g[NODE_SCENE_SCALE]["inputs"]
    assert (scale["width"], scale["height"]) == (960, 1728)


def test_reference_frame_loads_official_lightning_lora_with_distilled_defaults():
    g = fill_layout_graph(
        minimal_graph(),
        {"description": "Empty hallway", "images": ["scene.jpg"]},
    )

    lora = g[NODE_LIGHTNING_LORA]
    assert lora["class_type"] == "LoraLoaderModelOnly"
    assert lora["inputs"] == {
        "model": [NODE_MODEL_SAMPLING, 0],
        "lora_name": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
        "strength_model": 1.0,
    }
    assert g[NODE_SAMPLER]["inputs"]["model"] == [NODE_LIGHTNING_LORA, 0]
    assert g[NODE_SAMPLER]["inputs"]["steps"] == DEFAULT_STEPS == 4
    assert g[NODE_SAMPLER]["inputs"]["cfg"] == pytest.approx(DEFAULT_CFG)
    assert DEFAULT_CFG == pytest.approx(1.0)
    assert g[NODE_MODEL_SAMPLING]["inputs"]["shift"] == pytest.approx(DEFAULT_SHIFT)
    assert DEFAULT_SHIFT == pytest.approx(3.1)


def test_fill_prompt_does_not_override_agent_people_blocking():
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": (
                "Use Image1 for the corridor. Seat Image2 at frame left and Image3 at "
                "frame right; they exchange a deodorant can across the table."
            ),
            "images": ["scene.jpg", "actor_a.png", "actor_b.png"],
            "ref_labels": [
                "Image1 SCENE corridor right_view — composite character INTO this set",
                "Image2 CHARACTER Ana fullbody_threeview->front_crop",
                "Image3 CHARACTER Bo master",
            ],
        },
    )

    prompt = g[NODE_DESCRIPTION]["inputs"]["prompt"]
    assert prompt == (
        "Use Image1 for the corridor. Seat Image2 at frame left and Image3 at "
        "frame right; they exchange a deodorant can across the table."
    )
    assert "Photoreal cinematic production still" not in prompt


def test_fill_prompt_does_not_inject_prop_blocking():
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Her fingertips hover above the red folder without touching it.",
            "images": ["scene.jpg", "actor.png", "folder.png"],
            "ref_labels": [
                "Image1 SCENE rainy cafe",
                "Image2 CHARACTER Lin Ya",
                "Image3 PROP red folder",
            ],
        },
    )

    prompt = g[NODE_DESCRIPTION]["inputs"]["prompt"]
    assert prompt == "Her fingertips hover above the red folder without touching it."


def test_scene_only_uses_quality_reference_latent():
    """A single scene still receives the same full-resolution quality path."""
    g = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Empty corridor establishing shot",
            "images": ["scene_only.jpg"],
            "seed": 1,
            "ref_labels": ["SCENE only"],
        },
    )
    latent_id = g[NODE_SAMPLER]["inputs"]["latent_image"][0]
    assert g[latent_id]["class_type"] == "VAEEncode"
    refs = [node for node in g.values() if node.get("class_type") == "ReferenceLatent"]
    assert len(refs) == 2
    assert g[NODE_SAMPLER]["inputs"]["denoise"] == pytest.approx(1.0)


def test_fill_requires_description():
    with pytest.raises(ValueError, match="description"):
        fill_layout_graph(minimal_graph(), {"images": ["a.png"]})


def test_fill_rejects_too_many_images():
    with pytest.raises(ValueError, match="3"):
        fill_layout_graph(
            minimal_graph(),
            {
                "description": "x",
                "images": [f"{i}.png" for i in range(4)],
            },
        )


def test_map_history_outputs_layout_key():
    history = {
        "outputs": {
            NODE_SAVE: {
                "images": [
                    {
                        "filename": "layout_00001_.png",
                        "subfolder": "director-studio/j1",
                        "type": "output",
                    }
                ]
            }
        }
    }
    mapped = map_history_outputs(history)
    assert "layout" in mapped
    assert mapped["layout"].filename == "layout_00001_.png"


def test_build_prompt_orders_ref_keys():
    from app.pipelines import get_pipeline

    pipe = get_pipeline("ref_frame")
    job = JobRecord(
        id="job_ff_test",
        pipeline_id="ref_frame",
        asset_kind="layouts",
        status=JobStatus.queued,
        name="layout",
        params={
            "description": "blocking text here",
            "image_keys": ["ref_0", "ref_1", "ref_2"],
        },
        seed=7,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    graph, seed = pipe.build_prompt(
        job,
        uploaded_images={
            "ref_1": "b.png",
            "ref_0": "a.png",
            "ref_2": "c.png",
        },
    )
    assert seed == 7
    prompt = graph[NODE_DESCRIPTION]["inputs"]["prompt"]
    assert "blocking text here" in prompt
    assert graph[NODE_REF_IMAGE_1]["inputs"]["image"] == "a.png"
    loaders = [
        n
        for n in graph.values()
        if isinstance(n, dict) and n.get("class_type") == "LoadImage"
    ]
    assert len(loaders) == 3


def test_save_to_library_meta_pending_review(tmp_path, monkeypatch):
    from app.config import settings
    from app.pipelines import get_pipeline

    jobs_root = tmp_path / "jobs"
    lib_root = tmp_path / "library"
    jobs_root.mkdir()
    lib_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    monkeypatch.setattr(settings, "library_root", lib_root)

    job_id = "job_layout_1"
    jdir = jobs_root / job_id
    (jdir / "outputs").mkdir(parents=True)
    layout_file = jdir / "outputs" / "layout.png"
    layout_file.write_bytes(b"fake-png")

    job = JobRecord(
        id=job_id,
        pipeline_id="ref_frame",
        asset_kind="layouts",
        status=JobStatus.succeeded,
        name="Shot layout",
        params={
            "description": "cafe wide",
            "shot_id": "sht_abc",
            "source_asset_ids": ["act_1", "scn_2"],
        },
        seed=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        outputs={
            "layout": OutputSlot(key="layout", label="layout", path=str(layout_file)),
        },
    )

    pipe = get_pipeline("ref_frame")
    asset = pipe.save_to_library(job, name="Layout A")

    assert isinstance(asset, LibraryAsset)
    assert asset.kind == "layouts"
    assert asset.id.startswith("lay_")
    assert asset.meta["review_status"] == "pending_review"
    assert asset.meta["source_shot_id"] == "sht_abc"
    assert asset.meta["source_refs"] == ["act_1", "scn_2"]
    assert (lib_root / "layouts" / asset.id / "layout.png").exists()


def test_collect_ref_frame_refs_scene_first_includes_prop_when_slot_available(tmp_path, monkeypatch):
    """Qwen pack is scene→actor→handled prop while preserving concrete files."""
    from app.agents.director.service import DirectorService
    from app.config import settings
    from app.core.projects.models import RefRole, Shot, ShotRef, ShotStatus
    from app.core.schemas import LibraryAsset
    from PIL import Image

    lib = tmp_path / "library"
    monkeypatch.setattr(settings, "library_root", lib)

    def _write_asset(
        kind: str,
        aid: str,
        name: str,
        fname: str = "master.png",
        *,
        description: str = "",
    ) -> None:
        adir = lib / kind / aid
        adir.mkdir(parents=True)
        # Noisy image so PNG compresses >2KB (solid fills are skipped as placeholders)
        import random

        w, h = 256, 256
        pixels = [
            (
                (i * 3 + j * 7) % 256,
                (i * 5 + j * 11) % 256,
                (i * 13 + j) % 256,
            )
            for j in range(h)
            for i in range(w)
        ]
        im = Image.new("RGB", (w, h))
        im.putdata(pixels)
        im.save(adir / fname)
        assert (adir / fname).stat().st_size > 2048
        asset = LibraryAsset(
            id=aid,
            kind=kind,
            name=name,
            notes="",
            pipeline_id="external",
            job_id="",
            created_at="2026-01-01T00:00:00+00:00",
            files={"master": fname},
            meta={"description": description} if description else {},
        )
        (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")

    _write_asset(
        "actors",
        "act_girl",
        "girl",
        description="short black bob, belted beige trench coat over a black top",
    )
    _write_asset("scenes", "scn_hall", "走廊")
    _write_asset("props", "prp_spray", "除臭剂", "master.jpg")

    shot = Shot(
        id="sht_order",
        project_id="prj_x",
        scene_id="sc01",
        title="t",
        script_beat="spray",
        duration_s=5.0,
        status=ShotStatus.ref_frame_pending,
        refs=[
            ShotRef(role=RefRole.actor, asset_id="act_girl", picture_index=1),
            ShotRef(role=RefRole.scene, asset_id="scn_hall", picture_index=2),
            ShotRef(role=RefRole.prop, asset_id="prp_spray", picture_index=3, file_key="master"),
        ],
    )
    svc = DirectorService(plan_provider=type("P", (), {"complete": None})())
    packed = svc._collect_ref_frame_refs(shot)
    assert list(packed["images"].keys()) == ["ref_0", "ref_1", "ref_2"]
    # Labels describe scene-first order
    assert "SCENE" in packed["labels"][0]
    assert "CHARACTER" in packed["labels"][1]
    assert "short black bob, belted beige trench coat" in packed["labels"][1]
    assert "PROP" in packed["labels"][2]


def test_actor_ref_frame_honors_selected_threeview_as_single_front_crop(tmp_path, monkeypatch):
    from app.config import settings
    from app.agents.director.service import _actor_image_for_ref_frame
    from app.core.schemas import LibraryAsset
    from PIL import Image

    lib = tmp_path / "library"
    monkeypatch.setattr(settings, "library_root", lib)
    adir = lib / "actors" / "act_x"
    adir.mkdir(parents=True)
    # master: single person (must be >2KB — resolve_asset_image skips tiny placeholders)
    Image.new("RGB", (512, 768), (20, 20, 40)).save(adir / "master.png")
    # threeview: wide sheet
    Image.new("RGB", (1536, 768), (80, 80, 80)).save(adir / "fullbody_threeview.png")
    asset = LibraryAsset(
        id="act_x",
        kind="actors",
        name="X",
        notes="",
        pipeline_id="actor",
        job_id="j",
        created_at="t",
        files={"master": "master.png", "fullbody_threeview": "fullbody_threeview.png"},
    )
    (adir / "asset.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    name, data, used = _actor_image_for_ref_frame(
        asset, preferred_key="fullbody_threeview"
    )
    assert used == "fullbody_threeview->front_crop"
    assert name == "actor_front_panel.png"
