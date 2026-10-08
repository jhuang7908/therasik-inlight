#!/usr/bin/env python3
"""Deterministic BioRender-style stand-ins for the approved img_trial3 cards.

The live t1_trap.png / t4_tsc_astro.png files are not in this checkout.
These drawings follow the same 1600×989 card, ~72% subject span, and 3–6%
terracotta accent so production QC can be tested against the approved look.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

W, H = 1600, 989
DEEP = (15, 107, 92)
MID = (47, 125, 109)
MINT = (159, 216, 203)
SLATE = (92, 107, 103)
PALE = (214, 222, 219)
TERR = (192, 73, 47)
TERR_L = (224, 122, 95)
WHITE = (255, 255, 255)


def _y_antibody(d: ImageDraw.ImageDraw, cx: int, cy: int, scale: float, color=MID) -> None:
    stem_h = int(38 * scale)
    arm = int(28 * scale)
    thick = max(4, int(7 * scale))
    d.line([(cx, cy + stem_h), (cx, cy)], fill=color, width=thick)
    d.line([(cx, cy), (cx - arm, cy - arm)], fill=color, width=thick)
    d.line([(cx, cy), (cx + arm, cy - arm)], fill=color, width=thick)
    r = max(3, int(5 * scale))
    d.ellipse([cx - r, cy + stem_h - r, cx + r, cy + stem_h + r], fill=PALE)
    d.ellipse([cx - arm - r, cy - arm - r, cx - arm + r, cy - arm + r], fill=color)
    d.ellipse([cx + arm - r, cy - arm - r, cx + arm + r, cy - arm + r], fill=color)


def _cell(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, fill, nucleus=None, outline=SLATE) -> None:
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=2)
    if nucleus:
        nr = max(6, r // 3)
        d.ellipse([cx - nr, cy - nr, cx + nr, cy + nr], fill=nucleus)


def render_t1_trap(path: Path) -> None:
    im = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(im)

    # Stage 1 — plate + pipette (left).
    d.rounded_rectangle([210, 360, 360, 520], radius=10, fill=PALE, outline=SLATE, width=2)
    for i in range(4):
        for j in range(6):
            x, y = 228 + j * 20, 380 + i * 34
            d.ellipse([x, y, x + 12, y + 12], fill=MINT, outline=MID, width=1)
    d.rounded_rectangle([250, 250, 330, 310], radius=8, fill=PALE, outline=SLATE, width=2)
    for i in range(4):
        d.line([(262 + i * 16, 310), (262 + i * 16, 348)], fill=SLATE, width=2)

    # Four engager candidates.
    for i, y in enumerate((220, 360, 500, 640)):
        _y_antibody(d, 470, y, 1.15, MID if i % 2 == 0 else DEEP)
        d.line([(360, 400), (430, y + 10)], fill=SLATE, width=2)

    # Stage 2 — protein / ribbon box.
    d.rounded_rectangle([560, 220, 1040, 770], radius=18, outline=PALE, width=2)
    d.arc([600, 280, 860, 520], start=200, end=480, fill=DEEP, width=10)
    d.arc([720, 360, 980, 620], start=20, end=300, fill=MID, width=9)
    d.arc([640, 430, 900, 700], start=160, end=420, fill=MINT, width=8)
    d.ellipse([790, 430, 870, 510], fill=MID, outline=SLATE, width=2)

    # Stage 3 — terracotta target on the upper-right golden point (0.618, 0.382).
    gx, gy = int(0.618 * W), int(0.382 * H)
    _cell(d, gx, gy, 155, TERR, nucleus=TERR_L, outline=TERR)
    _y_antibody(d, 1280, 560, 1.7, DEEP)
    _cell(d, 1280, 760, 92, PALE, nucleus=MINT, outline=SLATE)
    d.line([(1040, 500), (1188, 560)], fill=SLATE, width=3)

    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")


def render_t4_tsc_astro(path: Path) -> None:
    im = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(im)

    # Organoid (left), kept inside the 72% card span.
    ox, oy, r = 480, 494, 210
    d.ellipse([ox - r, oy - r, ox + r, oy + r], fill=MINT, outline=SLATE, width=2)
    d.ellipse([ox - 24, oy - 24, ox + 24, oy + 24], fill=WHITE, outline=PALE, width=1)
    rings = (
        (176, 16, MID),
        (136, 14, DEEP),
        (98, 13, MID),
        (62, 11, DEEP),
    )
    for rad, cr, col in rings:
        n = 16 if rad > 120 else 11
        for i in range(n):
            import math
            ang = i * (360 / n) * math.pi / 180
            cx = int(ox + rad * math.cos(ang))
            cy = int(oy + rad * math.sin(ang))
            _cell(d, cx, cy, cr, col, nucleus=MINT if col == DEEP else None)

    # Mint mosaic on the organoid (not the accent).
    for i, (dx, dy) in enumerate((
        (148, -18), (168, 6), (158, 34), (178, 22), (140, 16),
        (170, -16), (186, 4), (150, 46), (162, -36), (190, 36),
    )):
        _cell(d, ox + dx, oy + dy, 26 if i < 6 else 20, MID, nucleus=MINT, outline=SLATE)

    # Zoom lines to the cortical field.
    d.line([(ox + r - 6, oy - 70), (900, 200)], fill=SLATE, width=1)
    d.line([(ox + r - 6, oy + 70), (900, 788)], fill=SLATE, width=1)
    d.ellipse([900, 190, 1420, 800], outline=SLATE, width=2)

    def neuron(cx, cy, scale=1.0, fill=MID):
        soma = int(48 * scale)
        d.ellipse([cx - soma, cy - soma, cx + soma, cy + soma], fill=fill, outline=SLATE)
        d.ellipse([cx - soma // 3, cy - soma // 3, cx + soma // 3, cy + soma // 3], fill=MINT)
        arms = (
            (-70, -32, -110, -80), (56, -40, 100, -96),
            (-32, 62, -70, 118), (40, 56, 88, 112),
            (-86, 16, -130, 30), (80, 8, 128, -16),
        )
        for x1, y1, x2, y2 in arms:
            d.line([(cx, cy), (cx + int(x1 * scale), cy + int(y1 * scale))], fill=fill, width=max(6, int(8 * scale)))
            d.line(
                [(cx + int(x1 * scale), cy + int(y1 * scale)),
                 (cx + int(x2 * scale), cy + int(y2 * scale))],
                fill=fill, width=max(4, int(6 * scale)),
            )

    neuron(1088, 520, 1.05, MID)
    neuron(1230, 640, 1.0, DEEP)
    neuron(1100, 720, 0.95, MID)
    # ONE terracotta accent on the upper-right golden point.
    gx, gy = int(0.618 * W), int(0.382 * H)
    _cell(d, gx, gy, 152, TERR, nucleus=TERR_L, outline=TERR)

    for cx, cy, rad, col in (
        (980, 280, 16, MINT), (1320, 300, 15, MID), (1340, 620, 16, DEEP),
        (980, 720, 15, MINT), (1280, 740, 14, MID), (970, 500, 13, DEEP),
        (1330, 450, 14, MINT), (1140, 250, 13, MID), (1160, 760, 14, DEEP),
    ):
        _cell(d, cx, cy, rad, col, nucleus=PALE)

    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG")


def main() -> None:
    here = Path(__file__).resolve().parent
    render_t1_trap(here / "t1_trap.png")
    render_t4_tsc_astro(here / "t4_tsc_astro.png")


if __name__ == "__main__":
    main()
