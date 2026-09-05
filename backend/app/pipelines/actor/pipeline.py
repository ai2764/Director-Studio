from __future__ import annotations

from typing import Any

from ...core.schemas import ComfyImageRef, JobRecord, LibraryAsset
from ..base import Pipeline
from . import workflow


class ActorPipeline(Pipeline):
    id = "actor"
    asset_kind = "actors"
    display_name = "Actor Casting"
    description = (
        "Actor reference → master + multipanel three-view (workbench); optional wardrobe; bust crop."
    )

    @property
    def output_labels(self) -> dict[str, str]:
        return dict(workflow.OUTPUT_LABELS)

    def meta_defaults(self) -> dict[str, Any]:
        base = super().meta_defaults()
        base.update(
            {
                "routing": {
                    "actor_ref": {
                        "none": "Text-to-actor (description only)",
                        "any": "Reference image → master + three-view (workbench)",
                    },
                    "wardrobe_ref": {
                        "none": "Keep outfit from master / description",
                        "upload": "Extract clothing and transfer onto master",
                    },
                    "order": (
                        "master (actor ref + description) → multipanel three-view "
                        "(master + actor ref) → bust crop"
                    ),
                },
                "default_negative": workflow.DEFAULT_NEGATIVE,
                "default_description": workflow.DEFAULT_DESCRIPTION,
                "default_body_description": "",
                "default_hair_description": "",
                "fields": [
                    {"id": "description", "label": "Actor description", "required": True},
                    {
                        "id": "actor_image",
                        "label": "Actor reference (optional)",
                        "required": False,
                    },
                    {
                        "id": "wardrobe_image",
                        "label": "Wardrobe / model photo (optional)",
                        "required": False,
                    },
                ],
                "output_slots": [
                    {"key": "master", "label": workflow.OUTPUT_LABELS["master"]},
                    {
                        "key": "fullbody_threeview",
                        "label": workflow.OUTPUT_LABELS["fullbody_threeview"],
                    },
                    {
                        "key": "bust_threeview",
                        "label": workflow.OUTPUT_LABELS["bust_threeview"],
                    },
                    {
                        "key": "asset_sheet",
                        "label": workflow.OUTPUT_LABELS["asset_sheet"],
                    },
                    {
                        "key": "wardrobe_ref",
                        "label": workflow.OUTPUT_LABELS["wardrobe_ref"],
                        "conditional": True,
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
        p = job.params
        description = (p.get("description") or "").strip()
        if not description and "actor" not in uploaded_images:
            raise ValueError("description is required when no actor reference is uploaded")

        return workflow.build_actor_prompt(
            description=description or workflow.DEFAULT_DESCRIPTION,
            body_description=p.get("body_description") or "",
            hair_description=p.get("hair_description") or "",
            negative_prompt=p.get("negative_prompt") or "",
            actor_image_name=uploaded_images.get("actor"),
            wardrobe_image_name=uploaded_images.get("wardrobe"),
            include_headwear=bool(p.get("include_headwear")),
            include_footwear=bool(p.get("include_footwear")),
            seed=job.seed,
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
        return ["actor", "wardrobe"]

    def postprocess_job_outputs(self, job: JobRecord, saved: dict[str, Any]) -> None:
        """No multipanel hair paste — hair is text-driven; bust is already a crop."""
        return None

    def save_to_library(
        self,
        job: JobRecord,
        *,
        name: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
    ) -> LibraryAsset:
        return super().save_to_library(
            job, name=name, notes=notes, project_id=project_id
        )
