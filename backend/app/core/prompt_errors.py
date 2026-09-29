"""Dependency-free prompt failure types shared by project data and H3."""
from typing import Literal, get_args

PromptFailureKind = Literal["unknown", "contract", "candidate", "tail_incompatible"]


class PromptFailureError(ValueError):
    def __init__(self, kind: PromptFailureKind, message: str):
        self.failure_kind = kind
        super().__init__(message)


class MaterialReviewError(PromptFailureError):
    """Upstream reference review failed; creative prompt repair cannot fix it."""
    code = "MATERIAL_REVIEW_INVALID"

    def __init__(self, message: str, *, issues: list[dict] | None = None):
        self.issues = issues or []
        super().__init__("contract", message)


class MaterialInputError(MaterialReviewError):
    code = "MATERIAL_INPUT_INVALID"


def prompt_failure_kind(error: object) -> PromptFailureKind:
    kind = getattr(error, "failure_kind", "unknown")
    return kind if kind in get_args(PromptFailureKind) else "unknown"
