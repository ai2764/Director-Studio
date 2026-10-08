"""Read-only semantic review, with bounded recovery for malformed verdicts."""
from .planner import StoryboardValidation, parse_storyboard_validation
from .prompts import STORYBOARD_VALIDATION_SYSTEM
from ...core.prompt_errors import PromptOutputTruncated


class StoryboardReviewError(ValueError):
    code = "STORYBOARD_REVIEW_INVALID"


async def review_storyboard(provider, user: str) -> StoryboardValidation:
    bounded = getattr(provider, "complete_bounded", None)
    for attempt in range(2):
        format_error = None
        # Only completed-but-truncated output enters format recovery here.
        # Other provider/transport/context exceptions propagate immediately.
        try:
            if callable(bounded):
                raw = await bounded(STORYBOARD_VALIDATION_SYSTEM, user,
                                    max_tokens=2048, schema=StoryboardValidation.model_json_schema(),
                                    guides=("storyboard-validation",))
            else:
                raw = await provider.complete(STORYBOARD_VALIDATION_SYSTEM, user,
                                              guides=("storyboard-validation",))
        except PromptOutputTruncated as exc:
            format_error = exc
        if format_error is None:
            try:
                return parse_storyboard_validation(raw)
            except ValueError as exc:
                format_error = exc
        if attempt == 1:
            raise StoryboardReviewError(
                "semantic validator returned an invalid structured verdict after 2 attempts. "
                "The storyboard was not changed. This is a review-system failure, not "
                f"a creative rejection: {format_error}"
            ) from format_error
        # Repeat the same evidence; do not inject model text as new direction.
        user += (
            "\n\nFORMAT RECOVERY: The previous response was not a valid semantic verdict. "
            "Review the same candidate and evidence above. Return only a complete JSON "
            "object with boolean valid, string-array issues and string-array warnings. "
            "valid must agree with whether issues is empty. Keep the verdict concise; "
            "do not rewrite the storyboard."
        )
    raise AssertionError("Unreachable review attempt")
