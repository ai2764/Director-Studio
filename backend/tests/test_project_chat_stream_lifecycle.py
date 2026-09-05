from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.agents.director.chat import ChatResult
from app.api import projects as projects_api
from app.config import settings
from app.core.projects.chat_history import load_chat_history
from app.core.projects.store import create_project
from app.core.vram import GenerationActiveError, GenerationReservation


@pytest.mark.asyncio
async def test_chat_work_continues_after_sse_client_disconnects(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Closing the progress stream must not cancel an in-flight state change."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects_dir)
    project = create_project("Stream lifecycle", "INT. HALLWAY - DAY")

    release = asyncio.Event()
    completed = asyncio.Event()

    async def fake_make_chat_fn(on_progress=None):
        return object()

    async def fake_handle_chat(*, on_progress, **kwargs):
        await on_progress({"type": "status", "text": "started"})
        await release.wait()
        completed.set()
        return ChatResult(reply="done", project=project)

    import app.agents.director.chat as chat_module

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)

    response = await projects_api.project_chat_stream_endpoint(
        project.id,
        projects_api.ChatBody(message="write the prompt"),
        svc=object(),
    )
    stream = response.body_iterator
    first_event = await anext(stream)
    assert "started" in first_event

    await stream.aclose()
    release.set()
    await asyncio.wait_for(completed.wait(), timeout=0.2)


@pytest.mark.asyncio
async def test_streamed_chat_persists_user_and_director_messages(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects_dir)
    project = create_project("Stream history", "INT. STUDIO - NIGHT")

    async def fake_make_chat_fn(on_progress=None):
        return object()

    async def fake_handle_chat(**kwargs):
        return ChatResult(reply="A durable reply.", project=project)

    import app.agents.director.chat as chat_module

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)

    response = await projects_api.project_chat_stream_endpoint(
        project.id,
        projects_api.ChatBody(message="Remember this request."),
        svc=object(),
    )
    events = [chunk async for chunk in response.body_iterator]

    assert any('"type": "result"' in chunk for chunk in events)
    assert [
        (message.role, message.content) for message in load_chat_history(project.id)
    ] == [
        ("user", "Remember this request."),
        ("assistant", "A durable reply."),
    ]


@pytest.mark.asyncio
async def test_late_generation_race_keeps_the_accepted_user_message(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects_dir)
    project = create_project("Late generation race", "INT. STUDIO - NIGHT")
    reservation = GenerationReservation(
        job_id="job_race",
        pipeline_id="h3_ref2va",
        kind="video",
        status="queued",
        phase="queued",
        queued_at="2026-08-31T10:00:00+00:00",
    )

    class RaceOrchestrator:
        async def generation_reservations(self):
            return []

        class Session:
            async def __aenter__(self):
                raise GenerationActiveError([reservation])

            async def __aexit__(self, *args):
                return None

        def llm_session(self, **kwargs):
            return self.Session()

    async def fake_handle_chat(*, chat_fn, **kwargs):
        await chat_fn("system", "user")
        raise AssertionError("chat must not continue after generation reservation")

    import app.agents.director.chat as chat_module

    monkeypatch.setattr("app.core.vram.get_orchestrator", lambda: RaceOrchestrator())
    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)

    response = await projects_api.project_chat_stream_endpoint(
        project.id,
        projects_api.ChatBody(message="write the prompt"),
        svc=object(),
    )
    events = [chunk async for chunk in response.body_iterator]

    assert any('"code": "GPU_GENERATION_ACTIVE"' in chunk for chunk in events)
    assert [
        (message.role, message.content) for message in load_chat_history(project.id)
    ] == [("user", "write the prompt")]


@pytest.mark.asyncio
async def test_stream_status_and_explicit_cancel_stop_the_active_runner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects_dir)
    project = create_project("Cancel lifecycle", "INT. STUDIO - NIGHT")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_make_chat_fn(on_progress=None):
        return object()

    async def fake_handle_chat(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    import app.agents.director.chat as chat_module

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)

    response = await projects_api.project_chat_stream_endpoint(
        project.id,
        projects_api.ChatBody(message="write the prompt"),
        svc=object(),
    )
    await asyncio.wait_for(started.wait(), timeout=0.2)

    status = await projects_api.project_chat_session_endpoint(project.id)
    assert status.active is True
    assert status.session_id is not None

    cancelled_status = await projects_api.cancel_project_chat_session_endpoint(project.id)
    assert cancelled_status.active is False
    await asyncio.wait_for(cancelled.wait(), timeout=0.2)
    assert (await projects_api.project_chat_session_endpoint(project.id)).active is False
    assert [
        (message.role, message.content) for message in load_chat_history(project.id)
    ] == [("user", "write the prompt")]
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_second_stream_for_the_same_project_is_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects_dir)
    project = create_project("Single session", "INT. STUDIO - NIGHT")
    started = asyncio.Event()

    async def fake_make_chat_fn(on_progress=None):
        return object()

    async def fake_handle_chat(**kwargs):
        started.set()
        await asyncio.Event().wait()

    import app.agents.director.chat as chat_module

    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)

    first = await projects_api.project_chat_stream_endpoint(
        project.id,
        projects_api.ChatBody(message="first"),
        svc=object(),
    )
    await asyncio.wait_for(started.wait(), timeout=0.2)
    try:
        with pytest.raises(HTTPException) as error:
            await projects_api.project_chat_stream_endpoint(
                project.id,
                projects_api.ChatBody(message="second"),
                svc=object(),
            )
        assert error.value.status_code == 409
        assert "already running" in str(error.value.detail)
    finally:
        await projects_api.cancel_project_chat_session_endpoint(project.id)
        await first.body_iterator.aclose()


@pytest.mark.asyncio
async def test_cancel_without_an_active_session_is_idempotent(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects_dir)
    project = create_project("Idle session", "INT. STUDIO - NIGHT")

    status = await projects_api.cancel_project_chat_session_endpoint(project.id)

    assert status.active is False

