"""Code-drawn explainer diagrams (docs/PLAN.md §7 H, the "graphic" visual type).

Each kind draws a full 1080x1920 frame on warm paper in the cozy palette (navy ink, rust, teal, gold), with its
elements popping in on their narration words:

- flow: boxes top to bottom joined by arrows (cause -> effect -> result);
- bounce: sunlight comes down, meets a layer, and bounces back to space;
- scatter: a beam of sunlight hits air molecules and sprays out in every direction;
- waves: a short blue wave scatters off a molecule, a long red wave passes straight;
- two_paths: the Earth, its air, an eye; the high sun's short path and the low sun's long path ("both", "long", "short");
- bars: 2-3 bars growing to relative heights (no axis numbers);
- thermometer: the level falls (or rises) on its word;
- cards: chapter cards sliding in, each in front of the last ("Old room: purpose" is a title and a note);
- layers: stacked bands, top to bottom.

The drawing stays between y 430 and 1250 (clear of the text slot at the top and the captions at y 1304-1392). Every label is
logged with its size and box for QA.
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .. import overlays as O
from .actions import back_out, ease

W, H = 1080, 1920
PAPER = (246, 238, 222)
INK = (32, 36, 64)
RUST = (196, 92, 54)
TEAL = (62, 138, 136)
GOLD = (240, 186, 80)
BLUE = (76, 132, 218)
RED = (214, 66, 48)
VIOLET = (138, 92, 200)
SKY = (176, 212, 240)
GROUND = (128, 150, 96)
LABEL = 58
TOP, BOTTOM = 430, 1250                  # clear of the text slot above and the captions (y 1304+) below
_paper_cache: dict = {}


def _noise(rng, cells_y: int, cells_x: int, blur: float = 0.0) -> np.ndarray:
    """Smooth value noise in [-1, 1] at full frame size from a coarse random grid."""
    g = rng.normal(0, 1, (cells_y, cells_x)).astype(np.float32)
    im = Image.fromarray(((g * 40) + 128).clip(0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return (np.asarray(im).astype(np.float32) - 128) / 40


def paper() -> Image.Image:
    """Warm watercolour paper in the cozy palette (user 2026-11-08: no flat clip-art on blank beige): a mottled
    wash in peach, ochre and grey-teal at several scales, paper granulation, a few soft blooms, and darker deckled
    edges like a painted page."""
    if 'p' not in _paper_cache:
        rng = np.random.default_rng(7)
        img = np.ones((H, W, 3), np.float32) * np.array(PAPER, np.float32)
        tints = [np.array(c, np.float32) for c in ((236, 196, 160), (222, 196, 140), (178, 196, 190))]
        for (cy, cx, amp), tint in zip(((5, 3, 0.9), (11, 6, 0.8), (22, 13, 0.6)), tints):
            n = np.clip(_noise(rng, cy, cx, 8), 0, None)[..., None]
            img = img * (1 - amp * 0.4 * n) + tint * (amp * 0.4 * n)
        gran = _noise(rng, H // 22, W // 22, 3)[..., None]            # soft granulation pooling in the wash
        fib = _noise(rng, H // 4, W // 4, 1.0)[..., None]
        img = img * (1 + gran * 0.035) + fib * 2.5
        img = img * np.array((1.0, 0.985, 0.95), np.float32)          # warm, never grey
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        edge = np.minimum.reduce([xx, W - 1 - xx, yy, H - 1 - yy]) + _noise(rng, 40, 24, 2) * 18
        rim = np.clip(1 - edge / 70, 0, 1) ** 2                      # deckled, darker painted edge
        vig = 1 - 0.13 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        img = img * vig[..., None] * (1 - 0.22 * rim[..., None]) + np.array((120, 80, 50), np.float32) * 0.22 * rim[..., None]
        _paper_cache['p'] = Image.fromarray(img.clip(0, 255).astype(np.uint8)).convert('RGBA')
    return _paper_cache['p'].copy()


def hand_drawn(layer: Image.Image, seed: int = 3) -> Image.Image:
    """Give a code-drawn layer the wobble of a hand-inked line: a smooth displacement of up to ~3 px and a soft edge."""
    key = ('warp', seed)
    if key not in _paper_cache:
        rng = np.random.default_rng(seed)
        dx = (_noise(rng, H // 70, W // 70, 3) * 2.6).round().astype(np.int32)
        dy = (_noise(rng, H // 70, W // 70, 3) * 2.6).round().astype(np.int32)
        yy, xx = np.mgrid[0:H, 0:W]
        _paper_cache[key] = (np.clip(yy + dy, 0, H - 1), np.clip(xx + dx, 0, W - 1))
    ys, xs = _paper_cache[key]
    a = np.asarray(layer)
    return Image.fromarray(a[ys, xs], 'RGBA').filter(ImageFilter.GaussianBlur(0.6))


def colour_for(label: str, k: int):
    l = label.lower()
    for word, c in (('violet', VIOLET), ('purple', VIOLET), ('blue', BLUE), ('red', RED), ('gold', GOLD), ('sun', GOLD)):
        if word in l:
            return c
    return (TEAL, RUST, GOLD, BLUE)[k % 4]


def text(frame, label, xy, size=LABEL, anchor='mm', fill=INK, q=1.0):
    """Draw a label at xy (anchor as PIL's), scaled by the pop q, and log it."""
    if q <= 0.02 or not label:
        return
    from .. import captions as C
    d = ImageDraw.Draw(frame, 'RGBA')
    lo, hi = C.SAFE['left'], C.SAFE['right']
    while True:                                       # inside the safe area: slide, then shrink (never below 40 px)
        f = O.font(O.FONT, max(8, int(size * q)))
        box = d.textbbox(xy, label, font=f, anchor=anchor)
        if box[2] - box[0] > hi - lo and size > 42:
            size -= 2
            continue
        shift = (hi - box[2]) if box[2] > hi else (lo - box[0]) if box[0] < lo else 0
        if shift:
            xy = (xy[0] + shift, xy[1])
            box = d.textbbox(xy, label, font=f, anchor=anchor)
        break
    d.text(xy, label, font=f, fill=fill, anchor=anchor)
    if q > 0.9:
        O.note_text(label, size, box)


def pop(t, at, dur=0.32):
    if at is None or t < at:
        return 0.0
    return back_out(min(1.0, (t - at) / dur), 1.6)


def grow(t, at, dur=0.6):
    if at is None or t < at:
        return 0.0
    return ease(min(1.0, (t - at) / dur))


def arrow(d, a, b, q=1.0, width=10, fill=INK, head=26):
    if q <= 0:
        return
    x = a[0] + (b[0] - a[0]) * q
    y = a[1] + (b[1] - a[1]) * q
    d.line([a, (x, y)], fill=fill, width=width)
    if q > 0.6:
        ang = math.atan2(y - a[1], x - a[0])
        for s in (-1, 1):
            d.line([(x, y), (x - head * math.cos(ang + s * 0.5), y - head * math.sin(ang + s * 0.5))], fill=fill, width=width)


def sun(d, c, r, q=1.0, col=GOLD):
    if q <= 0:
        return
    r *= q
    for k in range(10):
        a = k * math.pi / 5
        d.line([(c[0] + 1.25 * r * math.cos(a), c[1] + 1.25 * r * math.sin(a)), (c[0] + 1.6 * r * math.cos(a), c[1] + 1.6 * r * math.sin(a))],
               fill=col, width=max(2, int(r * 0.14)))
    d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=col, outline=INK, width=5)


def wave(d, x0, x1, y, wavelength, amp, col, phase, q=1.0, width=9):
    xe = x0 + (x1 - x0) * q
    pts = [(x, y + amp * math.sin(2 * math.pi * (x - x0) / wavelength - phase)) for x in np.arange(x0, xe, 4)]
    if len(pts) > 1:
        d.line(pts, fill=col, width=width, joint='curve')
    return pts[-1] if pts else (x0, y)


class Graphic:
    def __init__(self, kind: str, items: list, times: list, detail: str, t0: float, t1: float, seed: int = 5):
        self.kind, self.items, self.times, self.detail = kind, list(items), list(times), detail or ''
        self.t0, self.t1, self.seed = t0, t1, seed

    def frame(self, t: float) -> Image.Image:
        frame = paper()
        ink = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        getattr(self, '_' + self.kind, self._flow)(ink, t)
        ink = hand_drawn(ink, self.seed)
        a = np.asarray(ink).astype(np.float32)                     # the paper's texture shows through the fills: painted
        tex = np.asarray(frame.convert('L')).astype(np.float32)
        a[..., :3] *= (0.82 + 0.18 * tex / max(1.0, float(tex.mean())))[..., None]
        frame.alpha_composite(Image.fromarray(a.clip(0, 255).astype(np.uint8), 'RGBA'))
        return frame

    def at(self, k):
        return self.times[k] if k < len(self.times) else None

    # ---- kinds ---------------------------------------------------------------------------------------
    def _flow(self, frame, t):
        n = len(self.items)
        bh, gap = (150, 90) if n <= 3 else (126, 60)
        total = n * bh + (n - 1) * gap
        y = TOP + (BOTTOM - TOP - total) / 2
        d = ImageDraw.Draw(frame, 'RGBA')
        for k, item in enumerate(self.items):
            q = pop(t, self.at(k))
            if k > 0:
                arrow(d, (W / 2, y - gap + 8), (W / 2, y - 8), grow(t, self.at(k), 0.35), width=9)
            if q > 0:
                bw = 820 * q
                bx0, by0 = W / 2 - bw / 2, y + bh / 2 - bh * q / 2
                d.rounded_rectangle((bx0 + 6, by0 + 8, bx0 + bw + 6, by0 + bh * q + 8), radius=26, fill=(0, 0, 0, 40))
                d.rounded_rectangle((bx0, by0, bx0 + bw, by0 + bh * q), radius=26, fill=(255, 250, 240), outline=INK, width=6)
                d.rounded_rectangle((bx0, by0, bx0 + 30 * q, by0 + bh * q), radius=12, fill=colour_for(item, k))
                text(frame, item, (W / 2 + 12, y + bh / 2), q=q)
            y += bh + gap

    def _bounce(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        sun_c = (230, 600)
        band_y = 900
        q0, q1, q2 = (pop(t, self.at(k)) for k in range(3))
        g0, g1, g2 = (grow(t, self.at(k), 0.7) for k in range(3))
        # the ground: a gentle Earth curve
        d.pieslice((-400, 1080, W + 400, 2520), 180, 360, fill=GROUND, outline=INK, width=6)
        if q1 > 0:                                    # the veil: a soft wavy band
            band = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            bd = ImageDraw.Draw(band)
            pts_top = [(x, band_y - 26 + 10 * math.sin(x / 70 + t)) for x in range(0, W + 20, 20)]
            pts_bot = [(x, band_y + 26 + 10 * math.sin(x / 70 + t + 1)) for x in range(W, -20, -20)]
            bd.polygon(pts_top + pts_bot, fill=(222, 214, 176, int(230 * min(1, q1))))
            rng = random.Random(self.seed)
            for _ in range(70):
                x, y = rng.uniform(0, W), band_y + rng.uniform(-20, 20)
                bd.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(150, 140, 100, int(220 * min(1, q1))))
            frame.alpha_composite(band)
            d = ImageDraw.Draw(frame, 'RGBA')
        sun(d, sun_c, 62, q0)
        rays = [(330, 700), (420, 670), (510, 650)]
        hits = [(470, band_y - 30), (610, band_y - 30), (750, band_y - 30)]
        for (a, b) in zip(rays, hits):
            arrow(d, a, b, g0, width=9, fill=GOLD)
        for k, h in enumerate(hits):                   # most light bounces back up; a little gets through
            arrow(d, (h[0] + 10, h[1] - 8), (h[0] + 150, h[1] - 330), g2, width=9, fill=GOLD)
        if g2 > 0:
            arrow(d, (hits[1][0], band_y + 30), (hits[1][0] + 30, 1070), g2 * 0.8, width=5, fill=(200, 160, 70))
        text(frame, self.items[0] if self.items else '', (sun_c[0] + 130, sun_c[1] - 70), anchor='lm', q=q0)
        if len(self.items) > 1:
            text(frame, self.items[1], (W / 2, band_y + 105), q=q1)
        if len(self.items) > 2:
            text(frame, self.items[2], (W - 70, 450), anchor='rm', q=q2)

    def _scatter(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        q0, q1 = pop(t, self.at(0)), pop(t, self.at(1))
        g0 = grow(t, self.at(0), 0.8)
        centre = (560, 880)
        if g0 > 0:
            beam = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            bd = ImageDraw.Draw(beam)
            x1 = -40 + (centre[0] + 40) * g0
            y1 = 520 + (centre[1] - 520) * g0
            bd.polygon([(-40, 470), (-40, 570), (x1, y1 + 40), (x1, y1 - 40)], fill=GOLD + (190,))
            frame.alpha_composite(beam.filter(ImageFilter.GaussianBlur(3)))
            d = ImageDraw.Draw(frame, 'RGBA')
        rng = random.Random(self.seed)
        mols = [(centre[0] + rng.uniform(-200, 200), centre[1] + rng.uniform(-160, 160)) for _ in range(11)]
        for k, (x, y) in enumerate(mols):
            r = 20 * q1
            if r > 1:
                d.ellipse((x - r, y - r, x + r, y + r), fill=(200, 226, 230), outline=TEAL, width=5)
        if q1 > 0.9:
            u = t - (self.at(1) or t)
            for k, (x, y) in enumerate(mols):
                for j in range(5):
                    a = 2 * math.pi * (j / 5) + k * 0.7
                    ln = 40 + 60 * ((u * 1.4 + j * 0.2 + k * 0.13) % 1.0)
                    col = BLUE if (j + k) % 3 else GOLD
                    arrow(d, (x + 24 * math.cos(a), y + 24 * math.sin(a)), (x + ln * math.cos(a), y + ln * math.sin(a)), 1.0,
                          width=5, fill=col, head=12)
        text(frame, self.items[0] if self.items else '', (70, 680), anchor='lm', q=q0)
        if len(self.items) > 1:
            text(frame, self.items[1], (W / 2, 1150), q=q1)

    def _waves(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        q0, q1 = pop(t, self.at(0)), pop(t, self.at(1))
        g0, g1 = grow(t, self.at(0), 0.7), grow(t, self.at(1), 0.7)
        rows = [(720, BLUE, 56, 34), (1080, RED, 180, 40)]
        for k, ((y, col, wl, amp), q, g) in enumerate(zip(rows, (q0, q1), (g0, g1))):
            mx = 640
            if q > 0:
                d.ellipse((mx - 26, y - 26, mx + 26, y + 26), fill=(200, 226, 230), outline=TEAL, width=5)
            if g <= 0:
                continue
            phase = (t - self.t0) * 6
            if k == 0:                                 # the short wave stops at the molecule and scatters
                wave(d, 70, mx - 32, y, wl, amp, col, phase, g)
                if g >= 1:
                    u = t - (self.at(0) + 0.7)
                    for j in range(7):
                        a = -math.pi * 0.95 + j * (math.pi * 1.9 / 6)
                        ln = 60 + 70 * ((u * 1.2 + j * 0.15) % 1.0)
                        arrow(d, (mx + 32 * math.cos(a), y + 32 * math.sin(a)), (mx + ln * math.cos(a), y + ln * math.sin(a)), 1.0,
                              width=6, fill=col, head=14)
            else:                                      # the long wave passes the molecule
                end = wave(d, 70, W - 90, y, wl, amp, col, phase, g)
                if g >= 1:
                    arrow(d, (end[0] - 40, end[1]), (W - 50, end[1]), 1.0, width=9, fill=col)
        if self.items:
            text(frame, self.items[0], (70, 580), anchor='lm', fill=INK, q=q0)
        if len(self.items) > 1:
            text(frame, self.items[1], (70, 940), anchor='lm', fill=INK, q=q1)

    def _two_paths(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        mode = (self.detail or 'both').strip().lower()
        cx, cy, R, A = W / 2, 2780, 1640, 220
        eye = (cx, cy - R)
        d.ellipse((cx - R - A, cy - R - A, cx + R + A, cy + R + A), fill=(206, 226, 242), outline=(150, 190, 220), width=3)
        d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=GROUND, outline=INK, width=6)
        d.ellipse((eye[0] - 22, eye[1] - 22, eye[0] + 22, eye[1] + 22), fill=(255, 255, 255), outline=INK, width=5)
        d.ellipse((eye[0] - 8, eye[1] - 8, eye[0] + 8, eye[1] + 8), fill=INK)
        paths = []
        if mode in ('both', 'short'):
            paths.append(('short', (cx, 520)))
        if mode in ('both', 'long'):
            paths.append(('long', (70, cy - math.sqrt((R + A - 10) ** 2 - (cx - 70) ** 2) - 40)))
        for k, (kind, s) in enumerate(paths):
            q = pop(t, self.at(k) if mode == 'both' else self.at(0))
            g = grow(t, self.at(k) if mode == 'both' else self.at(0), 1.0)
            sun(d, s, 46 if kind == 'short' else 50, q, GOLD if kind == 'short' else (240, 130, 60))
            if g <= 0:
                continue
            a = (s[0] + (40 if kind == 'long' else 0), s[1] + (10 if kind == 'long' else 60))
            n = 40
            pts = [(a[0] + (eye[0] - a[0]) * i / n, a[1] + (eye[1] - 26 - a[1]) * i / n) for i in range(int(n * g) + 1)]
            for i in range(len(pts) - 1):
                f = i / n
                col = (255, 236, 170) if kind == 'short' else tuple(int(c0 + (c1 - c0) * f) for c0, c1 in zip((255, 236, 170), RED))
                d.line([pts[i], pts[i + 1]], fill=col, width=16)
                d.line([pts[i], pts[i + 1]], fill=INK, width=2)
            rng = random.Random(self.seed + k)
            inside = [p for p in pts if math.hypot(p[0] - cx, p[1] - cy) < R + A]
            step = 3 if kind == 'long' else 6
            for j, p in enumerate(inside[::step]):         # blue scattered out sideways along the path inside the air
                ang = rng.uniform(-2.6, -0.5)
                ln = 60 + 30 * rng.random()
                arrow(d, p, (p[0] + ln * math.cos(ang), p[1] + ln * math.sin(ang)), 1.0, width=7, fill=BLUE, head=14)
        labels = {'both': [(cx + 70, 660, 'lm'), (90, 800, 'lm')], 'long': [(250, 800, 'lm'), (cx + 50, 1050, 'lm')],
                  'short': [(cx + 70, 660, 'lm')]}.get(mode, [(cx + 70, 660, 'lm')])
        for k, item in enumerate(self.items[:len(labels)]):
            x, y, anc = labels[k]
            text(frame, item, (x, y), anchor=anc, q=pop(t, self.at(k)))

    def _bars(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        n = max(1, len(self.items))
        try:
            vals = [float(v) for v in self.detail.replace(',', '.').split('|')][:n]
        except ValueError:
            vals = []
        vals = vals + [1.0] * (n - len(vals))
        top = max(vals) or 1.0
        base_y, max_h = 1180, 560
        bw = 260 if n <= 2 else 210
        gap = (W - n * bw) / (n + 1)
        d.line([(80, base_y), (W - 80, base_y)], fill=INK, width=7)
        for k, (item, v) in enumerate(zip(self.items, vals)):
            g = grow(t, self.at(k), 0.7)
            x0 = gap + k * (bw + gap)
            h = max_h * v / top * g
            if g > 0:
                d.rounded_rectangle((x0, base_y - h, x0 + bw, base_y), radius=18, fill=colour_for(item, k), outline=INK, width=6)
            text(frame, item, (x0 + bw / 2, base_y - h - 46), size=LABEL if len(item) <= 12 else 46, q=pop(t, self.at(k)))

    def _thermometer(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        x, y0, y1, w = 470, 520, 1060, 100
        bulb = (x, 1130)
        d.rounded_rectangle((x - w / 2, y0, x + w / 2, y1 + 30), radius=w / 2, fill=(255, 252, 245), outline=INK, width=7)
        d.ellipse((bulb[0] - 84, bulb[1] - 84, bulb[0] + 84, bulb[1] + 84), fill=RED, outline=INK, width=7)
        falls = 'rise' not in self.detail.lower()
        a_lvl, b_lvl = (0.72, 0.42) if falls else (0.42, 0.72)
        g = grow(t, self.at(0), 1.4)
        lvl = a_lvl + (b_lvl - a_lvl) * g
        top = y1 - (y1 - y0) * lvl
        d.rectangle((x - w / 2 + 22, top, x + w / 2 - 22, y1 + 40), fill=RED)
        for k in range(1, 8):
            yy = y0 + (y1 - y0) * k / 8
            d.line([(x + w / 2, yy), (x + w / 2 + 26, yy)], fill=INK, width=4)
        mark_y = y1 - (y1 - y0) * a_lvl                 # where it was
        for xx in range(int(x - w / 2 - 40), int(x + w / 2 + 40), 22):
            d.line([(xx, mark_y), (xx + 11, mark_y)], fill=INK, width=4)
        q = pop(t, self.at(0))
        if q > 0:
            arrow(d, (x + 150, mark_y), (x + 150, top), min(1, g * 1.2), width=10, fill=BLUE if falls else RED)
            text(frame, self.items[0] if self.items else '', (x + 200, (mark_y + top) / 2), anchor='lm', q=q)
        if len(self.items) > 1:                         # a second label (where it happened) under the bulb
            q2 = pop(t, self.at(1))
            if q2 > 0:
                text(frame, self.items[1], (x, y1 + 150), q=q2)

    def _cards(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        n = len(self.items)
        cw, ch = 640, 330
        for k, item in enumerate(self.items):
            at = self.at(k)
            if at is None or t < at:
                continue
            g = ease(min(1.0, (t - at) / 0.5))
            x = (100 + k * 100 if n > 2 else 90 + k * 150) + (1 - g) * 700
            y = 500 + k * 150 if n > 2 else 540 + k * 300
            later = any(self.at(j) is not None and t >= self.at(j) for j in range(k + 1, n))
            fill = (236, 230, 216) if later else (255, 251, 242)
            d.rounded_rectangle((x + 8, y + 10, x + cw + 8, y + ch + 10), radius=24, fill=(0, 0, 0, 50))
            d.rounded_rectangle((x, y, x + cw, y + ch), radius=24, fill=fill, outline=INK, width=6)
            d.rounded_rectangle((x + 30, y - 34, x + 230, y + 10), radius=12, fill=colour_for(item, k), outline=INK, width=5)
            title, _, note = item.partition(':')
            ty = y + 78                                  # the title shows above the next card
            shown = 1.0 if g > 0.7 else 0.0               # the words ride in on the card once it has nearly arrived
            text(frame, title.strip(), (x + cw / 2, ty), q=shown)
            if note.strip():
                nx, ny = x + cw / 2, y + 168
                text(frame, note.strip(), (nx, ny), size=50, fill=RUST, q=shown)

    def _layers(self, frame, t):
        d = ImageDraw.Draw(frame, 'RGBA')
        n = max(1, len(self.items))
        h = (BOTTOM - TOP) / n
        cols = [(240, 232, 200), SKY, (150, 196, 230), GROUND]
        for k, item in enumerate(self.items):
            q = pop(t, self.at(k))
            if q <= 0:
                continue
            y = TOP + k * h
            d.rectangle((80, y, W - 80, y + h - 8), fill=cols[min(k, 3)] if n <= 4 else cols[k % 4], outline=INK, width=5)
            if k == 0:
                rng = random.Random(self.seed)
                for _ in range(40):
                    xx, yy = rng.uniform(100, W - 100), y + rng.uniform(10, h - 20)
                    d.ellipse((xx - 5, yy - 5, xx + 5, yy + 5), fill=(170, 160, 120))
            text(frame, item, (W / 2, y + h / 2 - 4), q=q)
