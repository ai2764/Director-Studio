from __future__ import annotations

import asyncio

import pytest

from app.core.schemas import JobRecord, JobStatus
from app.pipelines.gpt_actor.pipeline import GptActorPipeline


PNG = b"\x89PNG\r\n\x1a\n" + b"actor"


@pytest.mark.asyncio
async def test_text_only_gpt_actor_pipeline_returns_master_image(monkeypatch):
    pipeline = GptActorPipeline()
    job = JobRecord(
        id="job_actor_gpt",
        pipeline_id="gpt_actor",
        asset_kind="actors",
        status=JobStatus.queued,
        name="Mara",
        params={"generation_prompt": "Create one full-body character design."},
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    class Result:
        filename = "mara.png"
        image_bytes = PNG

        @staticmethod
        def provenance():
            return {"provider": "gpt", "artifact_id": "artifact_actor"}

    class Client:
        async def generate_image(self, prompt, ordered_images):
            assert prompt == "Create one full-body character design."
            assert ordered_images == []
            return Result()

    monkeypatch.setattr(
        "app.pipelines.gpt_actor.pipeline.ChatGptBridgeClient.from_settings",
        lambda: Client(),
    )

    result = await pipeline.run_external(
        job,
        inputs={},
        cancel_event=asyncio.Event(),
    )

    assert result.outputs == {"master": ("mara.png", PNG)}
    assert result.params_update["gpt_provenance"]["artifact_id"] == "artifact_actor"
