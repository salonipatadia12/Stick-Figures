"""Part artwork for the cut-out rig, built from The Empty Chair character art.

The head plate is cut from the approved Empty Chair art (its painted shading is kept;
the eyes, brows and mouth are removed so they can animate). Torso, limbs, hands and
feet are flat navy/white cel shapes in the same palette, drawn at high resolution.
Every part is cached, so a render only rotates, scales and places finished images.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'legacy/bespoke/character-motion-source/assets/character.png'
CACHE = ROOT / 'styles/cozy/rig'

# Palette sampled from the Empty Chair frames.
NAVY = (22, 22, 46)
NAVY_EDGE = (52, 54, 86)        # finger separations and inner edges
WHITE = (251, 245, 237)
SHADE = (222, 215, 216)         # the cool shading crescent on heads and torsos
BLUSH = (236, 150, 128)
TONGUE = (226, 112, 98)

# Head-plate fit, measured on SOURCE (circle fit to the head silhouette, 941x1672 art).
HEAD_CENTER, HEAD_R = (509.5, 624.7), 274.3
OUTLINE = 0.052                  # navy outline thickness as a fraction of the radius


def _inpaint(rgb: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """Normalized-convolution fill: each hole pixel becomes the blurred average of nearby valid pixels."""
    out = rgb.copy()
    weight = Image.fromarray((valid * 255).astype(np.uint8))
    blurred_w = np.asarray(weight.filter(ImageFilter.GaussianBlur(sigma))).astype(float) / 255
    for c in range(3):
        channel = Image.fromarray((rgb[..., c] * valid).astype(np.uint8))
        blurred = np.asarray(channel.filter(ImageFilter.GaussianBlur(sigma))).astype(float)
        fill = blurred / np.maximum(blurred_w, 1e-3)
        out[..., c] = np.where(valid, rgb[..., c], fill)
    return out


def build_head_plate() -> Image.Image:
    """Blank head from the Empty Chair art: shading kept, features painted out, clean navy outline redrawn."""
    art = np.asarray(Image.open(SOURCE).convert('RGBA')).astype(float)
    cx, cy = HEAD_CENTER
    r = HEAD_R
    pad = 8
    x0, y0 = int(cx - r - pad), int(cy - r - pad)
    size = int(2 * (r + pad))
    crop = art[y0:y0 + size, x0:x0 + size]
    yy, xx = np.mgrid[0:size, 0:size]
    dist = np.hypot(xx - (cx - x0), yy - (cy - y0))
    rgb = crop[..., :3]
    lum = rgb.mean(-1)
    interior = dist < r - 4
    # Anything dark inside the head is a feature or the inner edge of the outline: repaint it.
    dark = (lum < 200) & interior
    dark = np.asarray(Image.fromarray((dark * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))) > 0
    valid = interior & ~dark
    filled = _inpaint(rgb, valid, sigma=14)
    filled = _inpaint(filled, valid, sigma=40) * (~valid[..., None]) + filled * valid[..., None]
    # Rebuild at 2x so the redrawn outline is anti-aliased.
    scale = 2
    big = Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8)).resize((size * scale, size * scale), Image.LANCZOS)
    plate = Image.new('RGBA', big.size, (0, 0, 0, 0))
    disc = Image.new('L', big.size, 0)
    c = ((cx - x0) * scale, (cy - y0) * scale)
    R = (r - 2) * scale
    ImageDraw.Draw(disc).ellipse((c[0] - R, c[1] - R, c[0] + R, c[1] + R), fill=255)
    plate.paste(big, (0, 0), disc)
    ring = Image.new('L', big.size, 0)
    d = ImageDraw.Draw(ring)
    d.ellipse((c[0] - R, c[1] - R, c[0] + R, c[1] + R), fill=255)
    inner = R * (1 - OUTLINE)
    d.ellipse((c[0] - inner, c[1] - inner, c[0] + inner, c[1] + inner), fill=0)
    plate.paste(Image.new('RGBA', big.size, NAVY + (255,)), (0, 0), ring)
    bbox = disc.getbbox()
    plate = plate.crop(bbox).resize((size, size), Image.LANCZOS)
    return plate


@lru_cache(maxsize=2)
def head_plate(shade_side: str = 'left') -> Image.Image:
    """The blank head. The art is lit from the right (shade on the left); mirror it for light from the left."""
    path = CACHE / 'head.png'
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        build_head_plate().save(path)
    plate = Image.open(path).convert('RGBA')
    return plate if shade_side == 'left' else plate.transpose(Image.FLIP_LEFT_RIGHT)


@lru_cache(maxsize=64)
def scaled_head(diameter: int, shade_side: str) -> Image.Image:
    return head_plate(shade_side).resize((diameter, diameter), Image.LANCZOS)


# ---- hands -----------------------------------------------------------------------------------------
# A hand sprite is drawn pointing along +x from the wrist at (0, 0), thumb toward -y, at UNIT px per D.
HAND_UNIT = 600  # px per head diameter while drawing; sprites are scaled down when placed


def _capsule(d: ImageDraw.ImageDraw, a, b, radius, fill, outline=None, width=0):
    ax, ay = a
    bx, by = b
    ang = math.atan2(by - ay, bx - ax)
    nx, ny = -math.sin(ang) * radius, math.cos(ang) * radius
    poly = [(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)]
    if outline:
        grow = width
        gnx, gny = -math.sin(ang) * (radius + grow), math.cos(ang) * (radius + grow)
        d.polygon([(ax + gnx, ay + gny), (bx + gnx, by + gny), (bx - gnx, by - gny), (ax - gnx, ay - gny)], fill=outline)
        for (px, py) in (a, b):
            d.ellipse((px - radius - grow, py - radius - grow, px + radius + grow, py + radius + grow), fill=outline)
    d.polygon(poly, fill=fill)
    for (px, py) in (a, b):
        d.ellipse((px - radius, py - radius, px + radius, py + radius), fill=fill)


def _hand(shape: str) -> tuple[Image.Image, tuple[float, float], tuple[float, float]]:
    """Returns (sprite, wrist anchor, grip/hold point) in sprite pixels. Sprite points along +x."""
    U = HAND_UNIT
    W, H = int(0.62 * U), int(0.5 * U)
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    wx, wy = 0.04 * U, H / 2              # wrist
    px, py = 0.17 * U, H / 2              # palm centre
    fw = 0.042 * U                        # finger radius
    edge = 0.007 * U
    grip = (px + 0.05 * U, py)
    _capsule(d, (wx, wy), (px - 0.05 * U, py), 0.055 * U, NAVY)  # wrist stub, matches forearm width
    if shape in ('open', 'wave'):
        for i, ang in enumerate((-26, -9, 8, 25)):
            a = math.radians(ang)
            base = (px + 0.06 * U * math.cos(a), py + 0.06 * U * math.sin(a) + (i - 1.5) * 0.012 * U)
            length = (0.17, 0.19, 0.18, 0.15)[i] * U
            tip = (base[0] + length * math.cos(a), base[1] + length * math.sin(a))
            _capsule(d, base, tip, fw, NAVY, NAVY_EDGE, edge)
        d.ellipse((px - 0.10 * U, py - 0.10 * U, px + 0.10 * U, py + 0.10 * U), fill=NAVY)
        _capsule(d, (px - 0.02 * U, py - 0.06 * U), (px + 0.07 * U, py - 0.17 * U), fw * 1.05, NAVY, NAVY_EDGE, edge)
        grip = (px + 0.14 * U, py)
    elif shape == 'point':
        d.ellipse((px - 0.10 * U, py - 0.10 * U, px + 0.11 * U, py + 0.10 * U), fill=NAVY, outline=NAVY_EDGE, width=int(edge))
        _capsule(d, (px + 0.04 * U, py - 0.04 * U), (px + 0.30 * U, py - 0.05 * U), fw, NAVY, NAVY_EDGE, edge)
        for k in range(3):
            y = py + (k - 0.2) * 0.055 * U
            d.arc((px + 0.02 * U, y - 0.03 * U, px + 0.13 * U, y + 0.04 * U), -80, 80, fill=NAVY_EDGE, width=int(edge))
        _capsule(d, (px - 0.03 * U, py - 0.07 * U), (px + 0.06 * U, py - 0.09 * U), fw, NAVY, NAVY_EDGE, edge)
    elif shape in ('grip', 'fist'):
        d.ellipse((px - 0.10 * U, py - 0.105 * U, px + 0.12 * U, py + 0.105 * U), fill=NAVY, outline=NAVY_EDGE, width=int(edge))
        for k in range(4):  # curled fingers seen from the back: knuckle arcs
            y = py - 0.075 * U + k * 0.05 * U
            d.arc((px + 0.03 * U, y - 0.025 * U, px + 0.14 * U, y + 0.03 * U), -85, 85, fill=NAVY_EDGE, width=int(edge))
        _capsule(d, (px - 0.04 * U, py - 0.07 * U), (px + 0.07 * U, py - 0.03 * U), fw, NAVY, NAVY_EDGE, edge)
        grip = (px + 0.04 * U, py)
    else:  # relaxed mitten: fingers together, slight curl
        for i in range(4):
            base = (px + 0.04 * U, py - 0.06 * U + i * 0.04 * U)
            tip = (px + 0.20 * U - abs(i - 1.2) * 0.02 * U, base[1] + 0.02 * U)
            _capsule(d, base, tip, fw * 0.95, NAVY, NAVY_EDGE, edge)
        d.ellipse((px - 0.10 * U, py - 0.10 * U, px + 0.10 * U, py + 0.10 * U), fill=NAVY)
        _capsule(d, (px - 0.02 * U, py - 0.06 * U), (px + 0.08 * U, py - 0.12 * U), fw, NAVY, NAVY_EDGE, edge)
        grip = (px + 0.12 * U, py)
    # Faint warm highlight along the lit knuckle edge, like the painted hands.
    return im, (wx, wy), grip


@lru_cache(maxsize=16)
def hand_sprite(shape: str, mirrored: bool):
    im, wrist, grip = _hand(shape)
    if mirrored:
        im = im.transpose(Image.FLIP_TOP_BOTTOM)
        wrist = (wrist[0], im.height - wrist[1])
        grip = (grip[0], im.height - grip[1])
    return im, wrist, grip


# ---- torso and feet --------------------------------------------------------------------------------
TORSO_W, TORSO_H = 0.78, 1.50   # in head diameters (widest point, full height)
TORSO_TOP_W = 0.56               # shoulders are narrower and tucked under the head


def torso_outline(U: float, pad: float) -> list:
    """A tapered bean: narrow rounded shoulders, slightly fuller hips, rounded bottom."""
    h = TORSO_H * U
    pts_r = []
    for i in range(41):
        v = i / 40                                     # 0 top .. 1 bottom
        full = TORSO_TOP_W + (TORSO_W - TORSO_TOP_W) * (1 - (1 - min(1, v / 0.75)) ** 2)
        hw = full / 2 * U
        if v < 0.16:                                   # round shoulders
            k = v / 0.16
            hw *= math.sqrt(max(0.0, 1 - (1 - k) ** 2))
        if v > 0.86:                                   # round bottom
            k = (v - 0.86) / 0.14
            hw *= math.sqrt(max(0.0, 1 - k ** 2)) * 0.55 + 0.45 * (1 - k ** 3)
        pts_r.append((hw, v * h))
    cx = TORSO_W / 2 * U + pad
    right = [(cx + x, pad + y) for x, y in pts_r]
    left = [(cx - x, pad + y) for x, y in reversed(pts_r)]
    return right + left


@lru_cache(maxsize=4)
def torso_sprite(shade_side: str = 'right') -> Image.Image:
    """White torso with a navy outline and a soft shading band, at 600 px per D."""
    U = HAND_UNIT
    w, h = int(TORSO_W * U), int(TORSO_H * U)
    pad = int(0.03 * U)
    im = Image.new('RGBA', (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    mask = Image.new('L', im.size, 0)
    ImageDraw.Draw(mask).polygon(torso_outline(U, pad), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    body = Image.new('RGBA', im.size, WHITE + (255,))
    # Shading band: a soft gradient toward one side.
    grad = np.zeros((im.height, im.width), float)
    xs = np.linspace(0, 1, im.width)
    band = np.clip((xs - 0.70) / 0.26, 0, 1) if shade_side == 'right' else np.clip((0.30 - xs) / 0.26, 0, 1)
    grad[:] = band ** 1.4
    shade = Image.fromarray((grad * 255 * 0.9).astype(np.uint8))
    body.paste(Image.new('RGBA', im.size, SHADE + (255,)), (0, 0), shade)
    outline = Image.new('RGBA', im.size, NAVY + (255,))
    inner = mask.filter(ImageFilter.MinFilter(int(0.035 * U) | 1))
    im.paste(outline, (0, 0), mask)
    im.paste(body, (0, 0), inner)
    return im


@lru_cache(maxsize=8)
def foot_sprite() -> Image.Image:
    """A rounded navy foot pointing along +x, with its ankle at the left-centre."""
    U = HAND_UNIT
    im = Image.new('RGBA', (int(0.40 * U), int(0.20 * U)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((int(0.02 * U), int(0.03 * U), int(0.38 * U), int(0.18 * U)), radius=int(0.075 * U), fill=NAVY)
    d.arc((int(0.20 * U), int(0.05 * U), int(0.36 * U), int(0.16 * U)), -70, 40, fill=NAVY_EDGE, width=int(0.008 * U))
    return im
