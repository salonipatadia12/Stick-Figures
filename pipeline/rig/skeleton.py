"""A jointed cut-out character: pose model, two-bone IK, face, layering and held props.

Body space: origin at the hip root, units of one head diameter (D), y down. A Pose holds
every animatable channel; Character.render() turns a pose into an RGBA layer at screen
scale. Bones keep their lengths (a little rubber-hose stretch is allowed, never more
than STRETCH), hands follow the forearm, and a held prop is placed from the hand's grip
point so it moves with the hand.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace

import numpy as np
from PIL import Image, ImageDraw

from . import parts
from .parts import NAVY, TONGUE

# Proportions measured on the standing Empty Chair figure (in head diameters).
TORSO_BOTTOM = 0.12
TORSO_TOP = TORSO_BOTTOM - parts.TORSO_H
SHOULDER_X, SHOULDER_Y = 0.27, TORSO_TOP + 0.30
NECK = (0.0, TORSO_TOP + 0.12)
HEAD_C = (0.0, TORSO_TOP - 0.24)
HIP_X = 0.17
UPPER, FORE = 0.60, 0.58
THIGH, SHIN = 0.62, 0.62
ARM_W, LEG_W = 0.105, 0.19
STRETCH = 1.12
SS = 2  # supersampling for anti-aliased edges


@dataclass
class Pose:
    lean: float = 0.0          # degrees, + = top of the body to the right (clockwise)
    crouch: float = 0.0        # D: hips drop, feet stay planted (knees bend)
    lift: float = 0.0          # D: whole body up (hop); feet leave the floor
    stretch: float = 0.0       # torso squash (-) / stretch (+)
    breath: float = 0.0        # 0..1 chest rise
    head_tilt: float = 0.0     # degrees relative to the torso
    head_dx: float = 0.0
    head_dy: float = 0.0       # D, + = down (nod)
    turn: float = 0.0          # -1..1: face turns toward screen-left / screen-right
    gaze_x: float = 0.0
    gaze_y: float = 0.0        # -1..1 within the eye
    eye_open: float = 1.0
    eye_wide: float = 0.0
    brow_raise: float = 0.0    # -1..1
    brow_worry: float = 0.0    # 0..1: inner ends up
    smile: float = 0.45        # -1 frown .. +1 grin
    mouth_open: float = 0.0    # 0..1
    lh: tuple = (-0.42, -0.12)  # screen-left hand target, body space
    rh: tuple = (0.42, -0.12)   # screen-right hand target, body space
    l_shape: str = 'relaxed'
    r_shape: str = 'relaxed'
    l_rot: float = 0.0         # extra hand rotation, degrees
    r_rot: float = 0.0
    l_bend: float = 1.0        # +1 elbow outward, -1 inward
    r_bend: float = 1.0
    lf: tuple = (-0.24, 1.24)  # feet, body space at rest (planted; unaffected by crouch)
    rf: tuple = (0.24, 1.24)
    knee_bend: float = 1.0
    extra: dict = field(default_factory=dict)  # per-scene channels (prop angles, etc.)


NUMERIC = [f.name for f in fields(Pose) if f.type in ('float',)]
POINTS = ['lh', 'rh', 'lf', 'rf']
DISCRETE = ['l_shape', 'r_shape']


def blend(a: Pose, b: Pose, w: float) -> Pose:
    """Linear blend of two poses; discrete channels switch at the halfway point."""
    if w <= 0:
        return a
    if w >= 1:
        return b
    out = {}
    for name in NUMERIC:
        x, y = getattr(a, name), getattr(b, name)
        out[name] = x + (y - x) * w
    for name in POINTS:
        (x0, y0), (x1, y1) = getattr(a, name), getattr(b, name)
        out[name] = (x0 + (x1 - x0) * w, y0 + (y1 - y0) * w)
    for name in DISCRETE:
        out[name] = getattr(b if w >= 0.5 else a, name)
    extra = dict(a.extra)
    for k, v in b.extra.items():
        x = a.extra.get(k, v)
        extra[k] = x + (v - x) * w if isinstance(v, (int, float)) and isinstance(x, (int, float)) else (v if w >= .5 else x)
    out['extra'] = extra
    return replace(a, **out)


def rot(v, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def two_bone(root, target, l1, l2, outward):
    """Elbow/knee position for fixed bone lengths. `outward` (+1/-1) picks the side of the root->target line."""
    dx, dy = target[0] - root[0], target[1] - root[1]
    dist = math.hypot(dx, dy) or 1e-6
    reach = l1 + l2
    if dist > reach:  # rubber-hose: stretch both bones a little, then clamp
        k = min(dist / reach, STRETCH)
        l1, l2 = l1 * k, l2 * k
        if dist > l1 + l2:
            target = (root[0] + dx / dist * (l1 + l2) * 0.9999, root[1] + dy / dist * (l1 + l2) * 0.9999)
            dx, dy = target[0] - root[0], target[1] - root[1]
            dist = math.hypot(dx, dy)
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    ux, uy = dx / dist, dy / dist
    px, py = -uy, ux  # perpendicular
    mid = (root[0] + ux * a + px * h * outward, root[1] + uy * a + py * h * outward)
    return mid, target, (l1, l2)


def limb(root, target, l1, l2, side, bend, centre_x, droop=0.4):
    """Two-bone solve that picks the joint by body logic, not by line direction: with bend=+1 the
    elbow/knee goes away from the body's centre line (and down, by `droop`); bend=-1 goes inward.
    This keeps a raised arm's elbow out to the side instead of folding across the face."""
    best = None
    for o in (1, -1):
        mid, end, lengths = two_bone(root, target, l1, l2, o)
        score = bend * side * (mid[0] - centre_x) + droop * (mid[1] - root[1])
        if best is None or score > best[0]:
            best = (score, mid, end, lengths)
    return best[1], best[2], best[3]


def bezier3(p0, p1, p2, n=28):
    """Quadratic curve that passes through p1 at t=0.5 (a soft, hand-drawn bend)."""
    c = (2 * p1[0] - (p0[0] + p2[0]) / 2, 2 * p1[1] - (p0[1] + p2[1]) / 2)
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 2 * np.array(p0) + 2 * (1 - t) * t * np.array(c) + t ** 2 * np.array(p2)


def tube(d: ImageDraw.ImageDraw, pts: np.ndarray, w0: float, w1: float, fill=NAVY):
    """A tapered limb along a polyline with round caps."""
    seg = np.diff(pts, axis=0)
    tang = np.vstack([seg[:1], (seg[1:] + seg[:-1]) / 2, seg[-1:]])
    tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-6)
    normal = np.c_[-tang[:, 1], tang[:, 0]]
    widths = np.linspace(w0, w1, len(pts))[:, None] / 2
    left, right = pts + normal * widths, pts - normal * widths
    poly = [tuple(p) for p in np.vstack([left, right[::-1]])]
    d.polygon(poly, fill=fill)
    for p, w in ((pts[0], w0), (pts[-1], w1)):
        d.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill=fill)


def paste_rotated(canvas: Image.Image, sprite: Image.Image, anchor, target, angle_cw: float, scale: float = 1.0):
    """Place `sprite` so its `anchor` lands on `target`, rotated clockwise by angle_cw degrees."""
    if scale != 1.0:
        sprite = sprite.resize((max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))), Image.BICUBIC)
        anchor = (anchor[0] * scale, anchor[1] * scale)
    rotated = sprite.rotate(-angle_cw, Image.BICUBIC, expand=True)
    off = (anchor[0] - sprite.width / 2, anchor[1] - sprite.height / 2)
    r = rot(off, angle_cw)
    point = (rotated.width / 2 + r[0], rotated.height / 2 + r[1])
    canvas.alpha_composite(rotated, (round(target[0] - point[0]), round(target[1] - point[1])))


@dataclass
class Held:
    """A prop in a hand: sprite, the sprite point that sits in the grip, its size in D, and its rotation."""
    sprite: Image.Image
    grip: tuple
    size_d: float               # sprite width in head diameters
    angle: float = 0.0          # extra clockwise rotation relative to world-up
    follow_hand: float = 0.0    # 0: stays upright, 1: rotates with the forearm
    front: bool = False         # draw over the hand instead of under it


@dataclass
class Character:
    D: float                     # head diameter in world pixels
    shade_side: str = 'right'    # which side the shading falls on (light comes from the other side)
    rim: tuple = (255, 196, 120)  # warm rim light on the lit edge
    rim_strength: float = 0.55
    grade: tuple = (1.0, 0.975, 0.93)

    # ---- geometry -----------------------------------------------------------------------------
    def joints(self, pose: Pose, base: tuple, scale: float, origin: tuple) -> dict:
        """Joint positions in screen pixels. base = the hip root in world px when standing."""
        D = self.D * scale
        bx, by = (base[0] - origin[0]) * scale, (base[1] - origin[1]) * scale
        root = (bx, by + (pose.crouch - pose.lift) * D)
        s = pose.stretch

        def B(p):
            q = (p[0] * (1 - 0.5 * s), p[1] * (1 + s))
            q = rot(q, pose.lean)
            return (root[0] + q[0] * D, root[1] + q[1] * D)

        j = dict(D=D, root=root, B=B)
        j['torso_c'] = B((0, (TORSO_TOP + TORSO_BOTTOM) / 2 - 0.006 * pose.breath))
        head_local = (HEAD_C[0] - NECK[0] + pose.head_dx, HEAD_C[1] - NECK[1] + pose.head_dy - 0.012 * pose.breath)
        hc = rot(head_local, pose.head_tilt)
        j['head'] = B((NECK[0] + hc[0], NECK[1] + hc[1]))
        j['head_angle'] = pose.lean + pose.head_tilt
        for side, sign, hand, bend in (('l', -1, pose.lh, pose.l_bend), ('r', 1, pose.rh, pose.r_bend)):
            sh = B((sign * SHOULDER_X, SHOULDER_Y - 0.014 * pose.breath))
            target = B(hand)
            elbow, wrist, _ = limb(sh, target, UPPER * D, FORE * D, sign, bend, root[0])
            j[side + '_sh'], j[side + '_el'], j[side + '_wr'] = sh, elbow, wrist
        for side, sign, foot in (('l', -1, pose.lf), ('r', 1, pose.rf)):
            hip = (root[0] + sign * HIP_X * D * math.cos(math.radians(pose.lean * .3)), root[1] + TORSO_BOTTOM * 0.2 * D)
            f = (bx + foot[0] * D, by + (foot[1] - pose.lift) * D)
            knee, ankle, _ = limb(hip, f, THIGH * D, SHIN * D, sign, pose.knee_bend, root[0], droop=0.0)
            j[side + '_hip'], j[side + '_kn'], j[side + '_an'] = hip, knee, ankle
        return j

    # ---- drawing ------------------------------------------------------------------------------
    def render(self, pose: Pose, base: tuple, scale: float = 1.0, origin=(0, 0),
               held: dict | None = None, size=(1080, 1920)) -> tuple[Image.Image, tuple]:
        """Returns (layer, (x, y)) to alpha-composite onto the frame at (x, y)."""
        held = held or {}
        j = self.joints(pose, base, scale, origin)
        D = j['D']
        pts = [j['head'], j['l_wr'], j['r_wr'], j['l_an'], j['r_an'], j['l_sh'], j['r_sh'], j['l_el'], j['r_el']]
        reach = 0.75 * D + max([h.size_d * D for h in held.values()], default=0)
        x0 = int(min(p[0] for p in pts) - reach)
        y0 = int(min(p[1] for p in pts) - reach)
        x1 = int(max(p[0] for p in pts) + reach)
        y1 = int(max(p[1] for p in pts) + reach)
        x0, y0 = max(x0, -int(D)), max(y0, -int(D))
        x1, y1 = min(x1, size[0] + int(D)), min(y1, size[1] + int(D))
        W, H = (x1 - x0) * SS, (y1 - y0) * SS
        canvas = Image.new('RGBA', (max(W, 1), max(H, 1)), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)

        def L(p):
            return ((p[0] - x0) * SS, (p[1] - y0) * SS)

        Ds = D * SS
        # Legs and feet.
        for side, sign in (('l', -1), ('r', 1)):
            path = bezier3(L(j[side + '_hip']), L(j[side + '_kn']), L(j[side + '_an']))
            tube(d, path, LEG_W * Ds, LEG_W * 0.86 * Ds)
            foot = parts.foot_sprite()
            fs = Ds / parts.HAND_UNIT
            f = foot if sign > 0 else foot.transpose(Image.FLIP_LEFT_RIGHT)
            anchor = (0.10 * parts.HAND_UNIT, 0.12 * parts.HAND_UNIT) if sign > 0 else (f.width - 0.10 * parts.HAND_UNIT, 0.12 * parts.HAND_UNIT)
            paste_rotated(canvas, f, anchor, L(j[side + '_an']), 0, fs)
        # Torso.
        torso = parts.torso_sprite(self.shade_side)
        ts = Ds / parts.HAND_UNIT
        tw = max(1, round(torso.width * ts * (1 - 0.5 * pose.stretch)))
        th = max(1, round(torso.height * ts * (1 + pose.stretch + 0.012 * pose.breath)))
        t_img = torso.resize((tw, th), Image.BICUBIC)
        paste_rotated(canvas, t_img, (tw / 2, th / 2), L(j['torso_c']), pose.lean)
        # Head and face.
        head = self.head_layer(pose, Ds)
        paste_rotated(canvas, head, (head.width / 2, head.height / 2), L(j['head']), j['head_angle'])
        # Arms, held props, hands. The arm on the side the face turns away from is drawn first.
        order = ('r', 'l') if pose.turn < 0 else ('l', 'r')
        for side in order:
            sh, el, wr = L(j[side + '_sh']), L(j[side + '_el']), L(j[side + '_wr'])
            tube(d, bezier3(sh, el, wr), ARM_W * Ds, ARM_W * 0.92 * Ds)
            shape = pose.l_shape if side == 'l' else pose.r_shape
            hand_rot = pose.l_rot if side == 'l' else pose.r_rot
            fore = math.degrees(math.atan2(wr[1] - el[1], wr[0] - el[0])) + hand_rot
            sprite, wrist, grip = parts.hand_sprite(shape, mirrored=(side == 'r'))
            hs = Ds / parts.HAND_UNIT * 0.92
            # Where the grip lands in canvas space.
            g = rot(((grip[0] - wrist[0]) * hs, (grip[1] - wrist[1]) * hs), fore)
            grip_pt = (wr[0] + g[0], wr[1] + g[1])
            j[side + '_grip'] = (grip_pt[0] / SS + x0, grip_pt[1] / SS + y0)
            j[side + '_hand_angle'] = fore
            h = held.get(side)
            if h and not h.front:
                self._draw_held(canvas, h, grip_pt, fore, Ds)
            paste_rotated(canvas, sprite, wrist, wr, fore, hs)
            if h and h.front:
                self._draw_held(canvas, h, grip_pt, fore, Ds)
        layer = canvas.reduce(SS)
        layer = self.light(layer)
        self.last_joints = j
        return layer, (x0, y0)

    @staticmethod
    def _draw_held(canvas, h: Held, grip_pt, fore, Ds):
        s = h.size_d * Ds / h.sprite.width
        angle = h.angle + h.follow_hand * (fore + 90)  # a sprite drawn upright follows a forearm pointing up
        paste_rotated(canvas, h.sprite, h.grip, grip_pt, angle, s)

    def light(self, layer: Image.Image) -> Image.Image:
        """Warm grade plus a rim of window light on the lit edge, so the cut-out sits in the painted room."""
        a = np.asarray(layer).astype(np.float32)
        rgb, alpha = a[..., :3], a[..., 3:] / 255
        rgb *= np.array(self.grade, np.float32)
        shift = max(2, round(layer.width / 260))
        lit_from_left = self.shade_side == 'right'
        moved = np.roll(alpha, shift if lit_from_left else -shift, axis=1)
        if lit_from_left:
            moved[:, :shift] = 0
        else:
            moved[:, -shift:] = 0
        edge = np.clip(alpha - moved, 0, 1) * self.rim_strength
        rgb = rgb * (1 - edge) + np.array(self.rim, np.float32) * edge
        out = np.concatenate([np.clip(rgb, 0, 255), a[..., 3:]], axis=-1).astype(np.uint8)
        return Image.fromarray(out, 'RGBA')

    def head_layer(self, pose: Pose, Ds: float) -> Image.Image:
        size = max(8, round(Ds))
        plate = parts.scaled_head(size, 'left' if self.shade_side == 'left' else 'right').copy()
        d = ImageDraw.Draw(plate)
        c = size / 2
        u = size  # 1 D in head-layer pixels

        def P(x, y):
            return (c + x * u, c + y * u)

        t = max(-1.0, min(1.0, pose.turn))
        face_x = 0.17 * t
        spacing = 0.165 * (1 - 0.12 * abs(t))
        eye_y = 0.075
        gx, gy = 0.022 * pose.gaze_x, 0.018 * pose.gaze_y
        ew, eh = 0.050 * (1 + 0.15 * pose.eye_wide), 0.072 * (1 + 0.22 * pose.eye_wide)
        line = max(2, round(0.016 * u))
        for k in (-1, 1):
            far = (k == -1 and t > 0) or (k == 1 and t < 0)
            squeeze = 1 - 0.18 * abs(t) if far else 1.0
            ex, ey = face_x + k * spacing + gx, eye_y + gy
            open_ = max(0.0, min(1.0, pose.eye_open))
            if open_ > 0.22:
                h = eh * open_
                x0, y0 = P(ex - ew * squeeze, ey - h)
                x1, y1 = P(ex + ew * squeeze, ey + h)
                d.ellipse((x0, y0, x1, y1), fill=NAVY)
                hx, hy = P(ex + ew * 0.32 * squeeze, ey - h * 0.45)
                r = 0.017 * u * min(1, open_ * 1.3)
                d.ellipse((hx - r, hy - r, hx + r, hy + r), fill=(255, 255, 255))
            else:  # closed lid: a soft downward arc
                x0, y0 = P(ex - ew * 1.05 * squeeze, ey - 0.02)
                x1, y1 = P(ex + ew * 1.05 * squeeze, ey + 0.035)
                d.arc((x0, y0, x1, y1), 15, 165, fill=NAVY, width=line + 1)
            # Brows: arch, raise, and worry (inner ends up).
            by = eye_y - 0.135 - 0.045 * pose.brow_raise - 0.02 * pose.eye_wide
            bx = face_x + k * (spacing + 0.005)
            pts = []
            for i in range(9):
                v = i / 8  # 0 = inner end, 1 = outer end
                x = bx + k * (-0.045 + 0.10 * v) * squeeze
                y = by - 0.022 * 4 * v * (1 - v) - pose.brow_worry * 0.035 * (1 - v) + pose.brow_worry * 0.008 * v
                pts.append(P(x, y))
            d.line(pts, fill=NAVY, width=line, joint='curve')
        # Mouth.
        mx, my = face_x * 1.15, 0.255
        mw = 0.072 * (1 + 0.25 * max(0, pose.smile)) * (1 - 0.1 * abs(t))
        if pose.mouth_open > 0.06:
            o = pose.mouth_open
            top = [P(mx - mw + 2 * mw * i / 10, my - 0.006 * max(0, -pose.smile)) for i in range(11)]
            depth = 0.03 + 0.085 * o
            bottom = [P(mx + mw * math.cos(math.pi * i / 16) * (0.96 if pose.smile > 0 else 0.8),
                        my + depth * math.sin(math.pi * i / 16) * (1 if pose.smile >= 0 else 0.7)) for i in range(17)]
            shape = top + bottom
            d.polygon(shape, fill=NAVY)
            inner = [((x - P(mx, my)[0]) * 0.80 + P(mx, my)[0], (y - P(mx, my)[1]) * 0.78 + P(mx, my)[1] + 0.006 * u) for x, y in shape]
            d.polygon(inner, fill=(120, 42, 52))
            tx, ty = P(mx, my + depth * 0.62)
            tr = mw * 0.55 * u
            d.ellipse((tx - tr, ty - tr * 0.45, tx + tr, ty + tr * 0.55), fill=TONGUE)
        else:
            curve = []
            for i in range(13):
                v = i / 12
                curve.append(P(mx - mw + 2 * mw * v, my + pose.smile * 0.045 * 4 * v * (1 - v) * (1 if pose.smile > 0 else 0.8)))
            d.line(curve, fill=NAVY, width=line, joint='curve')
        return plate
