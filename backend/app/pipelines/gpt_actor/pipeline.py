from __future__ import annotations

import asyncio

from ...config import settings
from ...core.schemas import JobRecord, LibraryAsset
from ...integrations.chatgpt_bridge import ChatGptBridgeClient
from ..base import ExternalPipeline, ExternalPipelineResult


class GptActorPipeline(ExternalPipeline):
    id = "gpt_actor"
    asset_kind = "actors"
    display_name = "GPT Actor Design"
    description = "Generate one reviewable Actor design through ChatGPT Bridge."
    enabled = True
    replay_after_restart = False
    execution_lane = "chatgpt_bridge"

    @property
    def execution_lane_cooldown_sec(self) -> float:
        return settings.gpt_bridge_job_cooldown_sec

    @property
    def output_labels(self) -> dict[str, str]:
        return {"master": "Master"}

    async def run_external(
        self,
        job: JobRecord,
        *,
        inputs: dict[str, tuple[str, bytes]],
        cancel_event: asyncio.Event,
    ) -> ExternalPipelineResult:
        if cancel_event.is_set():
            raise asyncio.CancelledError
        prompt = str((job.params or {}).get("generation_prompt") or "").strip()
        if not prompt:
            raise ValueError("generation_prompt is required")
        client = ChatGptBridgeClient.from_settings()
        result = await client.generate_image(prompt, [])
        return ExternalPipelineResult(
            outputs={"master": (result.filename, result.image_bytes)},
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

        return save_asset_from_job(
            job,
            name=name,
            notes=notes,
            file_keys=["master"],
            meta={
                **dict(job.params or {}),
                "provider": "gpt",
                "review_status": "approved_by_chat",
            },
            project_id=project_id,
        )
