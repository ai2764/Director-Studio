"""Deterministic rear/side hair lock from multi-panel actor references.

Qwen edit often invents a half-up bun on back views even when the uploaded
actor sheet shows fully loose hair. When the actor input is a wide 3-panel
sheet (front | side | back), we composite the reference head/hair onto the
generated three-view side and back panels so structure cannot drift.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger("director_studio.actor.hair_lock")

# Multi-panel sheets are wider than tall. Real examples include ~1.5 (1536×1024)
# three-up hair plates and ultra-wide ~3.0 turnarounds — not only ≥2.0.
_MIN_PANEL_ASPECT = 1.42
_MAX_PANEL_ASPECT = 4.2


def is_multipanel_hair_sheet(img: Image.Image) -> bool:
    w, h = img.size
    if h <= 0 or w < 600:
        return False
    ar = w / h
    if ar < _MIN_PANEL_ASPECT or ar > _MAX_PANEL_ASPECT:
        return False
    # Prefer images that look like 3 equal columns (soft check on vertical seams)
    return _looks_like_three_columns(img)


def _looks_like_three_columns(img: Image.Image) -> bool:
    """True if columns near 1/3 and 2/3 differ from panel centers (separator or cut)."""
    small = img.convert("L").resize((90, 30), Image.Resampling.BILINEAR)
    px = small.load()
    assert px is not None
    w, h = small.size

    def col_mean(x: int) -> float:
        s = 0.0
        for y in range(h):
            s += px[min(max(x, 0), w - 1), y]
        return s / h

    # centers of three panels vs boundaries
    c0, c1, c2 = col_mean(w // 6), col_mean(w // 2), col_mean(5 * w // 6)
    b1, b2 = col_mean(w // 3), col_mean(2 * w // 3)
    # Separators often brighter/darker; or adjacent panel centers just differ
    sep_signal = abs(b1 - (c0 + c1) / 2) + abs(b2 - (c1 + c2) / 2)
    center_spread = abs(c0 - c1) + abs(c1 - c2)
    # Loose hair multipanel usually has enough horizontal variation
    return sep_signal > 4.0 or center_spread > 8.0


def split_equal_panels(img: Image.Image, n: int = 3) -> list[Image.Image]:
    w, h = img.size
    pw = w // n
    panels: list[Image.Image] = []
    for i in range(n):
        x0 = i * pw
        x1 = w if i == n - 1 else (i + 1) * pw
        panels.append(img.crop((x0, 0, x1, h)).convert("RGBA"))
    return panels


def join_panels(panels: list[Image.Image]) -> Image.Image:
    if not panels:
        raise ValueError("no panels")
    h = max(p.height for p in panels)
    # normalize heights
    norm: list[Image.Image] = []
    for p in panels:
        if p.height != h:
            nw = max(1, int(p.width * h / p.height))
            p = p.resize((nw, h), Image.Resampling.LANCZOS)
        norm.append(p.convert("RGBA"))
    w = sum(p.width for p in norm)
    out = Image.new("RGBA", (w, h), (240, 240, 240, 255))
    x = 0
    for p in norm:
        out.paste(p, (x, 0), p)
        x += p.width
    return out


def _vertical_alpha_mask(size: tuple[int, int], solid_frac: float, fade_frac: float) -> Image.Image:
    """Full opacity for top solid_frac, linear fade over next fade_frac, transparent below."""
    w, h = size
    solid_end = int(h * solid_frac)
    fade_end = int(h * (solid_frac + fade_frac))
    row_alphas: list[int] = []
    for y in range(h):
        if y < solid_end:
            a = 255
        elif y < fade_end and fade_end > solid_end:
            t = (y - solid_end) / (fade_end - solid_end)
            a = int(255 * (1.0 - t))
        else:
            a = 0
        row_alphas.append(a)
    # Build mask row-by-row (faster than per-pixel Python loops)
    mask = Image.new("L", (w, h), 0)
    for y, a in enumerate(row_alphas):
        if a:
            mask.paste(a, (0, y, w, y + 1))
    return mask


def _knockout_studio_background(panel: Image.Image) -> Image.Image:
    """Make near-uniform dark/gray studio backdrop transparent (multipanel sheets)."""
    rgba = panel.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    assert px is not None
    # Sample corners for bg estimate
    samples = [px[2, 2], px[w - 3, 2], px[2, h - 3], px[w - 3, h - 3]]
    br = sum(s[0] for s in samples) / 4
    bg = sum(s[1] for s in samples) / 4
    bb = sum(s[2] for s in samples) / 4
    # Threshold: pixels close to bg and low saturation → transparent
    out = Image.new("RGBA", (w, h))
    opx = out.load()
    assert opx is not None
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            dist = abs(r - br) + abs(g - bg) + abs(b - bb)
            sat = max(r, g, b) - min(r, g, b)
            val = max(r, g, b)
            # dark/gray studio backdrops (not skin / blonde hair)
            if (dist < 90 and sat < 36 and val < 150) or (sat < 22 and val < 95):
                opx[x, y] = (r, g, b, 0)
            else:
                opx[x, y] = (r, g, b, a)
    return out


def composite_head_from_ref(
    body_panel: Image.Image,
    hair_panel: Image.Image,
    *,
    coverage: float,
    fade: float,
    width_frac: float = 1.0,
    top_frac: float = 0.0,
    knockout_bg: bool = False,
) -> Image.Image:
    """Paste hair/head reference over the top of a generated panel with feathering.

    ``width_frac`` scales the hair plate relative to panel width (use ~0.4 for
    full-body panels so the head is not gigantic). ``top_frac`` shifts the paste
    down from the top of the panel.
    """
    body = body_panel.convert("RGBA")
    bw, bh = body.size
    src = hair_panel.convert("RGBA")
    if knockout_bg:
        src = _knockout_studio_background(src)
    hw0, hh0 = src.size
    target_w = max(1, int(bw * max(0.15, min(width_frac, 1.0))))
    scale = target_w / max(hw0, 1)
    nh = max(1, int(hh0 * scale))
    hair = src.resize((target_w, nh), Image.Resampling.LANCZOS)
    max_h = max(1, int(bh * (coverage + fade + 0.05)))
    if hair.height > max_h:
        hair = hair.crop((0, 0, target_w, max_h))

    x = (bw - hair.width) // 2
    y = max(0, int(bh * top_frac))
    layer = Image.new("RGBA", body.size, (0, 0, 0, 0))
    layer.paste(hair, (x, y), hair)

    # Feathered vertical mask
    mask = _vertical_alpha_mask(body.size, solid_frac=coverage, fade_frac=fade)
    if y > 0:
        shifted = Image.new("L", body.size, 0)
        shifted.paste(mask.crop((0, 0, bw, max(1, bh - y))), (0, y))
        mask = shifted
    # Combine with hair alpha so knocked-out bg stays transparent
    hair_alpha = Image.new("L", body.size, 0)
    hair_alpha.paste(hair.split()[3], (x, y))
    # min(mask, hair_alpha)
    mpx = mask.load()
    hpx = hair_alpha.load()
    assert mpx is not None and hpx is not None
    for yy in range(bh):
        for xx in range(bw):
            mpx[xx, yy] = min(mpx[xx, yy], hpx[xx, yy])

    return Image.composite(layer, body, mask)


def lock_threeview_hair_from_actor_sheet(
    threeview_path: Path,
    actor_path: Path,
    *,
    bust: bool = False,
) -> bool:
    """
    Overwrite side/back panel heads on a generated three-view using actor sheet.

    Returns True if a rewrite was applied.
    """
    if not threeview_path.is_file() or not actor_path.is_file():
        return False

    actor = Image.open(actor_path).convert("RGBA")
    if not is_multipanel_hair_sheet(actor):
        logger.info("hair_lock skip %s: actor not multipanel", threeview_path.name)
        return False

    sheet = Image.open(threeview_path).convert("RGBA")
    a_panels = split_equal_panels(actor, 3)
    t_panels = split_equal_panels(sheet, 3)
    if len(a_panels) < 3 or len(t_panels) < 3:
        return False

    # Bust plates are already head-and-shoulders → wide hair plate OK.
    # Full-body plates need a small centered head or the figure gets a giant head.
    if bust:
        cov, fade, wfrac, tfrac = 0.62, 0.18, 1.0, 0.0
        targets = (0, 1, 2)
        knockout = False  # bust already fills frame; soft studio ok after blend
    else:
        # Full-body: side vs back need different head scale (back crown must cover invented bun)
        targets = (1, 2)
        knockout = True

    out_panels: list[Image.Image] = []
    for i, body in enumerate(t_panels):
        if bust and i in targets:
            out_panels.append(
                composite_head_from_ref(
                    body,
                    a_panels[i],
                    coverage=cov,
                    fade=fade,
                    width_frac=wfrac,
                    top_frac=tfrac,
                    knockout_bg=knockout,
                )
            )
        elif (not bust) and i == 1:
            out_panels.append(
                composite_head_from_ref(
                    body,
                    a_panels[i],
                    coverage=0.30,
                    fade=0.08,
                    width_frac=0.46,
                    top_frac=0.02,
                    knockout_bg=True,
                )
            )
        elif (not bust) and i == 2:
            out_panels.append(
                composite_head_from_ref(
                    body,
                    a_panels[i],
                    coverage=0.34,
                    fade=0.07,
                    width_frac=0.52,
                    top_frac=0.01,
                    knockout_bg=True,
                )
            )
        else:
            out_panels.append(body)

    locked = join_panels(out_panels).convert("RGB")
    # backup original once
    bak = threeview_path.with_suffix(threeview_path.suffix + ".pre_hairlock.png")
    if not bak.exists():
        sheet.convert("RGB").save(bak)
    locked.save(threeview_path)
    logger.info(
        "hair_lock applied %s (bust=%s) from %s",
        threeview_path.name,
        bust,
        actor_path.name,
    )
    return True


def postprocess_actor_job_outputs(job_dir: Path) -> dict[str, bool]:
    """Apply hair lock to fullbody + bust threeviews when actor multipanel exists."""
    inputs = job_dir / "inputs"
    outputs = job_dir / "outputs"
    actor = None
    for name in ("actor.png", "actor.jpg", "actor.jpeg", "actor.webp"):
        p = inputs / name
        if p.is_file():
            actor = p
            break
    if actor is None:
        # any actor.*
        cands = list(inputs.glob("actor.*")) if inputs.is_dir() else []
        actor = cands[0] if cands else None
    if actor is None or not outputs.is_dir():
        return {}

    result: dict[str, bool] = {}
    fb = outputs / "fullbody_threeview.png"
    if not fb.exists():
        # alternate extensions
        alts = list(outputs.glob("fullbody_threeview.*"))
        fb = alts[0] if alts else fb
    bust = outputs / "bust_threeview.png"
    if not bust.exists():
        alts = list(outputs.glob("bust_threeview.*"))
        bust = alts[0] if alts else bust

    if fb.is_file():
        result["fullbody_threeview"] = lock_threeview_hair_from_actor_sheet(
            fb, actor, bust=False
        )
    if bust.is_file():
        result["bust_threeview"] = lock_threeview_hair_from_actor_sheet(
            bust, actor, bust=True
        )

    # Rebuild asset_sheet if both threeviews present (bust over fullbody vertically)
    sheet_path = outputs / "asset_sheet.png"
    if (
        result.get("fullbody_threeview") or result.get("bust_threeview")
    ) and fb.is_file() and bust.is_file():
        try:
            top = Image.open(bust).convert("RGB")
            bottom = Image.open(fb).convert("RGB")
            # match widths
            if top.width != bottom.width:
                bottom = bottom.resize(
                    (top.width, int(bottom.height * top.width / bottom.width)),
                    Image.Resampling.LANCZOS,
                )
            canvas = Image.new(
                "RGB",
                (top.width, top.height + bottom.height),
                (255, 255, 255),
            )
            canvas.paste(top, (0, 0))
            canvas.paste(bottom, (0, top.height))
            canvas.save(sheet_path)
            result["asset_sheet"] = True
        except Exception:
            logger.exception("hair_lock asset_sheet rebuild failed")
    return result
