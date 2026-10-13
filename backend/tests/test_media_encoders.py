"""Context-video normalisation must work with the bundled LGPL FFmpeg (no libx264)."""
import subprocess

import pytest

from app.core.media import binaries


def test_prefers_libx264_then_openh264_then_mpeg4(monkeypatch):
    for names, expected in [({"libx264", "libopenh264", "mpeg4"}, "libx264"),
                            ({"libopenh264", "mpeg4"}, "libopenh264"),
                            ({"mpeg4"}, "mpeg4")]:
        monkeypatch.setattr(binaries, "available_encoders", lambda _b, n=names: frozenset(n))
        args = binaries.video_encoder_args("ffmpeg")
        assert args[:2] == ["-c:v", expected]


def test_parses_encoder_listing(monkeypatch):
    listing = (" V....D libopenh264          OpenH264 H.264\n"
               " V.S..D mpeg4                MPEG-4 part 2\n A....D aac   AAC\n ------\n")
    monkeypatch.setattr(binaries, "_encoder_cache", {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, listing, ""))
    assert binaries.available_encoders("/x/ffmpeg") == {"libopenh264", "mpeg4", "aac"}


def test_transcode_context_video_with_real_ffmpeg(tmp_path):
    ffmpeg = binaries.find_tool("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    src = tmp_path / "in.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
                    *binaries.video_encoder_args(ffmpeg), "-pix_fmt", "yuv420p", str(src)], check=True)
    from app.pipelines.h3_ref2va.video_context import _transcode_context_video

    out = _transcode_context_video(src.read_bytes(), filename="in.mp4", width=256, height=144, keep_audio=False)
    assert len(out) > 1000
