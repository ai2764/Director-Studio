from __future__ import annotations

from app.pipelines.qwen21_layout.workflow import (
    NODE_KSAMPLER,
    NODE_SAVE,
    NODE_TEXT_ENCODE,
    NODE_UNET,
    fill_layout_graph,
    minimal_graph,
    workflow_file_valid,
)


def test_text_only_layout_uses_qwen_image_21_without_reference_inputs():
    graph = fill_layout_graph(
        minimal_graph(),
        {
            "description": "A handmade cardboard hallway rising from a flat floor.",
            "images": [],
            "seed": 42,
            "output_prefix": "director-studio/test/layout",
        },
    )

    assert graph[NODE_UNET]["inputs"]["unet_name"] == "qwen_image_2.1_bf16.safetensors"
    assert graph[NODE_TEXT_ENCODE]["inputs"]["prompt"] == (
        "A handmade cardboard hallway rising from a flat floor."
    )
    assert not any(node.get("class_type") == "LoadImage" for node in graph.values())
    assert "images.image_1" not in graph[NODE_TEXT_ENCODE]["inputs"]
    assert graph[NODE_KSAMPLER]["inputs"]["latent_image"] == ["15", 0]
    assert graph["15"]["class_type"] == "EmptySD3LatentImage"
    assert graph["15"]["inputs"]["width"] == 1536
    assert graph["15"]["inputs"]["height"] == 864
    assert graph[NODE_SAVE]["inputs"]["filename_prefix"] == "director-studio/test/layout"


def test_reference_layout_attaches_ordered_images_to_qwen_image_21():
    graph = fill_layout_graph(
        minimal_graph(),
        {
            "description": "Keep the scene architecture and place the actor by the door.",
            "images": ["scene.png", "actor.png"],
            "seed": 42,
        },
    )

    assert graph[NODE_TEXT_ENCODE]["inputs"]["images.image_1"] == ["4", 0]
    assert graph[NODE_TEXT_ENCODE]["inputs"]["images.image_2"] == ["5", 0]
    assert graph["4"]["inputs"]["image"] == "scene.png"
    assert graph["5"]["inputs"]["image"] == "actor.png"
    assert graph[NODE_KSAMPLER]["inputs"]["latent_image"] == ["15", 0]
    assert graph["15"]["inputs"]["width"] == 1536
    assert graph["15"]["inputs"]["height"] == 864


def test_text_only_portrait_layout_uses_portrait_canvas():
    graph = fill_layout_graph(
        minimal_graph(),
        {"description": "A paper hallway", "images": [], "aspect_ratio": "9:16"},
    )
    assert graph["15"]["inputs"]["width"] == 864
    assert graph["15"]["inputs"]["height"] == 1536


def test_qwen21_layout_pipeline_is_enabled_from_its_api_workflow():
    assert workflow_file_valid()

    from app.pipelines.qwen21_layout.pipeline import Qwen21LayoutPipeline

    pipeline = Qwen21LayoutPipeline()
    assert pipeline.id == "qwen21_layout"
    assert pipeline.execution_adapter_id == "comfy"
    assert pipeline.enabled
