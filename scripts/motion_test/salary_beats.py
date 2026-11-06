"""Salary episode, beats 3-9, on the painted base poses.

Each beat is a scene: word-keyed actions for its characters, props at their depths, its on-screen text
and the sound effects it needs. Times are absolute seconds on the episode timeline; T maps a beat's
words to their times ('one', then 'one#2' for a repeat). World coordinates are the base's plate pixels.
No side effects at import: tests build the same performances from word times.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from functools import lru_cache
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFilter

from pipeline import overlays as O
from pipeline.rig import props
from pipeline.rig.actions import Action, Idle, Performance, back_out, ease, ease_out, keys
from pipeline.rig.puppet import Puppet, Stage, load_base
from pipeline.rig.skeleton import Held, Pose, light_layer

import scene_salary as S

W, H = S.W, S.H
JAR_X, JAR_FLOOR, JAR_ASPECT = S.JAR_X, S.JAR_FLOOR, S.JAR_ASPECT


@lru_cache(maxsize=1)
def art():
    """The props every beat shares (painted AI props where they worked, code-drawn ones with a painted finish)."""
    A = S.ASSETS
    jar = props.clean_alpha(props.glass(props.key_neutral(str(A / S.CHOICES['prop-jar']), holes=False)))
    jar = jar.resize((jar.width, round(jar.width * JAR_ASPECT)), Image.LANCZOS)
    return SimpleNamespace(coin=props.key_out(str(A / 'prop-coin-s3.png')), jar=jar, phone=O.phone(300),
                           takeout=O.takeout_box(300))


_piles = {}


def pile(level: float):
    key = round(max(0.0, min(1.0, level)) * 40) / 40
    if key not in _piles:
        a = art()
        _piles[key] = props.coin_pile(a.coin, a.jar, key)
    return _piles[key]


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
    """Shared staging: a base, its puppets and performances, camera, props and sound cues."""
    base_name = 'lead-at-table'
    cast = ('lead',)
    face_ok: list = []

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
        """Which of `name`'s hands hold a prop at t (no art needed; the tests check those hands grip)."""
        return set()

    def holding(self, t: float) -> dict:
        """{(name, side): what} for the anatomy tests."""
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
            im = light_layer(im, 'right', rim_strength=0.3, back_rim=0.1, grade=(1.0, 0.97, 0.90))
        if angle:
            im = im.rotate(-angle, Image.BICUBIC, expand=True)
        if alpha < 1:
            im.putalpha(im.getchannel('A').point(lambda v: int(v * alpha)))
        x, y = self.to_screen(cam, xy)
        frame.alpha_composite(im, (round(x - im.width * anchor[0]), round(y - im.height * anchor[1])))

    def shadow(self, frame, cam, xy, w, h, strength=0.5):
        x, y = self.to_screen(cam, xy)
        ww, hh = max(2, int(w * cam[2])), max(2, int(h * cam[2]))
        m = Image.new('L', (ww * 2, hh * 3), 0)
        ImageDraw.Draw(m).ellipse((ww * 0.5, hh, ww * 1.5, hh * 2), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(2, hh * 0.35)))
        dark = Image.new('RGBA', m.size, (70, 36, 16, 0))
        dark.putalpha(m.point(lambda v: int(v * strength)))
        frame.alpha_composite(dark, (int(x - m.width / 2), int(y - m.height / 2)))

    def jar(self, frame, cam, x, floor, width, level, shadow=True):
        a = art()
        if shadow:
            self.shadow(frame, cam, (x + 0.05 * width, floor + 2), width, 0.1 * width, 0.5)
        self.place(frame, pile(level), cam, (x, floor), width, anchor=(0.5, 1.0))
        self.place(frame, a.jar, cam, (x, floor), width, anchor=(0.5, 1.0))

    def hop(self, frame, cam, t, t0, start, vx, vy, floor, size, fade=0.35, g=2400.0):
        """A coin thrown up and out of a jar, falling past `floor` and fading; constant size."""
        u = t - t0
        if u < 0:
            return
        x, y = start[0] + vx * u, start[1] + vy * u + 0.5 * g * u * u
        if y > floor + 200:
            return
        a = 1 - ease((y - floor) / 200) if y > floor else 1.0
        self.place(frame, art().coin, cam, (x, y), size, angle=u * 540, alpha=a)

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


# ---- b03 ----------------------------------------------------------------------------------------------
class B03(Beat):
    """'Then rent and bills take their bite in the first few days.' Coins hop out of the jar while a receipt
    prints over the table edge (RENT, BILLS); an arrow lands on 'bite'; the lead watches, worried."""
    LINES = (('LEFT', '$450'), ('BILLS', '-$250'), ('RENT', '-$800'))       # right to left: RENT prints first

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=0.95, gaze_x=0.7, gaze_y=0.5, smile=0.2)

        def watch(t, p):
            worry = ease((t - T['rent']) / 0.4)
            flinch = keys(t - T['bite'], [(-0.1, 0), (0.0, 0), (0.08, 1.0), (0.4, 0.3)], ease_out)
            shake = math.sin(2 * math.pi * (t - T['first']) * 3.0) * math.exp(-max(0.0, t - T['first']) * 2.5) if t > T['first'] else 0
            return replace(p, smile=0.1 - 0.8 * worry, brow_worry=worry, eye_wide=0.5 * flinch,
                           lean=-2.5 * flinch, head_tilt=-4 * flinch + 5 * shake, gaze_y=0.5 + 0.3 * worry,
                           lh=B((260, 900)), rh=B((404, 872)))
        self.level = lambda t: 1.0 - 0.70 * ease((t - T['rent']) / max(0.5, T['days'] + 0.3 - T['rent']))
        rng = random.Random(3)
        self.hops = [(T['rent'] + 0.05 + k * (T['days'] + 0.2 - T['rent']) / 9, rng.uniform(-380, -220), rng.uniform(-620, -480))
                     for k in range(10)]
        for t0, _, _ in self.hops:
            self.events.append((t0 + 0.55, 'coin', 0.5))
        self.events += [(T['rent'], 'paper', 0.9), (T['bills'], 'paper', 0.8), (T['bite'], 'thud', 0.55)]
        return {'lead': Performance(base, Idle(seed=12), [Action(self.t0 - 0.1, self.t1, watch, 0.01, 0.1, 'watch the jar, worried')])}

    def camera(self, t):
        q = ease((t - self.t0) / (self.t1 - self.t0))
        return self.stage.camera(440, 880, 740 - 40 * q)

    def receipt_len(self, t):
        T = self.T
        return 0.06 + 0.94 * ease_out((t - T['rent'] + 0.05) / (T['days'] + 0.4 - T['rent']))

    def on_table(self, t):
        def draw(frame, cam):
            D = self.p['lead'].D
            self.jar(frame, cam, JAR_X, JAR_FLOOR, 0.56 * D, self.level(t))
            # the receipt unrolls from the jar across the tabletop, toward the lead: RENT, BILLS, LEFT
            strip = O.receipt_strip(self.LINES, 150, 64)
            L = max(6, int(strip.width * self.receipt_len(t)))
            part = strip.crop((strip.width - L, 0, strip.width, strip.height))
            flat = part.resize((part.width, int(part.height * 0.62)), Image.BICUBIC)
            self.place(frame, flat, cam, (JAR_X - 40, 1000), L * 0.9, anchor=(1.0, 0.5), lit=True)
            for t0, vx, vy in self.hops:
                self.hop(frame, cam, t, t0, (JAR_X, JAR_FLOOR - 0.56 * D * JAR_ASPECT + 20), vx, vy, 1000, 0.12 * D)
        return draw

    def overlay(self, frame, t):
        cam = self.camera(t)
        a = self.to_screen(cam, (JAR_X - 30, 790))
        b = self.to_screen(cam, (JAR_X - 260, 985))
        O.arrow(frame, t, self.T['bite'] - 0.05, a, b, bend=-0.35)


# ---- b04 ----------------------------------------------------------------------------------------------
class B04(Beat):
    """'Next come the small ones. Subscriptions. Coffee. Delivery. Each one feels like nothing.'
    Phone in hand, a tap per subscription, a sip of the coffee, the delivery box slides in; a coin hops out
    of the jar with each; then an easy shrug. The checklist ticks each item with its price."""

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=0.5, gaze_x=-0.2, gaze_y=0.4, smile=0.6)
        phone_hold = B((205, 790))
        cup_xy = (148, 900)                     # the cup on its saucer
        sip = (392, 668)

        def phone(t, p):
            tap = sum(math.exp(-((t - (T['subscriptions'] + d)) / 0.06) ** 2) for d in (0.0, 0.25))
            return replace(p, lh=phone_hold, l_shape='grip', l_rot=10, rh=lerp(B((330, 840)), B((236, 772)), min(1, tap)),
                           r_shape='point', r_rot=-30, turn=0.3, gaze_x=-0.7, gaze_y=0.9, smile=0.65, lean=1.5, head_tilt=-5)

        def coffee(t, p):
            u = t - (T['coffee'] - 0.3)
            reach = ease(u / 0.25)
            lift = ease((u - 0.3) / 0.3) * (1 - ease((u - 0.85) / 0.3))
            hand = lerp(lerp(lead.rest('r'), B(cup_xy), reach), B(sip), lift)
            return replace(p, rh=hand, r_shape='grip', r_rot=-10, lh=phone_hold, l_shape='grip', l_rot=10,
                           turn=0.9, gaze_x=0.2, gaze_y=0.3 * (1 - lift), eye_open=1 - 0.75 * lift, smile=0.7, head_tilt=-4 * lift)

        def delivery(t, p):
            return replace(p, lh=phone_hold, l_shape='grip', rh=lead.rest('r'), r_shape='resting', turn=1.0, gaze_x=0.9,
                           gaze_y=0.4, eye_wide=0.3, smile=0.75)

        def shrug(t, p):
            q = back_out((t - T['each']) / 0.35)
            return replace(p, lh=B((110, 770)), rh=B((470, 760)), l_shape='open_palm', r_shape='open_palm', l_rot=-55, r_rot=55,
                           extra={'l_droop': 0.8, 'r_droop': 0.8}, lift=0.03 * q, lean=-2.0 * q, head_tilt=8 * q, turn=0.4,
                           gaze_x=0.0, smile=0.9, eye_open=0.35 + 0.65 * (1 - q), brow_raise=0.4)

        acts = [Action(self.t0 - 0.1, T['coffee'] - 0.35, phone, 0.01, 0.15, 'tap the phone'),
                Action(T['coffee'] - 0.35, T['delivery'] + 0.55, coffee, 0.12, 0.2, 'sip the coffee'),
                Action(T['delivery'] + 0.5, T['each'] - 0.1, delivery, 0.15, 0.15, 'the box arrives'),
                Action(T['each'] - 0.1, self.t1, shrug, 0.2, 0.2, 'shrug: feels like nothing')]
        self.leaks = [T['subscriptions'] + 0.1, T['coffee'] + 0.1, T['delivery'] + 0.1, T['nothing'] - 0.2, T['nothing'] + 0.15]
        for x in (0.0, 0.25):
            self.events.append((T['subscriptions'] + x, 'tap', 0.8))
        self.events += [(T['coffee'] - 0.05, 'clink', 0.25), (T['delivery'], 'slide', 0.9)]
        self.events += [(t + 0.55, 'coin', 0.5) for t in self.leaks]
        self.events += [(T['subscriptions'], 'tick', 0.7), (T['coffee'], 'tick', 0.7), (T['delivery'], 'tick', 0.7)]
        self.cup_window = (T['coffee'] - 0.12, T['delivery'] + 0.62)
        return {'lead': Performance(base, Idle(seed=13), acts)}

    def holds(self, name, t):
        out = {'l'} if t < self.T['each'] - 0.1 else set()
        return out | ({'r'} if self.cup_window[0] <= t < self.cup_window[1] else set())

    def held(self, name, t):
        out = {}
        if t < self.T['each'] - 0.1:
            ph = art().phone
            out['l'] = Held(ph, (ph.width * 0.5, ph.height * 0.80), 0.20, angle=-8, follow_hand=False)
        if self.cup_window[0] <= t < self.cup_window[1]:
            cup = self.base.cups[0][0]
            out['r'] = Held(cup, (cup.width * 0.78, cup.height * 0.45), cup.width / self.p['lead'].D)
        return out

    def cups_away(self, t):
        return {0} if self.cup_window[0] <= t < self.cup_window[1] else set()

    def camera(self, t):
        return self.stage.camera(440, 0, 860)

    def on_table(self, t):
        T = self.T
        rng = random.Random(5)
        vel = [(rng.uniform(250, 380), rng.uniform(-560, -420)) for _ in self.leaks]

        def draw(frame, cam):
            D = self.p['lead'].D
            level = 0.30 - 0.03 * sum(1 for x in self.leaks if t >= x)
            self.jar(frame, cam, JAR_X, JAR_FLOOR, 0.56 * D, level)
            u = t - T['delivery']
            if u > -0.05:
                x = 640 + 220 * (1 - ease_out(u / 0.45))
                self.shadow(frame, cam, (x + 6, 1012), 0.62 * D, 14, 0.45)
                self.place(frame, art().takeout, cam, (x, 1016), 0.6 * D, anchor=(0.5, 1.0))
            if t >= T['each'] - 0.1:               # the phone back on the table
                self.place(frame, art().phone, cam, (230, 1000), 0.18 * D, anchor=(0.5, 1.0), angle=78)
            for t0, (vx, vy) in zip(self.leaks, vel):
                self.hop(frame, cam, t, t0, (JAR_X + 30, JAR_FLOOR - 0.56 * D * JAR_ASPECT + 30), vx, vy, 1060, 0.12 * D)
        return draw

    def overlay(self, frame, t):
        T = self.T
        O.checklist(frame, t, [('Subscriptions', '-$15', T['subscriptions']), ('Coffee', '-$5', T['coffee']),
                               ('Delivery', '-$22', T['delivery'])], T['small'] - 0.1)


# ---- b05 ----------------------------------------------------------------------------------------------
class B05(Beat):
    """'One small leak is nothing. Fifty small leaks empty the jar.' An insert on the jar alone: one hairline
    crack and one coin, then cracks everywhere, coins pouring out, a jolt on 'empty'."""
    cast = ()

    def build(self):
        T = self.T
        rng = random.Random(7)
        self.cracks = []                        # (start time, points in jar-relative units)
        def crack_path(seed):
            r = random.Random(seed)
            x, y = r.uniform(0.18, 0.82), r.uniform(0.38, 0.88)
            pts = [(x, y)]
            ang = r.uniform(0, 2 * math.pi)
            for _ in range(7):
                ang += r.uniform(-0.9, 0.9)
                x += 0.06 * math.cos(ang)
                y += 0.05 * math.sin(ang)
                pts.append((x, y))
            return pts
        self.cracks.append((T['leak'], crack_path(1)))
        span = T['leaks'] + 0.3 - T['fifty']
        for k in range(11):
            self.cracks.append((T['fifty'] + span * k / 10 + rng.uniform(-0.05, 0.05), crack_path(10 + k)))
        self.drips = [(T['nothing'] - 0.15, self.cracks[0][1][0])]
        for k, (t0, pts) in enumerate(self.cracks[1:]):
            for j in range(3):
                self.drips.append((t0 + 0.2 + 0.28 * j + rng.uniform(0, 0.1), pts[0]))
        for j in range(14):                     # the jolt on 'empty' shakes out the rest
            c = self.cracks[1 + j % 11][1]
            self.drips.append((T['empty'] + 0.02 + 0.03 * j, c[0]))
        self.level = lambda t: 0.30 - 0.02 * ease((t - T['nothing']) / 0.3) - 0.25 * ease((t - T['fifty']) / (T['jar'] + 0.4 - T['fifty']))
        self.events += [(t0, 'crack', 0.6 if i == 0 else 0.35) for i, (t0, _) in enumerate(self.cracks)]
        self.events += [(t0 + 0.4, 'coin', 0.35) for t0, _ in self.drips[::2]]
        self.events += [(T['empty'], 'thud', 0.9), (T['fifty'], 'pop', 0.6)]
        return {}

    def __init__(self, T, t0, t1, load_art=True):
        super().__init__(T, t0, t1, load_art)
        from dataclasses import replace as dc_replace
        empty = Image.open(S.ROOT / 'styles/cozy/bases' / self.base_name / 'empty.png').convert('RGB')
        soft = empty.filter(ImageFilter.GaussianBlur(2.2))
        self.stage = Stage(dc_replace(self.base, plate=soft, bodies={}, occluders={}), (W, H))

    def camera(self, t):
        T = self.T
        x0, y0, s = self.stage.camera(JAR_X, 840, 430)
        u = t - T['empty']
        if 0 <= u < 0.5:
            jolt = math.exp(-u * 9) * math.sin(u * 60)
            x0 += 10 * jolt
            y0 += 6 * math.cos(u * 47) * math.exp(-u * 9)
        return (x0, y0, s)

    def on_table(self, t):
        def draw(frame, cam):
            D = 288.5
            jw, jh = 0.56 * D, 0.56 * D * JAR_ASPECT
            left, top = JAR_X - jw / 2, JAR_FLOOR - jh
            self.jar(frame, cam, JAR_X, JAR_FLOOR, jw, self.level(t))
            layer = Image.new('RGBA', frame.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            for t0, pts in self.cracks:
                p = ease_out((t - t0) / 0.3)
                if p <= 0:
                    continue
                n = max(2, int(len(pts) * p))
                scr = [self.to_screen(cam, (left + x * jw, top + y * jh)) for x, y in pts[:n]]
                d.line([(x + 2, y + 2) for x, y in scr], fill=(60, 50, 50, 120), width=4, joint='curve')
                d.line(scr, fill=(255, 255, 255, 230), width=3, joint='curve')
            frame.alpha_composite(layer)
            for k, (t0, (x, y)) in enumerate(self.drips):
                u = t - t0
                if u < 0:
                    continue
                sx, sy = left + x * jw, top + y * jh
                fy = sy + 0.5 * 2200 * u * u
                land = JAR_FLOOR + 6 + 10 * ((k * 37) % 5)
                drift = ((k * 53) % 9 - 4) * 9
                if fy >= land:
                    fy = land
                self.place(frame, art().coin, cam, (sx + drift * min(1, u * 3), fy), 0.075 * D, angle=0 if fy >= land else u * 600,
                           sx=1.0 if fy < land else 1.0)
        return draw

    def overlay(self, frame, t):
        O.slam(frame, O.stacked(('Small leaks', 'add up'), (104, 104)), t, self.T['fifty'])


# ---- b06 ----------------------------------------------------------------------------------------------
class B06Friend(Beat):
    """'Here's the catch.' The friend leans in and lifts a finger."""
    base_name = 'friend-at-table'
    cast = ('friend',)

    def build(self):
        T, fr = self.T, self.p['friend']
        B = fr.body
        base = self.rest_pose('friend', smile=0.7, gaze_x=-0.3)

        def catch(t, p):
            q = back_out((t - T["here's"] + 0.1) / 0.35)
            return replace(p, lh=B((520, 730)), l_shape='point', l_rot=-25, extra={'l_droop': 1.6}, lean=-3.0 * q,
                           turn=-0.45, gaze_x=-0.3, brow_raise=0.8 * q, smile=0.85, mouth_open=0.2, head_tilt=-5 * q)
        self.events.append((T["here's"], 'pop', 0.4))
        return {'friend': Performance(base, Idle(seed=21), [Action(self.t0 - 0.1, self.t1, catch, 0.05, 0.1, 'lean in, one finger up')])}

    def camera(self, t):
        q = ease((t - self.t0) / max(0.1, self.t1 - self.t0))
        return self.stage.camera(640, 800, 760 - 30 * q)


class B06Lead(Beat):
    """'You never see the total, because it's spread across every day until the next payday.'
    The standing lead watches a receipt unroll along the table, day by day; hands up on 'total', then a hand
    to the forehead; the camera pans along the paper to the circled NEXT PAYDAY."""
    base_name = 'lead-standing'
    face_ok = ['hand to forehead']
    DAYS = (('MON', '-$12'), ('TUE', '-$9'), ('WED', '-$14'), ('THU', '-$7'), ('FRI', '-$18'), ('SAT', '-$25'),
            ('PAYDAY', 'next'))

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=-0.7, gaze_x=-0.4, gaze_y=0.6, smile=0.3)

        def surprised(t, p):
            q = back_out((t - T['total']) / 0.3)
            return replace(p, lh=B((470, 560)), rh=B((790, 560)), l_shape='open_palm', r_shape='open_palm', l_rot=-20, r_rot=20,
                           extra={'l_droop': 0.6, 'r_droop': 0.6}, turn=-0.2, gaze_x=0.0, gaze_y=0.4, eye_wide=0.9 * q,
                           brow_raise=0.9, mouth_open=0.6 * q, smile=-0.2, head_dy=-0.02 * q)

        def forehead(t, p):
            follow = ease((t - T['spread']) / (T['payday'] - T['spread']))
            return replace(p, lh=B((560, 410)), l_shape='back', l_rot=55, extra={'l_front': 1.0, 'l_droop': 0.0},
                           rh=lead.rest('r'), r_shape='resting', turn=-0.3 + 0.9 * follow, gaze_x=0.2 + 0.7 * follow, gaze_y=0.6,
                           eye_wide=0.6, brow_worry=0.9, smile=-0.4, head_tilt=-4 + 6 * follow)
        acts = [Action(T['total'] - 0.12, T['because'], surprised, 0.12, 0.2, 'hands up'),
                Action(T['because'] - 0.05, self.t1, forehead, 0.25, 0.1, 'hand to forehead')]
        self.events += [(T['total'], 'pop', 0.5), (T['spread'], 'paper', 1.0), (T['every'], 'paper', 0.8),
                        (T['payday'], 'sparkle', 0.8)]
        return {'lead': Performance(base, Idle(seed=22), acts)}

    def length(self, t):
        T = self.T
        return 0.10 + 0.90 * ease_out((t - T['spread'] + 0.1) / (T['payday'] + 0.3 - T['spread'])) if t > T['spread'] - 0.1 else 0.10

    def camera(self, t):
        T = self.T
        q = ease((t - T['spread']) / (T['payday'] + 0.4 - T['spread']))
        return self.stage.camera(450 + 100 * q, 880, 780)

    STRIP_X, STRIP_Y, ENTRY = 34.0, 968.0, 92.0       # where the strip starts on the tabletop, one entry's width

    def on_table(self, t):
        def draw(frame, cam):
            # The receipt lies on the tabletop, unrolling to the right one day at a time.
            strip = O.receipt_strip(self.DAYS, 150, 96)
            L = max(6, int(strip.width * self.length(t)))
            part = strip.crop((0, 0, L, strip.height))
            flat = part.resize((part.width, int(part.height * 0.80)), Image.BICUBIC)
            k = self.ENTRY / 150
            self.place(frame, flat, cam, (self.STRIP_X, self.STRIP_Y), L * k, anchor=(0.0, 0.5), lit=True)
        return draw

    def overlay(self, frame, t):
        cam = self.camera(t)
        k = self.ENTRY / 150
        x = self.STRIP_X + (12 + 150 * (len(self.DAYS) - 0.5)) * k
        O.circle(frame, t, self.T['payday'] - 0.05, self.to_screen(cam, (x, self.STRIP_Y)), 0.55 * self.ENTRY * cam[2], 34 * cam[2])


# ---- b07 ----------------------------------------------------------------------------------------------
class B07(Beat):
    """'You glance at the balance, not the pattern. A full account whispers, go ahead.' The jar looks full;
    the camera punches in on 'balance' and finds coins trickling from a small hole at its side. The jar
    whispers; the lead leans back and waves it off."""

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=0.9, gaze_x=0.6, gaze_y=0.4, smile=0.55)

        def glance(t, p):
            g = math.exp(-((t - T['glance'] - 0.15) / 0.25) ** 2)
            return replace(p, gaze_x=0.8, gaze_y=0.5 * g, smile=0.6 + 0.2 * g, brow_raise=0.3 * g)

        def go_ahead(t, p):
            q = ease((t - T['go'] + 0.1) / 0.35)
            wave = math.sin(2 * math.pi * (t - T['go']) * 2.2) * ease((t - T['go']) / 0.2)
            return replace(p, lean=-3.0 * q, head_tilt=-6 * q, eye_open=1 - 0.55 * q, smile=0.95, brow_raise=0.2,
                           rh=lerp(lead.rest('r'), B((452, 742)), q), r_shape='open_palm', r_rot=35 + 25 * wave,
                           extra={'r_droop': 1.0}, turn=0.7, gaze_x=0.4, gaze_y=0.2)
        acts = [Action(self.t0 - 0.1, T['whispers'] - 0.2, glance, 0.01, 0.2, 'glance at the jar'),
                Action(T['go'] - 0.25, self.t1, go_ahead, 0.25, 0.1, 'lean back, wave: go ahead')]
        self.drips = [T['not'] + 0.08 * k for k in range(24)]
        self.events += [(T['balance'] - 0.05, 'whoosh', 0.8), (T['not'], 'trickle', 0.4), (T['whispers'], 'pop', 0.4)]
        return {'lead': Performance(base, Idle(seed=14), acts)}

    def camera(self, t):
        punch = ease_out((t - self.T['balance'] + 0.05) / 0.2)
        return self.stage.camera(440 + 20 * punch, 0 + 800 * punch, 860 - 240 * punch)

    def on_table(self, t):
        def draw(frame, cam):
            D = self.p['lead'].D
            jw, jh = 0.56 * D, 0.56 * D * JAR_ASPECT
            self.jar(frame, cam, JAR_X, JAR_FLOOR, jw, 0.95)
            hole = (JAR_X + 0.40 * jw, JAR_FLOOR - 0.16 * jh)
            if t > self.T['not'] - 0.1:
                x, y = self.to_screen(cam, hole)
                r = 6 * cam[2]
                layer = Image.new('RGBA', frame.size, (0, 0, 0, 0))
                ImageDraw.Draw(layer).ellipse((x - r, y - r * 0.8, x + r, y + r * 0.8), fill=(40, 30, 30, 200), outline=(255, 255, 255, 200), width=2)
                frame.alpha_composite(layer)
            for k, t0 in enumerate(self.drips):
                u = t - t0
                if 0 <= u < 0.6:
                    self.place(frame, art().coin, cam, (hole[0] + 60 * u, hole[1] + 0.5 * 2000 * u * u), 0.08 * D, angle=u * 400,
                               alpha=1 - ease((u - 0.4) / 0.2))
        return draw

    def overlay(self, frame, t):
        cam = self.camera(t)
        tip = self.to_screen(cam, (JAR_X + 40, JAR_FLOOR - 0.56 * 288.5 * JAR_ASPECT + 10))
        O.bubble(frame, t, self.T['whispers'], 'go ahead...', tip, offset=(10, -40))


# ---- b08 ----------------------------------------------------------------------------------------------
class B08(Beat):
    """'So split it on payday. Bills in one jar, then one spending jar for each week. When a jar's empty,
    you see it early.' Two small jars slide in; the lead scoops coins from the big jar into each; tags and
    ticks land on the words; week one empties and gets circled; the lead points: early."""
    SMALL = 0.38
    J1, J2 = 404.0, 296.0                      # the two small jars' x

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=0.95, gaze_x=0.6, gaze_y=0.5, smile=0.6)
        D = lead.D
        big_top = JAR_FLOOR - 0.56 * D * JAR_ASPECT
        small_top = JAR_FLOOR + 8 - self.SMALL * D * JAR_ASPECT

        def bills(t, p):
            return replace(p, rh=B((JAR_X - 70, big_top + 60)), r_shape='point', r_rot=-10, turn=1.0, gaze_x=0.8, gaze_y=0.4,
                           smile=0.7, brow_raise=0.3)

        def scoop(t, p):
            # big jar -> week one (on 'spending'), big jar -> week two (on 'week')
            path = [(T['then'] - 0.15, (JAR_X, big_top - 20)), (T['spending'] - 0.05, (self.J1 + 10, small_top - 50)),
                    (T['spending'] + 0.25, (self.J1 + 10, small_top - 50)), (T['for'], (JAR_X, big_top - 20)),
                    (T['week'] - 0.1, (self.J2 + 10, small_top - 50)), (T['week'] + 0.4, (self.J2 + 10, small_top - 50))]
            xy = keys(t, path)
            open_ = any(abs(t - x) < 0.2 for x in (T['spending'] + 0.1, T['week'] + 0.2))
            return replace(p, rh=B(xy), r_shape='open_palm' if open_ else 'fist', r_rot=10, extra={'r_droop': 1.6},
                           turn=1.0, gaze_x=0.4, gaze_y=0.7, smile=0.75)

        def early(t, p):
            q = ease((t - T['you'] + 0.1) / 0.25)
            nod = math.exp(-((t - T['early'] - 0.1) / 0.12) ** 2) * 0.06
            return replace(p, rh=B((self.J1 + 30, small_top - 30)), r_shape='point', r_rot=-15, extra={'r_droop': 1.2},
                           turn=0.8, gaze_x=0.2, gaze_y=0.2, brow_raise=0.8 * q, smile=0.9, head_dy=nod, eye_wide=0.3)
        acts = [Action(T['bills'] - 0.1, T['then'] - 0.15, bills, 0.15, 0.12, 'point at the bills jar'),
                Action(T['then'] - 0.15, T['week'] + 0.6, scoop, 0.12, 0.25, 'scoop coins into the week jars'),
                Action(T['you'] - 0.2, self.t1, early, 0.2, 0.1, 'point: you see it early')]
        self.small_level = lambda t, j: (0.0 if t < (T['spending'] if j == 1 else T['week']) + 0.15 else
                                         0.65 * ease((t - ((T['spending'] if j == 1 else T['week']) + 0.15)) / 0.4))
        self.spent = [T['when'] + 0.1 * k for k in range(7)]
        self.events += [(T['split'] - 0.1, 'slide', 0.9), (T['so'], 'pop', 0.5), (T['bills'], 'tick', 0.7),
                        (T['spending'] + 0.12, 'trickle', 0.6), (T['jar#2'], 'tick', 0.7), (T['week'] + 0.2, 'trickle', 0.6),
                        (T['week'], 'tick', 0.7), (T['empty'], 'pop', 0.4), (T['early'], 'sparkle', 0.7)]
        self.events += [(x, 'coin', 0.35) for x in self.spent]
        return {'lead': Performance(base, Idle(seed=15), acts)}

    def camera(self, t):
        return self.stage.camera(440, 0, 820)

    def wall(self, t):
        def draw(frame, cam):
            cal = props.WallCalendar(420, crossed=0).render(0, 1.0)
            pulse = 1 + 0.06 * math.exp(-((t - self.T['payday']) / 0.15) ** 2)
            self.place(frame, cal, cam, (S.CAL_XY[0] + S.CAL_W / 2, S.CAL_XY[1] + S.CAL_W * 0.44), S.CAL_W * pulse)
        return draw

    def on_table(self, t):
        T = self.T

        def draw(frame, cam):
            D = self.p['lead'].D
            self.jar(frame, cam, JAR_X, JAR_FLOOR, 0.56 * D, 0.85 - 0.25 * ease((t - T['then']) / (T['week'] + 0.6 - T['then'])))
            slide = ease_out((t - T['split'] + 0.1) / 0.5)
            for j, x in ((1, self.J1), (2, self.J2)):
                xx = x - 260 * (1 - slide)
                level = self.small_level(t, j)
                if j == 1:
                    level -= 0.65 * ease((t - T['when']) / (T['empty'] + 0.1 - T['when']))
                self.jar(frame, cam, xx, JAR_FLOOR + 8, self.SMALL * D, max(0.0, level))
            for k, t0 in enumerate(self.spent):          # week one's coins get spent, one by one
                self.hop(frame, cam, t, t0, (self.J1, JAR_FLOOR - 70), 120 + 40 * (k % 3), -650, 1100, 0.10 * D)
        return draw

    def overlay(self, frame, t):
        T = self.T
        cam = self.camera(t)
        D = self.p['lead'].D
        for txt, x, floor, w, t0 in (('BILLS', JAR_X, JAR_FLOOR, 0.56 * D, T['bills']),
                                     ('WEEK 1', self.J1, JAR_FLOOR + 8, self.SMALL * D, T['jar#2']),
                                     ('WEEK 2', self.J2, JAR_FLOOR + 8, self.SMALL * D, T['week'])):
            if t >= t0:
                q = back_out((t - t0) / 0.25)
                tg = O.tag(txt, 150)
                s = (w * 0.86 * cam[2] / tg.width) * max(0.05, q)
                im = tg.resize((max(1, int(tg.width * s)), max(1, int(tg.height * s))), Image.BICUBIC)
                cx, cy = self.to_screen(cam, (x, floor - w * JAR_ASPECT * 0.38))
                frame.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))
        O.slam(frame, O.stacked(('Split it', 'on payday'), (96, 96)), t, T['so'], until=T['bills'] - 0.35)
        O.checklist(frame, t, [('Bills', '', T['bills']), ('Week one', '', T['jar#2']), ('Week two', '', T['week'])],
                    T['bills'] - 0.15)
        x, y = self.to_screen(cam, (self.J1, JAR_FLOOR - 0.38 * D * JAR_ASPECT * 0.45))
        O.circle(frame, t, T['empty'] - 0.05, (x, y), 0.30 * D * cam[2], 0.42 * D * cam[2])


# ---- b09 ----------------------------------------------------------------------------------------------
class B09(Beat):
    """'Payday feels rich. Make it last till the next one.' The lead and the friend lift their cups and
    clink on 'last'; three jars with coins on the table; the calendar behind them circles the next payday."""
    base_name = 'together'
    cast = ('lead', 'friend')
    CLINK = (452, 742)

    def build(self):
        T = self.T
        lead, fr = self.p['lead'], self.p['friend']
        lead_base = self.rest_pose('lead', turn=0.8, gaze_x=0.6, smile=0.7)
        fr_base = self.rest_pose('friend', turn=-0.8, gaze_x=-0.6, smile=0.8)
        cups = self.base.meta['cups']

        def cup_hold(c):                         # a hand's target holding a cup by its body, near where it stands
            x, y = c['xy']
            w, h = c['size']
            return (x + w * 0.5, y + h * 0.45)

        def toast(p, who, t):
            pup = self.p[who]
            B = pup.body
            side = 'l' if who == 'lead' else 'r'
            home = cup_hold(cups[0 if who == 'lead' else 1])
            sh = pup.shoulders[side]
            raised = (sh[0] + 0.12 * pup.D * pup.facing, sh[1] - 0.05 * pup.D)       # up beside the chest, own side
            clink = (self.CLINK[0] - (26 if who == 'lead' else -26), self.CLINK[1])
            sip = (pup.s['head'][0] + 0.30 * pup.D * pup.facing, pup.s['head'][1] + 0.40 * pup.D)
            path = [(T['payday'] - 0.3, home), (T['payday'] + 0.25, home), (T['rich'], raised), (T['make'], raised),
                    (T['last'] - 0.03, clink), (T['last'] + 0.25, clink), (T['next'], sip), (self.t1, sip)]
            xy = keys(t, path)
            sipping = ease((t - T['till']) / 0.3)
            reach = ease((t - T['make']) / 0.25) * (1 - ease((t - T['last'] - 0.25) / 0.3))
            kw = {side + 'h': B(xy), side + '_shape': 'grip', side + '_rot': 0.0,
                  'extra': {side + '_droop': 0.5 + 0.4 * (1 - reach), side + '_front': 1.0}}
            return replace(p, smile=0.95, eye_open=1 - 0.7 * sipping, gaze_x=0.6 * pup.facing, mouth_open=0.25 * (1 - sipping),
                           brow_raise=0.4, lean=1.5 * pup.facing * ease((t - T['make']) / 0.3) * (1 - sipping), **kw)
        perf = {'lead': Performance(lead_base, Idle(seed=16), [Action(self.t0 - 0.1, self.t1, lambda t, p: toast(p, 'lead', t), 0.01, 0.1, 'lift the cup, clink')]),
                'friend': Performance(fr_base, Idle(seed=17), [Action(self.t0 - 0.1, self.t1, lambda t, p: toast(p, 'friend', t), 0.01, 0.1, 'lift the cup, clink')])}
        self.lift_from = T['payday'] - 0.1
        self.events += [(T['last'], 'clink', 1.0), (T['last'] + 0.05, 'sparkle', 0.7), (T['last'], 'pop', 0.4)]
        return perf

    def holds(self, name, t):
        return set() if t < self.lift_from else {'l' if name == 'lead' else 'r'}

    def held(self, name, t):
        if t < self.lift_from:
            return {}
        k = 0 if name == 'lead' else 1
        cup = self.base.cups[k][0]
        side = 'l' if name == 'lead' else 'r'
        return {side: Held(cup, (cup.width * 0.5, cup.height * 0.45), cup.width / self.p[name].D)}

    def cups_away(self, t):
        return {0, 1} if t >= self.lift_from else set()

    def camera(self, t):
        q = ease((t - self.t0) / max(0.1, self.t1 - self.t0))
        return self.stage.camera(470, 836, 941 - 50 * q)

    def wall(self, t):
        def draw(frame, cam):
            cal = props.WallCalendar(420, crossed=0, payday_row=2).render(0, ease((t - self.T['next']) / 0.4))
            self.place(frame, cal, cam, (580, 316), 160)
        return draw

    def on_table(self, t):
        def draw(frame, cam):
            D = 280.0
            for x, w, level, floor in ((452, 0.42 * D, 0.7, 948), (372, 0.30 * D, 0.55, 952), (532, 0.30 * D, 0.5, 952)):
                self.jar(frame, cam, x, floor, w, level)
        return draw

    def overlay(self, frame, t):
        O.slam(frame, O.stacked(('Make it', 'last'), (110, 130)), t, self.T['last'])
        cam = self.camera(t)
        if t > self.T['last']:
            u = t - self.T['last']
            if u < 0.5:
                x, y = self.to_screen(cam, self.CLINK)
                layer = Image.new('RGBA', frame.size, (0, 0, 0, 0))
                d = ImageDraw.Draw(layer)
                for k in range(8):
                    a = k * math.pi / 4
                    r0, r1 = 30 + 160 * u, 60 + 200 * u
                    d.line([(x + r0 * math.cos(a), y - 60 + r0 * math.sin(a)), (x + r1 * math.cos(a), y - 60 + r1 * math.sin(a))],
                           fill=(255, 236, 160, int(255 * (1 - u / 0.5))), width=6)
                frame.alpha_composite(layer)


BEATS = {'b03': B03, 'b04': B04, 'b05': B05, 'b06a': B06Friend, 'b06b': B06Lead, 'b07': B07, 'b08': B08, 'b09': B09}
