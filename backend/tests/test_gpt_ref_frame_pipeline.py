from __future__ import annotations

import asyncio
import json

import pytest

from app.core.schemas import JobRecord, JobStatus
from app.integrations.chatgpt_bridge import BridgeGenerationResult, ChatGptBridgeClient
from app.pipelines.gpt_ref_frame.pipeline import GptRefFramePipeline


PNG = b"\x89PNG\r\n\x1a\nresult"


def _job() -> JobRecord:
    return JobRecord(
        id="job_gpt_pipeline",
        pipeline_id="gpt_ref_frame",
        asset_kind="layouts",
        status=JobStatus.queued,
        name="layout:GPT",
        params={
            "generation_prompt": "Image1 controls the set. Image2 controls Lu. Return one image.",
            "image_keys": ["ref_0", "ref_1"],
            "shot_id": "shot_01",
            "project_id": "project_01",
            "layout_source_refs": [
                {"role": "scene", "asset_id": "scene_01", "file_key": "angle_00"},
                {"role": "actor", "asset_id": "actor_lu", "file_key": "master"},
            ],
        },
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        project_id="project_01",
    )


@pytest.mark.asyncio
async def test_gpt_pipeline_returns_normal_layout_and_sanitized_provenance(monkeypatch):
    captured = {}

    class FakeClient:
        async def generate_image(self, prompt, ordered_images):
            captured["prompt"] = prompt
            captured["ordered_images"] = ordered_images
            return BridgeGenerationResult(
                image_bytes=PNG,
                filename="gpt-layout.png",
                mime="image/png",
                validated_format="png",
                request_id="request_public",
                session_id="session_public",
                artifact_id="artifact_public",
                artifact_name="image",
            )

    monkeypatch.setattr(ChatGptBridgeClient, "from_settings", lambda: FakeClient())
    inputs = {
        "ref_0": ("corridor.png", PNG),
        "ref_1": ("lu.png", PNG),
    }

    result = await GptRefFramePipeline().run_external(
        _job(),
        inputs=inputs,
        cancel_event=asyncio.Event(),
    )

    assert result.outputs == {"layout": ("gpt-layout.png", PNG)}
    assert [item[0] for item in captured["ordered_images"]] == ["Image1", "Image2"]
    assert [item[1] for item in captured["ordered_images"]] == ["corridor.png", "lu.png"]
    provenance = result.params_update["gpt_provenance"]
    assert provenance["provider"] == "gpt"
    assert provenance["artifact_id"] == "artifact_public"
    assert "token" not in json.dumps(provenance).lower()


@pytest.mark.asyncio
async def test_gpt_pipeline_submits_text_only_generation_without_attachments(monkeypatch):
    captured = {}

    class FakeClient:
        async def generate_image(self, prompt, ordered_images):
            captured["prompt"] = prompt
            captured["ordered_images"] = ordered_images
            return BridgeGenerationResult(
                image_bytes=PNG,
                filename="empty-room.png",
                mime="image/png",
                validated_format="png",
                request_id="request_text_only",
                session_id="session_text_only",
                artifact_id="artifact_text_only",
                artifact_name="image",
            )

    monkeypatch.setattr(ChatGptBridgeClient, "from_settings", lambda: FakeClient())
    job = _job().model_copy(
        update={
            "params": {
                "generation_prompt": "Create one cinematic establishing plate of an empty review room.",
                "image_keys": [],
                "shot_id": "shot_01",
                "project_id": "project_01",
                "layout_source_refs": [],
            }
        }
    )

    result = await GptRefFramePipeline().run_external(
        job,
        inputs={},
        cancel_event=asyncio.Event(),
    )

    assert result.outputs == {"layout": ("empty-room.png", PNG)}
    assert captured["ordered_images"] == []


@pytest.mark.asyncio
async def test_gpt_pipeline_refuses_missing_ordered_input(monkeypatch):
    monkeypatch.setattr(
        ChatGptBridgeClient,
        "from_settings",
        lambda: (_ for _ in ()).throw(AssertionError("Bridge must not be called")),
    )

    with pytest.raises(ValueError, match="ref_1"):
        await GptRefFramePipeline().run_external(
            _job(),
            inputs={"ref_0": ("corridor.png", PNG)},
            cancel_event=asyncio.Event(),
        )
