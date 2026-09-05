from app.core.h3.frames import frames_for_seconds


def test_native_audio_frames_never_end_before_locked_audio():
    from app.core.h3.frames import frames_for_audio_seconds

    assert [frames_for_audio_seconds(seconds) for seconds in (9.06, 10.0, 9.52, 9.48, 7.96, 11.18)] == [
        226,
        243,
        243,
        243,
        192,
        277,
    ]
import pytest


def test_grid():
    n = frames_for_seconds(12.25)
    assert n % 17 == 5
    assert 124 <= n <= 362


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1.6, 39), (2.3, 56), (3.0, 73), (4.5, 107), (5.0, 124)],
)
def test_short_durations_snap_to_h3_grid(seconds, expected):
    assert frames_for_seconds(seconds) == expected
