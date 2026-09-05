from __future__ import annotations

from typing import Any

from ...core.schemas import ComfyImageRef, JobRecord
from ..base import Pipeline
from . import workflow


class PropPipeline(Pipeline):
    id = "prop"
    asset_kind = "props"
    display_name = "Props · Reference Sheet"
    description = (
        "Prep one uploaded prop photo into a multi-view H3 reference sheet: "
        "the same object shown consistently from useful angles."
    )

    @property
    def output_labels(self) -> dict[str, str]:
        return dict(workflow.OUTPUT_LABELS)

    def meta_defaults(self) -> dict[str, Any]:
        base = super().meta_defaults()
        base.update(
            {
                "canvas": {
                    "width": workflow.PROP_WIDTH,
                    "height": workflow.PROP_HEIGHT,
                },
                "fields": [
                    {
                        "id": "prop_image",
                        "label": "Prop photo",
                        "required": True,
                        "hint": "Phone snap, catalog still, or generated object photo",
                    },
                    {
                        "id": "name",
                        "label": "Name",
                        "required": True,
                    },
                    {
                        "id": "notes",
                        "label": "Notes",
                        "required": False,
                        "hint": "Optional identity details for the Director",
                    },
                ],
            }
        )
        return base

    def build_prompt(
        self,
        job: JobRecord,
        *,
        uploaded_images: dict[str, str],
    ) -> tuple[dict[str, Any], int]:
        image_name = uploaded_images.get("prop") or ""
        p = job.params or {}
        return workflow.build_prop_prompt(
            image_name=image_name,
            name=job.name or str(p.get("name") or ""),
            notes=job.notes or str(p.get("notes") or ""),
            seed=job.seed,
            output_prefix=p.get("output_prefix"),
            job_id=job.id,
        )

    def map_history_outputs(
        self,
        history: dict[str, Any],
        *,
        job: JobRecord | None = None,
    ) -> dict[str, ComfyImageRef]:
        return workflow.map_history_outputs(history)

    def library_input_keys(self) -> list[str]:
        return ["prop"]
