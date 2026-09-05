"""LLM plan parsing and ShotDraft validation."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from ...core.projects.models import RefRole


@runtime_checkable
class PlanProvider(Protocol):
    async def complete(
        self,
        system: str,
        user: str,
        *,
        guides: Iterable[str] = (),
    ) -> str: ...

    async def complete_with_images(
        self,
        system: str,
        user: str,
        *,
        images: list[str],
        guides: Iterable[str] = (),
    ) -> str: ...


class AssetMatchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    asset_id: str
    file_key: str | None = None
    picture_index: int | None = Field(default=None, ge=1, le=9)

    @field_validator("role")
    @classmethod
    def _normalize_role(cls, v: str) -> str:
        return (v or "").strip().lower()

    @field_validator("asset_id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("file_key")
    @classmethod
    def _strip_file_key(cls, v: str | None) -> str | None:
        value = (v or "").strip()
        return value or None


class VoiceMatchDraft(BaseModel):
    asset_id: str
    audio_index: int = Field(ge=1, le=3)
    file_key: str = "reference"
    speaker: str = ""
    reason: str = ""

    @field_validator("asset_id", "file_key", "speaker", "reason")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def _require_identity(self) -> "VoiceMatchDraft":
        if not self.asset_id:
            raise ValueError("voice asset_id is required")
        if not self.file_key:
            raise ValueError("voice file_key is required")
        return self


class ShotDraft(BaseModel):
    shot_id: str | None = Field(
        default=None,
        description=(
            "Existing Shot id when revising a storyboard. Preserve the id from "
            "PROJECT_STATE for every existing Shot; omit it only for a new Shot."
        ),
    )
    scene_id: str
    title: str
    script_beat: str
    shot_type: str = Field(
        description="Exact framing and shot size, such as medium two-shot or close-up."
    )
    camera_angle: str = Field(
        description="Camera height, side, lens perspective, and subject axis."
    )
    camera_motion: str = Field(
        description=(
            "Explicit camera movement path and end framing; use locked-off only "
            "when stillness is intentional."
        )
    )
    composition: str = Field(
        description=(
            "Subject screen positions, eyelines, foreground/background layers, "
            "and visual emphasis."
        )
    )
    duration_s: float = 8.0
    dialogue: list[str] = Field(default_factory=list)
    asset_matches: list[AssetMatchDraft] = Field(default_factory=list)
    voice_matches: list[VoiceMatchDraft] = Field(default_factory=list)

    @field_validator(
        "shot_id",
        "scene_id",
        "title",
        "script_beat",
        "shot_type",
        "camera_angle",
        "camera_motion",
        "composition",
    )
    @classmethod
    def _non_empty(cls, v: str | None, info) -> str | None:
        s = (v or "").strip()
        if info.field_name == "shot_id" and not s:
            return None
        if not s:
            raise ValueError("field must be non-empty")
        return s

    @field_validator("duration_s")
    @classmethod
    def _duration(cls, v: float) -> float:
        d = float(v)
        if d <= 0:
            raise ValueError("duration_s must be positive")
        return d

    @model_validator(mode="after")
    def _validate_picture_order(self) -> "ShotDraft":
        matches = list(self.asset_matches)
        if len(matches) > 9:
            raise ValueError("H3 supports at most 9 asset matches")
        specified = [m.picture_index for m in matches if m.picture_index is not None]
        if specified and len(specified) != len(matches):
            raise ValueError("picture_index must be set on every asset match or none")
        if specified and specified != list(range(1, len(matches) + 1)):
            raise ValueError("picture_index must be contiguous and ordered from 1")
        voices = list(self.voice_matches)
        if len(voices) > 3:
            raise ValueError("H3 supports at most 3 voice matches")
        if len({voice.asset_id for voice in voices}) != len(voices):
            raise ValueError("voice match assets must be unique")
        if [voice.audio_index for voice in voices] != list(range(1, len(voices) + 1)):
            raise ValueError("audio_index must be contiguous and ordered from 1")
        return self


class StoryboardSubmission(BaseModel):
    """Typed native-tool payload for lossless storyboard persistence."""

    expected_script_hash: str
    shots: list[ShotDraft] = Field(min_length=1)

    @field_validator("expected_script_hash")
    @classmethod
    def _require_script_hash(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("expected_script_hash is required")
        return normalized


class ShotRevisionSubmission(BaseModel):
    """A partial authored-field update for exactly one existing Shot."""

    model_config = ConfigDict(extra="forbid")

    shot_id: str
    scene_id: str | None = None
    title: str | None = None
    script_beat: str | None = None
    shot_type: str | None = None
    camera_angle: str | None = None
    camera_motion: str | None = None
    composition: str | None = None
    duration_s: float | None = None
    dialogue: list[str] | None = None

    @field_validator(
        "shot_id",
        "scene_id",
        "title",
        "script_beat",
        "shot_type",
        "camera_angle",
        "camera_motion",
        "composition",
    )
    @classmethod
    def _require_non_empty_text(cls, value: str | None, info) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be non-empty")
        return normalized

    @field_validator("duration_s")
    @classmethod
    def _require_positive_duration(cls, value: float | None) -> float:
        if value is None or float(value) <= 0:
            raise ValueError("duration_s must be positive")
        return float(value)

    @field_validator("dialogue")
    @classmethod
    def _require_dialogue_list(cls, value: list[str] | None) -> list[str]:
        if value is None:
            raise ValueError("dialogue must be a list")
        return list(value)

    @model_validator(mode="after")
    def _require_authored_update(self) -> "ShotRevisionSubmission":
        if self.model_fields_set <= {"shot_id"}:
            raise ValueError("at least one authored shot field must be supplied")
        return self


class ShotRefsPatch(BaseModel):
    """One exact image-reference replacement for an existing shot."""

    model_config = ConfigDict(extra="forbid")

    shot_id: str
    refs: list[AssetMatchDraft] = Field(max_length=9)

    @field_validator("shot_id")
    @classmethod
    def _require_shot_id(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("shot_id is required")
        return normalized

    @model_validator(mode="after")
    def _require_exact_picture_order(self) -> "ShotRefsPatch":
        indices = [ref.picture_index for ref in self.refs]
        if any(index is None for index in indices):
            raise ValueError("picture_index is required for every patched ref")
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError("picture_index must be contiguous and ordered from 1")
        identities = [(ref.role, ref.asset_id, ref.file_key) for ref in self.refs]
        if len(set(identities)) != len(identities):
            raise ValueError("patched refs must not contain duplicate bindings")
        return self


class ShotRefsPatchSubmission(BaseModel):
    """Typed native-tool payload for reference-only shot updates."""

    model_config = ConfigDict(extra="forbid")

    updates: list[ShotRefsPatch] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_unique_shots(self) -> "ShotRefsPatchSubmission":
        shot_ids = [update.shot_id for update in self.updates]
        if len(set(shot_ids)) != len(shot_ids):
            raise ValueError("each shot may appear only once in a reference patch")
        return self


class ShotSceneRefSelection(BaseModel):
    """Exact human-selected scene binding for one existing shot."""

    model_config = ConfigDict(extra="forbid")

    shot_id: str = Field(min_length=1)
    scene_asset_id: str = Field(min_length=1)
    file_key: str = Field(min_length=1)

    @field_validator("shot_id", "scene_asset_id", "file_key")
    @classmethod
    def _require_nonempty_selection_value(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("exact scene selection values must not be empty")
        return normalized


class StoryboardValidation(BaseModel):
    """Structured semantic verdict for one complete storyboard candidate."""

    model_config = ConfigDict(extra="forbid")

    valid: StrictBool
    issues: list[str]

    @field_validator("issues")
    @classmethod
    def _normalize_issues(cls, values: list[str]) -> list[str]:
        normalized = [str(value or "").strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("validation issues must be non-empty strings")
        return normalized

    @model_validator(mode="after")
    def _verdict_matches_issues(self) -> "StoryboardValidation":
        if self.valid and self.issues:
            raise ValueError("a valid storyboard cannot contain issues")
        if not self.valid and not self.issues:
            raise ValueError("an invalid storyboard must contain at least one issue")
        return self


# Align with chat.split_thinking — Qwen/Ollama CoT wrappers.
_THINK_BLOCK_RE = re.compile(
    r"<think>[\s\S]*?</(?:think|redacted_reasoning)>|"
    r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>|"
    r"<thinking>[\s\S]*?</thinking>|"
    r"<reasoning>[\s\S]*?</reasoning>",
    re.IGNORECASE,
)


def _strip_model_noise(text: str) -> str:
    """Remove chain-of-thought / fences so JSON extract can run."""
    raw = (text or "").strip()
    if not raw:
        return raw
    raw = _THINK_BLOCK_RE.sub("", raw).strip()
    # Unclosed think block: keep only text before the open tag, or from first JSON.
    open_m = re.search(r"<think(?:ing)?>", raw, flags=re.I)
    if open_m and not re.search(r"</think", raw, flags=re.I):
        before = raw[: open_m.start()].strip()
        after = raw[open_m.end() :].strip()
        # Prefer JSON after the open tag when present.
        json_start = re.search(r"[\{\[]", after)
        if json_start:
            raw = after[json_start.start() :].strip()
        elif before:
            raw = before
        else:
            raw = after
    # Prefer fenced ```json … ``` body when present (not necessarily whole string).
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.I)
    if fence:
        raw = fence.group(1).strip()
    return raw.strip()


def _extract_json_payload(text: str) -> Any:
    """Parse JSON from model text; tolerate optional markdown fences."""
    raw = _strip_model_noise(text)
    if not raw:
        raise ValueError("empty model output")

    # Strip ```json ... ``` fences if present (whole-string case)
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try first array or object substring
        for open_c, close_c in (("[", "]"), ("{", "}")):
            start = raw.find(open_c)
            end = raw.rfind(close_c)
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise


def parse_shot_drafts(text: str) -> list[ShotDraft]:
    """Validate model output into ShotDraft list. Raises ValueError on failure."""
    data = _extract_json_payload(text)
    if isinstance(data, dict) and "shots" in data:
        data = data["shots"]
    if not isinstance(data, list):
        raise ValueError("plan JSON must be a list of shots")
    if not data:
        raise ValueError("plan JSON list is empty")
    return [ShotDraft.model_validate(item) for item in data]


def parse_storyboard_validation(text: str) -> StoryboardValidation:
    """Parse a semantic validator response without accepting replacement content."""
    data = _extract_json_payload(text)
    if not isinstance(data, dict):
        raise ValueError("storyboard validation JSON must be an object")
    return StoryboardValidation.model_validate(data)


def parse_prompt_sections_json(text: str) -> dict[str, str]:
    """Parse six-section prompt object from model text."""
    data = _extract_json_payload(text)
    if not isinstance(data, dict):
        raise ValueError("prompt sections JSON must be an object")
    keys = [
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "overall_soundscape",
        "non_diegetic_music",
    ]
    out: dict[str, str] = {}
    for k in keys:
        val = data.get(k)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"prompt section {k!r} missing or empty")
        out[k] = val.strip()
    return out


def role_to_ref_role(role: str) -> RefRole | None:
    key = (role or "").strip().lower()
    mapping = {
        "actor": RefRole.actor,
        "costume": RefRole.costume,
        "scene": RefRole.scene,
        "prop": RefRole.prop,
        "other": RefRole.other,
        "layout_ref_frame": RefRole.layout_ref_frame,
        "layout": RefRole.layout_ref_frame,
    }
    return mapping.get(key)


def role_to_library_kind(role: str) -> str | None:
    key = (role or "").strip().lower()
    mapping = {
        "actor": "actors",
        "costume": "costumes",
        "scene": "scenes",
        "prop": "props",
        "layout_ref_frame": "layouts",
        "layout": "layouts",
        "other": None,
    }
    return mapping.get(key)
