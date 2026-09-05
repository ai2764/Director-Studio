"""H3 local frame-count grid: n % 17 == 5, including tested short clips."""

from __future__ import annotations

import math

MIN_FRAMES = 5
MAX_FRAMES = 362
FPS = 24
MOD = 17
RESIDUE = 5


def validate_frame_count(frames: int) -> int:
    """Validate an exact local H3 frame count and return it."""
    frames = int(frames)
    if frames < MIN_FRAMES or frames > MAX_FRAMES or frames % MOD != RESIDUE:
        raise ValueError(
            f"frame count {frames} is outside supported H3 grid "
            f"[{MIN_FRAMES}, {MAX_FRAMES}] (n % {MOD} == {RESIDUE})"
        )
    return frames


def frames_for_seconds(seconds: float) -> int:
    """Snap duration to nearest H3-valid frame count.

    target_frames = round(seconds * 24); nearest n with n % 17 == 5.
    Short clips below the nominal 124-frame training range are intentionally
    supported by the local ComfyUI H3 node for tighter shot control.
    """
    if seconds <= 0:
        raise ValueError(f"duration must be positive, got {seconds}")

    target = round(seconds * FPS)

    # Nearest n with n ≡ 5 (mod 17), unconstrained.
    # Among candidates target + d where (target + d) % 17 == 5.
    remainder = target % MOD
    # Offsets to nearest residues: go down to RESIDUE or up to RESIDUE.
    down = (remainder - RESIDUE) % MOD  # steps to subtract
    up = (RESIDUE - remainder) % MOD  # steps to add
    if down == 0 and up == 0:
        nearest = target
    elif down <= up:
        nearest = target - down
    else:
        nearest = target + up

    # Tie-break: when equidistant, prefer the lower frame count already handled
    # by down <= up (prefer down on equality).

    return validate_frame_count(nearest)


def frames_for_audio_seconds(seconds: float) -> int:
    """Choose the first valid grid point that fully contains locked audio."""
    if seconds <= 0:
        raise ValueError(f"duration must be positive, got {seconds}")
    target = math.ceil(seconds * FPS)
    up = (RESIDUE - (target % MOD)) % MOD
    return validate_frame_count(target + up)
