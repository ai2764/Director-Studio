from __future__ import annotations

import asyncio
from typing import Any

from ...config import settings
from ...core.schemas import JobRecord, LibraryAsset
from ...integrations.chatgpt_bridge import ChatGptBridgeClient
from ..base import ExternalPipeline, ExternalPipelineResult


class GptRefFramePipeline(ExternalPipeline):
    id = "gpt_ref_frame"
    asset_kind = "layouts"
    display_name = "GPT Layout Reference Frame"
    description = (
        "Generate one Layout through a separately installed local ChatGPT Bridge."
    )
    enabled = True
    replay_after_restart = False
    execution_lane = "chatgpt_bridge"

    @property
    def execution_lane_cooldown_sec(self) -> float:
        return settings.gpt_bridge_job_cooldown_sec

    @property
    def output_labels(self) -> dict[str, str]:
        return {"layout": "Layout"}

    async def run_external(
        self,
        job: JobRecord,
        *,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
    ) -> ExternalPipelineResult:
        if cancel_event.is_set():
            raise asyncio.CancelledError
        params = job.params or {}
        prompt = str(params.get("generation_prompt") or "").strip()
        if not prompt:
            raise ValueError("generation_prompt is required")
        raw_keys = params.get("image_keys")
        if not isinstance(raw_keys, list):
            raise ValueError("image_keys must be an ordered source list")

        ordered_images: list[tuple[str, str, bytes]] = []
        for index, raw_key in enumerate(raw_keys, start=1):
            key = str(raw_key)
            if key not in inputs:
                raise ValueError(f"ordered GPT source input is missing: {key}")
            filename, data = inputs[key]
            ordered_images.append((f"Image{index}", filename, data))

        client = ChatGptBridgeClient.from_settings()
        result = await client.generate_image(prompt, ordered_images)
        return ExternalPipelineResult(
            outputs={"layout": (result.filename, result.image_bytes)},
            params_update={"gpt_provenance": result.provenance()},
        )

    def save_to_library(
        self,
        job: JobRecord,
        *,
        name: str | None = None,
        notes: str | None = None,
        project_id: str | None = None,
    ) -> LibraryAsset:
        from ...core.library import save_asset_from_job

        params = job.params or {}
        resolved = project_id or job.project_id or params.get("project_id")
        source_refs = params.get("layout_source_refs")
        if not isinstance(source_refs, list):
            source_refs = []
        provenance = params.get("gpt_provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        return save_asset_from_job(
            job,
            name=name,
            notes=notes,
            file_keys=["layout"],
            input_keys=[],
            meta={
                "review_status": "pending_review",
                "provider": "gpt",
                "source_shot_id": params.get("shot_id"),
                "source_refs": source_refs,
                "generation_prompt": params.get("generation_prompt"),
                "gpt_provenance": provenance,
            },
            project_id=resolved if isinstance(resolved, str) else project_id,
        )
