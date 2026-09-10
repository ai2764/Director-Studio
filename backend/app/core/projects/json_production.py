from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from .models import PromptSections
from .store import project_dir

STORYBOARD_FILENAME = "production_storyboard.json"
STORYBOARD_TMP_FILENAME = "production_storyboard.json.tmp"
ASSET_KIND = Literal["picture", "audio"]

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


class JsonProductionStoredAsset(BaseModel):
    shot_id: str
    kind: ASSET_KIND
    index: int
    filename: str
    content_type: str
    size_bytes: int
    url: str
    slot_signature: str


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


def _asset_root(project_id: str) -> Path:
    root = project_dir(project_id) / "json-production" / "assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _shot_asset_dir(project_id: str, shot_id: str) -> Path:
    key = hashlib.sha256(shot_id.encode("utf-8")).hexdigest()[:24]
    path = _asset_root(project_id) / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slot_definition(
    shot: JsonProductionShot, kind: ASSET_KIND, index: int
) -> dict[str, object] | None:
    if kind == "picture":
        for slot in shot.pictures:
            if slot.index == index:
                return {
                    "kind": kind,
                    "index": index,
                    "role": slot.role.value,
                    "label": slot.label,
                }
    else:
        for slot in shot.audio:
            if slot.index == index:
                return {"kind": kind, "index": index, "label": slot.label}
    return None


def json_production_slot_signature(
    shot: JsonProductionShot, kind: ASSET_KIND, index: int
) -> str | None:
    definition = _slot_definition(shot, kind, index)
    if definition is None:
        return None
    encoded = json.dumps(
        definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_stored_asset_metadata(path: Path) -> JsonProductionStoredAsset | None:
    try:
        return JsonProductionStoredAsset.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError):
        return None


def save_json_production_asset(
    project_id: str,
    shot: JsonProductionShot,
    kind: ASSET_KIND,
    index: int,
    *,
    filename: str,
    content_type: str,
    data: bytes,
) -> JsonProductionStoredAsset:
    signature = json_production_slot_signature(shot, kind, index)
    if signature is None:
        raise ValueError(f"{kind.title()} {index} is not declared for shot {shot.id}")
    directory = _shot_asset_dir(project_id, shot.id)
    extension = Path(filename).suffix.lower()
    stored_name = f"{kind}_{index}{extension}"
    data_path = directory / stored_name
    data_temp = directory / f"{stored_name}.tmp"
    metadata_path = directory / f"{kind}_{index}.json"
    metadata_temp = directory / f"{kind}_{index}.json.tmp"

    previous = _read_stored_asset_metadata(metadata_path)

    asset = JsonProductionStoredAsset(
        shot_id=shot.id,
        kind=kind,
        index=index,
        filename=Path(filename).name,
        content_type=content_type,
        size_bytes=len(data),
        url=(
            f"/api/files/projects/{project_id}/json-production/assets/"
            f"{directory.name}/{stored_name}"
        ),
        slot_signature=signature,
    )
    try:
        data_temp.write_bytes(data)
        data_temp.replace(data_path)
        metadata_temp.write_text(asset.model_dump_json(indent=2), encoding="utf-8")
        metadata_temp.replace(metadata_path)
    except Exception:
        if data_temp.is_file():
            data_temp.unlink()
        if metadata_temp.is_file():
            metadata_temp.unlink()
        raise

    if previous and previous.url != asset.url:
        old_name = previous.url.rsplit("/", 1)[-1]
        old_path = directory / Path(old_name).name
        if old_path.is_file() and old_path != data_path:
            old_path.unlink()
    return asset


def load_json_production_asset(
    project_id: str,
    shot: JsonProductionShot,
    kind: ASSET_KIND,
    index: int,
) -> tuple[JsonProductionStoredAsset, Path] | None:
    directory = _shot_asset_dir(project_id, shot.id)
    metadata_path = directory / f"{kind}_{index}.json"
    if not metadata_path.is_file():
        return None
    asset = _read_stored_asset_metadata(metadata_path)
    if asset is None:
        return None
    signature = json_production_slot_signature(shot, kind, index)
    if (
        signature is None
        or asset.shot_id != shot.id
        or asset.kind != kind
        or asset.index != index
        or asset.slot_signature != signature
    ):
        return None
    filename = Path(asset.url.rsplit("/", 1)[-1]).name
    data_path = directory / filename
    if not data_path.is_file():
        return None
    return asset, data_path


def list_json_production_assets(
    project_id: str, document: JsonProductionDocument
) -> list[JsonProductionStoredAsset]:
    assets: list[JsonProductionStoredAsset] = []
    for shot in document.shots:
        for slot in shot.pictures:
            loaded = load_json_production_asset(
                project_id, shot, "picture", slot.index
            )
            if loaded:
                assets.append(loaded[0])
        for slot in shot.audio:
            loaded = load_json_production_asset(project_id, shot, "audio", slot.index)
            if loaded:
                assets.append(loaded[0])
    return assets


def delete_json_production_asset(
    project_id: str, shot_id: str, kind: ASSET_KIND, index: int
) -> None:
    directory = _shot_asset_dir(project_id, shot_id)
    metadata_path = directory / f"{kind}_{index}.json"
    if metadata_path.is_file():
        asset = _read_stored_asset_metadata(metadata_path)
        if asset is not None:
            data_path = directory / Path(asset.url.rsplit("/", 1)[-1]).name
            if data_path.is_file():
                data_path.unlink()
        metadata_path.unlink()
