"""Single lookup for external media tools (ffmpeg / ffprobe).

Order: ``DS_FFMPEG_DIR`` -> bundled ``<install>/runtime/ffmpeg/bin`` (LGPL static
build shipped by the installer) -> ``PATH``.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from ...config import settings
from ...runtime_paths import runtime_paths

TOOLS = ("ffmpeg", "ffprobe")


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def candidate_dirs() -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = []
    configured = str(getattr(settings, "ffmpeg_dir", "") or "").strip()
    if configured:
        root = Path(configured).expanduser()
        dirs += [("configured", root), ("configured", root / "bin")]
    bundled = runtime_paths.install_root / "runtime" / "ffmpeg"
    dirs += [("bundled", bundled / "bin"), ("bundled", bundled)]
    return dirs


def locate_tool(name: str) -> tuple[str | None, str]:
    """Return (path, source) where source is configured | bundled | path | missing."""
    for source, directory in candidate_dirs():
        candidate = directory / _exe(name)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate), source
    found = shutil.which(name)
    return (found, "path") if found else (None, "missing")


def find_tool(name: str) -> str | None:
    return locate_tool(name)[0]


# H.264 encoders in preference order. The installer ships an LGPL FFmpeg build (BtbN
# "lgpl" / static LGPL), which has no GPL libx264 but does include Cisco's libopenh264;
# mpeg4 is the last resort (always built in; ComfyUI/PyAV decode it fine).
_H264_ENCODERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("libx264", ("-c:v", "libx264", "-crf", "18", "-preset", "veryfast")),
    ("libopenh264", ("-c:v", "libopenh264", "-b:v", "8M")),
    ("mpeg4", ("-c:v", "mpeg4", "-q:v", "2")),
)
_encoder_cache: dict[str, frozenset[str]] = {}


def available_encoders(binary: str) -> frozenset[str]:
    """Names from ``ffmpeg -encoders`` (cached per binary; empty set on failure)."""
    cached = _encoder_cache.get(binary)
    if cached is not None:
        return cached
    import subprocess

    names: set[str] = set()
    try:
        out = subprocess.run([binary, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        parts = line.split()
        # " V....D libx264   libx264 H.264 ..." -> flags, name, description
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    result = frozenset(names)
    if result:
        _encoder_cache[binary] = result
    return result


def video_encoder_args(binary: str) -> list[str]:
    """``-c:v`` arguments for an MP4 video stream using the best encoder this FFmpeg has."""
    names = available_encoders(binary)
    for name, args in _H264_ENCODERS:
        if name in names:
            return list(args)
    return list(_H264_ENCODERS[0][1])  # unknown build: keep the historical default
