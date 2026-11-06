"""A jointed cut-out character built from The Empty Chair art.

Body space: origin at the hip root, units of one head diameter (D), y down. A Pose holds every
animatable channel; Character.render() turns a pose into layers at screen scale.

Anatomy rules enforced here (and checked by tests/test_rig.py):
- bones keep their lengths (at most 12% stretch); elbows and knees bend outward and down;
- a hand continues its forearm, bent at most WRIST_LIMIT degrees either way, and is only ever
  rotated in the picture plane; palm/back views are separate drawings (pipeline/rig/hands.py);
- a held prop's anchor sits at the hand's grip point, drawn between the hand's back layer
  (thumb behind) and front layer (fingers wrapped over it).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import hands, parts
from .parts import NAVY, TONGUE

SS = 2                      # supersampling for anti-aliased edges
STRETCH = 1.12
UPPER, FORE = 0.62, 0.58    # arm bones, head diameters
THIGH, SHIN = 0.70, 0.95
ARM_W = (0.135, 0.12, 0.112)   # shoulder, elbow, wrist widths
LEG_W = (0.23, 0.215, 0.20)    # hip, knee, ankle
HAND_LEN = 0.30             # wrist to fingertip, head diameters
HIP_X = 0.16
LIMB_LIGHT = (62, 64, 104)  # lit band on the navy limbs


def _torso_frame():
    """Body-space anchors (D units, hip root at 0,0) taken from the art-based torso."""
    sprite, a = parts.torso_art()
    d = a['art_d']
    hip = a['hip']

    def bs(p):
        return ((p[0] - hip[0]) / d, (p[1] - hip[1]) / d)

    sl, sr = bs(a['shoulder_l']), bs(a['shoulder_r'])
    top_mid = ((sl[0] + sr[0]) / 2, (sl[1] + sr[1]) / 2)
    head = (top_mid[0] + 0.05, bs(a['head'])[1])  # recentred over the torso (the art's head sits 3/4 to one side)
    return dict(sprite=sprite, d=d, origin=bs(a['origin']), size=(sprite.width / d, sprite.height / d),
                shoulder_l=sl, shoulder_r=sr, head=head, neck=(head[0], head[1] + 0.41))


@dataclass
class Pose:
    lean: float = 0.0          # degrees, + = top of the body to the right (clockwise)
    crouch: float = 0.0        # D: hips drop, feet stay planted
    lift: float = 0.0          # D: whole body up (hop / bounce)
    stretch: float = 0.0       # torso squash (-) / stretch (+)
    breath: float = 0.0        # 0..1 chest rise
    seated: float = 0.0        # 0 standing .. 1 seated
    knees: float = 1.0         # seated: the side the knees point to (+1 screen-right), a 3/4 seated pose as in the art
    head_tilt: float = 0.0     # degrees relative to the torso
    head_dx: float = 0.0
    head_dy: float = 0.0       # D, + = down (nod)
    turn: float = 0.0          # -1..1: face turns toward screen-left / screen-right
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    eye_open: float = 1.0
    eye_wide: float = 0.0
    brow_raise: float = 0.0
    brow_worry: float = 0.0
    smile: float = 0.45
    mouth_open: float = 0.0
    lh: tuple = (-0.42, -0.10)  # screen-left hand target (the character's RIGHT hand), from the shoulders' centre line
    rh: tuple = (0.42, -0.10)   # screen-right hand target (the character's LEFT hand)
    l_shape: str = 'resting'
    r_shape: str = 'resting'
    l_rot: float = 0.0         # wrist bend relative to the forearm, degrees (clamped to +-WRIST_LIMIT)
    r_rot: float = 0.0
    l_bend: float = 1.0        # elbow: +1 outward/down, -1 inward
    r_bend: float = 1.0
    lf: tuple = (-0.24, 1.40)  # feet, body space (planted)
    rf: tuple = (0.24, 1.40)
    extra: dict = field(default_factory=dict)


NUMERIC = [f.name for f in fields(Pose) if f.type in ('float',)]
POINTS = ['lh', 'rh', 'lf', 'rf']
DISCRETE = ['l_shape', 'r_shape']


def blend(a: Pose, b: Pose, w: float) -> Pose:
    if w <= 0:
        return a
    if w >= 1:
        return b
    out = {n: getattr(a, n) + (getattr(b, n) - getattr(a, n)) * w for n in NUMERIC}
    for n in POINTS:
        (x0, y0), (x1, y1) = getattr(a, n), getattr(b, n)
        out[n] = (x0 + (x1 - x0) * w, y0 + (y1 - y0) * w)
    for n in DISCRETE:
        out[n] = getattr(b if w >= 0.5 else a, n)
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
    """Joint position for fixed bone lengths; `outward` (+1/-1) picks the side of the root->target line."""
    dx, dy = target[0] - root[0], target[1] - root[1]
    dist = math.hypot(dx, dy) or 1e-6
    reach = l1 + l2
    if dist > reach:
        k = min(dist / reach, STRETCH)
        l1, l2 = l1 * k, l2 * k
        if dist > l1 + l2:
            target = (root[0] + dx / dist * (l1 + l2) * 0.9999, root[1] + dy / dist * (l1 + l2) * 0.9999)
            dx, dy = target[0] - root[0], target[1] - root[1]
            dist = math.hypot(dx, dy)
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    ux, uy = dx / dist, dy / dist
    mid = (root[0] + ux * a - uy * h * outward, root[1] + uy * a + ux * h * outward)
    return mid, target, (l1, l2)


def limb(root, target, l1, l2, side, bend, centre_x, droop=1.0):
    """Two-bone solve choosing the joint by body logic: bend=+1 puts it away from the body's centre line
    (and down, by `droop`), so a raised arm keeps its elbow out to the side."""
    best = None
    for o in (1, -1):
        mid, end, lengths = two_bone(root, target, l1, l2, o)
        score = bend * side * (mid[0] - centre_x) + droop * (mid[1] - root[1])
        if best is None or score > best[0]:
            best = (score, mid, end, lengths)
    return best[1], best[2], best[3]


def segment_path(a, b, sag, n=14):
    """A limb segment with a gentle bend (sag as a fraction of its length, + = to the left of a->b)."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1e-6
    nx, ny = -(b[1] - a[1]) / L, (b[0] - a[0]) / L
    c = (mx + nx * sag * L * 2, my + ny * sag * L * 2)
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 2 * np.array(a) + 2 * (1 - t) * t * np.array(c) + t ** 2 * np.array(b)


def tube(d: ImageDraw.ImageDraw, pts: np.ndarray, w0: float, w1: float, fill=NAVY):
    seg = np.diff(pts, axis=0)
    tang = np.vstack([seg[:1], (seg[1:] + seg[:-1]) / 2, seg[-1:]])
    tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-6)
    normal = np.c_[-tang[:, 1], tang[:, 0]]
    widths = np.linspace(w0, w1, len(pts))[:, None] / 2
    left, right = pts + normal * widths, pts - normal * widths
    d.polygon([tuple(p) for p in np.vstack([left, right[::-1]])], fill=fill)
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


def light_layer(layer: Image.Image, light: str = 'left', rim=(255, 190, 112), rim_strength: float = 0.6,
                back_rim: float = 0.22, grade=(1.0, 0.965, 0.90), shadow_side: float = 0.86) -> Image.Image:
    """Key light from the window side, a softer shadow side, warm grade and a warm rim on the lit edge.
    Used for the characters and for props, so everything sits in the same painted light."""
    a = np.asarray(layer).astype(np.float32)
    rgb, alpha = a[..., :3], a[..., 3:] / 255
    w = layer.width
    xs = np.linspace(0, 1, w)[None, :, None]
    ramp = 1.04 - (1.04 - shadow_side) * xs if light == 'left' else shadow_side + (1.04 - shadow_side) * xs
    rgb = rgb * ramp * np.array(grade, np.float32)
    shift = max(2, round(w / 300))
    lit_left = light == 'left'
    moved = np.roll(alpha, shift if lit_left else -shift, axis=1)
    back = np.roll(alpha, -shift if lit_left else shift, axis=1)
    if lit_left:
        moved[:, :shift] = 0
        back[:, -shift:] = 0
    else:
        moved[:, -shift:] = 0
        back[:, :shift] = 0
    edge = np.clip(np.clip(alpha - moved, 0, 1) * rim_strength + np.clip(alpha - back, 0, 1) * back_rim, 0, 1)
    rgb = rgb * (1 - edge) + np.array(rim, np.float32) * edge
    return Image.fromarray(np.concatenate([np.clip(rgb, 0, 255), a[..., 3:]], axis=-1).astype(np.uint8), 'RGBA')


@dataclass
class Held:
    """A prop in a hand: sprite, the sprite point that sits in the grip, its width in D, and its rotation."""
    sprite: Image.Image
    anchor: tuple
    size_d: float
    angle: float = 0.0          # clockwise rotation in the picture (0 = as drawn)
    follow_hand: bool = False   # add the hand's own rotation (the prop turns with the wrist)


GRIPPING = ('grip', 'fist')


@dataclass
class Character:
    D: float                     # head diameter in world pixels
    light: str = 'left'          # side the key light comes from (the window); the art is mirrored to match
    accessory: str = ''          # '' (the lead) or 'scarf' (the friend)
    rim: tuple = (255, 190, 112)
    rim_strength: float = 0.7
    back_rim: float = 0.22       # fainter rim on the shadow edge from the window behind
    grade: tuple = (1.0, 0.975, 0.93)
    shadow_side: float = 0.90    # brightness at the shadow edge relative to the lit edge

    def __post_init__(self):
        self.frame = _torso_frame()
        self.mirror = self.light == 'left'   # the art is lit from the right

    def _m(self, p):
        return (-p[0], p[1]) if self.mirror else p

    # ---- geometry -----------------------------------------------------------------------------
    def joints(self, pose: Pose, base: tuple, scale: float, origin: tuple) -> dict:
        D = self.D * scale
        bx, by = (base[0] - origin[0]) * scale, (base[1] - origin[1]) * scale
        root = (bx, by + (pose.crouch - pose.lift) * D)
        s = pose.stretch
        F = self.frame

        def B(p):
            q = (p[0] * (1 - 0.5 * s), p[1] * (1 + s))
            q = rot(q, pose.lean)
            return (root[0] + q[0] * D, root[1] + q[1] * D)

        j = dict(D=D, root=root, B=B, scale=scale, origin=origin)
        neck = self._m(F['neck'])
        head0 = self._m(F['head'])
        hl = (head0[0] - neck[0] + pose.head_dx, head0[1] - neck[1] + pose.head_dy - 0.012 * pose.breath)
        hc = rot(hl, pose.head_tilt)
        j['head'] = B((neck[0] + hc[0], neck[1] + hc[1]))
        j['neck'] = B(neck)
        j['head_angle'] = pose.lean + pose.head_tilt
        sl, sr = self._m(F['shoulder_l']), self._m(F['shoulder_r'])
        if self.mirror:
            sl, sr = sr, sl     # keep 'l' = screen-left
        mid_x = (sl[0] + sr[0]) / 2     # hand targets are measured from the centre of the shoulders
        j['mid_x'] = mid_x
        centre = B((mid_x, -0.6))[0]
        for side, sign, hand, bend, sh in (('l', -1, pose.lh, pose.l_bend, sl), ('r', 1, pose.rh, pose.r_bend, sr)):
            shp = B((sh[0], sh[1] - 0.014 * pose.breath))
            target = B((hand[0] + mid_x, hand[1]))
            elbow, wrist, _ = limb(shp, target, UPPER * D, FORE * D, sign, bend, centre)
            j[side + '_sh'], j[side + '_el'], j[side + '_wr'] = shp, elbow, wrist
            j[side + '_target'] = target
        seated = max(0.0, min(1.0, pose.seated))
        for side, sign, foot in (('l', -1, pose.lf), ('r', 1, pose.rf)):
            hip = B((sign * HIP_X, 0.02))
            f = (bx + foot[0] * D, by + (foot[1] - pose.lift) * D)
            knee_stand, ankle, _ = limb(hip, f, THIGH * D, SHIN * D, sign, 1.0, root[0], droop=0.0)
            # Seated (as in The Empty Chair): the thighs run sideways under the table, the shins drop to the floor.
            depth = 0.0 if sign * pose.knees > 0 else -0.05          # the far leg sits a little higher
            knee_sit = (hip[0] + pose.knees * 0.58 * D, hip[1] + (0.07 + depth) * D)
            knee = (knee_stand[0] + (knee_sit[0] - knee_stand[0]) * seated, knee_stand[1] + (knee_sit[1] - knee_stand[1]) * seated)
            j[side + '_hip'], j[side + '_kn'], j[side + '_an'] = hip, knee, (f if seated > 0.5 else ankle)
        return j

    # ---- drawing ------------------------------------------------------------------------------
    def render(self, pose: Pose, base: tuple, scale: float = 1.0, origin=(0, 0), held: dict | None = None,
               table_y: float | None = None, size=(1080, 1920)) -> dict:
        """Returns {'body': (layer, xy), 'front': (layer, xy) or None}. With table_y (world px), an arm
        whose hand is above the table top is drawn in 'front' (over the table); otherwise in 'body'."""
        held = held or {}
        for side, h in held.items():
            shape = pose.l_shape if side == 'l' else pose.r_shape
            if h is not None and shape not in GRIPPING:
                raise ValueError(f'{side} hand holds a prop but its pose is {shape!r}; use one of {GRIPPING}')
        j = self.joints(pose, base, scale, origin)
        D = j['D']
        pts = [j['head'], j['l_wr'], j['r_wr'], j['l_an'], j['r_an'], j['l_sh'], j['r_sh'], j['l_el'], j['r_el']]
        reach = 0.75 * D + max([h.size_d * D for h in held.values() if h], default=0)
        x0 = max(int(min(p[0] for p in pts) - reach), -int(D))
        y0 = max(int(min(p[1] for p in pts) - reach), -int(D))
        x1 = min(int(max(p[0] for p in pts) + reach), size[0] + int(D))
        y1 = min(int(max(p[1] for p in pts) + reach), size[1] + int(D))
        W, H = max(1, (x1 - x0) * SS), max(1, (y1 - y0) * SS)

        def L(p):
            return ((p[0] - x0) * SS, (p[1] - y0) * SS)

        Ds = D * SS
        body = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        front = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        table_line = None if table_y is None else (table_y - origin[1]) * scale
        lit = -1 if self.light == 'left' else 1   # x direction toward the light

        # Legs (thigh, knee, shin, foot).
        legs = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(legs)
        leg_order = (('l', -1), ('r', 1)) if pose.knees >= 0 or pose.seated < 0.5 else (('r', 1), ('l', -1))
        for side, sign in leg_order:
            hip, kn, an = L(j[side + '_hip']), L(j[side + '_kn']), L(j[side + '_an'])
            tube(dl, segment_path(hip, kn, 0.0), LEG_W[0] * Ds, LEG_W[1] * Ds)
            tube(dl, segment_path(kn, an, -sign * 0.025), LEG_W[1] * Ds, LEG_W[2] * Ds)
            dl.ellipse((kn[0] - LEG_W[1] * Ds / 2, kn[1] - LEG_W[1] * Ds / 2, kn[0] + LEG_W[1] * Ds / 2, kn[1] + LEG_W[1] * Ds / 2), fill=NAVY)
            foot = parts.foot_sprite()
            fs = Ds / parts.HAND_UNIT * 1.55
            toes = (pose.knees if pose.seated > 0.5 else sign) > 0     # seated feet point where the knees point
            f = foot if toes else foot.transpose(Image.FLIP_LEFT_RIGHT)
            anchor = (0.10 * parts.HAND_UNIT, 0.10 * parts.HAND_UNIT) if toes else (f.width - 0.10 * parts.HAND_UNIT, 0.10 * parts.HAND_UNIT)
            paste_rotated(legs, f, anchor, an, 0, fs)
        self._limb_light(legs, lit)
        body.alpha_composite(legs)
        # Torso from the Empty Chair art.
        F = self.frame
        torso = F['sprite'].transpose(Image.FLIP_LEFT_RIGHT) if self.mirror else F['sprite']
        tw = max(1, round(F['size'][0] * Ds * (1 - 0.5 * pose.stretch)))
        th = max(1, round(F['size'][1] * Ds * (1 + pose.stretch + 0.01 * pose.breath)))
        t_img = torso.resize((tw, th), Image.BICUBIC)
        ox, oy = F['origin']
        if self.mirror:
            ox = -(ox + F['size'][0])
        centre_body = (ox + F['size'][0] / 2, oy + F['size'][1] / 2)
        paste_rotated(body, t_img, (tw / 2, th / 2), L(j['B'](centre_body)), pose.lean)
        # Scarf (the friend), under the head.
        if self.accessory == 'scarf':
            sc, meta = parts.scarf_art()
            if self.mirror:
                sc = sc.transpose(Image.FLIP_LEFT_RIGHT)
            sw = 0.50 * Ds
            k = sw / sc.width
            hx, hy = j['head']
            neck_pt = (hx, hy + 0.36 * D)          # scarf top edge sits 0.36 D below the head centre
            off_x = (-0.26 * D if not self.mirror else -(0.50 - 0.26) * D)
            paste_rotated(body, sc, (0, 0), L((neck_pt[0] + off_x, neck_pt[1])), 0, k)
        # Head and face.
        head = self.head_layer(pose, Ds)
        paste_rotated(body, head, (head.width / 2, head.height / 2), L(j['head']), j['head_angle'])
        # Arms, held props and hands.
        order = ('r', 'l') if pose.turn < 0 else ('l', 'r')
        for side in order:
            target = front if (table_line is not None and j[side + '_wr'][1] < table_line + 0.12 * D) else body
            arm = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            da = ImageDraw.Draw(arm)
            sh, el, wr = L(j[side + '_sh']), L(j[side + '_el']), L(j[side + '_wr'])
            sign = -1 if side == 'l' else 1
            tube(da, segment_path(sh, el, 0.03 * sign), ARM_W[0] * Ds, ARM_W[1] * Ds)
            tube(da, segment_path(el, wr, 0.02 * sign), ARM_W[1] * Ds, ARM_W[2] * Ds)
            self._limb_light(arm, lit)
            target.alpha_composite(arm)
            hp = self.hand_placement(pose, j, side)
            drawing, angle = hp['drawing'], hp['angle']
            hs = hp['scale'] * SS
            grip_pt = L(hp['grip'])
            h = held.get(side)
            if drawing.back.getbbox():
                paste_rotated(target, drawing.back, drawing.wrist, wr, angle, hs)
            if h is not None:
                prop_angle = h.angle + (angle if h.follow_hand else 0.0)
                s = h.size_d * Ds / h.sprite.width
                paste_rotated(target, h.sprite, h.anchor, grip_pt, prop_angle, s)
                j[side + '_held_anchor'] = hp['grip']
                j[side + '_held_angle'] = prop_angle
            paste_rotated(target, drawing.front, drawing.wrist, wr, angle, hs)
        self.last_joints = j
        out = {'body': (self.light_pass(body.reduce(SS)), (x0, y0)), 'front': None}
        if front.getbbox():
            out['front'] = (self.light_pass(front.reduce(SS)), (x0, y0))
        return out

    def hand_placement(self, pose: Pose, j: dict, side: str) -> dict:
        """Where a hand goes: it continues the forearm, bent at most WRIST_LIMIT, rotated only in the picture plane.
        Screen-left is the character's RIGHT hand. Results are in screen px and also stored in `j`."""
        el, wr = j[side + '_el'], j[side + '_wr']
        shape = pose.l_shape if side == 'l' else pose.r_shape
        requested = pose.l_rot if side == 'l' else pose.r_rot
        bend = hands.clamp_bend(requested)
        fore = math.degrees(math.atan2(wr[1] - el[1], wr[0] - el[0]))
        axis = fore + bend
        drawing = hands.drawing(shape, 'R' if side == 'l' else 'L')
        scale = HAND_LEN * j['D'] / hands.S
        angle = axis + 90                         # canonical drawings point up (-90 degrees)
        g = rot(((drawing.grip[0] - drawing.wrist[0]) * scale, (drawing.grip[1] - drawing.wrist[1]) * scale), angle)
        grip = (wr[0] + g[0], wr[1] + g[1])
        j[side + '_bend_requested'], j[side + '_bend'], j[side + '_axis'] = requested, bend, axis
        j[side + '_forearm'] = fore
        j[side + '_grip'] = grip
        j[side + '_grip_radius'] = drawing.grip_radius * scale
        return dict(drawing=drawing, angle=angle, scale=scale, grip=grip, bend=bend, axis=axis)

    def _limb_light(self, layer: Image.Image, lit: int):
        """A lit band on the window side of each navy limb, inside its silhouette (painted modelling)."""
        a = layer.getchannel('A')
        shift = max(2, round(layer.width / 300))
        moved = a.transform(a.size, Image.AFFINE, (1, 0, lit * shift * 2.2, 0, 1, 0))
        band = np.clip(np.asarray(a, np.float32) - np.asarray(moved, np.float32), 0, 255) / 255
        band = np.asarray(Image.fromarray((band * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(shift * 0.8)), np.float32) / 255
        arr = np.asarray(layer).astype(np.float32)
        mask = (arr[..., :3].mean(-1) < 80)[..., None]   # only on navy parts
        col = np.array(LIMB_LIGHT, np.float32)
        arr[..., :3] = np.where(mask, arr[..., :3] * (1 - 0.75 * band[..., None]) + col * 0.75 * band[..., None], arr[..., :3])
        layer.paste(Image.fromarray(arr.astype(np.uint8), 'RGBA'))

    def light_pass(self, layer: Image.Image) -> Image.Image:
        return light_layer(layer, self.light, self.rim, self.rim_strength, self.back_rim, self.grade, self.shadow_side)

    def head_layer(self, pose: Pose, Ds: float) -> Image.Image:
        size = max(8, round(Ds))
        plate = parts.scaled_head(size, 'right' if self.mirror else 'left').copy()
        d = ImageDraw.Draw(plate)
        c = size / 2
        u = size

        def P(x, y):
            return (c + x * u, c + y * u)

        t = max(-1.0, min(1.0, pose.turn))
        face_x = 0.17 * t
        spacing = 0.165 * (1 - 0.12 * abs(t))
        eye_y = 0.075
        gx, gy = 0.022 * pose.gaze_x, 0.018 * pose.gaze_y
        ew, eh = 0.054 * (1 + 0.15 * pose.eye_wide), 0.082 * (1 + 0.22 * pose.eye_wide)   # as in the painted faces
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
            else:
                x0, y0 = P(ex - ew * 1.05 * squeeze, ey - 0.02)
                x1, y1 = P(ex + ew * 1.05 * squeeze, ey + 0.035)
                d.arc((x0, y0, x1, y1), 15, 165, fill=NAVY, width=line + 1)
            by = eye_y - 0.135 - 0.045 * pose.brow_raise - 0.02 * pose.eye_wide
            bx = face_x + k * (spacing + 0.005)
            pts = []
            for i in range(9):
                v = i / 8
                x = bx + k * (-0.045 + 0.10 * v) * squeeze
                y = by - 0.022 * 4 * v * (1 - v) - pose.brow_worry * 0.035 * (1 - v) + pose.brow_worry * 0.008 * v
                pts.append(P(x, y))
            d.line(pts, fill=NAVY, width=line, joint='curve')
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
        return plate
