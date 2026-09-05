"""Process-local lifecycle tracking for Director chat runners."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DirectorChatSessionSnapshot:
    active: bool
    session_id: str | None = None
    started_at: str | None = None


@dataclass
class _ActiveDirectorChatSession:
    session_id: str
    started_at: str
    task: asyncio.Task[None] | None = None


class DirectorChatSessionConflict(RuntimeError):
    """Raised when a project already owns an active Director chat session."""

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Director chat is already running for project {project_id}")
        self.project_id = project_id


class DirectorChatSessionRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, _ActiveDirectorChatSession] = {}

    async def reserve(self, project_id: str) -> DirectorChatSessionSnapshot:
        async with self._lock:
            if project_id in self._sessions:
                raise DirectorChatSessionConflict(project_id)
            session = _ActiveDirectorChatSession(
                session_id=f"chat_{uuid.uuid4().hex}",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._sessions[project_id] = session
            return DirectorChatSessionSnapshot(
                active=True,
                session_id=session.session_id,
                started_at=session.started_at,
            )

    async def attach(
        self,
        project_id: str,
        session_id: str,
        task: asyncio.Task[None],
    ) -> None:
        async with self._lock:
            session = self._sessions.get(project_id)
            if session is None or session.session_id != session_id:
                task.cancel()
                raise RuntimeError("Director chat session is no longer active")
            session.task = task

    async def snapshot(self, project_id: str) -> DirectorChatSessionSnapshot:
        async with self._lock:
            session = self._sessions.get(project_id)
            if session is None:
                return DirectorChatSessionSnapshot(active=False)
            return DirectorChatSessionSnapshot(
                active=True,
                session_id=session.session_id,
                started_at=session.started_at,
            )

    async def finish(self, project_id: str, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(project_id)
            if session is not None and session.session_id == session_id:
                del self._sessions[project_id]

    async def cancel(self, project_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(project_id, None)
        if session is None:
            return False
        if session.task is not None:
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.task
        return True


director_chat_sessions = DirectorChatSessionRegistry()
