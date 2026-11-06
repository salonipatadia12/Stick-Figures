"""Characters on a painted base pose, as in The Empty Chair motion edition.

The seated body, legs, chair, table and café are the Empty Chair painting itself (a base pose built by
scripts/motion_test/build_bases.py into styles/cozy/bases/<name>/). Only the head, arms, hands and props
are animated on top of it:

- the head is the frame's own painted head with its features painted out; the face is drawn live
  and the head nods, tilts and turns on its neck;
- the arms come out from behind the torso sides (the torso around each shoulder is laid back over the
  arm roots), with the art's proportions: thick navy segments, hands that reach the tabletop;
- the hands are the anatomical hand library (pipeline/rig/hands.py), with held props in the grip.

Pose hand targets are in head diameters (D) from the midpoint between the two shoulders, y down.
Layer order (Stage.compose): plate, wall props, arms under the table, table, props on the table,
far arm, head, near arm, shoulder occluder, cups in front, flying props.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import hands
from .parts import NAVY, TONGUE
from .skeleton import GRIPPING, Held, Pose, limb, paste_rotated, rot, segment_path, tube

ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = ROOT / 'styles/cozy/bases'
SS = 2
UPPER, FORE = 0.52, 0.46          # arm bones in D: seated at the table, the hands reach the tabletop (as in the art)
ARM_W = (0.072, 0.128, 0.118)     # root (tucked behind the torso side), elbow, wrist
HAND_LEN = 0.29
ARM_NAVY = (27, 29, 56)           # the painted limbs' navy
LIMB_LIGHT = (70, 72, 116)
NECK = 0.42                       # head centre to the neck pivot, in D
DROOP = 2.5                       # how strongly the elbow solver prefers elbows down


# ---- base poses -------------------------------------------------------------------------------------
@dataclass
class Base:
    name: str
    meta: dict
    plate: Image.Image
    layers: dict                     # 'table' / 'front' / 'occluder' -> (RGBA sprite, (x, y) in plate px)
    heads: dict                      # seat name -> (featureless head sprite, its centre in sprite px)

    @property
    def size(self):
        return tuple(self.meta['size'])

    def seat(self, name: str) -> dict:
        return next(s for s in self.meta['seats'] if s['name'] == name)


@lru_cache(maxsize=4)
def load_base(name: str) -> Base:
    d = BASE_DIR / name
    meta = json.loads((d / 'base.json').read_text())
    layers = {k: (Image.open(d / f'{k}.png').convert('RGBA'), tuple(xy)) for k, xy in meta['layers'].items()}
    heads = {}
    for s in meta['seats']:
        art = s['head_art']
        heads[s['name']] = (Image.open(d / art['sprite']).convert('RGBA'), tuple(art['centre']))
    return Base(name, meta, Image.open(d / 'plate.png').convert('RGB'), layers, heads)


# ---- the face ---------------------------------------------------------------------------------------
def draw_face(d: ImageDraw.ImageDraw, c, u: float, pose: Pose, span: float = 0.32, eye_y: float = 0.02,
              mouth_y: float = 0.23):
    """Eyes, brows and mouth in the painted faces' style, on a head of diameter u centred at c.
    turn +-1 puts the face in a strong three-quarter view toward screen-right / screen-left (as in the art)."""
    def P(x, y):
        return (c[0] + x * u, c[1] + y * u)

    t = max(-1.0, min(1.0, pose.turn))
    face_x = span * t + 0.03 * pose.gaze_x
    face_y = 0.06 * pose.gaze_y                    # looking down moves the whole face down the head
    spacing = 0.165 * (1 - 0.12 * abs(t))
    gx, gy = 0.018 * pose.gaze_x, 0.016 * pose.gaze_y
    ew = 0.050 * (1 - 0.28 * abs(t)) * (1 + 0.15 * pose.eye_wide)
    eh = 0.082 * (1 + 0.22 * pose.eye_wide)
    line = max(2, round(0.016 * u))
    open_ = max(0.0, min(1.0, pose.eye_open))
    for k in (-1, 1):
        far = (k == -1 and t > 0) or (k == 1 and t < 0)
        squeeze = 1 - 0.22 * abs(t) if far else 1.0
        ex, ey = face_x + k * spacing + gx, eye_y + face_y + gy + (0.012 * abs(t) if far else 0.0)
        if open_ > 0.22:
            h = eh * open_
            x0, y0 = P(ex - ew * squeeze, ey - h)
            x1, y1 = P(ex + ew * squeeze, ey + h)
            d.ellipse((x0, y0, x1, y1), fill=NAVY)
            hx, hy = P(ex + ew * 0.30 * squeeze, ey - h * 0.45)
            r = 0.016 * u * min(1, open_ * 1.3)
            d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=(255, 255, 255))
        else:                                       # happily / tightly shut: a curved lid line
            x0, y0 = P(ex - ew * 1.1 * squeeze, ey - 0.02)
            x1, y1 = P(ex + ew * 1.1 * squeeze, ey + 0.035)
            d.arc((x0, y0, x1, y1), 15, 165, fill=NAVY, width=line + 1)
        by = eye_y + face_y - 0.135 - 0.045 * pose.brow_raise - 0.02 * pose.eye_wide
        bx = face_x + k * (spacing + 0.005)
        pts = []
        for i in range(9):
            v = i / 8
            x = bx + k * (-0.045 + 0.10 * v) * squeeze
            y = by - 0.022 * 4 * v * (1 - v) - pose.brow_worry * 0.035 * (1 - v) + pose.brow_worry * 0.008 * v
            pts.append(P(x, y))
        d.line(pts, fill=NAVY, width=line, joint='curve')
    mx, my = face_x * 1.02, mouth_y + face_y
    mw = 0.068 * (1 + 0.25 * max(0, pose.smile)) * (1 - 0.15 * abs(t))
    if pose.mouth_open > 0.06:
        o = pose.mouth_open
        top = [P(mx - mw + 2 * mw * i / 10, my - 0.006 * max(0, -pose.smile)) for i in range(11)]
        depth = 0.03 + 0.085 * o
        bottom = [P(mx + mw * math.cos(math.pi * i / 16) * (0.96 if pose.smile > 0 else 0.8),
                    my + depth * math.sin(math.pi * i / 16) * (1 if pose.smile >= 0 else 0.7)) for i in range(17)]
        shape = top + bottom
        d.polygon(shape, fill=NAVY)
        cx, cy = P(mx, my)
        inner = [((x - cx) * 0.80 + cx, (y - cy) * 0.78 + cy + 0.006 * u) for x, y in shape]
        d.polygon(inner, fill=(120, 42, 52))
        tx, ty = P(mx, my + depth * 0.62)
        tr = mw * 0.55 * u
        d.ellipse((tx - tr, ty - tr * 0.45, tx + tr, ty + tr * 0.55), fill=TONGUE)
    else:
        curve = [P(mx - mw + 2 * mw * i / 12, my + pose.smile * 0.045 * 4 * (i / 12) * (1 - i / 12) * (1 if pose.smile > 0 else 0.8))
                 for i in range(13)]
        d.line(curve, fill=NAVY, width=line, joint='curve')


def limb_light(layer: Image.Image, lit: int):
    """A lit band on the light side of each navy limb, inside its silhouette (painted modelling)."""
    a = layer.getchannel('A')
    shift = max(2, round(layer.width / 260))
    moved = a.transform(a.size, Image.AFFINE, (1, 0, lit * shift * 2.0, 0, 1, 0))
    band = np.clip(np.asarray(a, np.float32) - np.asarray(moved, np.float32), 0, 255) / 255
    band = np.asarray(Image.fromarray((band * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(shift * 0.8)), np.float32) / 255
    arr = np.asarray(layer).astype(np.float32)
    mask = (arr[..., :3].mean(-1) < 80)[..., None]
    col = np.array(LIMB_LIGHT, np.float32)
    arr[..., :3] = np.where(mask, arr[..., :3] * (1 - 0.6 * band[..., None]) + col * 0.6 * band[..., None], arr[..., :3])
    layer.paste(Image.fromarray(arr.astype(np.uint8), 'RGBA'))


# ---- a character on a base --------------------------------------------------------------------------
@dataclass
class Puppet:
    base: Base
    seat_name: str
    face: dict = field(default_factory=lambda: dict(span=0.32, eye_y=0.02, mouth_y=0.23))

    def __post_init__(self):
        s = self.base.seat(self.seat_name)
        self.s = s
        self.D = float(s['D'])
        self.facing = s['facing']
        self.far = 'r' if self.facing > 0 else 'l'      # the arm on the far side of the body
        self.near = 'l' if self.facing > 0 else 'r'
        self.light = 1 if self.base.meta.get('light', 'right') == 'right' else -1
        self.shoulders = {k: tuple(v) for k, v in s['shoulders'].items()}
        (lx, ly), (rx, ry) = self.shoulders['l'], self.shoulders['r']
        self.mid = ((lx + rx) / 2, (ly + ry) / 2)
        self.head_sprite, self.head_centre = self.base.heads[self.seat_name]
        self._head_cache = {}

    # body space (D from the shoulder midpoint) <-> plate pixels
    def world(self, p) -> tuple:
        return (self.mid[0] + p[0] * self.D, self.mid[1] + p[1] * self.D)

    def body(self, xy) -> tuple:
        return ((xy[0] - self.mid[0]) / self.D, (xy[1] - self.mid[1]) / self.D)

    def rest(self, side: str) -> tuple:
        return self.body(self.s['rest'][side])

    def joints(self, pose: Pose, scale: float = 1.0, origin=(0.0, 0.0)) -> dict:
        D = self.D * scale

        def S(p):
            return ((p[0] - origin[0]) * scale, (p[1] - origin[1]) * scale)

        hx, hy = self.s['head']
        pivot = (hx, hy + NECK * self.D)
        # The painted body stays put: lean, breath and bounces show in the head and shoulders.
        dx = pose.head_dx + 0.006 * pose.lean
        dy = pose.head_dy - 0.014 * pose.breath + 0.45 * (pose.crouch - pose.lift) - 0.25 * pose.stretch
        hc = rot((0.0, -NECK * self.D), pose.head_tilt)
        head = (pivot[0] + hc[0] + dx * self.D, pivot[1] + hc[1] + dy * self.D)
        j = dict(D=D, scale=scale, origin=origin, head=S(head), head_angle=pose.head_tilt, neck=S(pivot))
        centre_x = S(self.mid)[0]
        lift = (0.010 * pose.breath + 0.25 * (pose.lift - pose.crouch)) * self.D
        for side, sign, hand, bend in (('l', -1, pose.lh, pose.l_bend), ('r', 1, pose.rh, pose.r_bend)):
            sx, sy = self.shoulders[side]
            shp = S((sx, sy - lift))
            target = S(self.world(hand))
            # Elbows hang down by default (the art's arms hug the torso); an action can lift one out to the side.
            droop = pose.extra.get(side + '_droop', DROOP)
            elbow, wrist, _ = limb(shp, target, UPPER * D, FORE * D, sign, bend, centre_x, droop=droop)
            j[side + '_sh'], j[side + '_el'], j[side + '_wr'], j[side + '_target'] = shp, elbow, wrist, target
        return j

    def hand_placement(self, pose: Pose, j: dict, side: str) -> dict:
        """The hand continues the forearm, bent at most WRIST_LIMIT, rotated only in the picture plane.
        Screen-left is the character's RIGHT hand (in three-quarter view as well as from the front)."""
        el, wr = j[side + '_el'], j[side + '_wr']
        shape = pose.l_shape if side == 'l' else pose.r_shape
        requested = pose.l_rot if side == 'l' else pose.r_rot
        bend = hands.clamp_bend(requested)
        fore = math.degrees(math.atan2(wr[1] - el[1], wr[0] - el[0]))
        axis = fore + bend
        drawing = hands.drawing(shape, 'R' if side == 'l' else 'L')
        scale = HAND_LEN * j['D'] / hands.S
        angle = axis + 90
        g = rot(((drawing.grip[0] - drawing.wrist[0]) * scale, (drawing.grip[1] - drawing.wrist[1]) * scale), angle)
        grip = (wr[0] + g[0], wr[1] + g[1])
        j[side + '_bend_requested'], j[side + '_bend'], j[side + '_axis'] = requested, bend, axis
        j[side + '_forearm'] = fore
        j[side + '_grip'] = grip
        j[side + '_grip_radius'] = drawing.grip_radius * scale
        return dict(drawing=drawing, angle=angle, scale=scale, grip=grip)

    def arm_group(self, pose: Pose, side: str) -> str:
        """'under' (below the tabletop), 'far' (behind the head) or 'near' (in front of everything)."""
        if pose.extra.get(side + '_under', 0) >= 0.5:
            return 'under'
        if side == self.far and pose.extra.get(side + '_front', 0) < 0.5:
            return 'far'
        return 'near'

    def arm_layer(self, pose: Pose, j: dict, side: str, h: Held | None):
        sh, el, wr = j[side + '_sh'], j[side + '_el'], j[side + '_wr']
        hp = self.hand_placement(pose, j, side)
        D = j['D']
        pts = [sh, el, wr, hp['grip']]
        reach = 0.45 * D + (h.size_d * D if h else 0)
        x0, y0 = int(min(p[0] for p in pts) - reach), int(min(p[1] for p in pts) - reach)
        x1, y1 = int(max(p[0] for p in pts) + reach), int(max(p[1] for p in pts) + reach)
        layer = Image.new('RGBA', ((x1 - x0) * SS, (y1 - y0) * SS), (0, 0, 0, 0))

        def L(p):
            return ((p[0] - x0) * SS, (p[1] - y0) * SS)

        Ds = D * SS
        sign = -1 if side == 'l' else 1
        arm = Image.new('RGBA', layer.size, (0, 0, 0, 0))
        da = ImageDraw.Draw(arm)
        tube(da, segment_path(L(sh), L(el), 0.03 * sign), ARM_W[0] * Ds, ARM_W[1] * Ds, fill=ARM_NAVY)
        tube(da, segment_path(L(el), L(wr), 0.02 * sign), ARM_W[1] * Ds, ARM_W[2] * Ds, fill=ARM_NAVY)
        limb_light(arm, self.light)
        layer.alpha_composite(arm)
        drawing, angle, hs = hp['drawing'], hp['angle'], hp['scale'] * SS
        if drawing.back.getbbox():
            paste_rotated(layer, drawing.back, drawing.wrist, L(wr), angle, hs)
        if h is not None:
            prop_angle = h.angle + (angle if h.follow_hand else 0.0)
            paste_rotated(layer, h.sprite, h.anchor, L(hp['grip']), prop_angle, h.size_d * Ds / h.sprite.width)
            j[side + '_held_anchor'] = hp['grip']
            j[side + '_held_angle'] = prop_angle
        paste_rotated(layer, drawing.front, drawing.wrist, L(wr), angle, hs)
        return layer.reduce(SS), (x0, y0)

    def head_layer(self, pose: Pose, j: dict):
        """The painted head (scaled), the live face drawn on it, rotated about its centre by the tilt."""
        s = j['scale']
        key = round(s, 3)
        if key not in self._head_cache:
            if len(self._head_cache) > 8:
                self._head_cache.clear()
            sp = self.head_sprite
            w, h = max(1, round(sp.width * s * SS)), max(1, round(sp.height * s * SS))
            self._head_cache[key] = sp.resize((w, h), Image.LANCZOS)
        big = self._head_cache[key].copy()
        cx, cy = self.head_centre[0] * s * SS, self.head_centre[1] * s * SS
        draw_face(ImageDraw.Draw(big), (cx, cy), self.D * s * SS, pose, **self.face)
        head = big.reduce(SS)
        canvas_w = int(head.width * 1.5)
        canvas = Image.new('RGBA', (canvas_w, canvas_w), (0, 0, 0, 0))
        c0 = (canvas_w / 2, canvas_w / 2)
        paste_rotated(canvas, head, (cx / SS, cy / SS), c0, j['head_angle'])
        hx, hy = j['head']
        return canvas, (round(hx - c0[0]), round(hy - c0[1]))

    def render(self, pose: Pose, scale: float = 1.0, origin=(0.0, 0.0), held: dict | None = None) -> dict:
        """Layers for Stage.compose: {'under': [...], 'far': [...], 'head': [...], 'near': [...]}."""
        held = held or {}
        for side, h in held.items():
            shape = pose.l_shape if side == 'l' else pose.r_shape
            if h is not None and shape not in GRIPPING:
                raise ValueError(f'{side} hand holds a prop but its pose is {shape!r}; use one of {GRIPPING}')
        j = self.joints(pose, scale, origin)
        out = {'under': [], 'far': [], 'head': [self.head_layer(pose, j)], 'near': []}
        for side in ('l', 'r'):
            out[self.arm_group(pose, side)].append(self.arm_layer(pose, j, side, held.get(side)))
        self.last_joints = j
        return out


# ---- the stage: one base, its puppets, a camera ------------------------------------------------------
class Stage:
    """Composes a frame from a base pose, its puppets and scene props, through a camera crop."""

    def __init__(self, base: Base, out_size=(1080, 1920)):
        self.base = base
        self.W, self.H = out_size

    def camera(self, cx: float, cy: float, cw: float):
        """A 9:16 crop of the plate of width cw centred near (cx, cy), kept inside the plate."""
        bw, bh = self.base.size
        cw = min(cw, bw, bh * self.W / self.H)
        ch = cw * self.H / self.W
        x0 = min(max(cx - cw / 2, 0), bw - cw)
        y0 = min(max(cy - ch / 2, 0), bh - ch)
        return (x0, y0, self.W / cw)

    def layer(self, name: str, cam):
        """A base layer (table / front / occluder) cropped and scaled to the camera, as (image, xy)."""
        sprite, (lx, ly) = self.base.layers[name]
        x0, y0, s = cam
        w, h = max(1, round(sprite.width * s)), max(1, round(sprite.height * s))
        return sprite.resize((w, h), Image.BICUBIC), (round((lx - x0) * s), round((ly - y0) * s))

    def plate(self, cam) -> Image.Image:
        x0, y0, s = cam
        box = (x0, y0, x0 + self.W / s, y0 + self.H / s)
        return self.base.plate.resize((self.W, self.H), Image.LANCZOS, box=box).convert('RGBA')

    def compose(self, cam, puppets: list, poses: list, held: list | None = None, wall=None, on_table=None,
                flying=None) -> Image.Image:
        """wall / on_table / flying: callables(frame, cam) for the scene's props at their depth."""
        held = held or [{} for _ in puppets]
        frame = self.plate(cam)
        if wall:
            wall(frame, cam)
        outs = [p.render(pose, cam[2], cam[:2], h) for p, pose, h in zip(puppets, poses, held)]
        for o in outs:
            for im, xy in o['under']:
                frame.alpha_composite(im, xy)
        if any(o['under'] for o in outs):
            frame.alpha_composite(*self.layer('table', cam))
        if on_table:
            on_table(frame, cam)
        for key in ('far', 'head', 'near'):
            for o in outs:
                for im, xy in o[key]:
                    frame.alpha_composite(im, xy)
        # The torso around the shoulders goes back over the arm roots (never over a head).
        occ, oxy = self.layer('occluder', cam)
        occ = occ.copy()
        cut = Image.new('L', occ.size, 0)
        dc = ImageDraw.Draw(cut)
        for p in puppets:
            hx, hy = p.last_joints['head']
            r = 0.52 * p.D * cam[2]
            dc.ellipse((hx - oxy[0] - r, hy - oxy[1] - r, hx - oxy[0] + r, hy - oxy[1] + r), fill=255)
        a = np.asarray(occ.getchannel('A'), np.float32) * (1 - np.asarray(cut, np.float32) / 255)
        occ.putalpha(Image.fromarray(a.astype(np.uint8)))
        frame.alpha_composite(occ, oxy)
        frame.alpha_composite(*self.layer('front', cam))
        if flying:
            flying(frame, cam)
        return frame
