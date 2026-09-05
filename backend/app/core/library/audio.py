from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceAudioMetadata:
    duration_s: float
    source_format: str
    source_sample_rate: int
    source_channels: int
    reference_sample_rate: int = 32000
    reference_channels: int = 2


def _require_binary(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise ValueError(f"{name} is required to prepare voice references")
    return resolved


def _run_checked(command: list[str], *, error: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(error) from exc


def probe_audio(path: Path) -> VoiceAudioMetadata:
    result = _run_checked(
        [
            _require_binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,format_name:stream=sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        error="unable to decode audio file",
    )
    try:
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [])[0]
        format_info = payload.get("format") or {}
        duration = float(format_info.get("duration"))
        sample_rate = int(stream.get("sample_rate"))
        channels = int(stream.get("channels"))
        format_name = str(format_info.get("format_name") or path.suffix.lstrip("."))
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("unable to decode audio metadata") from exc
    if duration <= 0 or sample_rate <= 0 or channels <= 0:
        raise ValueError("unable to decode audio metadata")
    return VoiceAudioMetadata(
        duration_s=duration,
        source_format=format_name,
        source_sample_rate=sample_rate,
        source_channels=channels,
    )


def normalize_voice_reference(source: Path, destination: Path) -> VoiceAudioMetadata:
    source_meta = probe_audio(source)
    if not 2.0 <= source_meta.duration_s <= 15.0:
        raise ValueError("voice reference duration must be between 2 and 15 seconds")

    _run_checked(
        [
            _require_binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "32000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        error="audio normalization failed",
    )
    reference_meta = probe_audio(destination)
    if (
        reference_meta.source_sample_rate != 32000
        or reference_meta.source_channels != 2
    ):
        raise ValueError("normalized voice reference must be 32 kHz stereo")
    return source_meta
