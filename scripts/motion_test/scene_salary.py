"""Salary episode, beats 1-2: staging, word-keyed choreography and props on a painted base pose.

The lead sits at the café table in The Empty Chair's own painting (base 'lead-at-table', from
04-waiting). The seated body, legs, chair and table are that painting; only the head, arms, hands and
props move (pipeline/rig/puppet.py). World coordinates are the plate's pixels (941x1672).

No side effects at import: tests build the same performance from a dict of word times.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from pipeline.rig import props
from pipeline.rig.actions import Action, Idle, Performance, back_out, ease, ease_out, keys
from pipeline.rig.puppet import Puppet, Stage, load_base
from pipeline.rig.skeleton import Held, Pose, light_layer

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'renders/motion-test/assets'
W, H = 1080, 1920
FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'

BASE = 'lead-at-table'
TWO = 'together'
LIGHT = 'right'                  # the painted heads are lit from the right (their shade is on the left)

# Plate positions (px). The calendar hangs where the painting has a framed picture, on the wall the lead faces.
CAL_XY, CAL_W = (430.0, 250.0), 240.0
JAR_X, JAR_FLOOR = 528.0, 944.0          # the jar stands on the right half of the tabletop
JAR_ASPECT = 1.44                        # height / width of the painted jar (prop-jar2-s17), fixed so tests match renders
UNDER = (176.0, 992.0)                   # a hand reaching under the table, beside the lap


def lead_base_pose(lead: Puppet) -> Pose:
    return Pose(turn=0.85, gaze_x=0.3, smile=0.5, lh=lead.rest('l'), rh=lead.rest('r'), l_shape='resting', r_shape='resting',
                l_rot=-15, r_rot=20)


def mix(a, b, q):
    return (a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q)


def build_performance(T: dict, cut: float, end: float, lead: Puppet, jar_top: float) -> tuple[Performance, list]:
    """The lead's performance. T maps words to absolute seconds. Returns (performance, face_ok action names)."""
    base = lead_base_pose(lead)
    B = lead.body                         # plate px -> body space
    rest_l, rest_r = lead.rest('l'), lead.rest('r')

    def cash_fan(t, p):
        """'Payday feels rich': fanning yourself with cash beside the head, chin up, eyes happily shut."""
        up = ease((t - (T['payday'] - 0.3)) / 0.35)
        fan = math.sin(2 * math.pi * (t - T['payday']) * 2.6) * up
        bliss = ease((t - T['feels']) / 0.2)
        rich = keys(t - T['rich'], [(-0.1, 0), (0.0, 0.0), (0.08, 0.05), (0.22, 0.0)])
        return replace(p, lh=mix(rest_l, B((104, 566)), up), l_shape='grip', l_rot=-6 + 22 * fan, l_bend=1,
                       rh=rest_r, r_shape='resting', r_rot=20, head_tilt=-6 * up, turn=0.6, gaze_x=0.0,
                       smile=0.95, mouth_open=0.22 * bliss, eye_open=1 - 0.8 * bliss, brow_raise=0.5, lift=rich)

    def calendar_glance(t, p):
        """'So why are you broke...': the fun stops; eyes go to the calendar on the wall as the X marks land."""
        shock = ease((t - T['so']) / 0.25)
        flinch = sum(0.012 * math.exp(-((t - (T['so'] + 0.15 * k)) / 0.05) ** 2) for k in range(7))
        return replace(p, lh=B((122, 652)), l_shape='grip', l_rot=-4, rh=rest_r, r_shape='resting', r_rot=20,
                       turn=1.0, gaze_x=0.8, gaze_y=-0.9, head_tilt=6, head_dy=-flinch,
                       eye_wide=0.7 * shock, smile=0.55 - 0.95 * shock, brow_raise=0.7 * shock)

    def slump(t, p):
        """'broke': the head sinks; the free hand goes under the table for the wallet."""
        u = t - T['broke']
        drop = keys(u, [(0, 0), (0.08, 1.0), (0.25, 0.8)], ease_out)
        return replace(p, head_dy=0.04 * drop, head_tilt=-5 * drop, lean=2.5 * drop, crouch=0.08 * drop,
                       lh=B(UNDER), l_shape='grip', l_rot=0,
                       rh=rest_r, r_shape='resting', r_rot=20, extra={'l_under': 1.0},
                       smile=-0.6, brow_worry=1.0, brow_raise=0.2, eye_wide=0.2, turn=0.7, gaze_y=0.6)

    def wallet_shake(t, p):
        """'a week later': the wallet comes up already upside down and is shaken out; eyes follow the moth up."""
        u = t - T['a']
        up = ease(u / 0.25)
        shake = math.sin(2 * math.pi * u * 7.0) * ease((u - 0.25) / 0.1) * (1 - ease((t - T['later'] - 0.45) / 0.2))
        follow = ease((t - (T['later'] - 0.05)) / 0.5)
        hand = mix(B(UNDER), B((112, 612)), up)
        return replace(p, lh=(hand[0], hand[1] - 0.04 * abs(shake)), l_shape='grip', l_rot=-12 + 18 * shake, l_bend=1,
                       rh=rest_r, r_shape='resting', r_rot=20, smile=-0.45, brow_worry=1.0, brow_raise=0.3, eye_wide=0.25,
                       turn=0.45 + 0.4 * follow, gaze_x=-0.6 + 1.3 * follow, gaze_y=0.1 - 1.0 * follow, head_tilt=4 * follow)

    def facepalm(t, p):
        """'...later?': the far hand comes in from the side, back of the hand to the forehead; eyes shut."""
        q = ease((t - (T['later'] + 0.25)) / 0.3)
        return replace(p, rh=B((418, 524)), r_shape='back', r_rot=-55, r_bend=1, extra={'r_front': 1.0, 'r_droop': 0.0},
                       head_tilt=-5 * q, eye_open=1 - 0.85 * q, brow_worry=1.0, smile=-0.55, turn=0.75,
                       lh=B((112, 650)), l_shape='grip', l_rot=-10)

    def rub_hands(t, p):
        """Beat 2 opens on the jar: hands together on the table beside it, rubbing."""
        k = math.sin(2 * math.pi * (t - cut) * 4.5) * 0.035
        return replace(p, lh=(B((392, 868))[0] + k, B((392, 868))[1]), rh=(B((446, 858))[0] - k, B((446, 858))[1]),
                       l_shape='resting', r_shape='resting', l_rot=-35, r_rot=30, turn=0.95, gaze_x=0.7, gaze_y=0.6,
                       smile=0.8, brow_raise=0.35)

    def coin_rain(t, p):
        """'Say $1,500 lands': both hands on the table edge, leaning in toward the jar, bouncing a little,
        eyes tracking the falling coins."""
        bounce = abs(math.sin(2 * math.pi * (t - T['amount']) * 2.2)) * 0.05
        track = 0.5 + 0.5 * math.sin(2 * math.pi * (t - T['amount']) * 1.6)
        lean_in = ease((t - T['amount']) / 0.35)
        return replace(p, lh=B((300, 905)), rh=B((432, 900)), l_shape='resting', r_shape='resting', l_rot=-20, r_rot=25,
                       lean=3.0 * lean_in, lift=bounce, head_dx=0.03 * lean_in, turn=1.0, gaze_x=0.9,
                       gaze_y=-0.9 + 1.5 * track, eye_wide=0.55, smile=0.95, mouth_open=0.35, brow_raise=0.7)

    def fist_pump(t, p):
        """'lands': a quick 'yes!' fist pump."""
        u = t - T['lands']
        pump = keys(u, [(0, 0), (0.12, 1.0), (0.22, 0.7), (0.32, 1.0)], ease_out)
        return replace(p, lh=mix(B((214, 700)), B((96, 478)), pump), l_shape='fist', l_rot=0, l_bend=1, rh=rest_r,
                       r_shape='resting', r_rot=20, smile=1.0, mouth_open=0.6 * pump, eye_open=1 - 0.75 * pump, turn=0.7,
                       extra={'l_droop': 1.0})

    def jar_pats(t, p):
        """'every other Friday': two pats on the jar lid with the far hand, then a satisfied nod."""
        pats = [T['every'], T['other']]
        lift = min(min(abs(t - x) for x in pats) / 0.12, 1.0)
        lid = B((JAR_X + 6, jar_top - 4))
        nod = math.exp(-((t - T['friday'] - 0.15) / 0.12) ** 2) * 0.06
        return replace(p, rh=(lid[0], lid[1] - 0.20 * lift), r_shape='back', r_rot=10, r_bend=1, lh=rest_l, l_shape='resting',
                       l_rot=-15, turn=0.95, gaze_x=0.6, gaze_y=0.7, head_dy=nod, smile=0.85, brow_raise=0.3)

    def feels_huge(t, p):
        """'It feels huge': a wind-up on 'It', then both arms flung up and wide with a bounce on 'huge'."""
        u = t - T['huge']
        pre = ease((t - T['it']) / 0.2) * (1 - ease((u + 0.12) / 0.1))
        up = back_out((u + 0.14) / 0.28)
        bounce = keys(u, [(-0.14, 0.0), (0.0, 0.10), (0.16, 0.0), (0.24, 0.0)])
        lo_l, lo_r = B((300, 790)), B((360, 782))
        hi_l, hi_r = B((84, 508)), B((492, 548))
        return replace(p, lh=mix(lo_l, hi_l, up), rh=mix(lo_r, hi_r, up), l_shape='open_palm' if up > 0.3 else 'fist',
                       r_shape='open_palm' if up > 0.3 else 'fist', l_rot=-15 * up, r_rot=15 * up, l_bend=1, r_bend=1,
                       extra={'l_droop': 2.5 - 1.6 * up, 'r_droop': 2.5 - 1.6 * up},
                       crouch=0.05 * pre, lift=max(0.0, bounce), turn=0.55, gaze_x=0.1, gaze_y=-0.2,
                       smile=1.0, mouth_open=0.3 * pre + 0.9 * ease((u + 0.1) / 0.12), eye_wide=0.7, brow_raise=0.9)

    acts = [
        Action(0.0, T['so'] - 0.05, cash_fan, 0.0, 0.2, 'fan self with cash'),
        Action(T['so'], T['broke'], calendar_glance, 0.15, 0.08, 'look at the calendar'),
        Action(T['broke'], T['a'] + 0.05, slump, 0.06, 0.1, 'slump; reach under the table'),
        Action(T['a'], T['later'] + 0.35, wallet_shake, 0.12, 0.25, 'shake the upturned wallet'),
        Action(T['later'] + 0.25, cut, facepalm, 0.25, 0.01, 'hand to forehead'),
        Action(cut, T['amount'] + 0.1, rub_hands, 0.01, 0.2, 'rub hands at the jar'),
        Action(T['amount'], T['lands'] - 0.02, coin_rain, 0.2, 0.08, 'watch the coins, bouncing'),
        Action(T['lands'], T['every'] - 0.1, fist_pump, 0.06, 0.15, 'fist pump'),
        Action(T['every'] - 0.25, T['it'] - 0.05, jar_pats, 0.2, 0.2, 'pat the jar twice, nod'),
        Action(T['it'], end, feels_huge, 0.12, 0.3, 'arms wide, bounce'),
    ]
    return Performance(base, Idle(seed=11), acts), ['hand to forehead']


class Scene:
    """Props, timing and drawing for beats 1-2."""

    def __init__(self, T: dict, cut: float, end: float, load_art: bool = True):
        self.T, self.cut, self.end = T, cut, end
        self.base = load_base(BASE)
        self.stage = Stage(self.base, (W, H))
        self.lead = Puppet(self.base, 'lead')
        D = self.lead.D
        self.D = D
        self.n_bills = 4
        self.jar_w = 0.56 * D
        self.jar_h = self.jar_w * JAR_ASPECT
        if load_art:
            self._load_art()
        self.jar_top = JAR_FLOOR - self.jar_h
        self.perf, self.face_ok = build_performance(T, cut, end, self.lead, self.jar_top)
        rng = random.Random(4)
        # Bills leave the fan one by one: thrown up and out, then falling, never changing size.
        self.bill_flights = [dict(t0=T['so'] + 0.06 + 0.17 * k, vx=-rng.uniform(60, 170), vy=-rng.uniform(560, 720),
                                  spin=rng.uniform(-260, 260)) for k in range(self.n_bills)]
        self.moth_t = T['later'] - 0.02
        self.coins = self._coins()
        if load_art:
            for b in self.bill_flights:
                b['origin'] = self.world_point(b['t0'], 'l_grip')
            self.moth_origin = self.world_point(self.moth_t, 'l_grip')

    def _load_art(self):
        A = ASSETS
        D = self.D
        self.wallet = props.empty_out(props.key_neutral(str(A / pick(A, 'prop-wallet-empty')), sat_max=58, lum_min=60, holes=False), min_blob=5)
        self.coin = props.key_out(str(A / 'prop-coin-s3.png'))
        clean_note(A)
        art = {k: (props.key_neutral(str(A / CHOICES[k])) if CHOICES.get(k) and (A / CHOICES[k]).exists() else None)
               for k in ('prop-bill', 'prop-moth', 'prop-jar')}
        self.bill = art['prop-bill'] or props.painterly(props.bill_sprite(300), 3)
        self.moth = props.keep_centre(art['prop-moth'], cut=13) if art['prop-moth'] else props.painterly(props.moth_sprite(1.2, 200), 5)
        self.jar = (props.clean_alpha(props.glass(props.key_neutral(str(A / CHOICES['prop-jar']), holes=False))) if art['prop-jar']
                    else props.painterly(props.stand_in_jar(), 7, 0.6))
        self.jar = self.jar.resize((self.jar.width, round(self.jar.width * JAR_ASPECT)), Image.LANCZOS)
        self.calendar = props.WallCalendar(420)
        fan_bill = self.bill.resize((300, int(300 * self.bill.height / self.bill.width)), Image.LANCZOS)
        self.fans = {n: props.fan_of(fan_bill, n) for n in range(1, self.n_bills + 1)}
        self.fan_bill_w = fan_bill.width
        self.bill_w = 0.48 * D                      # each bill's width in the world, in the hand and in flight

    # ---- helpers -------------------------------------------------------------------------------
    def world_point(self, t, key):
        """A joint (or grip) position at time t in plate px, from the same geometry the renderer uses."""
        pose = self.perf.pose(t)
        j = self.lead.joints(pose)
        for side in ('l', 'r'):
            self.lead.hand_placement(pose, j, side)
        return j[key]

    def _coins(self):
        rng = random.Random(9)
        T = self.T
        n = 16
        times = [T['amount'] + 0.05 + (T['lands'] - T['amount'] - 0.4) * (i / (n - 1)) ** 0.85 for i in range(n)]
        out = []
        for i, td in enumerate(times):
            f = props.Falling(t0=0, x=JAR_X + rng.uniform(-0.12, 0.12) * self.jar_w, y0=-80,
                              y1=JAR_FLOOR - 10 - (i + 1) / n * 0.70 * self.jar_h, spin=rng.uniform(-500, 500))
            land = td if i < n - 1 else T['lands']
            f.t0 = land - math.sqrt(2 * (f.y1 - f.y0) / f.g)
            out.append(f)
        return out

    def jar_level(self, t):
        return sum(1 for c in self.coins if c.landed_at() <= t) / len(self.coins)

    def holding(self, t) -> dict:
        """What each hand holds at time t (no art needed): {'l': 'cash' | 'wallet'}."""
        T = self.T
        if t < T['broke'] and sum(1 for b in self.bill_flights if t >= b['t0']) < self.n_bills:
            return {'l': 'cash'}
        if T['broke'] + 0.12 <= t < self.cut:
            return {'l': 'wallet'}
        return {}

    def held(self, t):
        T = self.T
        out = {}
        if t < T['broke']:
            flown = sum(1 for b in self.bill_flights if t >= b['t0'])
            if flown < self.n_bills:
                fan, anchor = self.fans[self.n_bills - flown]
                out['l'] = Held(fan, anchor, fan.width / self.fan_bill_w * self.bill_w / self.D, angle=-14, follow_hand=True)
        elif t >= T['broke'] + 0.12 and t < self.cut:
            wl = self.wallet       # gripped by its spine, upside down (opening down), so it hangs below the hand
            out['l'] = Held(wl, (wl.width * 0.5, wl.height * 0.94), 0.52, angle=180, follow_hand=True)
        return out

    # ---- camera ----------------------------------------------------------------------------------
    def camera(self, t):
        """Beat 1: a slow push-in from the full Empty Chair framing. Beat 2: a little closer, a punch on 'huge'.
        The top of the frame stays on the window and wall, where the text sits."""
        T = self.T
        if t < self.cut:
            q = ease(t / self.cut)
            return self.stage.camera(470 - 40 * q, 0, 941 - 70 * q)
        punch = ease_out((t - T['huge'] + 0.02) / 0.16)
        return self.stage.camera(420 - 30 * punch, 0, 850 - 80 * punch)

    @staticmethod
    def to_screen(cam, p):
        return ((p[0] - cam[0]) * cam[2], (p[1] - cam[1]) * cam[2])

    def place(self, frame, sprite, cam, xy, world_w, anchor=(0.5, 0.5), angle=0.0, alpha=1.0, sx=1.0, lit=True):
        s = world_w * cam[2] / sprite.width
        im = sprite.resize((max(1, round(sprite.width * s * sx)), max(1, round(sprite.height * s))), Image.BICUBIC)
        if lit:
            im = light_layer(im, LIGHT, rim_strength=0.3, back_rim=0.1, grade=(1.0, 0.97, 0.90))
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

    # ---- frame -------------------------------------------------------------------------------------
    def wall(self, t):
        T = self.T

        def draw(frame, cam):
            # The calendar on the wall, with a soft shadow; the X marks land one by one from 'So' to 'week'.
            marks = 7 * ease_out((t - (T['so'] + 0.02)) / (T['week'] - T['so']))
            circle = ease((t - T['payday']) / 0.35)
            cal = self.calendar.render(max(0.0, marks), circle)
            sh = Image.new('RGBA', cal.size, (60, 34, 16, 0))
            sh.putalpha(cal.getchannel('A').point(lambda v: int(v * 0.35)))
            self.place(frame, sh.filter(ImageFilter.GaussianBlur(10)), cam, (CAL_XY[0] - 6, CAL_XY[1] + 8), CAL_W, anchor=(0, 0), lit=False)
            self.place(frame, cal, cam, CAL_XY, CAL_W, anchor=(0, 0))
        return draw

    def on_table(self, t):
        def draw(frame, cam):
            if t < self.cut:
                return
            self.shadow(frame, cam, (JAR_X + 8, JAR_FLOOR + 2), self.jar_w * 1.0, 16, 0.5)
            level = self.jar_level(t)
            self.place(frame, props.coin_pile(self.coin, self.jar, level), cam, (JAR_X, JAR_FLOOR), self.jar_w, anchor=(0.5, 1.0))
            for c in self.coins:
                at = c.at(t)
                if at:
                    self.place(frame, self.coin, cam, (at[0], at[1]), 0.15 * self.D, angle=at[2])
            self.place(frame, self.jar, cam, (JAR_X, JAR_FLOOR), self.jar_w, anchor=(0.5, 1.0))
        return draw

    def flying(self, t):
        def draw(frame, cam):
            if t >= self.cut:
                return
            for b in self.bill_flights:   # thrown up and out, then falling; the same size as in the hand
                u = t - b['t0']
                if 0 <= u < 1.4:
                    x = b['origin'][0] + b['vx'] * u
                    y = b['origin'][1] + b['vy'] * u + 0.5 * 2000 * u * u
                    self.place(frame, self.bill, cam, (x, y), self.bill_w, angle=-30 + b['spin'] * u, alpha=1 - ease((u - 1.0) / 0.4))
            u = t - self.moth_t
            if 0 <= u < 2.2:              # the moth flutters out of the upturned wallet, up and away
                x = self.moth_origin[0] + 0.05 * self.D + 70 * u + 22 * math.sin(u * 6)
                y = self.moth_origin[1] + 0.45 * self.D - 330 * u + 26 * math.sin(u * 8.5)
                flap = 0.45 + 0.55 * abs(math.sin(u * 40))
                self.place(frame, self.moth, cam, (x, y), 0.20 * self.D, sx=flap, alpha=1 - ease((u - 1.7) / 0.5))
        return draw

    def frame(self, t: float, cam=None) -> Image.Image:
        cam = cam or self.camera(t)
        pose = self.perf.pose(t)
        return self.stage.compose(cam, [self.lead], [pose], [self.held(t)], wall=self.wall(t), on_table=self.on_table(t),
                                  flying=self.flying(t))


def has(folder: Path, prefix: str) -> bool:
    return any(folder.glob(f'{prefix}-s*.png'))


def pick(folder: Path, prefix: str) -> str:
    """The chosen seed for a prop: the first listed in CHOICES, else the first file found."""
    choice = CHOICES.get(prefix)
    if choice and (folder / choice).exists():
        return choice
    found = sorted(p.name for p in folder.glob(f'{prefix}-s*.png'))
    if not found:
        raise FileNotFoundError(f'no {prefix} art in {folder}; run gen_assets.py')
    return found[0]


CHOICES = {'prop-wallet-empty': 'prop-wallet-empty-s21.png', 'prop-moth': 'prop-moth-s8.png', 'prop-jar': 'prop-jar2-s17.png',
           'prop-bill': 'prop-bill2-s19-clean.png'}


def clean_note(folder: Path):
    """The painted note has one strip of tiny fake lettering; soften it into the ornament so no text shows."""
    src, dst = folder / 'prop-bill2-s19.png', folder / 'prop-bill2-s19-clean.png'
    if dst.exists() or not src.exists():
        return
    im = Image.open(src).convert('RGB')
    box = (int(im.width * 0.20), int(im.height * 0.69), int(im.width * 0.80), int(im.height * 0.81))
    band = im.crop(box).filter(ImageFilter.GaussianBlur(9))
    mask = Image.new('L', band.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, band.width - 1, band.height - 1), radius=band.height // 2, fill=255)
    im.paste(band, box[:2], mask.filter(ImageFilter.GaussianBlur(4)))
    im.save(dst)


def two_shot(t: float = 2.0) -> Image.Image:
    """The lead and the scarf friend seated at the table in 03-together's own painting (base 'together'):
    both in their chairs, each with their hands on their own side of the table."""
    base = load_base(TWO)
    stage = Stage(base, (W, H))
    lead, friend = Puppet(base, 'lead'), Puppet(base, 'friend')
    lead_pose = Idle(seed=11)(t, Pose(turn=0.8, gaze_x=0.5, smile=0.55, brow_worry=0.25, lh=lead.rest('l'), rh=lead.rest('r'),
                                      l_shape='resting', r_shape='resting', l_rot=-15, r_rot=20))
    friend_pose = Idle(seed=3)(t, Pose(turn=-0.8, gaze_x=-0.5, gaze_y=0.3, smile=0.8, mouth_open=0.25, brow_raise=0.3,
                                       lh=friend.body((590, 820)), l_shape='open_palm', l_rot=-25, rh=friend.rest('r'),
                                       r_shape='resting', r_rot=20))
    cam = stage.camera(470, 836, 941)
    return stage.compose(cam, [lead, friend], [lead_pose, friend_pose]).convert('RGB')
