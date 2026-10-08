"""Context video uploads stay out of picture and audio reference slots."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.core.comfy.client import ComfyClient
from app.core.jobs.execution_adapters.comfy import comfy_input_name
from app.core.schemas import JobRecord, JobStatus
from app.integrations.comfy_mcp import ComfyMcpClient, ComfyMcpError
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline

VALID_PROMPT = (
    "subject_definitions:\n<Picture 1> is Mia. <Audio 1> defines Mia.\n"
    "summary:\nB\nretention_analysis:\nC\n"
    "detailed_description:\nD\noverall_soundscape:\nE\nnon_diegetic_music:\nF"
)


def _result(payload: dict):
    return SimpleNamespace(
        is_error=False,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


class FakeToolSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


def _job(**params) -> JobRecord:
    body = {
        "prompt": VALID_PROMPT,
        "dialogue": [],
        "frames": 124,
        "width": 864,
        "height": 480,
    }
    body.update(params)
    return JobRecord(
        id="job_context",
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        status=JobStatus.queued,
        name="context transport",
        params=body,
        created_at="2026-10-04T00:00:00+00:00",
        updated_at="2026-10-04T00:00:00+00:00",
    )


def test_each_job_keeps_its_own_video_suffix():
    first = comfy_input_name("job_a", "context_video", "clip.mp4")
    second = comfy_input_name("job_b", "context_video", "clip.MP4")

    assert first == "ds_job_a_context_video.mp4"
    assert second == "ds_job_b_context_video.MP4"
    assert first != second


@pytest.mark.asyncio
async def test_http_upload_keeps_the_returned_subfolder(monkeypatch):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = await request.aread()
        return httpx.Response(
            200,
            json={"name": "clip.mp4", "subfolder": "director/job_a", "type": "input"},
        )

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler), timeout=kwargs["timeout"])

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    stored = await ComfyClient("http://comfy.test").upload_image(
        b"mp4-bytes",
        comfy_input_name("job_a", "context_video", "clip.mp4"),
    )

    assert stored == "director/job_a/clip.mp4"
    assert b"video/mp4" in seen["body"]
    assert b"ds_job_a_context_video.mp4" in seen["body"]


@pytest.mark.asyncio
async def test_mcp_upload_keeps_subfolder_and_separates_jobs():
    stored = {}
    paths = {}
    for job_id in ("job_a", "job_b"):
        session = FakeToolSession(
            [
                _result(
                    {
                        "uploads": [
                            {
                                "name": "clip.mp4",
                                "subfolder": f"director/{job_id}",
                                "cloud_name": "shared.mp4",
                            }
                        ]
                    }
                )
            ]
        )
        uploaded = await ComfyMcpClient(session=session).upload_inputs(
            job_id,
            {"context_video": ("clip.mp4", b"video-bytes")},
        )
        stored[job_id] = uploaded["context_video"]
        name, arguments = session.calls[0]
        assert name == "upload_file"
        assert arguments["overwrite"] is True
        paths[job_id] = Path(arguments["paths"][0]).name

    assert stored == {
        "job_a": "director/job_a/clip.mp4",
        "job_b": "director/job_b/clip.mp4",
    }
    assert paths == {
        "job_a": "ds_job_a_context_video.mp4",
        "job_b": "ds_job_b_context_video.mp4",
    }
    assert len(set(stored.values())) == 2
    assert len(set(paths.values())) == 2


@pytest.mark.asyncio
async def test_mcp_upload_still_accepts_cloud_name_without_a_subfolder():
    session = FakeToolSession(
        [_result({"uploads": [{"cloud_name": "ds_job_1_context_video.mp4"}]})]
    )
    uploaded = await ComfyMcpClient(session=session).upload_inputs(
        "job_1",
        {"context_video": ("clip.mp4", b"video-bytes")},
    )

    assert uploaded == {"context_video": "ds_job_1_context_video.mp4"}


@pytest.mark.asyncio
async def test_mcp_upload_rejects_a_parent_directory():
    session = FakeToolSession(
        [_result({"uploads": [{"subfolder": "../secret", "filename": "clip.mp4"}]})]
    )
    with pytest.raises(ComfyMcpError, match="unsafe path"):
        await ComfyMcpClient(session=session).upload_inputs(
            "job_a",
            {"context_video": ("clip.mp4", b"video-bytes")},
        )


def test_context_video_is_not_a_picture_or_audio_reference():
    graph, _seed = H3Ref2VaPipeline().build_prompt(
        _job(
            image_keys=["picture_1"],
            audio_keys=["audio_1"],
            context_video_key="context_video",
            video_context_source={"context_frames": 22, "carry_audio": False},
        ),
        uploaded_images={
            "picture_1": "face.png",
            "audio_1": "song.wav",
            "context_video": "ds_job_context_context_video.mp4",
        },
    )
    images = [
        node["inputs"].get("image")
        for node in graph.values()
        if node.get("class_type") == "LoadImage"
    ]
    audios = [
        node["inputs"].get("audio")
        for node in graph.values()
        if node.get("class_type") == "LoadAudio"
    ]
    videos = [
        node["inputs"].get("file")
        for node in graph.values()
        if node.get("class_type") == "LoadVideo"
    ]
    assert images == ["face.png"]
    assert audios == ["song.wav"]
    assert videos == ["ds_job_context_context_video.mp4"]

    with pytest.raises(ValueError, match="picture reference"):
        H3Ref2VaPipeline().build_prompt(
            _job(image_keys=["picture_1", "context_video"]),
            uploaded_images={"picture_1": "face.png", "context_video": "clip.mp4"},
        )
    with pytest.raises(ValueError, match="audio reference"):
        H3Ref2VaPipeline().build_prompt(
            _job(image_keys=["picture_1"], audio_keys=["context_video"]),
            uploaded_images={"picture_1": "face.png", "context_video": "clip.mp4"},
        )
    with pytest.raises(ValueError, match="was not uploaded"):
        H3Ref2VaPipeline().build_prompt(
            _job(image_keys=["picture_1"], context_video_key="context_video"),
            uploaded_images={"picture_1": "face.png"},
        )


def test_upload_fallback_ignores_a_context_video_without_image_keys():
    graph, _seed = H3Ref2VaPipeline().build_prompt(
        _job(
            prompt=VALID_PROMPT.replace(" <Audio 1> defines Mia.", ""),
            context_video_key="context_video",
        ),
        uploaded_images={
            "context_video": "clip.mp4",
            "picture_1": "face.png",
        },
    )
    images = [
        node["inputs"].get("image")
        for node in graph.values()
        if node.get("class_type") == "LoadImage"
    ]
    assert images == ["face.png"]
    assert not any(
        node.get("class_type") == "LoadImage" and node["inputs"].get("image") == "clip.mp4"
        for node in graph.values()
    )
