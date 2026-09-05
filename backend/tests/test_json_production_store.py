from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.projects import (
    JsonPictureRole,
    JsonProductionDocument,
    load_json_production_document,
    save_json_production_document,
)

VALID_DOCUMENT = {
    "version": 1,
    "revision": 1,
    "aspect_ratio": "16:9",
    "shots": [
        {
            "id": "shot_001",
            "title": "Corridor entry",
            "script_beat": "Lu enters the municipal archive corridor.",
            "duration_s": 6,
            "dialogue": [],
            "pictures": [
                {
                    "index": 1,
                    "role": "actor",
                    "label": "Lu identity and navy wardrobe",
                },
                {
                    "index": 2,
                    "role": "layout",
                    "label": "Post-entry blocking and corridor geography",
                },
            ],
            "audio": [],
            "prompt": {
                "subject_definitions": "<Picture 1> defines Lu's identity and wardrobe.",
                "summary": "<Picture 2> establishes the corridor composition.",
                "retention_analysis": "Hold attention through the doorway reveal.",
                "detailed_description": "0–6 seconds: Lu enters and stops at the desk.",
                "overall_soundscape": "Quiet rain and fluorescent hum.",
                "non_diegetic_music": "No non-diegetic music.",
            },
        }
    ],
}


def _shot_payload(**updates):
    payload = copy.deepcopy(VALID_DOCUMENT["shots"][0])
    payload.update(updates)
    return payload


def _document_with_shot(**shot_updates):
    return {
        "version": 1,
        "revision": 1,
        "aspect_ratio": "16:9",
        "shots": [_shot_payload(**shot_updates)],
    }


def test_valid_sample_document_parses():
    document = JsonProductionDocument.model_validate(VALID_DOCUMENT)
    assert document.shots[0].pictures[1].role == JsonPictureRole.layout
    assert document.revision == 1
    assert document.aspect_ratio == "16:9"


def test_rejects_duplicate_shot_ids():
    shot = _shot_payload()
    dup = _shot_payload(id="shot_001", title="Other")
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(
            {
                "version": 1,
                "revision": 1,
                "aspect_ratio": "16:9",
                "shots": [shot, dup],
            }
        )


def test_rejects_blank_prompt_sections():
    prompt = copy.deepcopy(VALID_DOCUMENT["shots"][0]["prompt"])
    prompt["summary"] = "   "
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(_document_with_shot(prompt=prompt))


def test_rejects_zero_pictures():
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(_document_with_shot(pictures=[]))


def test_rejects_more_than_nine_pictures():
    pictures = [
        {"index": i, "role": "other", "label": f"p{i}"}
        for i in range(1, 11)
    ]
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(_document_with_shot(pictures=pictures))


def test_rejects_more_than_three_audios():
    audio = [{"index": i, "label": f"a{i}"} for i in range(1, 5)]
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(_document_with_shot(audio=audio))


def test_rejects_non_contiguous_picture_indexes():
    pictures = [
        {"index": 1, "role": "actor", "label": "A"},
        {"index": 3, "role": "layout", "label": "B"},
    ]
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(_document_with_shot(pictures=pictures))


def test_rejects_non_contiguous_audio_indexes():
    audio = [
        {"index": 1, "label": "voice"},
        {"index": 3, "label": "fx"},
    ]
    with pytest.raises(ValidationError):
        JsonProductionDocument.model_validate(_document_with_shot(audio=audio))


def test_load_missing_returns_empty_document(tmp_projects_dir):
    document = load_json_production_document("prj_missing_json")
    assert document.version == 1
    assert document.revision == 0
    assert document.shots == []
    assert document.aspect_ratio == "16:9"


def test_save_creates_production_storyboard(tmp_projects_dir):
    project_id = "prj_json_save"
    (tmp_projects_dir / project_id).mkdir(parents=True)
    document = JsonProductionDocument.model_validate(VALID_DOCUMENT)

    save_json_production_document(project_id, document)

    path = tmp_projects_dir / project_id / "production_storyboard.json"
    assert path.is_file()
    loaded = load_json_production_document(project_id)
    assert loaded.revision == 1
    assert loaded.shots[0].id == "shot_001"
    assert not (tmp_projects_dir / project_id / "production_storyboard.json.tmp").exists()


def test_replace_failure_leaves_old_file_unchanged(tmp_projects_dir):
    project_id = "prj_json_atomic"
    project_path = tmp_projects_dir / project_id
    project_path.mkdir(parents=True)

    original = JsonProductionDocument.model_validate(VALID_DOCUMENT)
    save_json_production_document(project_id, original)

    final_path = project_path / "production_storyboard.json"
    before = final_path.read_text(encoding="utf-8")

    updated = original.model_copy(update={"revision": 2})

    def boom(self, target):
        raise OSError("simulated replace failure")

    with patch.object(Path, "replace", boom):
        with pytest.raises(OSError, match="simulated replace failure"):
            save_json_production_document(project_id, updated)

    assert final_path.read_text(encoding="utf-8") == before
    assert not (project_path / "production_storyboard.json.tmp").exists()
