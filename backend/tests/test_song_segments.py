from __future__ import annotations

import pytest

from app.core.projects.models import ProjectMusicMaster
from app.core.projects.store import create_project, project_dir, save_project
from app.core.projects.song_segments import (
    SegmentRevisionConflict,
    SongSegment,
    context_for_selection,
    load_segments,
    save_segments,
)


def _mv_with_song():
    project = create_project("Song film", "", mode="mv")
    project.music_master = ProjectMusicMaster(
        filename="song.wav",
        relative_path="music/master.wav",
        duration_s=325.12,
        content_sha256="a" * 64,
        source_format="wav",
    )
    save_project(project)
    return project


def test_saves_partial_timecoded_song_as_project_json() -> None:
    project = _mv_with_song()
    rows = [
        SongSegment(id="seg_a", start_s=0, end_s=2, text="Opening"),
        SongSegment(id="seg_b", start_s=300, end_s=325.12, text="Ending"),
    ]

    saved = save_segments(project, expected_revision=0, raw_input="source notes", segments=rows)

    assert saved.revision == 1
    assert saved.raw_input == "source notes"
    assert saved.master_sha256 == "a" * 64
    assert [row.id for row in load_segments(project.id).segments] == ["seg_a", "seg_b"]
    assert (project_dir(project.id) / "music" / "segments.json").is_file()


def test_rejects_overlap_without_changing_saved_segments() -> None:
    project = _mv_with_song()
    original = save_segments(
        project, expected_revision=0, raw_input="first", segments=[
            SongSegment(id="seg_a", start_s=0, end_s=2, text="Opening"),
        ],
    )

    with pytest.raises(ValueError, match="overlap"):
        save_segments(project, expected_revision=1, raw_input="bad", segments=[
            SongSegment(id="seg_a", start_s=0, end_s=3, text="Opening"),
            SongSegment(id="seg_b", start_s=2, end_s=4, text="Next"),
        ])

    assert load_segments(project.id) == original


def test_rejects_missing_master_or_concurrent_edit() -> None:
    project = create_project("Song film", "", mode="mv")
    rows = [SongSegment(id="seg_a", start_s=0, end_s=2, text="Opening")]
    with pytest.raises(ValueError, match="song master"):
        save_segments(project, expected_revision=0, raw_input="draft", segments=rows)

    project = _mv_with_song()
    save_segments(project, expected_revision=0, raw_input="first", segments=rows)
    with pytest.raises(ValueError, match="revision"):
        save_segments(project, expected_revision=0, raw_input="stale", segments=rows)


def test_selected_context_uses_saved_rows_and_current_song() -> None:
    project = _mv_with_song()
    saved = save_segments(project, expected_revision=0, raw_input="source", segments=[
        SongSegment(id="seg_a", start_s=1, end_s=2, text="First"),
        SongSegment(id="seg_b", start_s=2, end_s=3, text="Second"),
    ])
    context = context_for_selection(project, revision=saved.revision, ids=["seg_b"])
    assert "Second" in context
    assert "First" not in context
    assert "2.0" in context
    with pytest.raises(SegmentRevisionConflict):
        context_for_selection(project, revision=0, ids=["seg_b"])
    with pytest.raises(ValueError, match="not found"):
        context_for_selection(project, revision=1, ids=["missing"])
    with pytest.raises(ValueError, match="source order"):
        context_for_selection(project, revision=1, ids=["seg_b", "seg_a"])
