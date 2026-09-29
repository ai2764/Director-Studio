from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..library.audio import _require_binary, _run_checked, probe_audio
from ..projects.models import (
    Project,
    ProjectMode,
    ProjectMusicMaster,
    Shot,
    ShotMusicSegment,
)
from ..projects.store import project_dir


SUPPORTED_MUSIC_SUFFIXES = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
)


@dataclass(frozen=True)
class PreparedMusicSegment:
    filename: str
    data: bytes
    duration_s: float


def music_prompt_context(project: Project, shot: Shot) -> dict[str, object] | None:
    """Describe the job-time song excerpt without exposing a filesystem path."""
    if project.mode != ProjectMode.mv or shot.music_segment is None:
        return None
    segment = shot.music_segment
    return {
        "audio_tag": "<Audio 1>",
        "master_filename": (
            project.music_master.filename if project.music_master else None
        ),
        **segment.model_dump(mode="json"),
        "generation_duration_s": segment.submit_end_s - segment.submit_start_s,
        "timing_origin": (
            "Generation second 0 is submit_start_s; core timestamps identify "
            "the edit content inside this submitted excerpt."
        ),
    }


def music_prompt_signature(project: Project, shot: Shot) -> str:
    """Fingerprint the exact song bytes and interval described by the H3 prompt."""
    context = music_prompt_context(project, shot)
    if context is None or project.music_master is None:
        return ""
    payload = {
        "master_sha256": project.music_master.content_sha256,
        "segment": shot.music_segment.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def prepare_music_segment(
    project: Project,
    segment: ShotMusicSegment,
) -> PreparedMusicSegment:
    if project.mode != ProjectMode.mv:
        raise ValueError("Song segments are available only for Music Video projects")
    master = resolve_music_master(project)
    if project.music_master is None:
        raise ValueError("Project has no song master")
    tolerance_s = 0.05
    if segment.submit_end_s > project.music_master.duration_s + tolerance_s:
        raise ValueError(
            "Music submit interval ends after the song master "
            f"({segment.submit_end_s:g}s > {project.music_master.duration_s:g}s)"
        )
    duration_s = segment.submit_end_s - segment.submit_start_s
    temporary = master.parent / f".segment-{uuid.uuid4().hex}.wav"
    try:
        _run_checked(
            [
                _require_binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-ss",
                f"{segment.submit_start_s:.6f}",
                "-i",
                str(master),
                "-t",
                f"{duration_s:.6f}",
                "-vn",
                "-ac",
                "2",
                "-ar",
                "32000",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            error="unable to extract the planned song segment",
        )
        metadata = probe_audio(temporary)
        if metadata.source_sample_rate != 32000 or metadata.source_channels != 2:
            raise ValueError("prepared song segment must be 32 kHz stereo")
        if abs(metadata.duration_s - duration_s) > tolerance_s:
            raise ValueError("prepared song segment duration does not match the plan")
        return PreparedMusicSegment(
            filename="music_audio_1.wav",
            data=temporary.read_bytes(),
            duration_s=duration_s,
        )
    finally:
        temporary.unlink(missing_ok=True)
