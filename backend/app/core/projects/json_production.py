from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .models import PromptSections
from .store import project_dir

STORYBOARD_FILENAME = "production_storyboard.json"
STORYBOARD_TMP_FILENAME = "production_storyboard.json.tmp"

_PROMPT_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)


class JsonPictureRole(str, Enum):
    actor = "actor"
    costume = "costume"
    scene = "scene"
    prop = "prop"
    layout = "layout"
    other = "other"


class JsonProductionPicture(BaseModel):
    index: int = Field(ge=1, le=9)
    role: JsonPictureRole
    label: str = Field(min_length=1)


class JsonProductionAudio(BaseModel):
    index: int = Field(ge=1, le=3)
    label: str = Field(min_length=1)


class JsonProductionShot(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    script_beat: str = ""
    duration_s: float = Field(gt=0, le=15)
    dialogue: list[str] = Field(default_factory=list)
    pictures: list[JsonProductionPicture] = Field(min_length=1, max_length=9)
    audio: list[JsonProductionAudio] = Field(default_factory=list, max_length=3)
    prompt: PromptSections

    @model_validator(mode="after")
    def _validate_slots_and_prompt(self) -> "JsonProductionShot":
        picture_indexes = [p.index for p in self.pictures]
        if picture_indexes != list(range(1, len(self.pictures) + 1)):
            raise ValueError("picture indexes must be contiguous and ordered from 1")

        audio_indexes = [a.index for a in self.audio]
        if audio_indexes != list(range(1, len(self.audio) + 1)):
            raise ValueError("audio indexes must be contiguous and ordered from 1")

        for name in _PROMPT_FIELDS:
            value = getattr(self.prompt, name)
            if not (value or "").strip():
                raise ValueError(f"prompt.{name} must be non-empty")
        return self


class JsonProductionDocument(BaseModel):
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    shots: list[JsonProductionShot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_shot_ids(self) -> "JsonProductionDocument":
        ids = [shot.id for shot in self.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("shot ids must be unique")
        return self


def load_json_production_document(project_id: str) -> JsonProductionDocument:
    path = project_dir(project_id) / STORYBOARD_FILENAME
    if not path.is_file():
        return JsonProductionDocument()
    return JsonProductionDocument.model_validate_json(path.read_text(encoding="utf-8"))


def save_json_production_document(
    project_id: str, document: JsonProductionDocument
) -> None:
    document = JsonProductionDocument.model_validate(document.model_dump())
    root = project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    final_path = root / STORYBOARD_FILENAME
    temp_path = root / STORYBOARD_TMP_FILENAME
    try:
        temp_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(final_path)
    except Exception:
        if temp_path.is_file():
            temp_path.unlink()
        raise
