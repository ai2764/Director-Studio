"""Trusted process-local scope for a coordinator-issued Agent turn."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedTurnScope:
    project_id: str
    run_id: str
    event_id: str
    shot_id: str


managed_turn_scope: ContextVar[ManagedTurnScope | None] = ContextVar(
    "managed_turn_scope", default=None,
)
