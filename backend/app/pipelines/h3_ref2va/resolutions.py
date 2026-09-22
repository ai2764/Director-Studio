"""Local H3 dimensions shared by manual Production and managed runs."""

from __future__ import annotations


LOCAL_H3_PRESETS: dict[str, tuple[int, int]] = {
    "landscape-480": (864, 480),
    "landscape-720": (1280, 704),
    "landscape-768": (1376, 768),
    "landscape-1080": (1920, 1088),
    "portrait-480": (480, 864),
    "portrait-720": (704, 1280),
    "portrait-768": (768, 1376),
    "portrait-1080": (1088, 1920),
}


def resolve_local_resolution(preset: str) -> tuple[int, int]:
    try:
        return LOCAL_H3_PRESETS[preset]
    except KeyError as exc:
        raise ValueError(f"Unknown local H3 resolution preset: {preset}") from exc


def list_local_resolutions() -> list[dict[str, str | int]]:
    return [
        {
            "id": preset,
            "label": f"{preset.split('-', 1)[0].title()} {preset.split('-', 1)[1]} tier · {width}×{height}",
            "width": width,
            "height": height,
        }
        for preset, (width, height) in LOCAL_H3_PRESETS.items()
    ]
