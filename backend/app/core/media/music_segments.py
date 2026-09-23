from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from ..library.audio import probe_audio
from ..projects.models import Project, ProjectMusicMaster
from ..projects.store import project_dir


SUPPORTED_MUSIC_SUFFIXES = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
)


def import_music_master(
    project_id: str,
    filename: str,
    data: bytes,
) -> ProjectMusicMaster:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_MUSIC_SUFFIXES:
        raise ValueError("unsupported audio file type")
    if not data:
        raise ValueError("song master is empty")

    music_dir = project_dir(project_id) / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    staging = music_dir / f".{uuid.uuid4().hex}{suffix}"
    destination = music_dir / f"master{suffix}"
    try:
        staging.write_bytes(data)
        metadata = probe_audio(staging)
        os.replace(staging, destination)
    except Exception:
        staging.unlink(missing_ok=True)
        raise

    return ProjectMusicMaster(
        filename=Path(filename).name,
        relative_path=destination.relative_to(project_dir(project_id)).as_posix(),
        duration_s=metadata.duration_s,
        content_sha256=hashlib.sha256(data).hexdigest(),
        source_format=metadata.source_format,
    )


def resolve_music_master(project: Project) -> Path:
    if project.music_master is None:
        raise ValueError("Project has no song master")
    root = project_dir(project.id).resolve()
    candidate = (root / project.music_master.relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Song master must stay within the project directory")
    if not candidate.is_file():
        raise ValueError("Song master file is missing")
    return candidate
