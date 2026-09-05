from __future__ import annotations

import asyncio
import io
import json
import wave

import httpx
import pytest
from PIL import Image

from app.config import Settings, settings
from app.core.jobs import runner, store
from app.core.schemas import JobStatus
from app.integrations.minimax_h3 import MiniMaxH3Client
from app.pipelines.h3_ref2va.pipeline import H3Ref2VaPipeline


def _image_bytes(format_name: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (80, 60, 40)).save(buffer, format=format_name)
    return buffer.getvalue()


def _wav_bytes(duration_s: float = 2.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * int(8000 * duration_s))
    return buffer.getvalue()


PNG = _image_bytes("PNG")
JPG = _image_bytes("JPEG")
WAV = _wav_bytes()
MP4 = b"\x00\x00\x00\x18ftypmp42generated"


@pytest.mark.parametrize("variable", ["MINIMAX_API_KEY", "DS_H3_MINIMAX_API_KEY"])
def test_settings_accepts_standard_and_director_studio_api_key_names(
    tmp_path, variable
):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{variable}=key-from-env-file\n", encoding="utf-8")

    configured = Settings(_env_file=env_file)

    assert configured.h3_minimax_api_key == "key-from-env-file"


def _api_job(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(settings, "jobs_dir", jobs_root)
    return store.create_job(
        pipeline_id="h3_ref2va",
        asset_kind="productions",
        name="official-h3",
        params={
            "h3_provider": "minimax",
            "prompt": "<Picture 1> is the character. <Picture 2> is the set.",
            "frames": 124,
            "duration_s": 5,
            "width": 864,
            "height": 480,
            "image_keys": ["ref_0", "ref_1"],
            "audio_keys": ["voice_audio_1"],
            "layout_picture_indices": [],
        },
    )


def test_h3_api_payload_preserves_picture_and_audio_order(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "h3_minimax_resolution", "768P")
    job = _api_job(tmp_path, monkeypatch)
    payload = H3Ref2VaPipeline().build_api_payload(
        job,
        inputs={
            "ref_1": ("set.jpg", JPG),
            "voice_audio_1": ("voice.wav", WAV),
            "ref_0": ("actor.png", PNG),
        },
    )

    assert payload["model"] == "MiniMax-H3"
    assert payload["duration"] == 5
    assert payload["resolution"] == "768P"
    assert payload["ratio"] == "16:9"
    assert payload["content"][0] == {
        "type": "text",
        "text": "<Picture 1> is the character. <Picture 2> is the set.",
    }
    assert [item["role"] for item in payload["content"][1:]] == [
        "reference_image",
        "reference_image",
        "reference_audio",
    ]
    assert payload["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert payload["content"][2]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert payload["content"][3]["audio_url"]["url"].startswith(
        "data:audio/wav;base64,"
    )


def test_h3_api_payload_rejects_unsupported_or_oversized_inputs(monkeypatch, tmp_path):
    job = _api_job(tmp_path, monkeypatch)
    pipeline = H3Ref2VaPipeline()

    with pytest.raises(ValueError, match="Unsupported H3 API image format"):
        pipeline.build_api_payload(
            job,
            inputs={
                "ref_0": ("actor.gif", PNG),
                "ref_1": ("set.jpg", JPG),
                "voice_audio_1": ("voice.wav", WAV),
            },
        )

    monkeypatch.setattr(settings, "h3_minimax_max_request_mb", 0.000001)
    with pytest.raises(ValueError, match="64 MB|request body"):
        pipeline.build_api_payload(
            job,
            inputs={
                "ref_0": ("actor.png", PNG),
                "ref_1": ("set.jpg", JPG),
                "voice_audio_1": ("voice.wav", WAV),
            },
        )

    job.params["prompt"] = "x" * 7001
    with pytest.raises(ValueError, match="7000"):
        pipeline.build_api_payload(
            job,
            inputs={
                "ref_0": ("actor.png", PNG),
                "ref_1": ("set.jpg", JPG),
                "voice_audio_1": ("voice.wav", WAV),
            },
        )


def test_h3_api_uses_documented_mp3_data_uri_format(monkeypatch, tmp_path):
    job = _api_job(tmp_path, monkeypatch)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(pipeline, "_audio_duration_seconds", lambda *_args: 2.0)
    payload = pipeline.build_api_payload(
        job,
        inputs={
            "ref_0": ("actor.png", PNG),
            "ref_1": ("set.jpg", JPG),
            "voice_audio_1": ("voice.mp3", b"ID3audio"),
        },
    )

    assert payload["content"][3]["audio_url"]["url"].startswith(
        "data:audio/mp3;base64,"
    )


def test_h3_api_preflights_media_and_generation_constraints(monkeypatch, tmp_path):
    pipeline = H3Ref2VaPipeline()
    job = _api_job(tmp_path, monkeypatch)
    valid_inputs = {
        "ref_0": ("actor.png", PNG),
        "ref_1": ("set.jpg", JPG),
        "voice_audio_1": ("voice.wav", WAV),
    }

    job.params["duration_s"] = 5.5
    with pytest.raises(ValueError, match="integer"):
        pipeline.build_api_payload(job, inputs=valid_inputs)
    job.params["duration_s"] = 5

    monkeypatch.setattr(settings, "h3_minimax_model", "MiniMax-H3-Max")
    with pytest.raises(ValueError, match="MiniMax-H3"):
        pipeline.build_api_payload(job, inputs=valid_inputs)
    monkeypatch.setattr(settings, "h3_minimax_model", "MiniMax-H3")

    monkeypatch.setattr(settings, "h3_minimax_resolution", "480P")
    with pytest.raises(ValueError, match="768P|2K"):
        pipeline.build_api_payload(job, inputs=valid_inputs)
    monkeypatch.setattr(settings, "h3_minimax_resolution", "768P")

    small_png = io.BytesIO()
    Image.new("RGB", (128, 256)).save(small_png, format="PNG")
    with pytest.raises(ValueError, match="256.*5760"):
        pipeline.build_api_payload(
            job,
            inputs={**valid_inputs, "ref_0": ("actor.png", small_png.getvalue())},
        )

    with pytest.raises(ValueError, match="2.*15 seconds"):
        pipeline.build_api_payload(
            job,
            inputs={**valid_inputs, "voice_audio_1": ("voice.wav", _wav_bytes(1.5))},
        )

    job.params["native_audio_key"] = "native_audio"
    with pytest.raises(ValueError, match="locked source audio|local H3"):
        pipeline.build_api_payload(
            job,
            inputs={**valid_inputs, "native_audio": ("source.wav", WAV)},
        )


@pytest.mark.asyncio
async def test_minimax_client_submits_polls_and_downloads_with_bearer_auth():
    requests: list[httpx.Request] = []
    query_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_count
        requests.append(request)
        if request.url.host == "api.minimax.io":
            assert request.headers["authorization"] == "Bearer secret-test-key"
        else:
            assert "authorization" not in request.headers
        if request.method == "POST":
            assert json.loads(request.content)["model"] == "MiniMax-H3"
            return httpx.Response(200, json={"task_id": "task_123"})
        if request.url.path.endswith("/task_123"):
            query_count += 1
            status = "running" if query_count == 1 else "succeeded"
            content = {"url": "https://cdn.example/output.mp4"} if status == "succeeded" else {}
            return httpx.Response(
                200,
                json={"task": {"id": "task_123", "status": status, "content": content}},
            )
        return httpx.Response(200, content=MP4)

    client = MiniMaxH3Client(
        api_key="secret-test-key",
        poll_interval_sec=0,
        transport=httpx.MockTransport(handler),
    )
    task_id = await client.create_video(
        {"model": "MiniMax-H3", "content": [{"type": "text", "text": "shot"}]}
    )
    result = await client.wait_for_video(task_id, cancel_event=asyncio.Event())
    video = await client.download_video(result.download_url)

    assert task_id == "task_123"
    assert result.task["status"] == "succeeded"
    assert video == MP4
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET"]


@pytest.mark.asyncio
async def test_minimax_client_reports_api_error_without_leaking_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authorized_error", "message": "login failed"},
            },
        )

    client = MiniMaxH3Client(
        api_key="do-not-leak-this-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="login failed") as caught:
        await client.create_video(
            {"model": "MiniMax-H3", "content": [{"type": "text", "text": "shot"}]}
        )
    assert "do-not-leak-this-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_minimax_client_retries_safe_query_and_download_gets():
    query_count = 0
    download_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_count, download_count
        if request.url.path.endswith("/task_retry"):
            query_count += 1
            if query_count == 1:
                return httpx.Response(503, content=b"temporary upstream failure")
            return httpx.Response(
                200,
                json={
                    "task": {
                        "id": "task_retry",
                        "status": "succeeded",
                        "content": {"url": "https://cdn.example/retry.mp4"},
                    }
                },
            )
        download_count += 1
        if download_count == 1:
            return httpx.Response(503, content=b"temporary")
        return httpx.Response(200, content=MP4)

    client = MiniMaxH3Client(
        api_key="secret-test-key",
        poll_interval_sec=0,
        max_get_retries=2,
        retry_delay_sec=0,
        transport=httpx.MockTransport(handler),
    )
    result = await client.wait_for_video("task_retry", cancel_event=asyncio.Event())
    video = await client.download_video(result.download_url)

    assert query_count == 2
    assert download_count == 2
    assert video == MP4


@pytest.mark.asyncio
async def test_h3_api_runner_persists_remote_task_without_comfy_or_vram(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)

    class FakeClient:
        async def create_video(self, payload):
            assert payload["content"][1]["role"] == "reference_image"
            return "task_remote_1"

        async def wait_for_video(self, task_id, *, cancel_event):
            assert task_id == "task_remote_1"
            return type(
                "Result",
                (),
                {
                    "download_url": "https://cdn.example/generated.mp4",
                    "task": {"status": "succeeded", "usage": {"total_seconds": 5}},
                },
            )()

        async def download_video(self, url):
            return MP4

    monkeypatch.setattr(runner, "MiniMaxH3Client", FakeClient)
    monkeypatch.setattr(
        runner,
        "ComfyClient",
        lambda: (_ for _ in ()).throw(AssertionError("Comfy must not be created")),
    )

    class ForbiddenOrchestrator:
        def __getattr__(self, name):
            raise AssertionError(f"VRAM orchestrator must not be called: {name}")

    monkeypatch.setattr(runner, "get_orchestrator", lambda: ForbiddenOrchestrator())
    await runner.start_pipeline_job(
        job,
        images={
            "ref_0": ("actor.png", PNG),
            "ref_1": ("set.jpg", JPG),
            "voice_audio_1": ("voice.wav", WAV),
        },
    )
    final = await runner.await_pipeline_job(job.id)

    assert final is not None
    assert final.status == JobStatus.succeeded
    assert final.external_task_id == "task_remote_1"
    assert final.params["minimax_task"]["usage"]["total_seconds"] == 5
    assert final.outputs["video"].filename == "video.mp4"
    assert (store.job_dir(job.id) / "outputs" / "video.mp4").read_bytes() == MP4


@pytest.mark.asyncio
async def test_h3_api_cancel_during_download_cannot_overwrite_cancelled_status(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)

    class CancellingClient:
        async def create_video(self, payload):
            return "task_cancel_race"

        async def wait_for_video(self, task_id, *, cancel_event):
            self.cancel_event = cancel_event
            return type(
                "Result",
                (),
                {
                    "download_url": "https://cdn.example/generated.mp4",
                    "task": {"status": "succeeded"},
                },
            )()

        async def download_video(self, url):
            self.cancel_event.set()
            return MP4

    monkeypatch.setattr(runner, "MiniMaxH3Client", CancellingClient)
    await runner.start_pipeline_job(
        job,
        images={
            "ref_0": ("actor.png", PNG),
            "ref_1": ("set.jpg", JPG),
            "voice_audio_1": ("voice.wav", WAV),
        },
    )
    final = await runner.await_pipeline_job(job.id)

    assert final is not None
    assert final.status == JobStatus.cancelled
    assert not final.outputs


@pytest.mark.asyncio
async def test_h3_api_recovery_resumes_remote_task_without_resubmitting(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    job.status = JobStatus.running
    job.external_task_id = "task_already_submitted"
    store.save_job(job)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)

    class ResumeClient:
        async def create_video(self, payload):
            raise AssertionError("recovery must not submit a second paid task")

        async def wait_for_video(self, task_id, *, cancel_event):
            assert task_id == "task_already_submitted"
            return type(
                "Result",
                (),
                {
                    "download_url": "https://cdn.example/resumed.mp4",
                    "task": {"status": "succeeded", "id": task_id},
                },
            )()

        async def download_video(self, url):
            return MP4

    monkeypatch.setattr(runner, "MiniMaxH3Client", ResumeClient)

    recovered = await runner.recover_interrupted_jobs()
    final = await runner.await_pipeline_job(job.id)

    assert recovered == [job.id]
    assert final is not None
    assert final.status == JobStatus.succeeded
    assert final.external_task_id == "task_already_submitted"


@pytest.mark.asyncio
async def test_h3_api_recovery_never_replays_uncertain_unpersisted_submission(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    job.status = JobStatus.running
    store.save_job(job)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)

    async def forbidden_start(*args, **kwargs):
        raise AssertionError("an uncertain paid API request must not be replayed")

    monkeypatch.setattr(runner, "start_pipeline_job", forbidden_start)

    recovered = await runner.recover_interrupted_jobs()
    final = store.load_job(job.id)

    assert recovered == []
    assert final is not None
    assert final.status == JobStatus.failed
    assert "generate again" in (final.error or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JobStatus.queued, JobStatus.uploading])
async def test_h3_api_recovery_replays_only_provably_presubmit_statuses(
    tmp_path, monkeypatch, status
):
    job = _api_job(tmp_path, monkeypatch)
    job.status = status
    store.save_job(job)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)
    started: list[str] = []

    async def fake_start(replayed, *, images=None):
        started.append(replayed.id)
        return replayed

    monkeypatch.setattr(runner, "start_pipeline_job", fake_start)

    recovered = await runner.recover_interrupted_jobs()

    assert recovered == [job.id]
    assert started == [job.id]


@pytest.mark.asyncio
async def test_cloud_cancel_message_discloses_remote_task_may_continue(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    job.status = JobStatus.running
    job.external_task_id = "task_paid_running"
    store.save_job(job)
    monkeypatch.setattr(
        runner,
        "get_pipeline",
        lambda _pipeline_id: H3Ref2VaPipeline(),
    )

    cancelled = await runner.cancel_job(job.id)

    assert cancelled is not None
    assert cancelled.status == JobStatus.cancelled
    assert "polling cancelled locally" in (cancelled.error or "").lower()
    assert "task_paid_running" in (cancelled.error or "")
    assert "may continue" in (cancelled.error or "").lower()


@pytest.mark.asyncio
async def test_cancel_during_ambiguous_create_warns_against_duplicate_resubmit(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    pipeline = H3Ref2VaPipeline()
    monkeypatch.setattr(runner, "get_pipeline", lambda _pipeline_id: pipeline)
    post_started = asyncio.Event()
    release_post = asyncio.Event()

    class AmbiguousCreateClient:
        async def create_video(self, payload):
            post_started.set()
            await release_post.wait()
            raise httpx.ReadTimeout("response was lost after POST")

    monkeypatch.setattr(runner, "MiniMaxH3Client", AmbiguousCreateClient)
    await runner.start_pipeline_job(
        job,
        images={
            "ref_0": ("actor.png", PNG),
            "ref_1": ("set.jpg", JPG),
            "voice_audio_1": ("voice.wav", WAV),
        },
    )
    await asyncio.wait_for(post_started.wait(), timeout=2)

    immediate = await runner.cancel_job(job.id)
    release_post.set()
    final = await runner.await_pipeline_job(job.id)

    assert immediate is not None
    assert "submission outcome is uncertain" in (immediate.error or "").lower()
    assert final is not None
    assert final.status == JobStatus.cancelled
    assert "submission outcome is uncertain" in (final.error or "").lower()
    assert "may continue" in (final.error or "").lower()


def test_h3_api_rejects_missing_duplicate_or_overlapping_declared_keys(
    monkeypatch, tmp_path
):
    pipeline = H3Ref2VaPipeline()
    job = _api_job(tmp_path, monkeypatch)
    inputs = {
        "ref_0": ("actor.png", PNG),
        "ref_1": ("set.jpg", JPG),
        "voice_audio_1": ("voice.wav", WAV),
    }

    job.params["image_keys"] = ["ref_0", "missing"]
    with pytest.raises(ValueError, match="missing"):
        pipeline.build_api_payload(job, inputs=inputs)

    job.params["image_keys"] = ["ref_0", "ref_0"]
    with pytest.raises(ValueError, match="duplicate"):
        pipeline.build_api_payload(job, inputs=inputs)

    job.params["image_keys"] = ["ref_0", "ref_1"]
    job.params["audio_keys"] = ["ref_1"]
    with pytest.raises(ValueError, match="both image and audio"):
        pipeline.build_api_payload(job, inputs=inputs)


@pytest.mark.asyncio
async def test_h3_api_resume_endpoint_restarts_monitoring_existing_paid_task(
    tmp_path, monkeypatch
):
    job = _api_job(tmp_path, monkeypatch)
    job.status = JobStatus.failed
    job.error = "temporary query failure"
    job.external_task_id = "task_resume_paid"
    store.save_job(job)
    resumed: list[str] = []

    import app.pipelines.h3_ref2va.router as h3_router

    async def fake_resume(candidate):
        resumed.append(candidate.id)
        return candidate

    monkeypatch.setattr(h3_router, "resume_pipeline_job", fake_resume)

    response = await h3_router.resume_h3_job(job.id)

    assert response.status == JobStatus.running
    assert response.external_task_id == "task_resume_paid"
    assert resumed == [job.id]
    persisted = store.load_job(job.id)
    assert persisted is not None
    assert persisted.status == JobStatus.running
    assert persisted.error is None
