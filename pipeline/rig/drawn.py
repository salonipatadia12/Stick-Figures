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
        d.line(sm, fill=RED + (255,), width=int(8 * SS), joint='curve')
        d.line(sm, fill=(236, 110, 100, 120), width=int(3 * SS), joint='curve')
    # two loose ends trailing out
    if tangle > 0.6:
        for ang in (rng.uniform(2.2, 2.9), rng.uniform(5.3, 5.9)):
            end = (c + size * SS * 0.48 * math.cos(ang), c + size * SS * 0.48 * math.sin(ang))
            start = path[-1] if ang > 4 else path[0]
            mid = ((start[0] + end[0]) / 2 + 20 * SS, (start[1] + end[1]) / 2 - 15 * SS)
            d.line([start, mid, end], fill=RED + (255,), width=int(8 * SS), joint='curve')
    return _done(im, seed, 0.6)


def ribbon(length: int = 420, height: int = 120, kink: float = 0.0, phase: float = 0.0, seed: int = 2) -> Image.Image:
    """The airway as a tube: two navy walls with a pale blue inside and flow chevrons moving along it. kink 0..1
    pinches the walls together in the middle until the tube is shut; the flow stops at the pinch."""
    im = _canvas(length, height)
    d = ImageDraw.Draw(im)
    n = 64
    L, Hh = length * SS, height * SS
    top, bot, mid = [], [], []
    for i in range(n + 1):
        u = i / n
        x = u * L
        sway = math.sin(u * math.pi * 1.4 + 0.4) * Hh * 0.10
        pinch = math.exp(-((u - 0.5) / 0.14) ** 2) * kink
        half = Hh * 0.30 * (1 - 0.96 * pinch)
        y = Hh / 2 + sway
        top.append((x, y - half))
        bot.append((x, y + half))
        mid.append((x, y))
    d.polygon(top + bot[::-1], fill=(190, 226, 242, 200))
    # flow chevrons, spaced along the tube; they stop short of a closed pinch
    step = 0.11
    k = 0
    while True:
        u = ((k * step + phase * 0.05) % 1.0)
        k += 1
        if k > 1 / step + 1:
            break
        if kink > 0.5 and abs(u - 0.5) < 0.18:
            continue
        i = int(u * n)
        x, y = mid[i]
        half = (bot[i][1] - top[i][1]) / 2
        if half < 6 * SS:
            continue
        a = half * 0.55
        d.line([(x - a * 0.7, y - a), (x + a * 0.5, y), (x - a * 0.7, y + a)], fill=(90, 150, 196, 230), width=int(3 * SS), joint='curve')
    d.line(top, fill=NAVY + (255,), width=int(5 * SS), joint='curve')
    d.line(bot, fill=NAVY + (255,), width=int(5 * SS), joint='curve')
    for pts in (top, bot):                        # rounded open ends
        d.line([pts[0], (pts[0][0], mid[0][1])], fill=NAVY + (255,), width=int(5 * SS))
        d.line([pts[-1], (pts[-1][0], mid[-1][1])], fill=NAVY + (255,), width=int(5 * SS))
    if kink > 0.6:                                 # a red pinch mark where the walls meet
        cx, cy = mid[n // 2]
        r = 7 * SS
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED + (int(255 * min(1.0, (kink - 0.6) / 0.3)),))
    return _done(im, seed, 0.35)


def scrap_holed(size: int = 200, seed: int = 7) -> Image.Image:
    """A quilt scrap with two ragged holes torn in it (broken sleep)."""
    base = scrap(size, seed)
    for k, (fx, fy, hs) in enumerate(((0.36, 0.40, 0.34), (0.66, 0.62, 0.28))):
        h = hole(int(size * hs), seed + 10 + k)
        base.alpha_composite(h, (int(size * fx - h.width / 2), int(size * fy - h.height / 2)))
    return base


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


def sunbeam(w: int = 260, h: int = 420, specks: int = 40, seed: int = 11) -> Image.Image:
    """A wide, soft diagonal beam of golden light with dust specks floating in it, for 'sunlight hits the air'.
    Anchor: top-left of the sprite (the beam enters at the top left and widens toward the bottom right)."""
    rng = random.Random(seed)
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    x0, x1 = w * SS * 0.18, w * SS * 0.42
    for k in range(6, 0, -1):
        a = int(20 * (7 - k) / 3)
        grow = w * SS * 0.32 * k / 6
        d.polygon([(x0 - grow * 0.3, 0), (x1 + grow * 0.3, 0), (w * SS - 0.0 + grow * 0.0, h * SS), (w * SS * 0.45 - grow * 0.4, h * SS)], fill=WARM + (a,))
    im = im.filter(ImageFilter.GaussianBlur(6 * SS))
    d = ImageDraw.Draw(im)
    for _ in range(specks):                                     # dust in the beam: small cream dots, a few brighter
        fy = rng.random()
        left = x0 + (w * SS * 0.45 - x0) * fy
        right = x1 + (w * SS - x1) * fy
        x, y = rng.uniform(left, right), fy * h * SS
        r = rng.uniform(1.2, 3.2) * SS
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 248, 225, rng.randint(150, 240)))
    return im.reduce(SS)


def specks(w: int = 220, h: int = 160, n: int = 36, seed: int = 12) -> Image.Image:
    """Floating dust specks on their own (no beam), for air with motes in it."""
    rng = random.Random(seed)
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    for _ in range(n):
        x, y = rng.uniform(0.05, 0.95) * w * SS, rng.uniform(0.05, 0.95) * h * SS
        r = rng.uniform(1.4, 3.4) * SS
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 246, 220, rng.randint(140, 235)))
    return im.reduce(SS)


def photo(w: int = 240, h: int = 300, sky=(214, 150, 120), ground=(170, 110, 80), sun=(150, 170, 190), seed: int = 13) -> Image.Image:
    """A photo print with a cream border: a sky, a horizon with low hills, and a sun (a Martian sunset by default:
    a dusty pink sky, a blue-grey sun). Anchor: centre. Pass other colours for an Earth sky."""
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    W, H = w * SS, h * SS
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=6 * SS, fill=CREAM + (255,), outline=NAVY + (255,), width=3 * SS)
    m = int(0.07 * W)
    box = (m, m, W - m, H - int(0.14 * H))
    d.rectangle(box, fill=sky + (255,))
    hy = box[1] + (box[3] - box[1]) * 0.66
    sr = (box[2] - box[0]) * 0.11
    sx = (box[0] + box[2]) / 2
    haze = Image.new('RGBA', im.size, (0, 0, 0, 0))             # the sun's soft blue-grey halo
    dh = ImageDraw.Draw(haze)
    for k in range(3, 0, -1):
        r = sr * (1 + 0.5 * k)
        dh.ellipse((sx - r, hy - 0.1 * (box[3] - box[1]) - r, sx + r, hy - 0.1 * (box[3] - box[1]) + r), fill=sun + (int(34 / k),))
    im.alpha_composite(haze.filter(ImageFilter.GaussianBlur(4 * SS)))
    d = ImageDraw.Draw(im)
    d.ellipse((sx - sr, hy - 0.1 * (box[3] - box[1]) - sr, sx + sr, hy - 0.1 * (box[3] - box[1]) + sr), fill=sun + (255,))
    hills = [(box[0], box[3]), (box[0], hy + 6 * SS)]
    for i in range(1, 8):
        hills.append((box[0] + (box[2] - box[0]) * i / 8, hy + (6 if i % 2 else -4) * SS))
    hills += [(box[2], hy + 4 * SS), (box[2], box[3])]
    d.polygon(hills, fill=ground + (255,))
    d.rectangle(box, outline=NAVY + (160,), width=2 * SS)
    return _done(im, seed, 0.45)


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
