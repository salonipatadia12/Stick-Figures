"""Code-drawn props with a painted finish, for beats whose script asks for things no base has: a row of
hanging lamps, a tangled knot of thread, a ribbon of air, light through holes in a quilt, small far-off
figures in a street, a scrap of quilt, a coil spring. Each returns an RGBA sprite (deterministic by seed);
`props.painterly` gives them the soft, grainy edge of the paintings.
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import props

NAVY = (24, 26, 50)
WARM = (255, 214, 140)
BRASS = (190, 150, 80)
CREAM = (246, 236, 214)
RED = (196, 52, 48)
SS = 2


def _canvas(w, h):
    return Image.new('RGBA', (w * SS, h * SS), (0, 0, 0, 0))


def _done(im: Image.Image, seed: int = 1, strength: float = 0.7) -> Image.Image:
    im = im.reduce(SS)
    return props.painterly(im, seed, strength)


def lamp(size: int = 160, lit: bool = True, cord: int = 120, seed: int = 1) -> Image.Image:
    """A small pendant lamp on a cord: a bell shade, warm glow when lit, grey when dark. Anchor: top centre."""
    w, h = size, cord + size
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w * SS / 2
    top = cord * SS
    d.line([(cx, 0), (cx, top)], fill=NAVY + (255,), width=3 * SS)
    shade = BRASS if lit else (118, 112, 110)
    rim = size * 0.46 * SS
    d.polygon([(cx - rim * 0.22, top), (cx + rim * 0.22, top), (cx + rim, top + size * 0.45 * SS), (cx - rim, top + size * 0.45 * SS)],
              fill=shade + (255,), outline=NAVY + (255,), width=3 * SS)
    by = top + size * 0.45 * SS
    bulb_r = size * 0.14 * SS
    if lit:
        glow = Image.new('RGBA', im.size, (0, 0, 0, 0))
        dg = ImageDraw.Draw(glow)
        for k in range(5, 0, -1):
            r = bulb_r * (1 + 0.9 * k)
            dg.ellipse((cx - r, by - r * 0.6, cx + r, by + r * 1.3), fill=WARM + (int(26 * (6 - k) / 2),))
        im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6 * SS)))
        d.ellipse((cx - bulb_r, by - bulb_r * 0.4, cx + bulb_r, by + bulb_r * 1.6), fill=(255, 240, 190, 255))
    else:
        d.ellipse((cx - bulb_r, by - bulb_r * 0.4, cx + bulb_r, by + bulb_r * 1.6), fill=(160, 158, 150, 255), outline=NAVY + (200,), width=2 * SS)
    return _done(im, seed, 0.5)


def knot(size: int = 220, seed: int = 3, tangle: float = 1.0) -> Image.Image:
    """A tangled knot of red thread with a few loose ends. tangle 0..1 draws it progressively."""
    rng = random.Random(seed)
    im = _canvas(size, size)
    d = ImageDraw.Draw(im)
    c = size * SS / 2
    n = 9
    pts = []
    a = 0.0
    r = size * SS * 0.12
    for k in range(60):
        a += rng.uniform(0.35, 1.1)
        r = size * SS * rng.uniform(0.10, 0.36)
        pts.append((c + r * math.cos(a) * rng.uniform(0.7, 1.1), c + r * math.sin(a) * rng.uniform(0.7, 1.1)))
    m = max(2, int(len(pts) * max(0.0, min(1.0, tangle))))
    path = pts[:m]
    if len(path) > 2:
        sm = []
        for i in range(len(path) - 1):
            (x0, y0), (x1, y1) = path[i], path[i + 1]
            for j in range(6):
                u = j / 6
                sm.append((x0 + (x1 - x0) * u + 6 * SS * math.sin(u * math.pi) * rng.uniform(-1, 1),
                           y0 + (y1 - y0) * u + 6 * SS * math.sin(u * math.pi) * rng.uniform(-1, 1)))
        d.line(sm, fill=RED + (255,), width=int(5 * SS), joint='curve')
        d.line(sm, fill=(236, 110, 100, 120), width=int(2 * SS), joint='curve')
    # two loose ends trailing out
    if tangle > 0.6:
        for ang in (rng.uniform(2.2, 2.9), rng.uniform(5.3, 5.9)):
            end = (c + size * SS * 0.48 * math.cos(ang), c + size * SS * 0.48 * math.sin(ang))
            start = path[-1] if ang > 4 else path[0]
            mid = ((start[0] + end[0]) / 2 + 20 * SS, (start[1] + end[1]) / 2 - 15 * SS)
            d.line([start, mid, end], fill=RED + (255,), width=int(5 * SS), joint='curve')
    return _done(im, seed, 0.6)


def ribbon(length: int = 420, height: int = 120, kink: float = 0.0, phase: float = 0.0, seed: int = 2) -> Image.Image:
    """A pale blue ribbon of air flowing left to right; kink 0..1 pinches it shut in the middle like a bent hose."""
    im = _canvas(length, height)
    d = ImageDraw.Draw(im)
    n = 48
    top, bot = [], []
    for i in range(n + 1):
        u = i / n
        x = u * length * SS
        wave = math.sin(u * 2 * math.pi * 1.6 + phase) * height * 0.18 * SS
        thick = height * 0.26 * SS
        pinch = math.exp(-((u - 0.5) / 0.12) ** 2) * kink
        th = thick * (1 - 0.92 * pinch)
        bend = pinch * height * 0.22 * SS
        y = height * SS / 2 + wave + bend
        top.append((x, y - th / 2))
        bot.append((x, y + th / 2))
    poly = top + bot[::-1]
    d.polygon(poly, fill=(168, 214, 236, 170), outline=(120, 176, 210, 220), width=int(2 * SS))
    for i in range(0, n, 6):
        x0, y0 = top[i]
        x1, y1 = bot[i]
        d.line([((x0 + x1) / 2 - 8 * SS, (y0 + y1) / 2), ((x0 + x1) / 2 + 8 * SS, (y0 + y1) / 2)], fill=(220, 240, 250, 150), width=int(2 * SS))
    if kink > 0.5:
        cx, cy = top[n // 2][0], (top[n // 2][1] + bot[n // 2][1]) / 2
        d.line([(cx - 10 * SS, cy - 22 * SS), (cx + 10 * SS, cy + 22 * SS)], fill=NAVY + (int(200 * (kink - 0.5) * 2),), width=int(4 * SS))
        d.line([(cx + 10 * SS, cy - 22 * SS), (cx - 10 * SS, cy + 22 * SS)], fill=NAVY + (int(200 * (kink - 0.5) * 2),), width=int(4 * SS))
    return _done(im, seed, 0.4)


def light_shaft(w: int = 90, h: int = 260, seed: int = 4) -> Image.Image:
    """A soft shaft of golden light widening downward, for light through a hole in a quilt. Anchor: top centre."""
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w * SS / 2
    for k in range(6, 0, -1):
        a = int(22 * (7 - k) / 3)
        spread = w * SS * 0.5 * k / 6
        d.polygon([(cx - spread * 0.25, 0), (cx + spread * 0.25, 0), (cx + spread, h * SS), (cx - spread, h * SS)], fill=WARM + (a,))
    im = im.filter(ImageFilter.GaussianBlur(5 * SS))
    return im.reduce(SS)


def hole(size: int = 70, seed: int = 5) -> Image.Image:
    """A ragged hole in cloth with bright light showing through."""
    rng = random.Random(seed)
    im = _canvas(size, size)
    d = ImageDraw.Draw(im)
    c = size * SS / 2
    pts = []
    for k in range(14):
        a = 2 * math.pi * k / 14
        r = size * SS * rng.uniform(0.28, 0.46)
        pts.append((c + r * math.cos(a), c + r * math.sin(a)))
    d.polygon(pts, fill=(255, 246, 214, 255), outline=(120, 78, 56, 255), width=int(3 * SS))
    glow = Image.new('RGBA', im.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(pts, fill=WARM + (120,))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(8 * SS)))
    return _done(im, seed, 0.5)


def figure(height: int = 90, yawn: float = 0.0, slump: float = 0.0, step: float = 0.0, seed: int = 6) -> Image.Image:
    """A small far-off version of the character: round white head, bulb torso, navy legs, mid-stride.
    yawn opens the mouth and shuts the eyes; slump drops the head forward. Anchor: bottom centre."""
    h = height
    w = int(h * 0.7)
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w * SS / 2
    D = h * SS * 0.42
    lw = max(2, int(h * 0.05 * SS))
    # legs mid-stride
    hip = (cx, h * SS * 0.72)
    s = math.sin(step * 2 * math.pi) * h * SS * 0.12
    d.line([hip, (cx - s, h * SS - lw)], fill=NAVY + (255,), width=lw)
    d.line([hip, (cx + s, h * SS - lw)], fill=NAVY + (255,), width=lw)
    # torso
    d.ellipse((cx - D * 0.26, h * SS * 0.42, cx + D * 0.26, h * SS * 0.76), fill=(250, 250, 248, 255), outline=NAVY + (255,), width=lw)
    # head, dropped forward when slumped
    hx, hy = cx + slump * D * 0.18, D * 0.52 + slump * D * 0.12
    d.ellipse((hx - D / 2, hy - D / 2, hx + D / 2, hy + D / 2), fill=(252, 252, 250, 255), outline=NAVY + (255,), width=lw)
    ex = D * 0.17
    ey = hy - D * 0.02
    if yawn > 0.5:
        for sx in (-1, 1):
            d.arc((hx + sx * ex - D * 0.09, ey - D * 0.06, hx + sx * ex + D * 0.09, ey + D * 0.06), 200, 340, fill=NAVY + (255,), width=lw)
        d.ellipse((hx - D * 0.10, hy + D * 0.12, hx + D * 0.10, hy + D * 0.12 + D * 0.22 * yawn), fill=NAVY + (255,))
    else:
        for sx in (-1, 1):
            d.ellipse((hx + sx * ex - D * 0.05, ey - D * 0.06, hx + sx * ex + D * 0.05, ey + D * 0.06), fill=NAVY + (255,))
        d.line([(hx - D * 0.07, hy + D * 0.2), (hx + D * 0.07, hy + D * 0.2)], fill=NAVY + (255,), width=max(1, lw // 2))
    return _done(im, seed, 0.35)


def scrap(size: int = 200, seed: int = 7) -> Image.Image:
    """A torn scrap of the patchwork quilt: rust, cream and teal squares with a ragged edge."""
    rng = random.Random(seed)
    im = _canvas(size, size)
    d = ImageDraw.Draw(im)
    cols = [(184, 92, 70), (236, 222, 196), (116, 160, 150), (176, 150, 122)]
    cell = size * SS / 3
    for r in range(3):
        for c in range(3):
            d.rectangle((c * cell, r * cell, (c + 1) * cell, (r + 1) * cell), fill=cols[(r * 3 + c) % 4] + (255,))
    for k in range(1, 3):
        d.line([(k * cell, 0), (k * cell, size * SS)], fill=(90, 60, 40, 200), width=2 * SS)
        d.line([(0, k * cell), (size * SS, k * cell)], fill=(90, 60, 40, 200), width=2 * SS)
    mask = Image.new('L', im.size, 0)
    pts = []
    for k in range(26):
        a = 2 * math.pi * k / 26
        r = size * SS * rng.uniform(0.36, 0.5)
        pts.append((size * SS / 2 + r * math.cos(a), size * SS / 2 + r * math.sin(a)))
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    im.putalpha(mask)
    d = ImageDraw.Draw(im)
    d.line(pts + pts[:1], fill=NAVY + (255,), width=int(3 * SS), joint='curve')
    return _done(im, seed, 0.6)


def spring(width: int = 90, height: int = 220, turns: int = 7, squash: float = 0.0, seed: int = 8) -> Image.Image:
    """A tightly wound metal coil spring. squash 0..1 compresses it. Anchor: bottom centre."""
    im = _canvas(width, height)
    d = ImageDraw.Draw(im)
    h = height * SS * (1 - 0.45 * squash)
    top = height * SS - h
    pts = []
    n = turns * 24
    for i in range(n + 1):
        u = i / n
        a = u * turns * 2 * math.pi
        x = width * SS / 2 + math.sin(a) * width * SS * 0.42
        y = top + u * h + math.cos(a) * 4 * SS
        pts.append((x, y))
    d.line(pts, fill=(96, 98, 108, 255), width=int(7 * SS), joint='curve')
    d.line(pts, fill=(196, 200, 210, 255), width=int(3 * SS), joint='curve')
    return _done(im, seed, 0.4)


def steam(width: int = 80, height: int = 140, phase: float = 0.0, seed: int = 9) -> Image.Image:
    im = _canvas(width, height)
    d = ImageDraw.Draw(im)
    for k in range(3):
        pts = [(width * SS * (0.3 + 0.2 * k) + 10 * SS * math.sin(phase + y / 20 + k), y) for y in range(height * SS, 0, -6)]
        d.line(pts, fill=(255, 255, 255, 90), width=int(5 * SS), joint='curve')
    return im.filter(ImageFilter.GaussianBlur(3 * SS)).reduce(SS)


def shadow_disc(w: int, h: int) -> Image.Image:
    m = Image.new('L', (w * 2, h * 3), 0)
    ImageDraw.Draw(m).ellipse((w * 0.5, h, w * 1.5, h * 2), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(max(2, h * 0.35)))
    dark = Image.new('RGBA', m.size, (70, 36, 16, 0))
    dark.putalpha(m.point(lambda v: int(v * 0.5)))
    return dark
