from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agents.director.chat import ChatResult
from app.api import projects as projects_api
from app.core.projects.chat_history import load_chat_history
from app.core.projects.models import ProjectMusicMaster, Shot, ShotMusicSegment
from app.core.projects.store import create_project, project_dir, save_project, save_shot
from app.core.projects.song_segments import SongSegment, save_segments
from app.main import create_app


def _project_with_song():
    project = create_project("MV", "", mode="mv")
    project.music_master = ProjectMusicMaster(
        filename="song.wav", relative_path="music/master.wav", duration_s=12,
        content_sha256="a" * 64, source_format="wav",
    )
    path = project_dir(project.id) / "music" / "master.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFFdemo")
    save_project(project)
    return project


@pytest.mark.asyncio
@pytest.mark.parametrize("beat", [
    "Mia turns toward the camera while singing.",
    "[00:00.000–00:02.000] Old description, now rewritten.",
    "[00:03.000–00:06.000] Current lyric.",
])
async def test_mv_submit_uses_saved_music_interval_after_beat_rewrite(monkeypatch, beat):
    from fastapi import HTTPException
    project = _project_with_song()
    save_segments(project, expected_revision=0, raw_input="", segments=[
        SongSegment(id="seg_old", start_s=0, end_s=2, text="Old lyric."),
        SongSegment(id="seg_current", start_s=3, end_s=6, text="Current lyric."),
        SongSegment(id="seg_repeat", start_s=8, end_s=10, text="Current lyric."),
    ])
    shot = Shot(id="sht_song_rewrite", project_id=project.id, scene_id="room",
                title="Current lyric", script_beat=beat, duration_s=4.25,
                dialogue=["Current lyric."], music_segment=ShotMusicSegment(
                    core_start_s=3, core_end_s=6, submit_start_s=2.5, submit_end_s=6.75))
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    matches = projects_api._mv_dialogue_song_matches(project, shot)
    assert [s.id for s in matches[0][1]] == ["seg_current"]
    # Stop after lyric validation, before generation, at an unrelated configured gate.
    monkeypatch.setattr(projects_api.settings, "h3_minimax_api_key", "")
    with pytest.raises(HTTPException) as exc:
        await projects_api.submit_shot_endpoint(shot.id, svc=object(),
            options=projects_api.H3SubmitOptions(h3_provider="minimax"))
    assert exc.value.detail == "MiniMax H3 API key is not configured"


@pytest.mark.asyncio
async def test_mv_submit_still_rejects_wrong_music_interval_even_with_correct_beat(monkeypatch):
    from fastapi import HTTPException
    project = _project_with_song()
    save_segments(project, expected_revision=0, raw_input="", segments=[
        SongSegment(id="seg_wrong", start_s=0, end_s=2, text="Other lyric."),
        SongSegment(id="seg_current", start_s=3, end_s=6, text="Current lyric."),
    ])
    shot = Shot(id="sht_wrong_song", project_id=project.id, scene_id="room",
                title="Wrong interval", script_beat="[00:03.000–00:06.000] Current lyric.",
                duration_s=2.75, dialogue=["Current lyric."], music_segment=ShotMusicSegment(
                    core_start_s=0, core_end_s=2, submit_start_s=0, submit_end_s=2.75))
    save_shot(shot)
    project.shot_ids = [shot.id]
    save_project(project)
    monkeypatch.setattr(projects_api.settings, "h3_minimax_api_key", "")
    with pytest.raises(HTTPException) as exc:
        await projects_api.submit_shot_endpoint(shot.id, svc=object(),
            options=projects_api.H3SubmitOptions(h3_provider="minimax"))
    assert "active song interval does not contain" in exc.value.detail


def test_preview_save_and_read_project_song_segments():
    project = _project_with_song()
    base = f"/api/projects/{project.id}/song-segments"
    with TestClient(create_app()) as client:
        assert client.get(base).json()["document"] is None
        source = json.dumps({"segments": [{"start": 0, "end": 3, "text": "First line"}]})
        preview = client.post(f"{base}/preview", json={"raw_input": source})
        assert preview.status_code == 200
        rows = preview.json()["rows"]
        saved = client.put(base, json={"expected_revision": 0, "raw_input": source, "segments": rows})
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert client.get(base).json()["document"]["segments"][0]["text"] == "First line"
        assert client.put(base, json={"expected_revision": 0, "raw_input": source, "segments": rows}).status_code == 409
        stale_chat = client.post(f"/api/projects/{project.id}/chat/stream", json={
            "message": "Discuss this section",
            "segment_selection": {"revision": 2, "ids": [rows[0]["id"]]},
        })
        assert stale_chat.status_code == 409
        unknown_chat = client.post(f"/api/projects/{project.id}/chat/stream", json={
            "message": "Discuss this section",
            "segment_selection": {"revision": 1, "ids": ["made-up"]},
        })
        assert unknown_chat.status_code == 422
        project.music_master.content_sha256 = "b" * 64
        save_project(project)
        assert client.get(base).json()["master_stale"] is True
        assert client.post(f"/api/projects/{project.id}/chat/stream", json={
            "message": "Discuss this section",
            "segment_selection": {"revision": 1, "ids": [rows[0]["id"]]},
        }).status_code == 409


def test_song_audio_and_invalid_save():
    project = _project_with_song()
    base = f"/api/projects/{project.id}"
    with TestClient(create_app()) as client:
        audio = client.get(f"{base}/music-master/audio")
        assert audio.status_code == 200
        assert audio.content == b"RIFFdemo"
        invalid = client.put(f"{base}/song-segments", json={
            "expected_revision": 0, "raw_input": "bad", "segments": [
                {"id": "a", "start_s": 0, "end_s": 13, "text": "Too long"},
            ],
        })
        assert invalid.status_code == 422


def test_free_text_preview_reports_generation_busy_without_a_server_error(monkeypatch):
    from app.api import song_segments as segments_api
    from app.core.vram import GenerationActiveError
    project = _project_with_song()

    async def busy_parse(_):
        raise GenerationActiveError([])

    monkeypatch.setattr(segments_api, "parse_segment_text", busy_parse)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        result = client.post(f"/api/projects/{project.id}/song-segments/preview",
                             json={"raw_input": "0-3 First lyric"})
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "GPU_GENERATION_ACTIVE"


@pytest.mark.parametrize("runtime", ["legacy", "harness"])
@pytest.mark.asyncio
async def test_selected_context_is_request_scoped_not_saved_as_chat_text(monkeypatch, runtime):
    project = _project_with_song()
    save_segments(project, expected_revision=0, raw_input="notes", segments=[
        SongSegment(id="seg_a", start_s=0, end_s=3, text="Opening lyric"),
    ])
    captured = {}

    async def fake_make_chat_fn(on_progress=None):
        return object()

    async def fake_handle_chat(**kwargs):
        captured["message"] = kwargs["message"]
        return ChatResult(reply="A visual idea.", project=project)

    import app.agents.director.chat as chat_module
    monkeypatch.setattr(projects_api.settings, "director_agent_runtime", runtime)
    monkeypatch.setattr(projects_api, "_make_chat_fn", fake_make_chat_fn)
    monkeypatch.setattr(chat_module, "handle_chat", fake_handle_chat)
    response = await projects_api.project_chat_stream_endpoint(
        project.id,
        projects_api.ChatBody(message="Discuss this", segment_selection={
            "revision": 1, "ids": ["seg_a"],
        }),
        svc=object(),
    )
    _ = [chunk async for chunk in response.body_iterator]
    assert "Opening lyric" in captured["message"]
    assert '"start_s"' in captured["message"]
    assert [(item.role, item.content) for item in load_chat_history(project.id)] == [
        ("user", "Discuss this"), ("assistant", "A visual idea."),
    ]
