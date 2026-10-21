from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import build_api_router
from .api.security import CORS_ORIGIN_REGEX
from .config import settings
from .core.jobs import close_execution_runtimes, recover_interrupted_jobs
from .runtime_paths import runtime_paths

# Import pipelines package so pipelines register themselves
from . import pipelines as _pipelines  # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("director_studio")
APP_VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .core.comfy.runtime import comfy_runtime
    from .core.llm.llama_swap_runtime import llama_swap_runtime
    from .core.llm.ollama_runtime import ollama_runtime

    ollama_task = None
    llama_task = None
    try:
        ollama_task = asyncio.create_task(ollama_runtime.start())  # bundled Ollama (legacy, if enabled)
        llama_task = asyncio.create_task(llama_swap_runtime.start())  # built-in llama.cpp (if enabled)
        try:
            await comfy_runtime.start()
        except Exception:  # noqa: BLE001 - never block DS startup on ComfyUI
            logger.exception("ComfyUI runtime failed to start")
        from .core.managed_runs.continuation import reconcile_stopping_runs, schedule_pending_runs
        await reconcile_stopping_runs()
        recovered = await recover_interrupted_jobs()
        if recovered:
            logger.info("recovered %d interrupted jobs on startup", len(recovered))
        schedule_pending_runs()
        yield
    finally:
        await close_execution_runtimes()
        await comfy_runtime.stop()
        if ollama_task is not None and not ollama_task.done():
            ollama_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await ollama_task
        await ollama_runtime.stop()
        if llama_task is not None and not llama_task.done():
            llama_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await llama_task
        await llama_swap_runtime.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Director Studio",
        version=APP_VERSION,
        description="Extensible pre-production asset studio (pipelines + library + jobs).",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        # Same-origin UI (static build or Vite proxy) needs no CORS; only loopback
        # origins (dev servers on another port) may make cross-origin calls.
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_api_router())

    frontend_dist = settings.frontend_dist
    if frontend_dist.is_dir():
        @app.get("/mobile", include_in_schema=False)
        async def mobile_entrypoint() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        app if runtime_paths.frozen else "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=not runtime_paths.frozen,
    )


if __name__ == "__main__":
    run()
