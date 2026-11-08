"""The staging every beat shares, for any episode: a painted base, its puppets and performances, a camera,
props at their depths and sound cues. Beats subclass this and fill in `build()` (word-keyed actions),
`camera()`, the prop layers and `overlay()` (screen-space text).

Times are absolute seconds on the episode timeline; T maps a beat's words to their times ('one', then
'one#2' for a repeat). World coordinates are the base's plate pixels. No side effects at import.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from pipeline import overlays as O
from pipeline.rig.actions import ease
from pipeline.rig.puppet import Puppet, Stage, load_base
from pipeline.rig.skeleton import Pose, light_layer

W, H = 1080, 1920


def word_times(words: list) -> dict:
    """{'word': t, 'word#2': t, ...} from [(text, t)], lower-case, punctuation stripped."""
    T, seen = {}, {}
    for text, t in words:
        k = ''.join(c for c in text.lower() if c.isalnum() or c in "'$,")
        k = k.strip(',')
        seen[k] = seen.get(k, 0) + 1
        T[k if seen[k] == 1 else f'{k}#{seen[k]}'] = t
    return T


def lerp(a, b, q):
    return (a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q)


class Beat:
    base_name = 'lead-at-table'
    cast = ('lead',)
    face_ok: list = []
    light = 'right'

    def __init__(self, T: dict, t0: float, t1: float, load_art: bool = True):
        self.T, self.t0, self.t1 = T, t0, t1
        self.base = load_base(self.base_name)
        self.stage = Stage(self.base, (W, H))
        self.p = {name: Puppet(self.base, name) for name in self.cast}
        self.events = []                   # (time, sfx name, gain)
        self.load_art = load_art
        self.perfs = self.build()

    # -- helpers ----------------------------------------------------------------------------------
    def rest_pose(self, name: str, **kw) -> Pose:
        p = self.p[name]
        t = 0.85 if p.facing > 0 else -0.85
        base = dict(turn=t, gaze_x=0.3 * p.facing, smile=0.5, lh=p.rest('l'), rh=p.rest('r'), l_shape='resting',
                    r_shape='resting', l_rot=-15, r_rot=20)
        base.update(kw)
        return Pose(**base)

    def pose(self, name: str, t: float) -> Pose:
        return self.perfs[name].pose(t)

    def held(self, name: str, t: float) -> dict:
        return {}

    def holds(self, name: str, t: float) -> set:
        return set()

    def holding(self, t: float) -> dict:
        out = {}
        for name in self.cast:
            for side, h in self.held(name, t).items():
                out[(name, side)] = h
        return out

    def cups_away(self, t: float) -> set:
        return set()

    @staticmethod
    def to_screen(cam, p):
        return ((p[0] - cam[0]) * cam[2], (p[1] - cam[1]) * cam[2])

    def place(self, frame, sprite, cam, xy, world_w, anchor=(0.5, 0.5), angle=0.0, alpha=1.0, sx=1.0, lit=True):
        s = world_w * cam[2] / sprite.width
        im = sprite.resize((max(1, round(sprite.width * s * sx)), max(1, round(sprite.height * s))), Image.BICUBIC)
        if lit:
            im = light_layer(im, self.light, rim_strength=0.3, back_rim=0.1, grade=(1.0, 0.97, 0.90))
        if angle:
            im = im.rotate(-angle, Image.BICUBIC, expand=True)
        if alpha < 1:
            im.putalpha(im.getchannel('A').point(lambda v: int(v * alpha)))
        x, y = self.to_screen(cam, xy)
        xy = (round(x - im.width * anchor[0]), round(y - im.height * anchor[1]))
        frame.alpha_composite(im, xy)
        if 'em' in sprite.info:                       # a prop that carries type: log the size it reads at, and where
            O.note_text(sprite.info.get('label', '?'), sprite.info['em'] * s, (xy[0], xy[1], xy[0] + im.width, xy[1] + im.height))

    def shadow(self, frame, cam, xy, w, h, strength=0.5):
        x, y = self.to_screen(cam, xy)
        ww, hh = max(2, int(w * cam[2])), max(2, int(h * cam[2]))
        m = Image.new('L', (ww * 2, hh * 3), 0)
        ImageDraw.Draw(m).ellipse((ww * 0.5, hh, ww * 1.5, hh * 2), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(2, hh * 0.35)))
        dark = Image.new('RGBA', m.size, (70, 36, 16, 0))
        dark.putalpha(m.point(lambda v: int(v * strength)))
        frame.alpha_composite(dark, (int(x - m.width / 2), int(y - m.height / 2)))

    def prop(self, frame, cam, name: str, xy=None, width=None, angle=0.0, dx=0.0, dy=0.0, anchor=(0.5, 1.0)):
        """A prop cut from a base (`base.props`), standing at `xy` (plate px, its foot) at `width` D."""
        sprite = PROPS[name] if name in PROPS else self.base.props[name][0]
        place = self.base.meta.get('places', {}).get(name, {})
        xy = xy or tuple(place.get('xy', (0, 0)))
        D = self.p[self.cast[0]].D
        self.place(frame, sprite, cam, (xy[0] + dx, xy[1] + dy), (width or place.get('width', 0.3)) * D, anchor=anchor, angle=angle)

    def camera(self, t):
        return self.stage.camera(470, 0, 900)

    def wall(self, t):
        return None

    def on_table(self, t):
        return None

    def flying(self, t):
        return None

    def overlay(self, frame, t):
        """On-screen text and graphics (screen space), drawn after everything else."""

    def frame(self, t: float) -> Image.Image:
        cam = self.camera(t)
        names = list(self.cast)
        frame = self.stage.compose(cam, [self.p[n] for n in names], [self.pose(n, t) for n in names],
                                   [self.held(n, t) for n in names], wall=self.wall(t), on_table=self.on_table(t),
                                   flying=self.flying(t), cups_away=self.cups_away(t))
        self.overlay(frame, t)
        return frame


PROPS: dict = {}            # shared prop sprites by name (the brass clock every bedroom base uses), set by the episode


def punch(t, t0, amount=0.1, dur=0.18):
    """A camera punch-in: 0 -> amount (fraction of the width) eased, starting at t0."""
    return amount * (1 - (1 - ease((t - t0) / dur)) ** 2) if t >= t0 else 0.0


def shake(t, t0, amp=1.0, dur=0.35, freq=22.0):
    """A decaying camera shake offset (x, y) in plate px, starting at t0."""
    u = t - t0
    if u < 0 or u > dur:
        return 0.0, 0.0
    k = (1 - u / dur) ** 2 * amp
    return 14 * k * math.sin(2 * math.pi * freq * u), 9 * k * math.sin(2 * math.pi * freq * 1.3 * u + 1.0)
