"""Typed errors and health warnings for H3 workflow profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProfileStorageError(RuntimeError):
    """A profile-store file is unsafe, missing, or malformed."""


class ProfileChangedError(ProfileStorageError):
    """A workflow's bytes no longer match its recorded SHA-256 digest."""


class ProfileStateError(ProfileStorageError):
    """A lifecycle operation was requested before its prerequisites passed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ProfileWarning:
    """A non-fatal reason that resolution fell back to the official profile."""

    code: str
    message: str
    details: dict[str, Any] | None = None
