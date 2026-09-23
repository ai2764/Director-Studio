from __future__ import annotations

import wave

import pytest

from app.core.library.audio import probe_audio
from app.core.media import music_segments
from app.core.projects.models import Project, ProjectMusicMaster, ShotMusicSegment
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

