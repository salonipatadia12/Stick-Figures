"""Hand pose library in The Empty Chair style: chunky navy fingers, warm separation lines.

Anatomy (checked against a reference photo of a right hand, front and back):
- RIGHT hand, palm toward the camera, fingers up: the thumb is on the viewer's right (+x).
- RIGHT hand, back toward the camera: the thumb is on the viewer's left (-x).
- A LEFT hand is the mirror image of the right hand showing the same face.
Every pose is drawn in a canonical frame: wrist at the bottom centre, the hand's axis (the
direction of the fingers or of the curled fist) pointing up. The rig rotates a drawing only
in the picture plane to continue the forearm, within the wrist limit. Showing the palm or
the back is always a different drawing, never a flipped one.

Each drawing has two layers so that a held object sits IN the grip: `back` (parts behind the
object, e.g. the thumb wrapped round the far side) and `front` (fingers wrapped over it).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

NAVY = (24, 24, 46)
NAVY_DARK = (12, 12, 30)
LINE = (96, 62, 64)      # warm separation lines between fingers, as in the painted hands
S = 520                   # px per hand length (wrist to middle fingertip) while drawing
PAD = 0.75                # canvas half-width in hand lengths
WRIST_LIMIT = 65.0        # max bend between forearm and hand axis, degrees, either way

POSES = ['open_palm', 'back', 'fist', 'grip', 'point', 'thumbs_up', 'resting']
FACING = {'open_palm': 'palm', 'back': 'back', 'fist': 'palm', 'grip': 'back', 'point': 'back',
          'thumbs_up': 'palm', 'resting': 'back'}


@dataclass
class HandDrawing:
    back: Image.Image          # drawn behind a held object
    front: Image.Image         # drawn in front of it
    wrist: tuple               # canvas px
    grip: tuple                # canvas px: where a held object's anchor sits
    grip_radius: float         # canvas px: how far from `grip` an object anchor may sit and still be "in the hand"


def _xy(p):
    """Hand units (x right, y up from the wrist) -> canvas px."""
    return (PAD * S + p[0] * S, 1.15 * S - p[1] * S)


def _capsule(d, a, b, r0, r1, fill):
    ax, ay = _xy(a)
    bx, by = _xy(b)
    ang = math.atan2(by - ay, bx - ax)
    nx, ny = -math.sin(ang), math.cos(ang)
    R0, R1 = r0 * S, r1 * S
    d.polygon([(ax + nx * R0, ay + ny * R0), (bx + nx * R1, by + ny * R1), (bx - nx * R1, by - ny * R1), (ax - nx * R0, ay - ny * R0)], fill=fill)
    d.ellipse((ax - R0, ay - R0, ax + R0, ay + R0), fill=fill)
    d.ellipse((bx - R1, by - R1, bx + R1, by + R1), fill=fill)


def _finger(d, base, angle_deg, length, width, curl=0.0):
    """A finger from base along angle (0 = straight up, + toward +x), outlined in the warm line colour."""
    a = math.radians(angle_deg)
    if curl:
        mid = (base[0] + math.sin(a) * length * 0.55, base[1] + math.cos(a) * length * 0.55)
        a2 = a + math.radians(curl)
        tip = (mid[0] + math.sin(a2) * length * 0.45, mid[1] + math.cos(a2) * length * 0.45)
        for fill, grow in ((LINE, 0.016), (NAVY, 0)):
            _capsule(d, base, mid, width / 2 + grow, width * 0.47 + grow, fill)
            _capsule(d, mid, tip, width * 0.47 + grow, width * 0.43 + grow, fill)
        return tip
    tip = (base[0] + math.sin(a) * length, base[1] + math.cos(a) * length)
    _capsule(d, base, tip, width / 2 + 0.016, width * 0.44 + 0.016, LINE)
    _capsule(d, base, tip, width / 2, width * 0.44, NAVY)
    return tip


def _palm(d, top=0.50, half=0.28, wrist=0.19):
    pts = []
    for i in range(25):  # wrist (narrow) widening to the knuckle line, rounded
        v = i / 24
        y = 0.02 + (top - 0.02) * v
        w = wrist + (half - wrist) * math.sin(min(1, v * 1.25) * math.pi / 2)
        pts.append((w, y))
    poly = [(x, y) for x, y in pts] + [(-x, y) for x, y in reversed(pts)]
    d.polygon([_xy(p) for p in poly], fill=NAVY)
    cx, cy = _xy((0, top - 0.04))
    d.ellipse((cx - half * S, cy - 0.10 * S, cx + half * S, cy + 0.10 * S), fill=NAVY)


def _arc(d, centre, rx, ry, start, end, width=0.016, fill=LINE):
    cx, cy = _xy(centre)
    d.arc((cx - rx * S, cy - ry * S, cx + rx * S, cy + ry * S), start, end, fill=fill, width=max(1, int(width * S)))


def _new():
    size = (int(2 * PAD * S), int(1.5 * S))
    return Image.new('RGBA', size, (0, 0, 0, 0))


def _finish(im: Image.Image) -> Image.Image:
    """Dark outer edge (keeps the warm lines inside only) and a slight painted softness."""
    a = im.getchannel('A')
    inner = a.filter(ImageFilter.MinFilter(int(0.022 * S) | 1))
    ring = ImageChops.subtract(a, inner)
    edge = Image.new('RGBA', im.size, NAVY_DARK + (255,))
    out = im.copy()
    out.paste(edge, (0, 0), ring)
    # Painted modelling: a little lighter toward the top (where the window light falls), darker at the wrist.
    h = im.height
    ramp = np.linspace(1.18, 0.92, h)[:, None, None]
    rgb = np.asarray(out).astype(np.float32)
    rgb[..., :3] = np.clip(rgb[..., :3] * ramp, 0, 255)
    out = Image.fromarray(rgb.astype(np.uint8), 'RGBA')
    out.putalpha(a)
    return out.filter(ImageFilter.GaussianBlur(1.0))


def _draw_thumb_plus_x(pose: str):
    """Right hand with the thumb toward +x (palm toward camera). Returns (back, front, grip, grip_radius)."""
    back, front = _new(), _new()
    db, df = ImageDraw.Draw(back), ImageDraw.Draw(front)
    grip, grip_r = (0.0, 0.30), 0.10
    if pose == 'open_palm':
        # Pinky first (far side), index last: each later finger's warm outline separates it from the previous.
        for x, y, ang, ln, w in ((-0.20, 0.44, -12, 0.29, 0.145), (-0.07, 0.49, -4, 0.37, 0.155),
                                 (0.07, 0.50, 3, 0.40, 0.16), (0.20, 0.47, 10, 0.35, 0.155)):
            _finger(df, (x, y), ang, ln, w)
        _palm(df)
        _arc(df, (0.14, 0.22), 0.16, 0.20, 160, 260)                      # thenar crease round the thumb base
        _arc(df, (0.0, 0.46), 0.22, 0.05, 15, 165, 0.012)                 # crease under the finger bases
        _finger(df, (0.17, 0.14), 50, 0.33, 0.165, curl=-18)               # thumb out to +x
    elif pose in ('fist', 'thumbs_up'):
        _palm(df, top=0.40, half=0.27)
        for i, x in enumerate((-0.195, -0.065, 0.065, 0.195)):              # four curled fingers seen from the front
            top = 0.50 - 0.03 * abs(i - 1.5)
            _capsule(df, (x, 0.24), (x, top), 0.075, 0.075, LINE)
            _capsule(df, (x, 0.24), (x, top), 0.06, 0.06, NAVY)
        if pose == 'fist':
            _finger(df, (0.25, 0.22), -80, 0.27, 0.13)                     # thumb folded across the fingers
        else:
            _finger(df, (0.22, 0.30), 80, 0.36, 0.16)                      # thumb out to +x (up, once the arm is placed)
        grip, grip_r = (0.0, 0.34), 0.12
    return back, front, grip, grip_r


def _draw_thumb_minus_x(pose: str):
    """Right hand with the thumb toward -x (back of hand toward camera)."""
    back, front = _new(), _new()
    db, df = ImageDraw.Draw(back), ImageDraw.Draw(front)
    grip, grip_r = (0.0, 0.30), 0.10
    if pose == 'back':
        _finger(df, (-0.17, 0.14), -50, 0.33, 0.165, curl=18)             # thumb out to -x, behind the palm edge
        _palm(df)
        for x, y, ang, ln, w in ((0.20, 0.44, 12, 0.29, 0.145), (0.07, 0.49, 4, 0.37, 0.155),
                                 (-0.07, 0.50, -3, 0.40, 0.16), (-0.20, 0.47, -10, 0.35, 0.155)):
            _finger(df, (x, y), ang, ln, w)
        for x in (-0.20, -0.07, 0.07, 0.19):                                # knuckles
            _arc(df, (x, 0.45), 0.05, 0.03, 200, 340, 0.012)
        _palm(df, top=0.44, half=0.27)                                      # back of the hand over the finger bases
        for x in (-0.20, -0.07, 0.07, 0.19):
            _arc(df, (x, 0.43), 0.045, 0.028, 200, 340, 0.012)
    elif pose == 'resting':
        _finger(df, (-0.18, 0.12), -30, 0.30, 0.14, curl=12)               # thumb relaxed along the -x side
        _palm(df)
        for x, ln in ((0.19, 0.27), (0.065, 0.34), (-0.065, 0.36), (-0.19, 0.31)):
            _finger(df, (x, 0.46), -2 * (x > 0) + 2 * (x < 0), ln, 0.13, curl=14 * (1 if x > 0 else -1) * 0 + 16)
        _palm(df, top=0.44, half=0.27)
        for x in (-0.19, -0.065, 0.065, 0.19):
            _arc(df, (x, 0.43), 0.045, 0.028, 200, 340, 0.012)
    elif pose == 'grip':
        # The thumb wraps round the far side of the held object (back layer); the curled fingers come over the front.
        _finger(db, (-0.20, 0.16), -10, 0.30, 0.15, curl=40)
        _palm(df, top=0.30, half=0.26)
        for i, x in enumerate((0.195, 0.065, -0.065, -0.195)):
            top = 0.46 - 0.025 * abs(i - 1.5)
            _capsule(df, (x, 0.22), (x, top), 0.075, 0.072, LINE)
            _capsule(df, (x, 0.22), (x, top), 0.06, 0.058, NAVY)
            _arc(df, (x, top - 0.02), 0.045, 0.03, 200, 340, 0.012)        # knuckle highlight
        grip, grip_r = (0.0, 0.40), 0.13
    elif pose == 'point':
        _palm(df, top=0.40, half=0.26)
        for i, x in enumerate((0.19, 0.065)):                               # curled pinky and ring
            _capsule(df, (x, 0.24), (x, 0.47), 0.075, 0.072, LINE)
            _capsule(df, (x, 0.24), (x, 0.47), 0.06, 0.058, NAVY)
        _capsule(df, (-0.065, 0.24), (-0.065, 0.48), 0.075, 0.072, LINE)  # curled middle
        _capsule(df, (-0.065, 0.24), (-0.065, 0.48), 0.06, 0.058, NAVY)
        _finger(df, (-0.19, 0.42), -2, 0.50, 0.13)                         # index straight out along the axis
        _finger(df, (-0.25, 0.14), 40, 0.26, 0.14)                         # thumb tucked across the middle finger
        grip, grip_r = (-0.19, 0.92), 0.06                                  # the fingertip (for touching things)
    return back, front, grip, grip_r


@lru_cache(maxsize=32)
def drawing(pose: str, hand: str) -> HandDrawing:
    """pose in POSES; hand = 'R' or 'L' (the character's own right or left hand)."""
    if pose not in POSES:
        raise ValueError(f'unknown hand pose {pose!r}')
    plus_x = FACING[pose] == 'palm'
    back, front, grip, grip_r = (_draw_thumb_plus_x if plus_x else _draw_thumb_minus_x)(pose)
    back, front = _finish(back) if back.getbbox() else back, _finish(front)
    wrist = _xy((0, 0))
    g = _xy(grip)
    if hand == 'L':  # a left hand is the mirror image of the right hand showing the same face
        back, front = back.transpose(Image.FLIP_LEFT_RIGHT), front.transpose(Image.FLIP_LEFT_RIGHT)
        wrist = (front.width - wrist[0], wrist[1])
        g = (front.width - g[0], g[1])
    return HandDrawing(back, front, wrist, g, grip_r * S)


def thumb_side(pose: str, hand: str) -> int:
    """+1 if the thumb is on the canvas's +x side in the canonical (fingers-up) drawing, else -1."""
    s = 1 if FACING[pose] == 'palm' else -1
    return s if hand == 'R' else -s


def clamp_bend(bend: float) -> float:
    return max(-WRIST_LIMIT, min(WRIST_LIMIT, bend))


def contact_sheet(path, references: list | None = None):
    """All poses, right and left hands, plus optional Empty Chair hand crops for comparison."""
    from PIL import ImageFont
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf', 22)
    small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf', 16)
    cell = 230
    cols = len(POSES)
    rows = 2 + (1 if references else 0)
    sheet = Image.new('RGB', (cols * cell + 180, rows * (cell + 40) + 70), (246, 238, 222))
    d = ImageDraw.Draw(sheet)
    d.text((20, 18), 'Hand pose library (Empty Chair style) - thumbs checked against a reference photo', font=font, fill=(30, 30, 50))
    for c, pose in enumerate(POSES):
        d.text((180 + c * cell + 10, 52), pose.replace('_', ' '), font=small, fill=(60, 60, 70))
    for r, (hand, label) in enumerate((('R', "character's RIGHT\n(screen left)"), ('L', "character's LEFT\n(screen right)"))):
        y = 80 + r * (cell + 40)
        d.multiline_text((14, y + cell / 2 - 20), label, font=small, fill=(30, 30, 50))
        for c, pose in enumerate(POSES):
            h = drawing(pose, hand)
            im = Image.new('RGBA', h.front.size, (0, 0, 0, 0))
            if h.back.getbbox():
                im.alpha_composite(h.back)
            im.alpha_composite(h.front)
            if pose == 'thumbs_up':  # shown the way it is used: forearm sideways, thumb up
                im = im.rotate(90 if thumb_side(pose, hand) > 0 else -90, Image.BICUBIC, expand=True)
                t_note = 'thumb up'
            im = im.crop(im.getbbox())
            k = (cell - 30) / max(im.size)
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
            sheet.paste(im, (180 + c * cell + (cell - im.width) // 2, y + (cell - im.height) // 2), im)
            face = FACING[pose]
            t = f'{face} - thumb ' + ('right' if thumb_side(pose, hand) > 0 else 'left')
            if pose == 'thumbs_up':
                t = f'{face} side - thumb up'
            d.text((180 + c * cell + 10, y + cell + 6), t, font=small, fill=(110, 90, 80))
    if references:
        y = 80 + 2 * (cell + 40)
        d.multiline_text((14, y + cell / 2 - 20), 'The Empty Chair\n(source hands)', font=small, fill=(30, 30, 50))
        for c, ref in enumerate(references[:cols]):
            im = ref.copy()
            k = (cell - 20) / max(im.size)
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
            sheet.paste(im, (180 + c * cell + (cell - im.width) // 2, y + (cell - im.height) // 2))
    sheet.save(path, quality=92)
    return path
