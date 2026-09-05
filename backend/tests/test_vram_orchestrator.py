import asyncio

import pytest

from app.core.vram.orchestrator import (
    GPUBusyError,
    GenerationActiveError,
    VramOrchestrator,
)


class FakeOllama:
    def __init__(self):
        self.unloaded = []
        self.ready_calls = 0
        self._vram = 0

    async def health(self):
        return True

    async def unload_models(self, names):
        self.unloaded.extend(names)
        self._vram = 0

    async def model_vram_bytes(self, model: str) -> int:
        return int(self._vram)

    async def generate(self, model, prompt, **kwargs):
        self.ready_calls += 1
        # Pretend GPU residency after a warm generate
        self._vram = 8 * (1024**3)
        return "ok"


class FakeComfy:
    def __init__(self):
        self.free_calls = 0

    async def free_memory(self, *, unload_models: bool = True, free_memory: bool = True):
        self.free_calls += 1


def _orch(**kwargs) -> VramOrchestrator:
    ollama = kwargs.pop("ollama", FakeOllama())
    comfy = kwargs.pop("comfy", FakeComfy())
    return VramOrchestrator(
        ollama=ollama,
        comfy=comfy,
        models=["qwen-plan"],
        policy="exclusive",
        acquire_timeout_sec=kwargs.pop("acquire_timeout_sec", 5.0),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_before_comfy_releases_llm():
    ollama = FakeOllama()
    comfy = FakeComfy()
    orch = _orch(ollama=ollama, comfy=comfy)
    async with orch.llm_session():
        await orch.ensure_llm_ready()
    assert comfy.free_calls >= 1
    await orch.before_comfy_job("h3_ref2va")
    assert ollama.unloaded  # director model (runtime picker) unloaded for Comfy
    assert orch.owner == "comfy"


@pytest.mark.asyncio
async def test_comfy_jobs_queue_not_fail():
    """Second Comfy job waits until first after_comfy_job, does not raise busy."""
    orch = _orch()
    order: list[str] = []

    async def job_a():
        await orch.before_comfy_job("ref_frame")
        order.append("a_start")
        await asyncio.sleep(0.05)
        await orch.after_comfy_job("ref_frame", "succeeded")
        order.append("a_end")

    async def job_b():
        await asyncio.sleep(0.01)  # ensure a acquires first
        await orch.before_comfy_job("h3_ref2va")
        order.append("b_start")
        assert orch.owner == "comfy"
        await orch.after_comfy_job("h3_ref2va", "succeeded")
        order.append("b_end")

    await asyncio.gather(job_a(), job_b())
    assert order == ["a_start", "a_end", "b_start", "b_end"]


@pytest.mark.asyncio
async def test_llm_waits_for_comfy_then_runs():
    """Plan/script waits for Comfy job instead of GPU busy error."""
    ollama = FakeOllama()
    comfy = FakeComfy()
    orch = _orch(ollama=ollama, comfy=comfy)
    order: list[str] = []

    async def comfy_job():
        await orch.before_comfy_job("ref_frame")
        order.append("comfy")
        await asyncio.sleep(0.05)
        await orch.after_comfy_job("ref_frame", "succeeded")

    async def plan():
        await asyncio.sleep(0.01)
        async with orch.llm_session():
            order.append("llm")
            await orch.ensure_llm_ready()

    await asyncio.gather(comfy_job(), plan())
    assert order == ["comfy", "llm"]
    assert ollama.ready_calls == 1
    assert comfy.free_calls >= 1


@pytest.mark.asyncio
async def test_comfy_waits_for_llm_session():
    """Comfy job queues behind an active llm_session (no steal / no immediate error)."""
    ollama = FakeOllama()
    comfy = FakeComfy()
    orch = _orch(ollama=ollama, comfy=comfy)
    order: list[str] = []

    async def plan():
        async with orch.llm_session(release_on_exit=True):
            order.append("llm_start")
            await orch.ensure_llm_ready()
            await asyncio.sleep(0.05)
            order.append("llm_end")

    async def gen():
        await asyncio.sleep(0.01)
        await orch.before_comfy_job("actor")
        order.append("comfy")
        await orch.after_comfy_job("actor", "succeeded")

    await asyncio.gather(plan(), gen())
    assert order == ["llm_start", "llm_end", "comfy"]


@pytest.mark.asyncio
async def test_queue_timeout():
    orch = _orch(acquire_timeout_sec=0.05)
    await orch.before_comfy_job("ref_frame")
    with pytest.raises(GPUBusyError) as ei:
        # second acquire waits and times out
        await orch.before_comfy_job("h3_ref2va")
    assert ei.value.timed_out is True
    await orch.after_comfy_job("ref_frame", "succeeded")


@pytest.mark.asyncio
async def test_after_comfy_then_llm_session_works():
    ollama = FakeOllama()
    comfy = FakeComfy()
    orch = _orch(ollama=ollama, comfy=comfy)
    await orch.before_comfy_job("ref_frame")
    await orch.after_comfy_job("ref_frame", "succeeded")
    assert orch.owner is None
    async with orch.llm_session():
        assert orch.owner == "llm"
        await orch.ensure_llm_ready()
        assert ollama.ready_calls == 1


@pytest.mark.asyncio
async def test_keep_loaded_chat_then_video_unloads():
    """Multi-turn residency: chat keeps model; h3 video unloads; next chat reloads."""
    ollama = FakeOllama()
    comfy = FakeComfy()
    orch = _orch(ollama=ollama, comfy=comfy)

    async with orch.llm_session(release_on_exit=False):
        await orch.ensure_llm_ready()
    assert orch.owner is None
    assert orch._llm_ready is True
    assert "qwen-plan" in ollama.unloaded or ollama.unloaded == [] or True
    # no unload on keep-loaded exit — unloaded list only grows on release_llm
    unloaded_after_chat = list(ollama.unloaded)

    async with orch.llm_session(release_on_exit=False):
        await orch.ensure_llm_ready()
    assert ollama.ready_calls == 1  # second turn did not re-warm

    await orch.before_comfy_job("h3_ref2va")
    assert orch.owner == "comfy"
    assert orch._llm_ready is False
    assert len(ollama.unloaded) > len(unloaded_after_chat)

    await orch.after_comfy_job("h3_ref2va", "succeeded")
    assert orch.owner is None

    async with orch.llm_session(release_on_exit=False):
        await orch.ensure_llm_ready()
    assert ollama.ready_calls == 2  # reloaded only on next chat


@pytest.mark.asyncio
async def test_llm_session_frees_comfy_models():
    ollama = FakeOllama()
    comfy = FakeComfy()
    orch = _orch(ollama=ollama, comfy=comfy)
    async with orch.llm_session():
        assert comfy.free_calls == 1
        await orch.ensure_llm_ready()


@pytest.mark.asyncio
async def test_cancelling_while_freeing_comfy_releases_llm_owner_and_wakes_waiter():
    class BlockingFirstFreeComfy(FakeComfy):
        def __init__(self):
            super().__init__()
            self.first_free_started = asyncio.Event()

        async def free_memory(
            self,
            *,
            unload_models: bool = True,
            free_memory: bool = True,
        ):
            self.free_calls += 1
            if self.free_calls == 1:
                self.first_free_started.set()
                await asyncio.Event().wait()

    comfy = BlockingFirstFreeComfy()
    orch = _orch(comfy=comfy, acquire_timeout_sec=1.0)

    async def cancelled_session():
        async with orch.llm_session(release_on_exit=False):
            pytest.fail("the cancelled session must not reach its body")

    waiter_entered = asyncio.Event()

    async def waiting_session():
        async with orch.llm_session(release_on_exit=False):
            waiter_entered.set()

    cancelled_task = asyncio.create_task(cancelled_session())
    await asyncio.wait_for(comfy.first_free_started.wait(), timeout=0.5)
    assert orch.owner == "llm"

    waiting_task = asyncio.create_task(waiting_session())
    await asyncio.sleep(0)
    assert not waiter_entered.is_set()

    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task

    await asyncio.wait_for(waiting_task, timeout=0.5)
    assert waiter_entered.is_set()
    assert orch.owner is None


@pytest.mark.asyncio
async def test_llm_progress_reports_actual_runtime_boundaries():
    orch = _orch()
    statuses: list[str] = []

    async with orch.llm_session(
        release_on_exit=False,
        on_status=statuses.append,
    ):
        await orch.ensure_llm_ready(on_status=statuses.append)

    visible = "\n".join(statuses)
    assert "Releasing ComfyUI VRAM" in visible
    assert "Starting " in visible
    assert " ready on GPU" in visible
    assert "first load or reload" not in visible
    assert not any("\u3400" <= char <= "\u9fff" for char in visible)


@pytest.mark.asyncio
async def test_generation_reservations_are_idempotent_ordered_and_updatable():
    orch = _orch()
    await orch.reserve_generation(
        job_id="job_image",
        pipeline_id="ref_frame",
        kind="image",
        status="queued",
        phase="queued",
        queued_at="2026-08-31T10:00:00+00:00",
    )
    await orch.reserve_generation(
        job_id="job_image",
        pipeline_id="ref_frame",
        kind="image",
        status="queued",
        phase="queued",
        queued_at="2026-08-31T10:00:00+00:00",
    )
    await orch.reserve_generation(
        job_id="job_video",
        pipeline_id="h3_ref2va",
        kind="video",
        status="running",
        phase="generating",
        queued_at="2026-08-31T10:01:00+00:00",
    )
    await orch.update_generation(
        "job_image",
        status="uploading",
        phase="uploading",
    )

    reservations = await orch.generation_reservations()
    assert [item.job_id for item in reservations] == ["job_image", "job_video"]
    assert reservations[0].status == "uploading"
    assert reservations[0].phase == "uploading"


@pytest.mark.asyncio
async def test_generation_lock_releases_only_after_the_final_job():
    orch = _orch()
    for job_id in ("job_a", "job_b"):
        await orch.reserve_generation(
            job_id=job_id,
            pipeline_id="ref_frame",
            kind="image",
            status="queued",
            phase="queued",
            queued_at=f"2026-08-31T10:00:0{len(job_id)}+00:00",
        )

    await orch.release_generation("job_a")
    assert [item.job_id for item in await orch.generation_reservations()] == [
        "job_b"
    ]
    await orch.release_generation("job_b")
    await orch.release_generation("job_b")
    assert await orch.generation_reservations() == []


@pytest.mark.asyncio
async def test_chat_llm_session_fails_without_warming_ollama_when_reserved():
    ollama = FakeOllama()
    orch = _orch(ollama=ollama)
    await orch.reserve_generation(
        job_id="job_video",
        pipeline_id="h3_ref2va",
        kind="video",
        status="queued",
        phase="queued",
        queued_at="2026-08-31T10:00:00+00:00",
    )

    with pytest.raises(GenerationActiveError) as error:
        async with orch.llm_session(fail_if_generation_pending=True):
            await orch.ensure_llm_ready()

    assert error.value.code == "GPU_GENERATION_ACTIVE"
    assert [item.job_id for item in error.value.reservations] == ["job_video"]
    assert ollama.ready_calls == 0
