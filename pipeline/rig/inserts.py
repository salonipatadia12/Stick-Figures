"""Animate a painted insert scene in code (docs/PLAN.md §7 H).

An insert is a full-frame painting with no characters (`pipeline/inserts.py` makes and keeps them). The script
names 0-4 effects, each optionally on a narration word; `InsertScene.frame(t)` draws one 1080x1920 frame:

- camera: a slow push by default; `push` (stronger, from its word), `pan`, `shake`;
- light and colour: `crossfade` to a changed version of the same place, `grade` ("blue -> red", "warm -> cold"),
  `dim`, `flash`, `sun_path` (a painted-looking sun moving "high -> low", warming as it sinks);
- particles over the picture: `ash`, `snow`, `dust`, `embers` (rising from the brightest orange spot, the crater
  glow, when there is one), `rain`, and `haze` (a pale band drifting across a region: a veil, smog);
- annotation: `label` (a paper tag that pops in) and `arrow` ("from -> to" regions), drawn in the safe area.

Everything is deterministic in t, so any frame renders on its own (the runner renders shots in parallel).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .. import overlays as O
from .actions import back_out, ease

W, H = 1080, 1920
# where a region sits on screen (fractions): particles and the sun use these; labels use LABEL_Y (safe area)
REGION = {'': (0.5, 0.45), 'sky': (0.5, 0.22), 'top': (0.5, 0.15), 'centre': (0.5, 0.45), 'ground': (0.5, 0.6),
          'left': (0.27, 0.4), 'right': (0.73, 0.4), 'peak': (0.5, 0.36), 'horizon': (0.5, 0.42)}
LABEL_Y = {'': 860, 'sky': 520, 'top': 440, 'centre': 860, 'ground': 1010, 'left': 720, 'right': 720, 'peak': 640, 'horizon': 760}
BAND = {'sky': (0.12, 0.30), 'top': (0.05, 0.2), 'horizon': (0.36, 0.48), 'centre': (0.38, 0.52), 'ground': (0.55, 0.7),
        '': (0.14, 0.30), 'peak': (0.2, 0.34), 'left': (0.14, 0.3), 'right': (0.14, 0.3)}
FOCUS = {'': (0, 0), 'centre': (0, 0), 'sky': (0, -1), 'top': (0, -1), 'peak': (0, -0.7), 'horizon': (0, -0.2),
         'ground': (0, 1), 'left': (-1, 0), 'right': (1, 0)}
TINT = {'blue': (95, 150, 235), 'red': (236, 84, 52), 'orange': (246, 140, 58), 'gold': (255, 200, 110), 'warm': (255, 196, 120),
        'cold': (140, 180, 235), 'grey': (150, 152, 162), 'gray': (150, 152, 162), 'night': (40, 52, 96), 'dusk': (150, 90, 120),
        'pink': (236, 150, 160), 'green': (110, 170, 110), 'noon': (200, 225, 255), 'violet': (150, 100, 210),
        'butterscotch': (224, 164, 96), 'mars-dusk': (138, 98, 84)}


@dataclass
class Effect:
    kind: str
    at: float | None          # absolute time of its word, or None for the whole beat
    region: str = ''
    detail: str = ''
    seed: int = 1
    data: dict = field(default_factory=dict)


def _ramp(t, start, dur):
    return 0.0 if t < start else min(1.0, (t - start) / max(1e-3, dur))


def _parse_pair(detail: str, default=('', '')):
    if '->' in detail:
        a, b = (x.strip().lower() for x in detail.split('->', 1))
        return a, b
    return default


# How long each light/colour change takes to build (the ramps below); it must finish SETTLE seconds before the cut.
RAMP = dict(grade=2.2, crossfade=1.4, haze=1.4, dim=1.2, sun_path=1.6, focus=1.0)
SETTLE = 0.6


LANDS = ('label', 'arrow', 'pin', 'cross')   # effects that pop in and must be seen before the cut


def settle_effects(effects: list, t0: float, t1: float) -> list:
    """Effects timed so the viewer sees them land (user review 2026-11-08: the Martian sunset began on "blue-gray",
    1.2 s before the cut, and never got dark): a light or colour change starts early enough to finish SETTLE seconds
    before the cut; labels and arrows land overlays.MIN_HOLD before it, in order. Effects after t1 (the next shot's)
    and whole-beat ones (at None) are left alone. Idempotent."""
    # (a pin carries its text too: a "Burn" pin on a beat's last word showed 0.13 s, Apollo 13 2026-11-10)
    for e in effects:
        if e.at is not None and e.kind in RAMP and e.at <= t1:
            e.at = max(min(e.at, t1 - SETTLE - RAMP[e.kind]), min(e.at, t0 + 0.1))
    info = [e for e in effects if e.kind in LANDS and e.at is not None]
    for e, at in zip(info, O.settle([e.at for e in info], t0, t1)):
        e.at = at
    return effects


class InsertScene:
    def __init__(self, scene: Image.Image, effects: list, t0: float, t1: float, variants: dict | None = None, seed: int = 3):
        self.t0, self.t1 = t0, t1
        self.effects = settle_effects(effects, t0, t1)
        self.plate = scene.convert('RGB')
        self.variants = {k: v.convert('RGB').resize(self.plate.size, Image.LANCZOS) for k, v in (variants or {}).items()}
        self.seed = seed
        self.emitter = self._find_glow()
        for e in effects:
            if e.kind in ('ash', 'snow', 'dust', 'embers', 'rain'):
                e.data['parts'] = self._particles(e)
            if e.kind == 'haze':
                e.data['tex'] = self._haze_texture(e.seed)

    # -- setup ----------------------------------------------------------------------------------------
    def _find_glow(self):
        """The brightest orange spot in the upper part of the picture (a crater, a fire), in screen fractions."""
        a = np.asarray(self.plate.resize((118, 210))).astype(np.int32)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        m = (r > 200) & (g > 80) & (g < 190) & (b < 110) & (r - b > 120)
        m[int(0.75 * m.shape[0]):] = False
        ys, xs = np.nonzero(m)
        if len(ys) < 4:
            return None
        return (float(np.median(xs)) / m.shape[1], float(np.percentile(ys, 30)) / m.shape[0])

    def _particles(self, e: Effect):
        rng = random.Random(e.seed * 97 + len(e.kind))
        n = dict(ash=170, snow=150, dust=70, embers=60, rain=140)[e.kind]
        y0, y1 = (0.0, 1.0) if e.region in ('', 'centre') else BAND.get(e.region, (0.0, 1.0))
        if e.kind in ('ash', 'snow', 'rain'):
            y0, y1 = 0.0, 1.0
        parts = []
        for _ in range(n):
            depth = rng.choice((0.5, 0.75, 1.0, 1.0, 1.4))
            parts.append(dict(x=rng.random() * W, y=(y0 + rng.random() * (y1 - y0)) * H, depth=depth, ph=rng.random() * 6.28,
                              w=rng.uniform(0.6, 1.6), size=rng.uniform(2.0, 5.0) * depth, life=rng.uniform(1.2, 2.4),
                              birth=rng.random() * 2.4, shade=rng.randint(-18, 18), vx=rng.uniform(-30, 30)))
        return parts

    def _haze_texture(self, seed):
        rng = np.random.default_rng(seed)
        small = rng.random((24, 90)).astype(np.float32)
        im = Image.fromarray((small * 255).astype(np.uint8)).resize((W * 2, 260), Image.BICUBIC).filter(ImageFilter.GaussianBlur(18))
        a = np.asarray(im).astype(np.float32) / 255
        ys = np.linspace(-1, 1, a.shape[0])[:, None]
        a = np.clip(a * 1.4 - 0.15, 0, 1) * np.exp(-(ys ** 2) * 2.2)
        return a

    # -- the camera --------------------------------------------------------------------------------------
    def camera(self, t):
        u = ease((t - self.t0) / max(0.1, self.t1 - self.t0))
        zoom, dx, dy = 1.0 + 0.05 * u, 0.0, 0.0
        for e in self.effects:
            if e.kind == 'push':
                q = ease(_ramp(t, e.at, 1.2)) if e.at is not None else u
                zoom = max(zoom, 1.0 + 0.13 * q)
            elif e.kind == 'pan':
                d = -1 if 'left' in e.detail.lower() else 1
                zoom = max(zoom, 1.08)
                dx += d * 60 * (u - 0.5) * 2
            elif e.kind == 'focus':                     # a close-up: push into one region of the painting
                q = ease(_ramp(t, e.at if e.at is not None else self.t0, 1.0))
                z = 1.0 + 0.45 * q
                zoom = max(zoom, z)
                rx, ry = FOCUS.get(e.region, (0.0, 0.0))
                dx += rx * q * (W / 2) * (1 - 1 / z)
                dy += ry * q * (H / 2) * (1 - 1 / z)
            elif e.kind == 'shake' and e.at is not None and 0 <= t - e.at <= 0.5:
                k = (1 - (t - e.at) / 0.5) ** 2
                dx += 16 * k * math.sin(2 * math.pi * 21 * (t - e.at))
                dy += 11 * k * math.sin(2 * math.pi * 27 * (t - e.at) + 1)
        zoom = max(zoom, 1.0 / max(0.5, 1 - 2 * (abs(dx) + 4) / W), 1.0 / max(0.5, 1 - 2 * (abs(dy) + 4) / H))   # a pan never shows past the edge
        return zoom, dx, dy

    def base_frame(self, t):
        img = self.plate
        for e in self.effects:
            if e.kind == 'crossfade' and e.detail in self.variants:
                q = ease(_ramp(t, e.at if e.at is not None else self.t0 + 0.4 * (self.t1 - self.t0), 1.4))
                if q > 0:
                    img = Image.blend(img, self.variants[e.detail], q)
        zoom, dx, dy = self.camera(t)
        pw, ph = img.size
        cw = pw / zoom                                   # the plate is 944x1680, the frame's aspect to 0.1 %
        ch = cw * H / W
        if ch > ph:
            ch = ph / zoom
            cw = ch * W / H
        cx = min(max(pw / 2 + dx * pw / W, cw / 2), pw - cw / 2)
        cy = min(max(ph / 2 + dy * ph / H, ch / 2), ph - ch / 2)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return img.resize((W, H), Image.BICUBIC, box=box).convert('RGBA')

    # -- effects -------------------------------------------------------------------------------------------
    host = None                 # optional callable(frame, t, zoom, dx, dy): the host composited into the scene (script v5)

    def frame(self, t):
        frame = self.base_frame(t)
        arr = None
        for e in self.effects:
            if e.kind in ('grade', 'dim', 'flash'):
                arr = np.asarray(frame.convert('RGB')).astype(np.float32) if arr is None else arr
                arr = self._colour(arr, e, t)
        if arr is not None:
            frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert('RGBA')
        if self.host is not None:       # after the grade: the host takes the scene's light as a capped tint and a rim light,
            self.host(frame, t, *self.camera(t))      # not the full grade, so it stays white (user 2026-11-09)
        for e in self.effects:
            if e.kind == 'sun_path':
                self._sun(frame, e, t)
            elif e.kind == 'haze':
                self._haze(frame, e, t)
        for e in self.effects:
            if e.kind in ('ash', 'snow', 'dust', 'embers', 'rain'):
                self._draw_particles(frame, e, t)
        for e in self.effects:
            if e.kind == 'pin':
                self._pin(frame, e, t)
            elif e.kind == 'cross':
                self._cross(frame, e, t)
        for e in self.effects:
            if e.kind == 'label':
                self._label(frame, e, t)
            elif e.kind == 'arrow':
                self._arrow(frame, e, t)
        return frame

    def _colour(self, arr, e, t):
        whole = e.at is None
        if e.kind == 'grade':
            a_name, b_name = _parse_pair(e.detail, ('', 'warm'))
            q = ease((t - self.t0) / max(0.1, self.t1 - self.t0)) if whole else ease(_ramp(t, e.at, 2.2))
            tint = np.array(TINT.get(b_name, TINT['warm']), np.float32)
            ys = np.linspace(1.0, 0.45, H, dtype=np.float32)[:, None, None]
            lum = arr.mean(-1, keepdims=True)
            coloured = np.clip(lum / 140.0 * tint, 0, 255)
            strength = 0.68 * q * ys
            out = arr * (1 - strength) + coloured * strength
            if b_name in ('cold', 'night', 'grey', 'gray', 'dusk', 'mars-dusk'):
                out *= 1 - (0.22 if b_name == 'mars-dusk' else 0.12) * q
            return out
        if e.kind == 'dim':
            q = ease(_ramp(t, e.at if e.at is not None else self.t0, 1.2))
            return arr * (1 - 0.38 * q)
        if e.kind == 'flash' and e.at is not None and 0 <= t - e.at <= 0.35:
            k = 1 - (t - e.at) / 0.35
            return arr + (255 - arr) * 0.7 * k
        return arr

    def _sun(self, frame, e, t):
        if 'blue' in e.detail.lower():                       # a Martian sunset: a small pale sun with a soft blue glow
            return self._pale_sun(frame, e, t)
        a_name, b_name = _parse_pair(e.detail, ('high', 'low'))
        hi, lo = (0.64, 0.12), (0.40, 0.40)
        p0, p1 = (hi, lo) if a_name != 'low' else (lo, hi)
        start = e.at if e.at is not None else self.t0
        q = ease(min(1.0, max(0.0, (t - start) / max(0.1, self.t1 - start))))
        x = (p0[0] + (p1[0] - p0[0]) * q) * W
        y = (p0[1] + (p1[1] - p0[1]) * q) * H - math.sin(math.pi * q) * 0.03 * H
        low = q if b_name == 'low' else 1 - q
        col = tuple(int(c0 + (c1 - c0) * low) for c0, c1 in zip((255, 250, 228), (255, 112, 52)))
        r = 44 + 18 * low
        glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(glow)
        for k in range(6, 0, -1):
            rr = r * (1 + 0.9 * k)
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=col + (int(16 + 10 * low),))
        glow = glow.filter(ImageFilter.GaussianBlur(28))
        frame.alpha_composite(glow)
        ImageDraw.Draw(frame).ellipse((x - r, y - r, x + r, y + r), fill=col + (255,))

    def _pale_sun(self, frame, e, t):
        q = ease(_ramp(t, e.at if e.at is not None else self.t0, 1.2))
        if q <= 0:
            return
        x, y = 0.62 * W, (0.37 if e.region in ('', 'horizon') else REGION.get(e.region, REGION['horizon'])[1]) * H
        y += (1 - q) * 30                                   # it settles onto the horizon as it appears
        glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(glow)
        for k in range(8, 0, -1):
            rr = 24 * (1 + 0.75 * k)
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(108, 156, 238, int(52 * q)))
        glow = glow.filter(ImageFilter.GaussianBlur(44))
        a = np.asarray(glow.getchannel('A')).astype(np.float32) * 2.0
        ys = np.arange(H, dtype=np.float32)[:, None]
        a *= np.clip(1 - (ys - (y + 30)) / 70, 0, 1)            # the glow stays in the sky, behind the horizon
        glow.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
        frame.alpha_composite(glow)
        ImageDraw.Draw(frame).ellipse((x - 24, y - 24, x + 24, y + 24), fill=(242, 240, 232, int(255 * q)))

    def _haze(self, frame, e, t):
        tex = e.data['tex']
        q = ease(_ramp(t, e.at if e.at is not None else self.t0, 1.4))
        if q <= 0:
            return
        y0, y1 = BAND.get(e.region, BAND['sky'])
        cy = (y0 + y1) / 2 * H
        off = int((t * 26) % W)
        a = tex[:, off:off + W]
        if a.shape[1] < W:
            a = np.concatenate([a, tex[:, :W - a.shape[1]]], 1)
        band = np.zeros((a.shape[0], W, 4), np.uint8)
        band[..., 0], band[..., 1], band[..., 2] = 252, 244, 214
        band[..., 3] = np.clip(a * 190 * q, 0, 255).astype(np.uint8)
        frame.alpha_composite(Image.fromarray(band, 'RGBA'), (0, int(cy - a.shape[0] / 2)))

    def _draw_particles(self, frame, e, t):
        start = e.at if e.at is not None else self.t0
        if t < start:
            return
        fade = min(1.0, (t - start) / 0.6)
        u = t - self.t0
        layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for p in e.data['parts']:
            s = p['size']
            if e.kind in ('ash', 'snow'):
                vy = (55 if e.kind == 'ash' else 70) * p['depth']
                x = (p['x'] + p['vx'] * u * 0.5 + 18 * p['depth'] * math.sin(p['w'] * u + p['ph'])) % W
                y = (p['y'] + vy * u) % H
                c = (128 + p['shade'],) * 3 if e.kind == 'ash' else (246, 248, 255)
                a = int((200 if e.kind == 'ash' else 230) * fade * min(1.0, 0.55 + 0.45 * p['depth']))
                d.ellipse((x - s, y - s * 0.8, x + s, y + s * 0.8), fill=c + (a,))
            elif e.kind == 'rain':
                x = (p['x'] - 120 * u) % W
                y = (p['y'] + 900 * p['depth'] * u) % H
                d.line([(x, y), (x - 6, y + 28 * p['depth'])], fill=(210, 220, 235, int(150 * fade)), width=2)
            elif e.kind == 'dust':
                x = (p['x'] + 14 * math.sin(0.4 * u + p['ph']) + 10 * u) % W
                y = (p['y'] + 10 * math.cos(0.5 * u + p['ph'] * 1.3)) % H
                tw = 0.55 + 0.45 * math.sin(2.2 * u + p['ph'])
                rr = s * 0.7
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 236, 180, int(220 * fade * tw)))
            elif e.kind == 'embers':
                ex, ey = self.emitter or REGION.get(e.region or 'peak', REGION['peak'])
                age = (u + p['birth']) % p['life']
                k = age / p['life']
                x = ex * W + p['vx'] * 2.2 * age + 12 * math.sin(3 * age + p['ph'])
                y = ey * H - 260 * age * p['depth']
                rr = s * 0.8 * (1 - 0.5 * k)
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 150 - int(60 * k), 60, int(240 * fade * (1 - k))))
        if e.kind in ('ash', 'snow', 'embers'):
            layer = layer.filter(ImageFilter.GaussianBlur(0.8))
        frame.alpha_composite(layer)

    def _label(self, frame, e, t):
        if e.at is None:
            at = self.t0 + 0.3
        else:
            at = e.at
        if t < at or not e.detail:
            return
        q = back_out(min(1.0, (t - at) / 0.32), 1.6)
        im = O.tag_label(e.detail, 60)
        if q < 0.999:
            im = im.resize((max(1, int(im.width * q)), max(1, int(im.height * q))), Image.BICUBIC)
        cx = REGION.get(e.region, REGION[''])[0] * W
        cx = min(max(cx, 60 + im.width / 2), 1020 - im.width / 2)
        cy = LABEL_Y.get(e.region, 860)
        xy = (int(cx - im.width / 2), int(cy - im.height / 2))
        frame.alpha_composite(im, xy)
        if q > 0.9:
            O.note_text(e.detail, 60, (xy[0], xy[1], xy[0] + im.width, xy[1] + im.height))

    def _cross(self, frame, e, t):
        """A big red brush X stamped over the region, stroke by stroke (an idea ruled out)."""
        at = e.at if e.at is not None else self.t0 + 0.3
        if t < at:
            return
        cx, cy = REGION.get(e.region, REGION[''])[0] * W, REGION.get(e.region, REGION[''])[1] * H
        r = 0.27 * W
        d = ImageDraw.Draw(frame, 'RGBA')
        for k, (a, b) in enumerate((((cx - r, cy - r), (cx + r, cy + r)), ((cx + r, cy - r), (cx - r, cy + r)))):
            p = min(1.0, max(0.0, (t - at - 0.18 * k) / 0.16))
            if p <= 0:
                continue
            end = (a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p)
            d.line([(a[0] + 6, a[1] + 8), (end[0] + 6, end[1] + 8)], fill=(40, 10, 10, 90), width=58)   # soft shadow
            d.line([a, end], fill=(214, 40, 36, 245), width=52)
            d.line([(a[0] + 4, a[1] - 6), (end[0] + 4, end[1] - 6)], fill=(240, 92, 80, 160), width=14)  # brush highlight

    def _pin(self, frame, e, t):
        """A map pin drops onto the region (with a bounce) and its label pops beside it."""
        at = e.at if e.at is not None else self.t0 + 0.3
        if t < at:
            return
        u = t - at
        x, y = REGION.get(e.region, REGION[''])[0] * W, REGION.get(e.region, REGION[''])[1] * H
        drop = 0.0 if u > 0.35 else (1 - back_out(u / 0.35, 1.2)) * 220
        d = ImageDraw.Draw(frame, 'RGBA')
        d.ellipse((x - 26, y - 8, x + 26, y + 8), fill=(30, 20, 20, 70))
        tip = y - drop
        d.polygon([(x - 26, tip - 60), (x + 26, tip - 60), (x, tip)], fill=(205, 52, 42), outline=(40, 22, 30))
        d.ellipse((x - 34, tip - 112, x + 34, tip - 44), fill=(220, 60, 48), outline=(40, 22, 30), width=5)
        d.ellipse((x - 12, tip - 90, x + 12, tip - 66), fill=(255, 240, 230))
        if e.detail and u > 0.3:
            q = back_out(min(1.0, (u - 0.3) / 0.3), 1.6)
            im = O.tag_label(e.detail, 56)
            if q < 0.999:
                im = im.resize((max(1, int(im.width * q)), max(1, int(im.height * q))), Image.BICUBIC)
            lx = x + 50 if x < W / 2 else x - 50 - im.width
            lx = min(max(lx, 60), 1020 - im.width)
            xy = (int(lx), int(tip - 100 - im.height / 2))
            frame.alpha_composite(im, xy)
            if q > 0.9:
                O.note_text(e.detail, 56, (xy[0], xy[1], xy[0] + im.width, xy[1] + im.height))

    def _arrow(self, frame, e, t):
        if e.at is None:
            return
        a_name, b_name = _parse_pair(e.detail, ('top', 'centre'))
        a = REGION.get(a_name, REGION['top'])
        b = REGION.get(b_name, REGION['centre'])
        O.arrow(frame, t, e.at, (a[0] * W, max(380, a[1] * H)), (b[0] * W, b[1] * H))
