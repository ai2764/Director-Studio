from __future__ import annotations

from typing import Any

from ...core.schemas import ComfyImageRef, JobRecord, LibraryAsset
from ..base import Pipeline
from . import workflow


class ScenePipeline(Pipeline):
    id = "scene"
    asset_kind = "scenes"
    display_name = "Set Design · Multi-Angle"
    description = (
        "Qwen Edit 2511 multi-angle scene ref: one scene image → multiple camera angles "
        "(multi-angle LoRA + CR Prompt List). Output files are named by viewpoint."
    )

    @property
    def output_labels(self) -> dict[str, str]:
        return workflow.angle_output_labels(workflow.DEFAULT_ANGLES)

    def labels_for_job(self, job: JobRecord) -> dict[str, str]:
        scene_name = job.name or ""
        used = job.params.get("used_angles")
        if isinstance(used, list) and used:
            return workflow.labels_for_lines(
                [str(x) for x in used],
                scene_name=scene_name,
            )
        stems = job.params.get("output_stems")
        angles = job.params.get("angle_prompts") or workflow.DEFAULT_ANGLES
        if isinstance(stems, list) and stems:
            lines = workflow.parse_angle_lines(angles)
            labels: dict[str, str] = {}
            for i, stem in enumerate(stems):
                name = workflow.angle_view_name(lines[i]) if i < len(lines) else str(stem)
                labels[str(stem)] = f"{i + 1:02d} · {name}"
            return labels
        return workflow.angle_output_labels(angles, scene_name=scene_name)

    def meta_defaults(self) -> dict[str, Any]:
        base = super().meta_defaults()
        sample = workflow.unique_angle_stems(
            workflow.parse_angle_lines(workflow.DEFAULT_ANGLES),
            scene_name="SceneName",
        )
        base.update(
            {
                "default_angles": workflow.DEFAULT_ANGLES,
                "default_prepend": workflow.DEFAULT_PREPEND,
                "default_append": "",
                "naming": "{scene_name}_{view_suffix}",
                "sample_output_stems": sample,
                "fields": [
                    {
                        "id": "scene_image",
                        "label": "Scene reference",
                        "required": True,
                        "hint": "Single plate / set still — multi-angle LoRA re-shoots it",
                    },
                    {
                        "id": "angle_prompts",
                        "label": "Angle list (one per line)",
                        "required": True,
                    },
                    {
                        "id": "prepend_text",
                        "label": "Prepend to each angle (optional)",
                        "required": False,
                        "hint": "Locked onto every angle; default keeps the same set and only changes camera",
                    },
                    {
                        "id": "append_text",
                        "label": "Append to each angle (optional)",
                        "required": False,
                    },
                ],
                "output_slots": [
                    {
                        "key": "scene_view_stem",
                        "label": "Named {scene}_{view}, e.g. Audition_Room_01_front_view_h0_v0",
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
        scene = uploaded_images.get("scene")
        if not scene:
            raise ValueError("scene reference image is required")
        p = job.params
        prompt, seed, used, stems = workflow.build_scene_prompt(
            scene_image_name=scene,
            scene_name=job.name or "",
            angle_prompts=p.get("angle_prompts") or workflow.DEFAULT_ANGLES,
            prepend_text=p.get("prepend_text") or "",
            append_text=p.get("append_text") or "",
            start_index=int(p.get("start_index") or 0),
            max_rows=p.get("max_rows"),
            seed=job.seed,
            job_id=job.id,
        )
        job.params["used_angles"] = used
        job.params["output_stems"] = stems
        job.params["scene_name_slug"] = workflow.scene_name_slug(job.name or "")
        return prompt, seed

    def map_history_outputs(
        self,
        history: dict[str, Any],
        *,
        job: JobRecord | None = None,
    ) -> dict[str, ComfyImageRef]:
        stems = None
        lines = None
        scene_name = ""
        if job is not None:
            scene_name = job.name or ""
            raw = job.params.get("output_stems")
            if isinstance(raw, list):
                stems = [str(x) for x in raw]
            used = job.params.get("used_angles")
            if isinstance(used, list):
                lines = [str(x) for x in used]
        return workflow.map_history_outputs(
            history,
            angle_lines=lines,
            output_stems=stems,
            scene_name=scene_name,
        )

    def library_input_keys(self) -> list[str]:
        return ["scene"]

    def save_to_library(
        self,
        job: JobRecord,
        *,
        name: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
    ) -> LibraryAsset:
        import shutil

        from ...core.library import save_asset_from_job
        from ...core.library.store import write_asset
        from ...core.paths import asset_write_dir

        labels = self.labels_for_job(job)
        asset = save_asset_from_job(
            job,
            name=name,
            notes=notes,
            file_keys=list(job.outputs.keys()) or list(labels.keys()),
            input_keys=self.library_input_keys(),
            meta={
                **dict(job.params),
                "angle_labels": labels,
                "output_stems": job.params.get("output_stems") or list(job.outputs.keys()),
            },
            project_id=project_id,
        )
        files = dict(asset.files or {})
        src_name = files.get("input_scene")
        if src_name and not files.get("master"):
            adir = asset_write_dir(asset.kind, asset.id, project_id=asset.project_id)
            src = adir / src_name
            if src.is_file():
                dest = adir / f"master{src.suffix or '.png'}"
                shutil.copy2(src, dest)
                files["master"] = dest.name
                asset = write_asset(asset.model_copy(update={"files": files}))
        return asset
