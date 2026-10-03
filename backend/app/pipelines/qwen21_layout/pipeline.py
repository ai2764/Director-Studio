from __future__ import annotations

from typing import Any

from ...core.schemas import ComfyImageRef, JobRecord
from ..ref_frame.pipeline import RefFramePipeline
from . import workflow


class Qwen21LayoutPipeline(RefFramePipeline):
    id = "qwen21_layout"
    execution_adapter_id = "comfy"
    display_name = "Qwen Image 2.1 Layout"
    description = "Generate a Layout with Qwen Image 2.1, with optional visual references."

    def __init__(self) -> None:
        super().__init__()
        self.enabled = workflow.workflow_file_valid()

    @property
    def output_labels(self) -> dict[str, str]:
        return dict(workflow.OUTPUT_LABELS)

    def meta_defaults(self) -> dict[str, Any]:
        base = super().meta_defaults()
        base["defaults"]["max_ref_images"] = workflow.MAX_REF_IMAGES
        base["node_ids"].update({
            "description": workflow.NODE_TEXT_ENCODE,
            "sampler": workflow.NODE_KSAMPLER,
            "save": workflow.NODE_SAVE,
        })
        return base

    def build_prompt(self, job: JobRecord, *, uploaded_images: dict[str, str]) -> tuple[dict[str, Any], int]:
        params = job.params or {}
        keys = params.get("image_keys")
        ordered_keys = [str(key) for key in keys] if isinstance(keys, list) else sorted(uploaded_images)
        images = [uploaded_images[key] for key in ordered_keys if key in uploaded_images]
        if not images:
            images = list(uploaded_images.values())
        return workflow.build_layout_prompt(
            description=str(params.get("description") or ""), image_names=images,
            seed=job.seed, output_prefix=params.get("output_prefix"), job_id=job.id,
            aspect_ratio=params.get("aspect_ratio"),
        )

    def map_history_outputs(self, history: dict[str, Any], *, job: JobRecord | None = None) -> dict[str, ComfyImageRef]:
        return workflow.map_history_outputs(history)
