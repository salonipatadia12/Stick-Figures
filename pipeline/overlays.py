"""On-screen graphics for the motion recipes: text slams, the checklist card, hand-drawn arrows and
circles, a speech bubble, paper tags, plus a few code-drawn props (receipt, phone, takeout box) with the
painted finish. Everything is drawn in screen space unless it says otherwise.

Text stays in the upper-middle, out of the bottom quarter and the right edge (pipeline/captions.SAFE).
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .rig.actions import back_out, ease, ease_out
from .rig.props import painterly

FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'
MONO = '/System/Library/Fonts/Menlo.ttc'
HAND = '/System/Library/Fonts/Supplemental/Chalkboard.ttc'
NAVY = (22, 22, 46)
INK = (196, 44, 38)
CREAM = (252, 246, 232)
GOLD = (255, 222, 112)
TEXT_C = (318, 350)                       # upper-middle, clear of the calendar and the right edge


@lru_cache(maxsize=32)
def font(path: str, size: int):
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=64)
def text_layer(text: str, size: int, fill=(255, 255, 255), stroke=NAVY, path: str = FONT) -> Image.Image:
    f = font(path, size)
    sw = max(3, size // 9)
    l, t, r, b = f.getbbox(text, stroke_width=sw)
    im = Image.new('RGBA', (r - l + 24, b - t + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((12 - l + 4, 12 - t + 6), text, font=f, fill=NAVY + (110,), stroke_width=sw, stroke_fill=NAVY + (110,))
    d.text((12 - l, 12 - t), text, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke)
    return im


@lru_cache(maxsize=16)
def stacked(lines: tuple, sizes: tuple, fill=(255, 255, 255)) -> Image.Image:
    parts = [text_layer(t, s, fill) for t, s in zip(lines, sizes)]
    im = Image.new('RGBA', (max(p.width for p in parts), sum(p.height for p in parts) - 18 * (len(parts) - 1)), (0, 0, 0, 0))
    y = 0
    for p in parts:
        im.alpha_composite(p, ((im.width - p.width) // 2, y))
        y += p.height - 18
    return im


def slam(frame: Image.Image, im: Image.Image, t: float, t0: float, centre=TEXT_C, until: float | None = None):
    """Text that slams in at t0 (overshoot and a little shake), optionally fading out at `until`."""
    if t < t0 or (until is not None and t > until + 0.25):
        return
    u = t - t0
    s = 1.0 + 0.45 * (1 - back_out(u / 0.2, 2.2))
    shake = 10 * math.sin(u * 70) * math.exp(-u * 12)
    sized = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BICUBIC)
    if until is not None and t > until:
        a = 1 - ease((t - until) / 0.25)
        sized.putalpha(sized.getchannel('A').point(lambda v: int(v * a)))
    frame.alpha_composite(sized, (int(centre[0] - sized.width / 2 + shake), int(centre[1] - sized.height / 2)))


def checklist(frame: Image.Image, t: float, rows: list, t_in: float, centre=TEXT_C, width: int = 470):
    """A paper card with rows that tick on their words: rows = [(label, price or '', tick time)]."""
    if t < t_in:
        return
    q = back_out((t - t_in) / 0.3, 1.4)
    row_h, pad = 74, 26
    h = pad * 2 + row_h * len(rows)
    card = Image.new('RGBA', (width + 20, h + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((14, 16, width + 14, h + 16), radius=26, fill=(40, 26, 20, 90))
    d.rounded_rectangle((6, 6, width + 6, h + 6), radius=26, fill=CREAM + (255,), outline=(150, 120, 92), width=3)
    lab, num = font(FONT, 40), font(FONT, 36)
    for i, (label, price, tt) in enumerate(rows):
        y = pad + 6 + i * row_h
        box = (pad + 6, y + 10, pad + 54, y + 58)
        d.rounded_rectangle(box, radius=9, outline=NAVY, width=4)
        done = (t - tt) / 0.22 if tt is not None else -1
        if done > 0:
            p = min(1, done)
            a = (box[0] + 9, box[1] + 26)
            b = (box[0] + 22, box[1] + 40)
            c = (box[0] + 46, box[1] + 6)
            pts = [a, (a[0] + (b[0] - a[0]) * min(1, p * 2), a[1] + (b[1] - a[1]) * min(1, p * 2))]
            if p > 0.5:
                pts.append((b[0] + (c[0] - b[0]) * (p - 0.5) * 2, b[1] + (c[1] - b[1]) * (p - 0.5) * 2))
            d.line(pts, fill=(36, 140, 82), width=9, joint='curve')
        d.text((pad + 74, y + 12), label, font=lab, fill=NAVY)
        if price:
            d.text((width - pad + 6, y + 14), price, font=num, fill=INK if price.startswith('-') else (36, 120, 70), anchor='ra')
    card = painterly(card, 4, 0.5)
    s = 0.6 + 0.4 * min(1.0, q)
    sized = card.resize((max(1, int(card.width * s)), max(1, int(card.height * s))), Image.BICUBIC)
    frame.alpha_composite(sized, (int(centre[0] - sized.width / 2), int(centre[1] - sized.height / 2)))


def _ink(d: ImageDraw.ImageDraw, pts: list, width: int, fill=INK):
    if len(pts) > 1:
        d.line(pts, fill=fill, width=width, joint='curve')


def arrow(frame: Image.Image, t: float, t0: float, a, b, bend: float = 0.25, dur: float = 0.35, width: int = 12):
    """A hand-drawn curved arrow from a to b (screen px), drawn on from t0."""
    if t < t0:
        return
    p = ease_out((t - t0) / dur)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    nx, ny = -(b[1] - a[1]), b[0] - a[0]
    c = (mx + nx * bend, my + ny * bend)
    n = 40
    pts = []
    for i in range(int(n * p) + 1):
        u = i / n
        pts.append(((1 - u) ** 2 * a[0] + 2 * (1 - u) * u * c[0] + u * u * b[0], (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * c[1] + u * u * b[1]))
    layer = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    _ink(d, [(x + 3, y + 4) for x, y in pts], width, (40, 20, 20, 90))
    _ink(d, pts, width)
    if p >= 1:
        ex, ey = pts[-1]
        px, py = pts[-4]
        ang = math.atan2(ey - py, ex - px)
        for side in (-1, 1):
            hx = ex - 42 * math.cos(ang + side * 0.5)
            hy = ey - 42 * math.sin(ang + side * 0.5)
            _ink(d, [(ex, ey), (hx, hy)], width)
    frame.alpha_composite(layer)


def circle(frame: Image.Image, t: float, t0: float, centre, rx: float, ry: float, dur: float = 0.45, width: int = 10):
    """A hand-drawn ink loop around something, drawn on from t0 (a little over a full turn)."""
    if t < t0:
        return
    p = ease_out((t - t0) / dur)
    pts = []
    for i in range(int(64 * p * 1.08) + 1):
        a = -2.2 + 2 * math.pi * i / 64
        wob = 1 + 0.04 * math.sin(i * 0.7)
        pts.append((centre[0] + rx * wob * math.cos(a), centre[1] + ry * wob * math.sin(a)))
    layer = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    _ink(ImageDraw.Draw(layer), pts, width)
    frame.alpha_composite(layer)


def bubble(frame: Image.Image, t: float, t0: float, text: str, tail, offset=(60, -150), until: float | None = None):
    """A small speech bubble with a tail pointing at `tail` (screen px)."""
    if t < t0 or (until is not None and t > until + 0.2):
        return
    q = back_out((t - t0) / 0.25, 1.8)
    f = font(HAND, 46)
    l, tt, r, b = f.getbbox(text)
    w, h = r - l + 60, b - tt + 44
    im = Image.new('RGBA', (w + 10, h + 70), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((5, 5, w + 5, h + 5), radius=h // 2, fill=CREAM + (255,), outline=NAVY, width=4)
    d.polygon([(w * 0.25, h - 2), (w * 0.12, h + 60), (w * 0.42, h - 2)], fill=CREAM + (255,))
    d.line([(w * 0.25, h + 1), (w * 0.12, h + 60), (w * 0.42, h + 1)], fill=NAVY, width=4)
    d.text((35 - l, 26 - tt), text, font=f, fill=NAVY)
    s = max(0.05, q)
    sized = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BICUBIC)
    if until is not None and t > until:
        a = 1 - ease((t - until) / 0.2)
        sized.putalpha(sized.getchannel('A').point(lambda v: int(v * a)))
    x = tail[0] + offset[0] - sized.width * 0.12
    y = tail[1] + offset[1] - sized.height + 60 * s
    frame.alpha_composite(sized, (int(x), int(y)))


@lru_cache(maxsize=16)
def tag(text: str, width: int = 150, seed: int = 1) -> Image.Image:
    """A small paper label (painted finish), for jars."""
    f = font(FONT, int(width * 0.2))
    l, t, r, b = f.getbbox(text)
    h = int(width * 0.42)
    im = Image.new('RGBA', (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, width - 1, h - 1), radius=h // 4, fill=CREAM + (255,), outline=(150, 120, 92), width=3)
    d.text((width / 2, h / 2), text, font=f, fill=NAVY, anchor='mm')
    return painterly(im, seed, 0.5)


@lru_cache(maxsize=4)
def receipt_paper(lines: tuple, width: int = 260, line_h: int = 46, seed: int = 2) -> Image.Image:
    """A long thermal receipt (world px), one entry per line: [(label, amount)], top to bottom."""
    h = 40 + line_h * len(lines) + 30
    im = Image.new('RGBA', (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, width - 1, h - 1), fill=(250, 247, 238, 255))
    for x in range(0, width, 14):                     # torn top edge
        d.polygon([(x, 0), (x + 7, 7), (x + 14, 0)], fill=(0, 0, 0, 0))
    f = font(MONO, int(line_h * 0.52))
    y = 30
    for label, amount in lines:
        col = INK if label.upper().startswith('NEXT PAYDAY') or label.upper() == 'PAYDAY' else (60, 56, 64)
        d.text((16, y), label, font=f, fill=col)
        if amount:
            d.text((width - 16, y), amount, font=f, fill=col, anchor='ra')
        y += line_h
    for yy in range(36, h, line_h):                   # faint perforation dots
        d.line([(10, yy - 8), (width - 10, yy - 8)], fill=(225, 220, 208), width=1)
    return painterly(im, seed, 0.6)


@lru_cache(maxsize=2)
def phone(width: int = 300) -> Image.Image:
    h = int(width * 1.95)
    im = Image.new('RGBA', (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, width - 1, h - 1), radius=width // 6, fill=(34, 36, 52, 255), outline=NAVY, width=6)
    m = width // 14
    screen = Image.new('RGBA', (width - 2 * m, h - 2 * m), (0, 0, 0, 0))
    g = np.linspace(0, 1, screen.height)[:, None]
    top, bottom = np.array([120, 210, 196]), np.array([70, 140, 170])
    col = (top * (1 - g) + bottom * g)[:, None, :].repeat(screen.width, 1)
    sa = np.dstack([col, np.full(col.shape[:2], 255)]).astype(np.uint8)
    screen = Image.fromarray(sa, 'RGBA')
    mask = Image.new('L', screen.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, screen.width - 1, screen.height - 1), radius=width // 9, fill=255)
    im.paste(screen, (m, m), mask)
    d.rounded_rectangle((width * 0.36, m + 10, width * 0.64, m + 24), radius=7, fill=(34, 36, 52, 255))
    for i in range(3):                                # three app rows, no text
        y = h * (0.30 + 0.16 * i)
        d.rounded_rectangle((width * 0.16, y, width * 0.84, y + h * 0.10), radius=12, fill=(255, 255, 255, 150))
    return painterly(im, 6, 0.5)


@lru_cache(maxsize=2)
def takeout_box(width: int = 300) -> Image.Image:
    """A white paper takeout box with folded flaps and a wire handle, in the cel style."""
    h = int(width * 1.05)
    im = Image.new('RGBA', (width, h + width // 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    top = width // 3
    body = [(width * 0.10, top + h * 0.22), (width * 0.90, top + h * 0.22), (width * 0.80, top + h - 4), (width * 0.20, top + h - 4)]
    d.arc((width * 0.22, 4, width * 0.78, top * 2.4), 190, 350, fill=(120, 120, 130, 255), width=max(3, width // 60))
    d.polygon([(width * 0.12, top + h * 0.22), (width * 0.30, top - 4), (width * 0.70, top - 4), (width * 0.88, top + h * 0.22)],
              fill=(236, 232, 224, 255), outline=NAVY, width=max(3, width // 75))
    d.line([(width * 0.50, top - 4), (width * 0.50, top + h * 0.22)], fill=(200, 194, 186, 255), width=max(2, width // 100))
    d.polygon(body, fill=(250, 248, 242, 255), outline=NAVY, width=max(3, width // 75))
    d.polygon([(width * 0.50, top + h * 0.22), (width * 0.90, top + h * 0.22), (width * 0.80, top + h - 4), (width * 0.50, top + h - 4)],
              fill=(226, 222, 214, 255))
    d.polygon(body, outline=NAVY, width=max(3, width // 75))
    d.ellipse((width * 0.40, top + h * 0.48, width * 0.60, top + h * 0.68), fill=(196, 60, 52, 255))   # a red seal, no text
    return painterly(im, 8, 0.7)


@lru_cache(maxsize=8)
def receipt_strip(entries: tuple, entry_w: int = 150, height: int = 64, seed: int = 3) -> Image.Image:
    """A long receipt seen lying flat and read left to right: one entry per panel (label over amount),
    upright text, dashed tear lines between entries (world px)."""
    n = len(entries)
    w = entry_w * n + 24
    im = Image.new('RGBA', (w, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w - 1, height - 1), fill=(250, 247, 238, 255))
    f_label, f_amt = font(MONO, int(height * 0.34)), font(MONO, int(height * 0.32))
    for i, (label, amount) in enumerate(entries):
        x0 = 12 + i * entry_w
        red = label.upper().startswith('NEXT') or label.upper() == 'PAYDAY' or amount == 'next'
        col = INK if red else (54, 50, 60)
        d.text((x0 + entry_w / 2, height * 0.30), label, font=f_label, fill=col, anchor='mm')
        if amount:
            d.text((x0 + entry_w / 2, height * 0.72), amount, font=f_amt, fill=INK if amount.startswith('-') or red else (36, 120, 70), anchor='mm')
        if i:
            for y in range(4, height - 4, 8):
                d.line([(x0, y), (x0, y + 4)], fill=(205, 198, 186), width=2)
    for y in range(0, height, 10):                    # torn ends
        d.polygon([(0, y), (5, y + 5), (0, y + 10)], fill=(0, 0, 0, 0))
        d.polygon([(w - 1, y), (w - 6, y + 5), (w - 1, y + 10)], fill=(0, 0, 0, 0))
    return painterly(im, seed, 0.6)
