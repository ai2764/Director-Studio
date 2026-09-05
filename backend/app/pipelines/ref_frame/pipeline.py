from __future__ import annotations

from typing import Any

from ...core.schemas import ComfyImageRef, JobRecord, LibraryAsset
from ..base import Pipeline
from . import workflow


class RefFramePipeline(Pipeline):
    id = "ref_frame"
    asset_kind = "layouts"
    display_name = "Layout Reference Frame"
    description = (
        "Compose a layout reference-frame still from a blocking description and ordered "
        "reference images (actor/scene/etc.). Saved to library layouts with "
        "review_status=pending_review."
    )
    enabled = True

    def __init__(self) -> None:
        # Only enable when the checked-in workflow shell validates
        self.enabled = workflow.workflow_file_valid()

    @property
    def output_labels(self) -> dict[str, str]:
        return dict(workflow.OUTPUT_LABELS)

    def meta_defaults(self) -> dict[str, Any]:
        base = super().meta_defaults()
        base.update(
            {
                "defaults": {
                    "max_ref_images": workflow.MAX_REF_IMAGES,
                },
                "fields": [
                    {
                        "id": "description",
                        "label": "Layout description (blocking)",
                        "required": True,
                    },
                    {
                        "id": "image_keys",
                        "label": "Ordered logical input keys (ref_0…ref_n)",
                        "required": False,
                    },
                    {
                        "id": "shot_id",
                        "label": "Source shot id (optional)",
                        "required": False,
                    },
                    {
                        "id": "source_asset_ids",
                        "label": "Source library asset ids",
                        "required": False,
                    },
                ],
                "output_slots": [
                    {"key": "layout", "label": workflow.OUTPUT_LABELS["layout"]},
                ],
                "node_ids": {
                    "description": workflow.NODE_DESCRIPTION,
                    "sampler": workflow.NODE_SAMPLER,
                    "save": workflow.NODE_SAVE,
                    "ref_image_0": workflow.NODE_REF_IMAGE_0,
                },
            }
        )
        return base

    def build_prompt(
        self,
        job: JobRecord,
        *,
        uploaded_images: dict[str, str],
    ) -> tuple[dict[str, Any], int]:
        p = job.params or {}
        description = (p.get("description") or "").strip()
        if not description:
            raise ValueError("description is required")

        image_keys = p.get("image_keys")
        if isinstance(image_keys, list) and image_keys:
            ordered_keys = [str(k) for k in image_keys]
        else:
            # Prefer ref_0, ref_1, … then any remaining uploads in stable order
            ref_keys = sorted(
                (k for k in uploaded_images if k.startswith("ref_")),
                key=lambda k: (
                    int(k.split("_", 1)[1])
                    if k.split("_", 1)[-1].isdigit()
                    else 10**9,
                    k,
                ),
            )
            other = [k for k in uploaded_images if k not in ref_keys]
            ordered_keys = ref_keys + other

        image_names: list[str] = []
        for key in ordered_keys:
            name = uploaded_images.get(key)
            if name:
                image_names.append(name)

        if not image_names and uploaded_images:
            image_names = list(uploaded_images.values())

        output_prefix = p.get("output_prefix")
        ref_labels = p.get("ref_labels")
        if not isinstance(ref_labels, list):
            ref_labels = []
        return workflow.build_layout_prompt(
            description=description,
            image_names=image_names,
            seed=job.seed,
            output_prefix=output_prefix,
            job_id=job.id,
            ref_labels=[str(x) for x in ref_labels],
            aspect_ratio=str(p.get("aspect_ratio") or ""),
        )

    def map_history_outputs(
        self,
        history: dict[str, Any],
        *,
        job: JobRecord | None = None,
    ) -> dict[str, ComfyImageRef]:
        return workflow.map_history_outputs(history)

    def library_input_keys(self) -> list[str]:
        return []

    def save_to_library(
        self,
        job: JobRecord,
        *,
        name: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
    ) -> LibraryAsset:
        from ...core.library import save_asset_from_job

        p = job.params or {}
        source_refs = p.get("source_asset_ids") or []
        if not isinstance(source_refs, list):
            source_refs = []
        # Prefer explicit, then job, then params.project_id (shot pipeline)
        resolved = project_id or job.project_id or p.get("project_id")

        return save_asset_from_job(
            job,
            name=name,
            notes=notes,
            file_keys=list(self.output_labels.keys()) or None,
            input_keys=self.library_input_keys(),
            meta={
                "review_status": "pending_review",
                "source_shot_id": p.get("shot_id"),
                "source_refs": list(source_refs),
            },
            project_id=resolved if isinstance(resolved, str) else project_id,
        )
