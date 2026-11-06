"""Prop layers for rig scenes: AI-drawn objects cut from a plain background, plus procedural
props whose insides must animate (calendar marks, a filling jar, cash, a moth, falling coins).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .parts import NAVY

INK_RED = (196, 62, 52)
PAPER = (250, 243, 226)
GOLD, GOLD_DARK, GOLD_LIGHT = (232, 178, 64), (176, 116, 34), (255, 226, 132)
GREEN, GREEN_DARK, GREEN_LIGHT = (122, 168, 104), (70, 112, 70), (186, 214, 160)


@lru_cache(maxsize=16)
def key_out(path: str, tolerance: int = 26) -> Image.Image:
    """Cut an AI-drawn object from its plain light background: flood-fill from the borders, feather the edge."""
    im = Image.open(path).convert('RGB')
    a = np.asarray(im).astype(int)
    bgc = np.median(np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]]), axis=0)
    close = (np.abs(a - bgc).max(-1) < tolerance)
    # Keep only background-coloured pixels connected to the border.
    mask = Image.fromarray((close * 255).astype(np.uint8))
    filled = mask.copy()
    ImageDraw.floodfill(filled, (0, 0), 128, thresh=0)
    for xy in ((im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)):
        ImageDraw.floodfill(filled, xy, 128, thresh=0)
    bg = np.asarray(filled) == 128
    alpha = Image.fromarray(((~bg) * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.4))
    out = im.convert('RGBA')
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    return out.crop(box) if box else out


# ---- cash ------------------------------------------------------------------------------------------
@lru_cache(maxsize=4)
def bill_sprite(width: int = 360) -> Image.Image:
    """A painted banknote with no legible text, landscape, drawn at `width` px."""
    w, h = width, int(width * 0.46)
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = int(h * 0.08)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=GREEN_DARK)
    d.rounded_rectangle((h * 0.05, h * 0.05, w - h * 0.05, h - h * 0.05), radius=r, fill=GREEN)
    d.rounded_rectangle((h * 0.14, h * 0.14, w - h * 0.14, h - h * 0.14), radius=r, outline=GREEN_LIGHT, width=max(2, h // 40))
    cx, cy, rr = w / 2, h / 2, h * 0.26
    d.ellipse((cx - rr * 1.15, cy - rr, cx + rr * 1.15, cy + rr), fill=GREEN_LIGHT, outline=GREEN_DARK, width=max(2, h // 50))
    d.ellipse((cx - rr * 0.6, cy - rr * 0.6, cx + rr * 0.6, cy + rr * 0.6), fill=GREEN)
    for x in (h * 0.36, w - h * 0.36):
        d.ellipse((x - h * 0.12, cy - h * 0.12, x + h * 0.12, cy + h * 0.12), outline=GREEN_DARK, width=max(2, h // 50))
    return im


def bill_fan(n: int, spread: float = 16.0, width: int = 360) -> tuple[Image.Image, tuple]:
    """n bills fanned from a common corner. Returns (sprite, grip point at the fan's pivot)."""
    bill = bill_sprite(width)
    size = int(width * 2.3)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pivot = (size * 0.5, size * 0.62)
    for i in range(n):
        ang = -spread * (n - 1) / 2 + spread * i - 90  # pointing up
        r = bill.rotate(-ang, Image.BICUBIC, expand=True)
        # Place the bill so its short left edge sits at the pivot, extending along `ang`.
        a = math.radians(ang)
        cx = pivot[0] + math.cos(a) * width * 0.42
        cy = pivot[1] + math.sin(a) * width * 0.42
        canvas.alpha_composite(r, (int(cx - r.width / 2), int(cy - r.height / 2)))
    box = canvas.getbbox()
    return canvas.crop(box), (pivot[0] - box[0], pivot[1] - box[1])


# ---- moth --------------------------------------------------------------------------------------------
def moth_sprite(phase: float, size: int = 120) -> Image.Image:
    """A small dusty moth; wings flap with phase (radians)."""
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size / 2
    open_ = 0.35 + 0.65 * abs(math.sin(phase))
    for k in (-1, 1):
        wx = c + k * size * 0.24 * open_
        d.ellipse((min(c, wx + k * size * 0.2), c - size * 0.22, max(c, wx + k * size * 0.2), c + size * 0.06), fill=(186, 172, 150), outline=(120, 106, 92), width=2)
        d.ellipse((min(c, wx + k * size * 0.1), c - size * 0.02, max(c, wx + k * size * 0.12), c + size * 0.16), fill=(166, 152, 132), outline=(120, 106, 92), width=2)
    d.ellipse((c - size * 0.05, c - size * 0.18, c + size * 0.05, c + size * 0.16), fill=(92, 80, 70))
    d.line([(c - 2, c - size * 0.17), (c - size * 0.12, c - size * 0.3)], fill=(92, 80, 70), width=2)
    d.line([(c + 2, c - size * 0.17), (c + size * 0.12, c - size * 0.3)], fill=(92, 80, 70), width=2)
    return im


# ---- calendar ------------------------------------------------------------------------------------------
def _wobble_line(d, a, b, width, fill, seed, wob=0.012):
    """A slightly uneven hand-drawn stroke."""
    rng = random.Random(seed)
    n = 8
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    nx, ny = -(b[1] - a[1]) / max(length, 1), (b[0] - a[0]) / max(length, 1)
    pts = []
    for i in range(n + 1):
        t = i / n
        off = (rng.uniform(-1, 1) * wob * length) * math.sin(math.pi * t)
        pts.append((a[0] + (b[0] - a[0]) * t + nx * off, a[1] + (b[1] - a[1]) * t + ny * off))
    d.line(pts, fill=fill, width=width, joint='curve')


@dataclass
class Calendar:
    """A paper wall calendar: a month grid, one circled payday, and X marks drawn on one by one."""
    width: int = 420
    payday: tuple = (1, 2)       # (row, col) of the circled day
    crossed: int = 7             # days after payday that can be crossed

    def cells(self):
        r0, c0 = self.payday
        out = []
        r, c = r0, c0
        for _ in range(self.crossed):
            c += 1
            if c > 6:
                r, c = r + 1, 0
            out.append((r, c))
        return out

    def render(self, marks: float, circle: float = 1.0) -> Image.Image:
        """marks: how many X marks are drawn (fractional = the current one is mid-stroke)."""
        w = self.width
        h = int(w * 1.18)
        im = Image.new('RGBA', (w, h + int(w * 0.06)), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        sh = int(w * 0.025)
        d.rounded_rectangle((sh, sh * 2, w - 1, h - 1 + sh), radius=int(w * 0.03), fill=(60, 40, 30, 70))
        d.rounded_rectangle((0, 0, w - sh, h), radius=int(w * 0.03), fill=PAPER, outline=(170, 150, 120), width=max(2, w // 140))
        head = int(h * 0.20)
        d.rounded_rectangle((0, 0, w - sh, head), radius=int(w * 0.03), fill=INK_RED)
        d.rectangle((0, head * 0.5, w - sh, head), fill=INK_RED)
        for x in (w * 0.28, w * 0.68):  # rings
            d.ellipse((x - w * 0.025, -w * 0.02, x + w * 0.025, w * 0.05), fill=(90, 90, 96))
        # Month title as a soft painted bar (no legible text).
        d.rounded_rectangle((w * 0.30, head * 0.42, w * 0.66, head * 0.66), radius=int(w * 0.02), fill=(240, 205, 190))
        gx0, gy0, gx1, gy1 = w * 0.06, head + h * 0.05, w - sh - w * 0.06, h - h * 0.05
        cw, ch = (gx1 - gx0) / 7, (gy1 - gy0) / 5
        line = max(1, w // 210)
        for i in range(8):
            d.line([(gx0 + i * cw, gy0), (gx0 + i * cw, gy1)], fill=(205, 192, 170), width=line)
        for j in range(6):
            d.line([(gx0, gy0 + j * ch), (gx1, gy0 + j * ch)], fill=(205, 192, 170), width=line)
        for j in range(5):
            for i in range(7):  # little day dots stand in for numbers
                d.ellipse((gx0 + i * cw + cw * 0.12, gy0 + j * ch + ch * 0.12, gx0 + i * cw + cw * 0.24, gy0 + j * ch + ch * 0.26), fill=(185, 170, 150))
        pr, pc = self.payday
        cx, cy = gx0 + (pc + 0.5) * cw, gy0 + (pr + 0.5) * ch
        if circle > 0:
            d.arc((cx - cw * 0.52, cy - ch * 0.55, cx + cw * 0.52, cy + ch * 0.55), -90, -90 + 370 * min(1, circle), fill=(52, 140, 92), width=max(3, w // 70))
            d.ellipse((cx - cw * 0.16, cy - ch * 0.16, cx + cw * 0.16, cy + ch * 0.16), fill=(52, 140, 92))
        for k, (r, c) in enumerate(self.cells()):
            p = marks - k
            if p <= 0:
                break
            x, y = gx0 + (c + 0.5) * cw, gy0 + (r + 0.5) * ch
            s = min(cw, ch) * 0.36
            width = max(3, w // 75)
            first = min(1, p * 2)
            _wobble_line(d, (x - s, y - s), (x - s + 2 * s * first, y - s + 2 * s * first), width, INK_RED, k * 2)
            if p > 0.5:
                second = min(1, (p - 0.5) * 2)
                _wobble_line(d, (x + s, y - s), (x + s - 2 * s * second, y - s + 2 * s * second), width, INK_RED, k * 2 + 1)
        return im


# ---- jar and coins -----------------------------------------------------------------------------------
@lru_cache(maxsize=8)
def coin_sprite(path: str | None, size: int = 96) -> Image.Image:
    """The AI coin when available, otherwise a painted procedural coin."""
    if path and Path(path).exists():
        im = key_out(path)
        return im.resize((size, max(1, round(size * im.height / im.width))), Image.LANCZOS)
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=GOLD_DARK)
    d.ellipse((size * .06, size * .06, size * .94, size * .94), fill=GOLD)
    d.ellipse((size * .22, size * .22, size * .78, size * .78), outline=GOLD_LIGHT, width=max(2, size // 20))
    return im


@dataclass
class Jar:
    """A glass jar drawn in the cel style; coins inside rise with `level` (0..1)."""
    width: int = 300

    def render(self, level: float, coin: Image.Image, seed: int = 3) -> tuple[Image.Image, Image.Image]:
        """Returns (back, front): back = rear glass + coin pile, front = highlights and rim, drawn over coins landing inside."""
        w = self.width
        h = int(w * 1.25)
        back = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        front = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(back)
        body = (w * 0.06, h * 0.16, w * 0.94, h * 0.98)
        d.rounded_rectangle(body, radius=int(w * 0.16), fill=(232, 240, 236, 70))
        # Coin pile clipped to the jar's inner shape.
        inner = Image.new('L', (w, h), 0)
        ImageDraw.Draw(inner).rounded_rectangle((w * 0.10, h * 0.20, w * 0.90, h * 0.955), radius=int(w * 0.13), fill=255)
        pile = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        top = h * 0.95 - (h * 0.95 - h * 0.24) * max(0.0, min(1.0, level))
        rng = random.Random(seed)
        cs = int(w * 0.24)
        c = coin.resize((cs, max(1, int(cs * 0.55))), Image.LANCZOS)  # coins lie flat: squashed ellipses
        y = h * 0.95
        while y > top - cs * 0.2:
            for i in range(6):
                x = w * 0.08 + (w * 0.84) * (i + rng.uniform(-0.2, 0.2)) / 5.2
                pile.alpha_composite(c, (int(x), int(y - c.height + rng.uniform(-4, 4))))
            y -= c.height * 0.42
        pile.putalpha(Image.fromarray(np.minimum(np.asarray(pile.getchannel('A')), np.asarray(inner))))
        back.alpha_composite(pile)
        fd = ImageDraw.Draw(front)
        lw = max(3, w // 45)
        fd.rounded_rectangle(body, radius=int(w * 0.16), outline=NAVY + (255,), width=lw)
        fd.rounded_rectangle((w * 0.04, h * 0.10, w * 0.96, h * 0.18), radius=int(w * 0.04), fill=(222, 232, 228, 150), outline=NAVY + (255,), width=lw)
        fd.line([(w * 0.20, h * 0.30), (w * 0.20, h * 0.80)], fill=(255, 255, 255, 170), width=int(w * 0.05))
        fd.line([(w * 0.30, h * 0.30), (w * 0.30, h * 0.45)], fill=(255, 255, 255, 120), width=int(w * 0.025))
        return back, front

    @staticmethod
    def surface_y(level: float) -> float:
        """Height of the coin surface, as a fraction of the jar sprite height from the top."""
        return 0.95 - 0.71 * max(0.0, min(1.0, level))


@dataclass
class Falling:
    """One coin under gravity from (x, y0) starting at t0, landing at y1 (world px)."""
    t0: float
    x: float
    y0: float
    y1: float
    spin: float
    g: float = 2600.0

    def landed_at(self) -> float:
        return self.t0 + math.sqrt(2 * (self.y1 - self.y0) / self.g)

    def at(self, t: float):
        u = t - self.t0
        if u < 0 or t > self.landed_at():
            return None
        return self.x, self.y0 + 0.5 * self.g * u * u, self.spin * u


# ---- painted (AI) props made animatable -------------------------------------------------------------
def fan_of(bill: Image.Image, n: int, spread: float = 13.0) -> tuple[Image.Image, tuple]:
    """n copies of a painted banknote fanned from their bottom edge. Returns (sprite, pivot) - the pivot is
    where the hand grips the stack. Every bill keeps the size of `bill`."""
    upright = bill.rotate(90, Image.BICUBIC, expand=True)           # long side vertical
    w, h = upright.size
    size = int(h * 2.4)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pivot = (size / 2, size * 0.78)
    for i in range(n):
        ang = -spread * (n - 1) / 2 + spread * i                     # clockwise degrees from vertical
        tint = 1.0 - 0.05 * (n - 1 - i)
        b = upright.copy()
        arr = np.asarray(b).astype(np.float32)
        arr[..., :3] *= tint
        b = Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')
        r = b.rotate(-ang, Image.BICUBIC, expand=True)
        # bill's bottom-centre sits at the pivot (a little above it, inside the fist)
        off = (0, -h / 2 + h * 0.10)
        a = math.radians(ang)
        cx = pivot[0] + (off[0] * math.cos(a) - off[1] * math.sin(a))
        cy = pivot[1] + (off[0] * math.sin(a) + off[1] * math.cos(a))
        canvas.alpha_composite(r, (int(cx - r.width / 2), int(cy - r.height / 2)))
    box = canvas.getbbox()
    return canvas.crop(box), (pivot[0] - box[0], pivot[1] - box[1])


def _interior(alpha: np.ndarray) -> np.ndarray:
    """Pixels enclosed by an object's silhouette (holes filled)."""
    solid = Image.fromarray(((alpha > 20) * 255).astype(np.uint8))
    filled = solid.copy()
    ImageDraw.floodfill(filled, (0, 0), 128, thresh=0)
    return np.asarray(filled) != 128


def glass(jar: Image.Image) -> Image.Image:
    """Make a painted jar's body see-through: pale, unsaturated glass areas become mostly transparent,
    highlights stay; the painted rim and outline stay opaque."""
    arr = np.asarray(jar).astype(np.float32)
    rgb = arr[..., :3]
    lum, sat = rgb.mean(-1), rgb.max(-1) - rgb.min(-1)
    pale = (lum > 200) & (sat < 40)
    highlight = lum > 246
    a = arr[..., 3]
    a = np.where(pale, a * np.where(highlight, 0.55, 0.16), a)
    arr[..., 3] = a
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')


def coin_pile(coin: Image.Image, jar: Image.Image, level: float, seed: int = 3) -> Image.Image:
    """Coins lying flat inside the jar up to `level` (0..1), clipped to the jar's inside. Same size as the jar sprite."""
    w, h = jar.size
    inside = _interior(np.asarray(jar.getchannel('A')))
    ys, xs = np.nonzero(inside)
    if len(ys) == 0 or level <= 0:
        return Image.new('RGBA', (w, h), (0, 0, 0, 0))
    top, bottom = ys.min() + 0.10 * (ys.max() - ys.min()), ys.max() - 0.02 * h
    left, right = xs.min(), xs.max()
    mask = Image.fromarray((inside * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(int(0.09 * w) | 1))
    pile = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    cs = int(0.27 * (right - left))
    c = coin.resize((cs, max(1, int(cs * 0.5))), Image.LANCZOS)
    rng = random.Random(seed)
    surface = bottom - (bottom - top) * max(0.0, min(1.0, level))
    y = bottom
    while y > surface:
        for i in range(5):
            x = left + (right - left) * (i + rng.uniform(-0.25, 0.25)) / 4.4
            pile.alpha_composite(c, (int(x), int(y - c.height + rng.uniform(-3, 3))))
        y -= c.height * 0.45
    pile.putalpha(Image.fromarray(np.minimum(np.asarray(pile.getchannel('A')), np.asarray(mask))))
    return pile


class PaintedCalendar:
    """An AI-painted wall calendar with hand-drawn ink marks: a circled payday and X marks drawn on one by one.
    The grid is measured from the painting (header band, then the day boxes); if the painted lines are too
    irregular to measure, the grid area is divided evenly."""

    def __init__(self, sprite: Image.Image, payday=(1, 2), crossed=7):
        self.sprite = sprite
        self.payday, self.crossed = payday, crossed
        a = np.asarray(sprite).astype(np.float32)
        rgb, alpha = a[..., :3], a[..., 3]
        red = (rgb[..., 0] > rgb[..., 1] + 45) & (rgb[..., 0] > rgb[..., 2] + 45) & (alpha > 128)
        rows = red.mean(1)
        head_rows = np.nonzero(rows > 0.25)[0]
        ys, xs = np.nonzero(alpha > 128)
        x0, x1, y1 = xs.min(), xs.max(), ys.max()
        head_bottom = head_rows.max() if len(head_rows) else ys.min() + 0.22 * (y1 - ys.min())
        self.grid = (x0 + 0.07 * (x1 - x0), head_bottom + 0.06 * (y1 - head_bottom), x1 - 0.07 * (x1 - x0), y1 - 0.05 * (y1 - head_bottom))
        self.rows, self.cols = 5, 7

    def cell(self, r, c):
        gx0, gy0, gx1, gy1 = self.grid
        cw, ch = (gx1 - gx0) / self.cols, (gy1 - gy0) / self.rows
        return gx0 + (c + 0.5) * cw, gy0 + (r + 0.5) * ch, min(cw, ch)

    def cells(self):
        r, c = self.payday
        out = []
        for _ in range(self.crossed):
            c += 1
            if c >= self.cols:
                r, c = r + 1, 0
            out.append((r, c))
        return out

    def render(self, marks: float, circle: float = 1.0) -> Image.Image:
        im = self.sprite.copy()
        ink = Image.new('RGBA', im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ink)
        width = max(3, im.width // 60)
        x, y, s = self.cell(*self.payday)
        if circle > 0:
            d.arc((x - s * 0.55, y - s * 0.55, x + s * 0.55, y + s * 0.55), -90, -90 + 370 * min(1, circle), fill=(40, 120, 80, 235), width=width)
        for k, (r, c) in enumerate(self.cells()):
            p = marks - k
            if p <= 0:
                break
            x, y, s = self.cell(r, c)
            s *= 0.34
            first = min(1, p * 2)
            _wobble_line(d, (x - s, y - s), (x - s + 2 * s * first, y - s + 2 * s * first), width, (190, 52, 44, 235), k * 2)
            if p > 0.5:
                second = min(1, (p - 0.5) * 2)
                _wobble_line(d, (x + s, y - s), (x + s - 2 * s * second, y - s + 2 * s * second), width, (190, 52, 44, 235), k * 2 + 1)
        im.alpha_composite(ink.filter(ImageFilter.GaussianBlur(0.6)))
        return im


@lru_cache(maxsize=16)
def key_neutral(path: str, sat_max: int = 34, lum_min: int = 95, holes: bool = True) -> Image.Image:
    """Cut a painted object from a grey/white backdrop that has a gradient or a painted floor shadow: removes
    low-saturation light pixels connected to the border (the object's own colours are warmer)."""
    im = Image.open(path).convert('RGB')
    a = np.asarray(im).astype(int)
    sat = a.max(-1) - a.min(-1)
    lum = a.mean(-1)
    bgish = ((sat < sat_max) & (lum > lum_min)).astype(np.uint8) * 255
    m = Image.fromarray(bgish).copy()  # writable (fromarray can share a read-only buffer)
    for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1),
               (im.width // 2, im.height - 1), (0, im.height // 2), (im.width - 1, im.height // 2)):
        if m.getpixel(xy) == 255:
            ImageDraw.floodfill(m, xy, 128, thresh=0)
    # Enclosed patches of backdrop (between chair spindles, under a handle) are backdrop too, if large enough.
    arr = np.asarray(m)
    for y in (range(0, im.height, 12) if holes else ()):
        for x in range(0, im.width, 12):
            if arr[y, x] == 255:
                before = np.asarray(m).copy()
                ImageDraw.floodfill(m, (x, y), 200, thresh=0)
                now = np.asarray(m)
                grown = (now == 200) & (before == 255)
                if grown.sum() > 0.0012 * im.width * im.height:
                    m.paste(128, mask=Image.fromarray((grown * 255).astype(np.uint8)))
                else:
                    m.paste(0, mask=Image.fromarray((grown * 255).astype(np.uint8)))
                arr = np.asarray(m)
    bg = np.asarray(m) == 128
    alpha = Image.fromarray(((~bg) * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    out = im.convert('RGBA')
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 30 else 0).getbbox()
    return out.crop(box) if box else out


def empty_out(sprite: Image.Image, min_blob: int = 11) -> Image.Image:
    """Paint out cards, notes and paper inside a painted wallet: large blue/grey/pale blobs (not the thin pale
    stitching) are refilled from the surrounding leather and darkened like the inside of an empty pocket."""
    arr = np.asarray(sprite).astype(np.float32)
    rgb, a = arr[..., :3], arr[..., 3]
    lum, sat = rgb.mean(-1), rgb.max(-1) - rgb.min(-1)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    paper = (lum > 165) & (r - b < 70) & (r > b)                     # cream pages and white cards
    foreign = ((b > r + 8) | paper | ((sat < 22) & (lum > 70))) & (a > 120)
    m = Image.fromarray((foreign * 255).astype(np.uint8))
    m = m.filter(ImageFilter.MinFilter(min_blob)).filter(ImageFilter.MaxFilter(min_blob + 8))   # drop thin lines, keep blobs
    hole = (np.asarray(m) > 0) & (a > 120)
    valid = (a > 200) & ~hole & ~foreign
    out = rgb.copy()
    w = Image.fromarray((valid * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(18))
    wv = np.asarray(w).astype(np.float32) / 255
    for c in range(3):
        ch = Image.fromarray((rgb[..., c] * valid).astype(np.uint8)).filter(ImageFilter.GaussianBlur(18))
        fill = np.asarray(ch).astype(np.float32) / np.maximum(wv, 1e-3)
        out[..., c] = np.where(hole, fill * 0.62, rgb[..., c])
    res = np.concatenate([np.clip(out, 0, 255), arr[..., 3:]], axis=-1).astype(np.uint8)
    return Image.fromarray(res, 'RGBA')


def stand_in_jar(width: int = 300) -> Image.Image:
    """A plain glass jar used only until the painted jar exists."""
    back, front = Jar(width).render(0.0, coin_sprite(None, 60))
    back.alpha_composite(front)
    return back


@lru_cache(maxsize=8)
def key_colours(path: str, dark_max: int = 115, sat_min: int = 70) -> Image.Image:
    """Keep only an object's own colours (dark iron, saturated wood); drops a pale painted floor shadow."""
    im = Image.open(path).convert('RGB')
    a = np.asarray(im).astype(int)
    keep = (a.mean(-1) < dark_max) | ((a.max(-1) - a.min(-1)) > sat_min)
    m = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    m = m.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.0))
    out = im.convert('RGBA')
    out.putalpha(m)
    box = m.point(lambda v: 255 if v > 30 else 0).getbbox()
    return out.crop(box) if box else out


def painterly(im: Image.Image, seed: int = 1, strength: float = 1.0) -> Image.Image:
    """Give a code-drawn prop a painted finish: soft uneven tint, paper grain, softened edges, light from the top."""
    rng = np.random.default_rng(seed)
    w, h = im.size
    low = Image.fromarray((rng.random((max(2, h // 40), max(2, w // 40))) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    grain = rng.normal(0, 1, (h, w))
    arr = np.asarray(im).astype(np.float32)
    tint = 1 + strength * (0.07 * (np.asarray(low, np.float32) / 255 - 0.5) + 0.025 * grain)
    light = np.linspace(1.06, 0.92, h)[:, None]
    arr[..., :3] = np.clip(arr[..., :3] * (tint * light)[..., None], 0, 255)
    out = Image.fromarray(arr.astype(np.uint8), 'RGBA').filter(ImageFilter.GaussianBlur(0.9))
    out.putalpha(im.getchannel('A').filter(ImageFilter.GaussianBlur(0.7)))
    return out


def keep_centre(sprite: Image.Image, cut: int = 5) -> Image.Image:
    """Keep only the object connected to the sprite's centre (drops decorative corners, frames, stray marks).
    Thin links (a frame line touching a wing tip) are cut first with a small erosion."""
    a = sprite.getchannel('A').point(lambda v: 255 if v > 100 else 0)
    thin = a.filter(ImageFilter.MinFilter(cut))
    seed = (sprite.width // 2, sprite.height // 2)
    m = thin.copy()
    ImageDraw.floodfill(m, seed, 128, thresh=0)
    core = Image.fromarray(((np.asarray(m) == 128) * 255).astype(np.uint8))
    grown = core.filter(ImageFilter.MaxFilter(cut + 4))
    alpha = Image.fromarray(np.minimum(np.asarray(grown), np.asarray(sprite.getchannel('A'))))
    out = sprite.copy()
    out.putalpha(alpha)
    return out.crop(alpha.getbbox())


class WallCalendar:
    """A story-sized wall calendar that reads on a phone: three week rows with day numbers, payday (a Friday)
    circled in green, and the seven days after it crossed off with bold red X marks, one by one.
    The paper is drawn once with a painted finish; only the ink is drawn per frame."""

    FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'

    def __init__(self, width: int = 420, payday_col: int = 4, crossed: int = 7, seed: int = 9):
        from PIL import ImageFont
        self.w = w = width
        self.h = h = int(width * 0.88)
        self.payday = (0, payday_col)
        self.crossed = crossed
        im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r = int(w * 0.035)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=PAPER, outline=(150, 120, 92), width=max(2, w // 120))
        head = int(h * 0.19)
        d.rounded_rectangle((0, 0, w - 1, head), radius=r, fill=INK_RED)
        d.rectangle((0, head // 2, w - 1, head), fill=INK_RED)
        for x in (w * 0.26, w * 0.74):                     # binding rings
            d.ellipse((x - w * 0.03, -w * 0.035, x + w * 0.03, w * 0.045), fill=(84, 82, 90))
            d.ellipse((x - w * 0.015, -w * 0.02, x + w * 0.015, w * 0.03), fill=(150, 150, 158))
        d.rounded_rectangle((w * 0.30, head * 0.40, w * 0.70, head * 0.70), radius=int(w * 0.02), fill=(244, 200, 184))
        self.grid = gx0, gy0, gx1, gy1 = w * 0.05, head + h * 0.11, w * 0.95, h * 0.95
        cw, ch = (gx1 - gx0) / 7, (gy1 - gy0) / 3
        self.cw, self.ch = cw, ch
        small = ImageFont.truetype(self.FONT, max(8, int(cw * 0.42)))
        for i, letter in enumerate('MTWTFSS'):           # weekday initials
            x = gx0 + (i + 0.5) * cw
            d.text((x, head + h * 0.055), letter, font=small, fill=(120, 96, 80), anchor='mm')
        line = max(1, w // 160)
        for i in range(8):
            d.line([(gx0 + i * cw, gy0), (gx0 + i * cw, gy1)], fill=(206, 190, 166), width=line)
        for j in range(4):
            d.line([(gx0, gy0 + j * ch), (gx1, gy0 + j * ch)], fill=(206, 190, 166), width=line)
        num = ImageFont.truetype(self.FONT, max(8, int(cw * 0.36)))
        for j in range(3):
            for i in range(7):
                d.text((gx0 + i * cw + cw * 0.10, gy0 + j * ch + ch * 0.08), str(1 + j * 7 + i), font=num, fill=(110, 92, 80))
        self.paper = painterly(im, seed, 0.8)

    def cell(self, r, c):
        gx0, gy0, _, _ = self.grid
        return gx0 + (c + 0.5) * self.cw, gy0 + (r + 0.55) * self.ch

    def cells(self):
        r, c = self.payday
        out = []
        for _ in range(self.crossed):
            c += 1
            if c > 6:
                r, c = r + 1, 0
            out.append((r, c))
        return out

    def render(self, marks: float, circle: float = 1.0) -> Image.Image:
        im = self.paper.copy()
        ink = Image.new('RGBA', im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ink)
        s = min(self.cw, self.ch)
        x, y = self.cell(*self.payday)
        if circle > 0:
            rr = self.cw * 0.50
            d.arc((x - rr, y - rr, x + rr, y + rr), -90, -90 + 370 * min(1, circle),
                  fill=(36, 128, 78, 245), width=max(3, int(s * 0.13)))
        width = max(3, int(s * 0.16))
        for k, (r, c) in enumerate(self.cells()):
            p = marks - k
            if p <= 0:
                break
            x, y = self.cell(r, c)
            a = s * 0.36
            first = min(1, p * 2)
            _wobble_line(d, (x - a, y - a), (x - a + 2 * a * first, y - a + 2 * a * first), width, (196, 44, 38, 245), k * 2)
            if p > 0.5:
                second = min(1, (p - 0.5) * 2)
                _wobble_line(d, (x + a, y - a), (x + a - 2 * a * second, y - a + 2 * a * second), width, (196, 44, 38, 245), k * 2 + 1)
        im.alpha_composite(ink.filter(ImageFilter.GaussianBlur(0.7)))
        return im
