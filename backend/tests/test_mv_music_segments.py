from __future__ import annotations

import wave

import pytest

from app.core.library.audio import probe_audio
from app.core.media import music_segments
from app.core.projects.models import Project, ProjectMusicMaster, Shot, ShotMusicSegment
from app.core.projects.store import project_dir


def _write_mono_wav(path, duration_s: float = 3.0) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\0\0" * int(44100 * duration_s))


def test_prepare_music_segment_outputs_job_local_32khz_stereo_window() -> None:
    project = Project(
        id="prj_music_extract",
        name="MV",
        script_text="",
        mode="mv",
        created_at="2026-09-23T00:00:00Z",
        updated_at="2026-09-23T00:00:00Z",
        music_master=ProjectMusicMaster(
            filename="song.wav",
            relative_path="music/master.wav",
            duration_s=3.0,
            content_sha256="a" * 64,
            source_format="wav",
        ),
    )
    master = project_dir(project.id) / "music" / "master.wav"
    master.parent.mkdir(parents=True, exist_ok=True)
    _write_mono_wav(master)

    prepared = music_segments.prepare_music_segment(
        project,
        ShotMusicSegment(
            core_start_s=1.0,
            core_end_s=2.0,
            submit_start_s=0.5,
            submit_end_s=2.75,
        ),
    )

    output = project_dir(project.id) / "prepared.wav"
    output.write_bytes(prepared.data)
    metadata = probe_audio(output)
    assert prepared.duration_s == pytest.approx(2.25)
    assert metadata.duration_s == pytest.approx(2.25, abs=0.05)
    assert metadata.source_sample_rate == 32000
    assert metadata.source_channels == 2


@pytest.mark.parametrize("use_as_audio_reference", [False, True])
def test_song_prompt_context_omits_editorial_filename(use_as_audio_reference):
    project = Project(id="prj_mv", name="MV", mode="mv", script_text="",
                      created_at="", updated_at="", music_master=ProjectMusicMaster(
                          filename="Song Title (Remix).wav", relative_path="music/master.wav",
                          duration_s=30, content_sha256="a" * 64, source_format="wav"))
    shot = Shot(id="sht_mv", project_id=project.id, scene_id="room", title="MV",
                script_beat="Performance", duration_s=4,
                music_segment=ShotMusicSegment(core_start_s=20, core_end_s=23,
                    submit_start_s=19.5, submit_end_s=23.75,
                    use_as_audio_reference=use_as_audio_reference))
    context = music_segments.music_prompt_context(project, shot)
    assert context["master_filename"] is None
    assert "Song Title" not in str(context)
    assert context["audio_tag"] == ("<Audio 1>" if use_as_audio_reference else None)


def test_disabled_song_reference_keeps_timing_without_audio_binding():
    project = Project(id="prj_mv", name="MV", mode="mv", script_text="",
                      created_at="", updated_at="", music_master=ProjectMusicMaster(
                          filename="song.wav", relative_path="music/master.wav",
                          duration_s=30, content_sha256="a" * 64, source_format="wav"))
    segment = ShotMusicSegment(core_start_s=20.8, core_end_s=24.02,
                               submit_start_s=20.3, submit_end_s=24.77)
    shot = Shot(id="sht_lobby", project_id=project.id, scene_id="lobby",
                title="Lobby", script_beat="Empty lobby", duration_s=3.22, music_segment=segment)
    enabled_signature = music_segments.music_prompt_signature(project, shot)
    shot.music_segment = segment.model_copy(update={"use_as_audio_reference": False})
    context = music_segments.music_prompt_context(project, shot)
    assert context["audio_tag"] is None
    assert context["use_as_audio_reference"] is False
    assert context["master_filename"] is None
    assert context["generation_duration_s"] == pytest.approx(4.47)
    assert context["core_clip_start_s"] == pytest.approx(0.5)
    assert context["core_clip_end_s"] == pytest.approx(3.72)
    assert music_segments.music_prompt_signature(project, shot) != enabled_signature
    with pytest.raises(ValueError, match="disabled"):
        music_segments.prepare_music_segment(project, shot.music_segment)


def _two_line_song(monkeypatch, *, audio=True, stale=False):
    from app.core.projects.song_segments import SongSegment, SongSegmentsDocument
    project = Project(id="prj_two_lines", name="MV", mode="mv", script_text="",
                      created_at="", updated_at="", music_master=ProjectMusicMaster(
                          filename="song.wav", relative_path="music/master.wav", duration_s=200,
                          content_sha256="a" * 64, source_format="wav"))
    shot = Shot(id="sht_two_lines", project_id=project.id, scene_id="roof", title="Two lines",
                script_beat="Two lyrics, one continuous shot", duration_s=7.94,
                music_segment=ShotMusicSegment(core_start_s=134.92, core_end_s=141.86,
                    submit_start_s=134.42, submit_end_s=142.61, use_as_audio_reference=audio))
    document = SongSegmentsDocument(revision=1, master_sha256=("b" if stale else "a") * 64,
        segments=[
            SongSegment(id="before", start_s=134.42, end_s=134.8, text="Previous lyric."),
            SongSegment(id="first", start_s=134.92, end_s=138.08, text="No need to hurry, no need to choose."),
            SongSegment(id="second", start_s=138.48, end_s=141.86, text="When the rhythm finds me, anywhere we'll do."),
            SongSegment(id="after", start_s=142, end_s=142.61, text="Following lyric."),
        ])
    monkeypatch.setattr(music_segments, "load_segments", lambda _: document)
    return project, shot, document


def test_merged_shot_writer_receives_each_lyric_window_and_the_gap(monkeypatch):
    project, shot, _ = _two_line_song(monkeypatch)
    context = music_segments.music_prompt_context(project, shot)
    rows = context["lyric_segments"]
    assert [row["id"] for row in rows] == ["first", "second"]
    assert [(row["clip_start_s"], row["clip_end_s"]) for row in rows] == [(0.5, 3.66), (4.06, 7.44)]
    assert rows[1]["clip_start_s"] - rows[0]["clip_end_s"] == pytest.approx(0.4)
    assert rows[1]["text"] == "When the rhythm finds me, anywhere we'll do."
    assert context["generation_duration_s"] == pytest.approx(8.19)


@pytest.mark.parametrize("audio,stale", [(False, False), (True, True)])
def test_lyric_windows_are_not_exposed_for_editorial_or_wrong_master(monkeypatch, audio, stale):
    project, shot, _ = _two_line_song(monkeypatch, audio=audio, stale=stale)
    assert music_segments.music_prompt_context(project, shot)["lyric_segments"] == []


def test_imported_lyric_timing_changes_invalidate_a_saved_song_prompt(monkeypatch):
    project, shot, document = _two_line_song(monkeypatch)
    before = music_segments.music_prompt_signature(project, shot)
    document.segments[2].start_s = 138.6
    assert music_segments.music_prompt_signature(project, shot) != before

