import asyncio

import pytest

from app.core.projects.chat_sessions import (
    DirectorChatSessionConflict,
    DirectorChatSessionRegistry,
)


@pytest.mark.asyncio
async def test_registry_rejects_a_second_session_for_one_project():
    registry = DirectorChatSessionRegistry()

    first = await registry.reserve("prj_1")

    with pytest.raises(DirectorChatSessionConflict):
        await registry.reserve("prj_1")

    snapshot = await registry.snapshot("prj_1")
    assert snapshot.active is True
    assert snapshot.session_id == first.session_id


@pytest.mark.asyncio
async def test_registry_explicit_cancel_stops_the_attached_task():
    registry = DirectorChatSessionRegistry()
    session = await registry.reserve("prj_1")
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await registry.attach("prj_1", session.session_id, task)
    await started.wait()

    assert await registry.cancel("prj_1") is True
    assert task.cancelled()
    assert (await registry.snapshot("prj_1")).active is False


@pytest.mark.asyncio
async def test_finish_only_removes_the_matching_session():
    registry = DirectorChatSessionRegistry()
    session = await registry.reserve("prj_1")

    await registry.finish("prj_1", "chat_stale")
    assert (await registry.snapshot("prj_1")).active is True

    await registry.finish("prj_1", session.session_id)
    assert (await registry.snapshot("prj_1")).active is False


@pytest.mark.asyncio
async def test_cancel_is_idempotent_when_no_session_exists():
    registry = DirectorChatSessionRegistry()

    assert await registry.cancel("prj_missing") is False
