from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, AsyncIterator, Literal, Protocol, Sequence

from ...config import settings
from .ollama_client import OllamaClient

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("director_studio.vram")

Owner = Literal["llm", "comfy"] | None
GenerationKind = Literal["image", "video"]
GenerationPhase = Literal["queued", "uploading", "generating", "saving"]


@dataclass(frozen=True)
class GenerationReservation:
    job_id: str
    pipeline_id: str
    kind: GenerationKind
    status: str
    phase: GenerationPhase
    queued_at: str


class GPUBusyError(RuntimeError):
    """Raised when exclusive VRAM wait times out (queue wait exceeded)."""

    def __init__(self, owner: str, *, timed_out: bool = False) -> None:
        self.owner = owner
        self.timed_out = timed_out
        if timed_out:
            super().__init__(
                f"GPU queue timeout waiting while busy: {owner}"
            )
        else:
            super().__init__(f"GPU busy: {owner}")


class GenerationActiveError(GPUBusyError):
    """Raised when a new chat turn would cut ahead of local generation."""

    code = "GPU_GENERATION_ACTIVE"

    def __init__(self, reservations: Sequence[GenerationReservation]) -> None:
        self.reservations = tuple(reservations)
        super().__init__("comfy")


# Alias for callers / tests that look for GPU_BUSY
GPU_BUSY = GPUBusyError


class ComfyFreeClient(Protocol):
    async def free_memory(
        self, *, unload_models: bool = True, free_memory: bool = True
    ) -> None: ...


class VramOrchestrator:
    """
    Exclusive GPU ownership between Ollama (LLM) and ComfyUI jobs.

    Contended acquires **queue** (wait) instead of failing immediately.
    - Before Comfy: wait for free GPU, unload Ollama, set owner=comfy
    - Before LLM: wait for free GPU, unload Comfy models via POST /free, set owner=llm
    """

    def __init__(
        self,
        *,
        provider: "LLMProvider | None" = None,
        ollama: OllamaClient | None = None,
        comfy: ComfyFreeClient | None = None,
        models: Sequence[str] | None = None,
        policy: str = "exclusive",
        acquire_timeout_sec: float | None = None,
    ) -> None:
        if provider is None:
            from ..llm.ollama import OllamaLLMProvider

            provider = OllamaLLMProvider(ollama or OllamaClient())
        self.provider = provider
        # Compatibility alias for older callers/tests while inference migrates.
        self.ollama = provider.client
        self.comfy = comfy  # lazy default via _get_comfy if None
        if models is not None:
            self.models = list(models)
        else:
            from .director_model import get_director_model

            self.models = [get_director_model(provider.provider_id)]
        self.policy = policy or "exclusive"
        self.acquire_timeout_sec = (
            acquire_timeout_sec
            if acquire_timeout_sec is not None
            else float(getattr(settings, "vram_acquire_timeout_sec", 3600.0))
        )
        self._lock = asyncio.Lock()
        self._cv = asyncio.Condition(self._lock)
        self.owner: Owner = None
        self.comfy_pipeline: str | None = None
        self._llm_ready: bool = False
        self._waiters: int = 0  # debug / status
        self._generation_reservations: dict[str, GenerationReservation] = {}
        self.last_comfy_free: dict | None = None
        self.last_comfy_free_error: str | None = None

    def _generation_snapshot_unlocked(self) -> list[GenerationReservation]:
        return sorted(
            self._generation_reservations.values(),
            key=lambda item: (item.queued_at, item.job_id),
        )

    async def reserve_generation(
        self,
        *,
        job_id: str,
        pipeline_id: str,
        kind: GenerationKind,
        status: str,
        phase: GenerationPhase,
        queued_at: str,
    ) -> None:
        """Register one queued/active local generation job idempotently."""
        reservation = GenerationReservation(
            job_id=job_id,
            pipeline_id=pipeline_id,
            kind=kind,
            status=status,
            phase=phase,
            queued_at=queued_at,
        )
        async with self._cv:
            self._generation_reservations[job_id] = reservation
            self._cv.notify_all()

    async def update_generation(
        self,
        job_id: str,
        *,
        status: str,
        phase: GenerationPhase,
    ) -> None:
        """Update a reservation phase without creating an unknown job."""
        async with self._cv:
            current = self._generation_reservations.get(job_id)
            if current is None:
                return
            self._generation_reservations[job_id] = replace(
                current,
                status=status,
                phase=phase,
            )

    async def release_generation(self, job_id: str) -> None:
        """Release one local generation reservation idempotently."""
        async with self._cv:
            self._generation_reservations.pop(job_id, None)
            self._cv.notify_all()

    async def generation_reservations(self) -> list[GenerationReservation]:
        """Return a stable queue-ordered snapshot for APIs and admission checks."""
        async with self._cv:
            return self._generation_snapshot_unlocked()

    def _get_comfy(self) -> ComfyFreeClient:
        if self.comfy is not None:
            return self.comfy
        from ..comfy.client import ComfyClient

        self.comfy = ComfyClient()
        return self.comfy

    async def release_llm(self) -> None:
        """Release configured models through the active provider lifecycle."""
        # Always unload the *current* director model + any previously tracked names.
        from .director_model import get_director_model

        names = list(
            dict.fromkeys(
                [
                    *(self.models or []),
                    get_director_model(self.provider.provider_id),
                ]
            )
        )
        await self.provider.lifecycle.release(names)
        self._llm_ready = False
        if self.owner == "llm":
            self.owner = None
        logger.info("%s LLM release requested for %s", self.provider.provider_id, names)

    async def release_comfy_models(self, *, require_ok: bool = False) -> dict | None:
        """
        Unload ComfyUI image/video models and free VRAM.

        Always attempted before Plan/LLM so image models do not stay resident.
        """
        try:
            stats = await self._get_comfy().free_memory(
                unload_models=True, free_memory=True
            )
            self.last_comfy_free = stats if isinstance(stats, dict) else {"ok": True}
            self.last_comfy_free_error = None
            gained = (
                (stats or {}).get("vram_free_gained") if isinstance(stats, dict) else None
            )
            logger.info(
                "ComfyUI models unloaded via /free (vram_free_gained=%s)",
                gained,
            )
            return self.last_comfy_free
        except Exception as exc:
            self.last_comfy_free_error = str(exc)
            logger.exception("ComfyUI free_memory failed: %s", exc)
            if require_ok:
                raise
            return None

    async def _wait_until_free(self, *, want: str) -> None:
        """
        Wait under condition until owner is None.

        Must be called with self._cv held (async with self._cv).
        """
        timeout = self.acquire_timeout_sec
        if timeout <= 0:
            timeout = None

        while self.owner is not None:
            busy = self.owner
            self._waiters += 1
            logger.info(
                "GPU queue: %s waiting (held by %s, pipeline=%s, waiters=%s)",
                want,
                busy,
                self.comfy_pipeline,
                self._waiters,
            )
            try:
                try:
                    await asyncio.wait_for(self._cv.wait(), timeout=timeout)
                except TimeoutError as exc:
                    raise GPUBusyError(str(busy), timed_out=True) from exc
            finally:
                self._waiters = max(0, self._waiters - 1)

    async def ensure_llm_ready(self, on_status=None) -> None:
        """
        Health-check Ollama and warm the primary plan model if needed.

        Must run inside an active llm_session (owner=="llm").
        Forces GPU offload (num_gpu) and logs if model ends up on CPU only.

        ``on_status`` is an optional async callable(str) for UI progress
        (e.g. "Loading ornith:35b onto the GPU…").
        """
        local_gpu = self.provider.lifecycle.uses_local_gpu
        if local_gpu and self.owner == "comfy":
            raise GPUBusyError("comfy")
        if local_gpu and self.owner != "llm":
            raise RuntimeError("ensure_llm_ready requires active llm_session (owner=llm)")

        from .director_model import get_director_model

        model = get_director_model(self.provider.provider_id)
        # Keep unload list in sync with runtime model picker
        self.models = [model]
        try:
            await self.provider.lifecycle.prepare(
                model,
                on_status=on_status,
            )
        except Exception as exc:
            self._llm_ready = False
            raise RuntimeError(
                f"Failed to prepare {self.provider.provider_id} model {model!r}: {exc}"
            ) from exc
        self._llm_ready = True

    async def before_comfy_job(self, pipeline_id: str) -> None:
        """
        Queue for exclusive GPU for a Comfy job (ref_frame / h3 video / casting…).

        Waits if another Comfy job or LLM session holds the GPU, then **always
        unloads Ollama** so image/video generation owns VRAM. Next chat/plan
        reloads the LLM via ensure_llm_ready.
        """
        async with self._cv:
            await self._wait_until_free(want=f"comfy:{pipeline_id}")
            # Local runtimes must release weights before Comfy takes the GPU.
            was_ready = self._llm_ready
            if self.provider.lifecycle.uses_local_gpu:
                try:
                    await self.release_llm()
                except Exception:
                    logger.exception("release_llm during before_comfy_job failed")
                    self._llm_ready = False
                    if self.provider.lifecycle.release_failure_is_fatal:
                        raise
            self.owner = "comfy"
            self.comfy_pipeline = pipeline_id
            logger.info(
                "GPU acquired by comfy pipeline=%s (provider=%s released=%s, was_ready=%s)",
                pipeline_id,
                self.provider.provider_id,
                self.provider.lifecycle.uses_local_gpu,
                was_ready,
            )

    async def after_comfy_job(self, pipeline_id: str, terminal_status: str) -> None:
        """Release Comfy ownership, free Comfy models, wake queue waiters."""
        async with self._cv:
            if self.owner == "comfy" and (
                self.comfy_pipeline is None or self.comfy_pipeline == pipeline_id
            ):
                self.owner = None
                self.comfy_pipeline = None
            _ = terminal_status
            self._cv.notify_all()
            logger.info(
                "GPU released by comfy pipeline=%s status=%s",
                pipeline_id,
                terminal_status,
            )
        # Outside lock: network free may take a moment
        await self.release_comfy_models()

    @asynccontextmanager
    async def llm_session(
        self,
        *,
        release_on_exit: bool = True,
        on_status=None,
        fail_if_generation_pending: bool = False,
    ) -> AsyncIterator[VramOrchestrator]:
        """
        Queue for exclusive GPU for LLM work.

        Waits if Comfy (or another LLM session) holds the GPU. Then unloads
        Comfy models (POST /free) so Plan/script can use VRAM.

        release_on_exit:
          True  — unload Ollama + clear owner (default; free VRAM for Comfy)
          False — multi-turn residency: clear owner so Comfy can still queue,
                  but keep Ollama loaded (_llm_ready stays True). Comfy path
                  calls release_llm / unload in before_comfy_job.

        on_status: optional async/sync callable(str) for UI while waiting / freeing.
        """

        async def _status(msg: str) -> None:
            logger.info("llm_session: %s", msg)
            if on_status is None:
                return
            try:
                res = on_status(msg)
                if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                    await res  # type: ignore[func-returns-value]
            except Exception:
                logger.exception("llm_session on_status failed")

        if not self.provider.lifecycle.uses_local_gpu:
            async with self._cv:
                if fail_if_generation_pending:
                    reservations = self._generation_snapshot_unlocked()
                    if reservations:
                        raise GenerationActiveError(reservations)
            yield self
            return

        # Snapshot busy owner for user-visible wait reason
        busy = self.owner
        pipe = self.comfy_pipeline
        if busy is not None:
            reason = f"Comfy ({pipe})" if busy == "comfy" and pipe else str(busy)
            await _status(f"Waiting for GPU: {reason}")

        async with self._cv:
            if fail_if_generation_pending:
                reservations = self._generation_snapshot_unlocked()
                if reservations:
                    raise GenerationActiveError(reservations)
            await self._wait_until_free(want="llm")
            self.owner = "llm"
            logger.info("GPU acquired by llm — unloading Comfy models before Ollama")

        try:
            await _status("Releasing ComfyUI VRAM…")
            # Free Comfy models WHILE holding llm ownership so a concurrent comfy
            # job cannot start mid-free. This is what Plan/script must do every time.
            await self.release_comfy_models(require_ok=False)
            yield self
        finally:
            if release_on_exit:
                async with self._cv:
                    await self.release_llm()
                    if self.owner == "llm":
                        self.owner = None
                    self._cv.notify_all()
                    logger.info("GPU released by llm (unloaded)")
            else:
                # Keep weights resident; drop exclusive owner so Comfy can queue.
                async with self._cv:
                    if self.owner == "llm":
                        self.owner = None
                    self._cv.notify_all()
                    logger.info(
                        "GPU ownership released by llm (model kept loaded, ready=%s)",
                        self._llm_ready,
                    )


_orchestrator: VramOrchestrator | None = None


def get_orchestrator() -> VramOrchestrator:
    """Process-wide singleton orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        from ..llm import get_llm_provider
        from .director_model import get_director_model

        provider = get_llm_provider()
        _orchestrator = VramOrchestrator(
            provider=provider,
            models=[get_director_model(provider.provider_id)],
            policy=settings.vram_policy,
        )
    return _orchestrator
