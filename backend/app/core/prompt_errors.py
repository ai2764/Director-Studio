"""Dependency-free prompt failure types shared by project data and H3."""
from typing import Literal, get_args
import re

PromptFailureKind = Literal["unknown", "contract", "candidate", "tail_incompatible"]


class PromptFailureError(ValueError):
    def __init__(self, kind: PromptFailureKind, message: str):
        self.failure_kind = kind
        super().__init__(message)


class PromptOutputTruncated(ValueError):
    """The provider completed a response at its output limit, not a transport failure."""


class PromptContextOverflow(PromptFailureError):
    code = "PROMPT_CONTEXT_OVERFLOW"

    def __init__(self, message: str):
        super().__init__("contract", "Prompt context exceeds the model input budget. "
            "Reduce duplicate/context material or increase model capacity before retrying. " + message)


def is_context_overflow(error: object) -> bool:
    return isinstance(error, PromptContextOverflow) or bool(re.search(
        r"exceed_context_size_error|context_length_exceeded|"
        r"exceeds? (?:the )?(?:available |maximum )?context (?:size|length)|"
        r"maximum context length|context (?:window|length|size).*(?:exceed|overflow)",
        str(error), re.IGNORECASE))


class MaterialReviewError(PromptFailureError):
    """Upstream reference review failed; creative prompt repair cannot fix it."""
    code = "MATERIAL_REVIEW_INVALID"

    def __init__(self, message: str, *, issues: list[dict] | None = None):
        self.issues = issues or []
        super().__init__("contract", message)


class MaterialInputError(MaterialReviewError):
    code = "MATERIAL_INPUT_INVALID"


class ShotConfigurationConflict(PromptFailureError):
    """An explicit directing request disagrees with saved authoring parameters."""
    code = "SHOT_CONFIGURATION_CONFLICT"

    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__("contract", "Saved shot parameters conflict with an explicit user request: "
                         + "; ".join(f"{issue['field']}: {issue['reason']}" for issue in issues))


def prompt_failure_kind(error: object) -> PromptFailureKind:
    kind = getattr(error, "failure_kind", "unknown")
    return kind if kind in get_args(PromptFailureKind) else "unknown"
