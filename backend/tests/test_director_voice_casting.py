from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.core.library.store import asset_dir, write_asset
from app.core.schemas import LibraryAsset


CAMERA_DRAFT = {
    "shot_type": "medium shot",
    "camera_angle": "eye level",
    "camera_motion": "locked-off",
    "composition": "speaker framed clearly against the scene",
}


def _voice_asset(project_id: str, asset_id: str, name: str) -> LibraryAsset:
    directory = asset_dir("voices", asset_id, project_id=project_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reference.wav").write_bytes(b"RIFF-reference")
    return write_asset(
        LibraryAsset(
            id=asset_id,
            kind="voices",
            name=name,
            notes="neutral English, calm intimate delivery",
            pipeline_id="external",
            job_id="",
            created_at="2026-08-25T00:00:00+00:00",
            files={"source": "source.wav", "reference": "reference.wav"},
            meta={
                "description": "neutral English, calm intimate delivery",
                "duration_s": 4.0,
                "h3_ready": True,
            },
            project_id=project_id,
        )
    )


@pytest.fixture
def director_voice_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    jobs = tmp_path / "jobs"
    library = tmp_path / "library"
    projects.mkdir()
    jobs.mkdir()
    library.mkdir()
    monkeypatch.setattr(settings, "projects_dir", projects)
    monkeypatch.setattr(settings, "jobs_dir", jobs)
    monkeypatch.setattr(settings, "library_root", library)
    return {"projects": projects, "jobs": jobs, "library": library}


def test_plan_parser_accepts_ordered_voice_matches():
    from app.agents.director.planner import parse_shot_drafts

    drafts = parse_shot_drafts(
        json.dumps(
            [
                    {
                        **CAMERA_DRAFT,
                        "scene_id": "sc01",
                    "title": "Whisper",
                    "script_beat": "Mia whispers; Daniel answers.",
                    "duration_s": 6,
                    "dialogue": ["Mia: Go.", "Daniel: Wait."],
                    "asset_matches": [],
                    "voice_matches": [
                        {
                            "asset_id": "voi_mia",
                            "file_key": "reference",
                            "audio_index": 1,
                            "speaker": "Mia",
                            "reason": "name and delivery match",
                        },
                        {
                            "asset_id": "voi_daniel",
                            "file_key": "reference",
                            "audio_index": 2,
                            "speaker": "Daniel",
                            "reason": "name match",
                        },
                    ],
                }
            ]
        )
    )

    assert [match.asset_id for match in drafts[0].voice_matches] == [
        "voi_mia",
        "voi_daniel",
    ]
    assert [match.audio_index for match in drafts[0].voice_matches] == [1, 2]


@pytest.mark.parametrize(
    "matches",
    [
        [
            {"asset_id": "voi_1", "audio_index": 1},
            {"asset_id": "voi_2", "audio_index": 3},
        ],
        [{"asset_id": "", "audio_index": 1}],
        [
            {"asset_id": f"voi_{index}", "audio_index": index}
            for index in range(1, 5)
        ],
    ],
    ids=["non-contiguous", "blank-id", "too-many"],
)
def test_plan_parser_rejects_invalid_voice_matches(matches):
    from app.agents.director.planner import parse_shot_drafts

    payload = [
        {
            "scene_id": "sc01",
            "title": "Shot",
            "script_beat": "beat",
            "asset_matches": [],
            "voice_matches": matches,
        }
    ]
    with pytest.raises(ValueError):
        parse_shot_drafts(json.dumps(payload))


def test_inventory_exposes_voice_casting_metadata(director_voice_env):
    from app.agents.director.service import _inventory

    _voice_asset("prj_voice", "voi_mia", "Mia")
    row = next(item for item in _inventory("prj_voice") if item["id"] == "voi_mia")

    assert row["kind"] == "voices"
    assert row["name"] == "Mia"
    assert row["description"] == "neutral English, calm intimate delivery"
    assert row["duration_s"] == 4.0
    assert row["h3_ready"] is True
    assert row["file_keys"] == ["source", "reference"]


def test_shot_from_draft_persists_exact_voice_ids(director_voice_env):
    from app.agents.director.planner import ShotDraft, VoiceMatchDraft
    from app.agents.director.service import _asset_index, _inventory, _shot_from_draft

    _voice_asset("prj_voice", "voi_mia", "Mia")
    draft = ShotDraft(
        **CAMERA_DRAFT,
        scene_id="sc01",
        title="Whisper",
        script_beat="Mia whispers.",
        duration_s=6,
        dialogue=["Mia: Go now."],
        voice_matches=[
            VoiceMatchDraft(
                asset_id="voi_mia",
                file_key="reference",
                audio_index=1,
                speaker="Mia",
                reason="exact name match",
            )
        ],
    )

    shot = _shot_from_draft(
        "prj_voice",
        draft,
        inventory=_inventory("prj_voice"),
        index=_asset_index("prj_voice"),
        script_text="Mia: Go now.",
    )

    assert len(shot.voice_refs) == 1
    assert shot.voice_refs[0].asset_id == "voi_mia"
    assert shot.voice_refs[0].audio_index == 1
    assert shot.voice_refs[0].speaker == "Mia"


def test_shot_from_draft_does_not_guess_missing_voice_id(director_voice_env):
    from app.agents.director.planner import ShotDraft, VoiceMatchDraft
    from app.agents.director.service import _asset_index, _inventory, _shot_from_draft

    _voice_asset("prj_voice", "voi_mia", "Mia")
    draft = ShotDraft(
        **CAMERA_DRAFT,
        scene_id="sc01",
        title="Unknown voice",
        script_beat="An unidentified off-screen voice speaks.",
        dialogue=["Open the gate."],
        voice_matches=[VoiceMatchDraft(asset_id="voi_invented", audio_index=1)],
    )
    shot = _shot_from_draft(
        "prj_voice",
        draft,
        inventory=_inventory("prj_voice"),
        index=_asset_index("prj_voice"),
    )

    assert shot.voice_refs == []
