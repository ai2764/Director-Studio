"""MV song text import, timeline persistence, and original song playback."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..agents.director.segment_import import SegmentPreview, parse_segment_text
from ..core.media.music_segments import resolve_music_master
from ..core.projects.models import Project, ProjectMode
from ..core.projects.song_segments import (
    SegmentRevisionConflict,
    SegmentValidationError,
    SongSegment,
    SongSegmentsDocument,
    load_segments,
    save_segments,
)
from ..core.projects.store import load_project


router = APIRouter()


class SegmentSourceBody(BaseModel):
    raw_input: str = Field(min_length=1)


class SaveSegmentsBody(SegmentSourceBody):
    expected_revision: int = Field(ge=0)
    segments: list[SongSegment]


class SongSegmentsState(BaseModel):
    document: SongSegmentsDocument | None
    master_stale: bool = False


def _mv_project(project_id: str) -> Project:
    project = load_project(project_id)
    if project is None or project.mode != ProjectMode.mv:
        raise HTTPException(404, "Music Video project not found")
    return project


@router.get("/projects/{project_id}/song-segments", response_model=SongSegmentsState)
def get_song_segments(project_id: str) -> SongSegmentsState:
    project = _mv_project(project_id)
    document = load_segments(project_id)
    stale = bool(document and (
        project.music_master is None
        or document.master_sha256 != project.music_master.content_sha256
    ))
    return SongSegmentsState(document=document, master_stale=stale)


@router.post("/projects/{project_id}/song-segments/preview", response_model=SegmentPreview)
async def preview_song_segments(project_id: str, body: SegmentSourceBody) -> SegmentPreview:
    _mv_project(project_id)
    try:
        return await parse_segment_text(body.raw_input)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/projects/{project_id}/song-segments", response_model=SongSegmentsDocument)
def put_song_segments(project_id: str, body: SaveSegmentsBody) -> SongSegmentsDocument:
    project = _mv_project(project_id)
    try:
        return save_segments(
            project, expected_revision=body.expected_revision,
            raw_input=body.raw_input, segments=body.segments,
        )
    except SegmentRevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (SegmentValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/music-master/audio")
def get_music_master_audio(project_id: str) -> FileResponse:
    project = _mv_project(project_id)
    if project.music_master is None:
        raise HTTPException(404, "Song master not found")
    try:
        path = resolve_music_master(project)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "Song master not found")
    return FileResponse(path, filename=project.music_master.filename, content_disposition_type="inline")
