"""Dependency-free prompt failure types shared by project data and H3."""
from typing import Literal, get_args

PromptFailureKind = Literal["unknown", "contract", "candidate", "tail_incompatible"]


class PromptFailureError(ValueError):
    def __init__(self, kind: PromptFailureKind, message: str):
        self.failure_kind = kind
        super().__init__(message)


def prompt_failure_kind(error: object) -> PromptFailureKind:
    kind = getattr(error, "failure_kind", "unknown")
    return kind if kind in get_args(PromptFailureKind) else "unknown"
