"""Salary episode, beats 1-2: staging, word-keyed choreography and props for the cut-out rig.

No side effects at import: tests build the same performance from a dict of word times.
World coordinates are the background's pixels (bg-room-s47, 768x1344). The lead sits at an
AI-painted café table (a separate layer, so the hands can go over or behind it).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pipeline.rig import props
from pipeline.rig.actions import Action, Idle, Performance, back_out, ease, ease_out, keys
from pipeline.rig.skeleton import Character, Held, Pose, light_layer

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'renders/motion-test/assets'
W, H = 1080, 1920
FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'

D = 215.0
FLOOR = 1175.0
TABLE_X, TABLE_W = 385.0, 2.6 * D
SEAT = 896.0                          # hip root when seated, at the table's front edge
LEAD_X = 340.0
CAL_XY, CAL_W = (546.0, 300.0), 98.0   # on the wall beside the door
JAR_X, JAR_W = 478.0, 0.62 * D
LIGHT = 'left'                        # the open door and window are behind and to the left


def lead_base_pose():
    floor = (FLOOR - 6 - SEAT) / D
    return Pose(seated=1.0, knees=1.0, lf=(0.50, floor - 0.02), rf=(0.72, floor), turn=0.15, smile=0.55,
                lh=(-0.45, -0.52), rh=(0.45, -0.52), l_shape='resting', r_shape='resting', l_rot=-20, r_rot=20)


def table_hand(dx):
    """Body-space hand target resting on the tabletop."""
    return (dx, (TABLE_TOP - SEAT) / D + 0.02)


TABLE_TOP = 841.0   # tabletop surface centre (set again from the sprite in Scene.__init__)


@dataclass
class Beat:
    name: str
    start: float
    end: float


def build_performance(T: dict, cut: float, end: float) -> tuple[Performance, list]:
    """The lead's performance. T maps words to absolute seconds. Returns (performance, face_ok action names)."""
    base = lead_base_pose()

    def cash_fan(t, p):
        """'Payday feels rich': fanning yourself with cash, chin up, eyes happily shut."""
        up = ease((t - (T['payday'] - 0.3)) / 0.35)
        fan = math.sin(2 * math.pi * (t - T['payday']) * 2.6) * up
        bliss = ease((t - T['feels']) / 0.2)
        rich = keys(t - T['rich'], [(-0.1, 0), (0.0, 0.0), (0.08, 0.05), (0.22, 0.0)])
        return replace(p, lh=(-0.55 - 0.22 * up, -0.80 - 0.70 * up), l_shape='grip', l_rot=-8 + 26 * fan, l_bend=1,
                       rh=table_hand(0.40), r_shape='resting', r_rot=25,
                       lean=-2.5 * up, head_tilt=-6 * up, smile=0.95, mouth_open=0.22 * bliss, eye_open=1 - 0.8 * bliss,
                       brow_raise=0.5, lift=rich, turn=0.0)

    def calendar_glance(t, p):
        """'So why are you broke...': the fun stops; eyes go up to the calendar as the X marks land."""
        shock = ease((t - T['so']) / 0.25)
        flinch = sum(0.012 * math.exp(-((t - (T['so'] + 0.15 * k)) / 0.05) ** 2) for k in range(7))
        return replace(p, lh=(-0.80, -1.25), l_shape='grip', l_rot=-5, rh=table_hand(0.40), r_shape='resting', r_rot=25,
                       turn=0.55, gaze_x=0.8, gaze_y=-1.0, head_tilt=8, head_dy=-flinch,
                       eye_wide=0.7 * shock, smile=0.55 - 0.9 * shock, brow_raise=0.7 * shock)

    def slump(t, p):
        """'broke': shoulders drop, head sinks; the free hand goes below the table for the wallet."""
        u = t - T['broke']
        drop = keys(u, [(0, 0), (0.08, 1.0), (0.25, 0.8)], ease_out)
        return replace(p, crouch=0.05 * drop, stretch=-0.05 * drop, head_dy=0.05 * drop, lean=2 * drop,
                       lh=(-0.55, -0.05), l_shape='grip', l_rot=0, rh=table_hand(0.38), r_shape='resting', r_rot=25,
                       smile=-0.6, brow_worry=1.0, brow_raise=0.2, eye_wide=0.2, turn=0.1, gaze_y=0.3)

    def wallet_shake(t, p):
        """'a week later': the wallet comes up already upside down and is shaken out to the side; eyes follow the moth."""
        u = t - T['a']
        up = ease(u / 0.25)
        shake = math.sin(2 * math.pi * u * 7.0) * ease((u - 0.25) / 0.1) * (1 - ease((t - T['later'] - 0.45) / 0.2))
        follow = ease((t - (T['later'] - 0.05)) / 0.5)
        return replace(p, lh=(-0.55 - 0.25 * up, -0.05 - 1.30 * up - 0.05 * abs(shake)), l_shape='grip', l_rot=-12 + 18 * shake,
                       l_bend=1, rh=table_hand(0.38), r_shape='resting', r_rot=25,
                       smile=-0.45, brow_worry=1.0, brow_raise=0.3, eye_wide=0.25,
                       turn=-0.45 + 0.2 * follow, gaze_x=-0.8 + 0.3 * follow, gaze_y=-0.1 - 0.8 * follow, head_tilt=-4 * follow)

    def facepalm(t, p):
        """'...later?': the other hand to the forehead (back of the hand to camera), eyes shut."""
        q = ease((t - (T['later'] + 0.25)) / 0.3)
        return replace(p, rh=(-0.02, -1.86), r_shape='back', r_rot=0, r_bend=1, head_tilt=-4 * q, eye_open=1 - 0.85 * q,
                       brow_worry=1.0, smile=-0.55, lean=-2 * q, lh=(-0.75, -0.75), l_shape='grip', l_rot=-10)

    def rub_hands(t, p):
        """Beat 2 opens on the jar: hands together over the table, rubbing."""
        k = math.sin(2 * math.pi * (t - cut) * 4.5) * 0.035
        return replace(p, lh=(-0.10, -0.92 + k), rh=(0.10, -0.92 - k), l_shape='resting', r_shape='resting', l_rot=-30, r_rot=30,
                       l_bend=1, r_bend=1, turn=0.55, gaze_x=0.7, gaze_y=0.5, smile=0.8, brow_raise=0.35, lean=1.5)

    def coin_rain(t, p):
        """'Say $1,500 lands': fists together under the chin, bouncing in the chair, eyes tracking the coins."""
        bounce = abs(math.sin(2 * math.pi * (t - T['amount']) * 2.2)) * 0.025
        track = 0.5 + 0.5 * math.sin(2 * math.pi * (t - T['amount']) * 1.6)
        return replace(p, lh=(-0.10, -1.08), rh=(0.10, -1.06), l_shape='fist', r_shape='fist', l_rot=-25, r_rot=25,
                       l_bend=1, r_bend=1, lift=bounce, turn=0.6, gaze_x=0.8, gaze_y=-0.9 + 1.4 * track, eye_wide=0.55,
                       smile=0.95, mouth_open=0.35, brow_raise=0.7)

    def fist_pump(t, p):
        """'lands': a quick 'yes!' fist pump."""
        u = t - T['lands']
        pump = keys(u, [(0, 0), (0.12, 1.0), (0.22, 0.7), (0.32, 1.0)], ease_out)
        return replace(p, lh=(-0.62, -0.95 - 0.95 * pump), l_shape='fist', l_rot=0, l_bend=1, rh=table_hand(0.40),
                       r_shape='resting', r_rot=25, smile=1.0, mouth_open=0.6 * pump, eye_open=1 - 0.75 * pump, turn=0.2)

    def jar_pats(t, p):
        """'every other Friday': two pats on the jar lid, then a satisfied nod."""
        pats = [T['every'], T['other']]
        lift = min(min(abs(t - x) for x in pats) / 0.12, 1.0)
        lid = ((JAR_X - LEAD_X) / D + 0.02, (TABLE_TOP - JAR_W * 1.05 - SEAT) / D - 0.05)
        nod = math.exp(-((t - T['friday'] - 0.15) / 0.12) ** 2) * 0.06
        return replace(p, rh=(lid[0], lid[1] - 0.20 * lift), r_shape='back', r_rot=20, r_bend=1,
                       lh=table_hand(-0.40), l_shape='resting', l_rot=-25,
                       turn=0.5, gaze_x=0.6, gaze_y=0.5, head_dy=nod, smile=0.85, brow_raise=0.3, lean=3)

    def feels_huge(t, p):
        """'It feels huge': a wind-up on 'It', then arms flung wide and up with a bounce in the chair on 'huge'."""
        u = t - T['huge']
        pre = ease((t - T['it']) / 0.2) * (1 - ease((u + 0.12) / 0.1))
        up = back_out((u + 0.14) / 0.28)
        bounce = keys(u, [(-0.14, 0.0), (0.0, 0.10), (0.16, 0.0), (0.24, 0.0)])
        squash = keys(u, [(-0.3, 0.0), (-0.16, -0.05), (-0.05, 0.06), (0.16, 0.0), (0.22, -0.05), (0.45, 0.0)])
        lx, ly = -0.40 - 0.46 * up, -0.55 - 1.05 * up
        return replace(p, lh=(lx, ly), rh=(-lx, ly), l_shape='open_palm' if up > 0.3 else 'fist',
                       r_shape='open_palm' if up > 0.3 else 'fist', l_rot=-15 * up, r_rot=15 * up, l_bend=1, r_bend=1,
                       crouch=0.05 * pre, lift=max(0.0, bounce), stretch=squash, turn=0.0, gaze_x=0, gaze_y=-0.2,
                       smile=1.0, mouth_open=0.3 * pre + 0.9 * ease((u + 0.1) / 0.12), eye_wide=0.7, brow_raise=0.9)

    acts = [
        Action(0.0, T['so'] - 0.05, cash_fan, 0.0, 0.2, 'fan self with cash'),
        Action(T['so'], T['broke'], calendar_glance, 0.15, 0.08, 'look up at the calendar'),
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
        global TABLE_TOP
        self.T, self.cut, self.end = T, cut, end
        self.lead = Character(D=D, light=LIGHT)
        self.perf, self.face_ok = build_performance(T, cut, end)
        rng = random.Random(4)
        # Bills leave the fan one by one: thrown up and out, then falling, never changing size.
        self.n_bills = 4
        self.bill_flights = [dict(t0=T['so'] + 0.06 + 0.17 * k, vx=-rng.uniform(150, 260), vy=-rng.uniform(380, 520),
                                  spin=rng.uniform(-260, 260)) for k in range(self.n_bills)]
        self.moth_t = T['later'] - 0.02
        if not load_art:
            return
        A = ASSETS
        self.bg = Image.open(A / 'bg-room-s47.png').convert('RGB')
        self.bg2 = self.bg.resize((self.bg.width * 2, self.bg.height * 2), Image.LANCZOS)
        self.table = props.key_colours(str(A / 'prop-table-s2.png'))   # wood and iron only; drops its painted floor shadow
        k = TABLE_W / self.table.width
        self.table_h = self.table.height * k
        self.table_y0 = FLOOR - self.table_h + 2
        TABLE_TOP = self.table_y0 + 64 / 514 * self.table_h
        self.chair = props.key_neutral(str(A / pick(A, 'prop-chair')))
        # The painted wallet came with cards inside; they are painted out so the wallet is truly empty.
        self.wallet = props.empty_out(props.key_neutral(str(A / pick(A, 'prop-wallet-empty')), sat_max=58, lum_min=60, holes=False), min_blob=5)
        self.coin = props.key_out(str(A / 'prop-coin-s3.png'))
        clean_note(A)
        art = {k: (props.key_neutral(str(A / CHOICES[k])) if CHOICES.get(k) and (A / CHOICES[k]).exists() else None)
               for k in ('prop-bill', 'prop-moth', 'prop-jar', 'prop-calendar')}
        # Painted art where SDXL gave a usable cut-out; otherwise code-drawn stand-ins with a painted finish.
        self.bill = art['prop-bill'] or props.painterly(props.bill_sprite(300), 3)
        self.moth = props.keep_centre(art['prop-moth'], cut=13) if art['prop-moth'] else props.painterly(props.moth_sprite(1.2, 200), 5)
        self.jar = (props.glass(props.key_neutral(str(A / CHOICES['prop-jar']), holes=False)) if art['prop-jar']
                    else props.painterly(props.stand_in_jar(), 7, 0.6))
        cal = art['prop-calendar'] or props.painterly(props.Calendar(width=420).render(0, 0), 9, 0.8)
        self.calendar = props.PaintedCalendar(cal)
        fan_bill = self.bill.resize((300, int(300 * self.bill.height / self.bill.width)), Image.LANCZOS)
        self.fans = {n: props.fan_of(fan_bill, n) for n in range(1, self.n_bills + 1)}
        self.fan_bill_w = fan_bill.width    # a bill's long side, in fan-sprite pixels
        self.bill_w = 0.50 * D                      # each bill's width in the world, in the hand and in flight
        self.fan_w = self.bill_w * self.fans[self.n_bills][0].width / fan_bill.width
        for b in self.bill_flights:
            b['origin'] = self.world_point(b['t0'], 'l_grip')
        self.moth_origin = self.world_point(self.moth_t, 'l_grip')
        self.coins = self._coins()

    # ---- helpers -------------------------------------------------------------------------------
    def world_point(self, t, key):
        """A joint (or grip) position at time t in world px, from the same geometry the renderer uses."""
        pose = self.perf.pose(t)
        j = self.lead.joints(pose, (LEAD_X, SEAT), 1.0, (0.0, 0.0))
        for side in ('l', 'r'):
            self.lead.hand_placement(pose, j, side)
        return j[key]

    def _coins(self):
        rng = random.Random(9)
        T = self.T
        n = 16
        jar_top = TABLE_TOP - JAR_W * self.jar.height / self.jar.width
        times = [T['amount'] + 0.05 + (T['lands'] - T['amount'] - 0.4) * (i / (n - 1)) ** 0.85 for i in range(n)]
        out = []
        for i, td in enumerate(times):
            f = props.Falling(t0=0, x=JAR_X + rng.uniform(-0.12, 0.12) * JAR_W, y0=jar_top - 560,
                              y1=TABLE_TOP - 8 - (i + 1) / n * 0.70 * (TABLE_TOP - jar_top), spin=rng.uniform(-500, 500))
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
                # size from the single-bill width, so every bill stays the same size as bills leave the fan
                out['l'] = Held(fan, anchor, fan.width / self.fan_bill_w * self.bill_w / D, angle=-12, follow_hand=True)
        elif t >= T['broke'] + 0.12 and t < self.cut:
            # Gripped by its spine and held upside down (opening down), so it hangs below the hand.
            wl = self.wallet
            out['l'] = Held(wl, (wl.width * 0.5, wl.height * 0.94), 0.55, angle=180, follow_hand=True)
        return out

    # ---- camera ----------------------------------------------------------------------------------
    def camera(self, t):
        T = self.T
        if t < self.cut:
            q = ease(t / self.cut)
            cw, cx = 640 - 40 * q, 336 + 14 * q
            ch = cw * H / W
            cy = self.bg.height - ch / 2
        else:
            punch = ease_out((t - T['huge'] + 0.02) / 0.16)
            cw, cx = 560 - 44 * punch, 348
            ch = cw * H / W
            cy = self.bg.height - ch / 2 - 10 * punch
        x0 = min(max(cx - cw / 2, 0), self.bg.width - cw)
        y0 = min(max(cy - ch / 2, 0), self.bg.height - ch)
        return x0, y0, W / cw

    def background(self, cam):
        x0, y0, s = cam
        box = (x0 * 2, y0 * 2, (x0 + W / s) * 2, (y0 + H / s) * 2)
        return self.bg2.resize((W, H), Image.BICUBIC, box=box).convert('RGBA')

    @staticmethod
    def to_screen(cam, p):
        return ((p[0] - cam[0]) * cam[2], (p[1] - cam[1]) * cam[2])

    def place(self, frame, sprite, cam, xy, world_w, anchor=(0.5, 0.5), angle=0.0, alpha=1.0, sx=1.0, lit=True):
        s = world_w * cam[2] / sprite.width
        im = sprite.resize((max(1, round(sprite.width * s * sx)), max(1, round(sprite.height * s))), Image.BICUBIC)
        if lit:
            im = light_layer(im, LIGHT, rim_strength=0.35)
        if angle:
            im = im.rotate(-angle, Image.BICUBIC, expand=True)
        if alpha < 1:
            im.putalpha(im.getchannel('A').point(lambda v: int(v * alpha)))
        x, y = self.to_screen(cam, xy)
        frame.alpha_composite(im, (round(x - im.width * anchor[0]), round(y - im.height * anchor[1])))

    def shadow(self, frame, cam, xy, w, h, strength=0.55):
        x, y = self.to_screen(cam, xy)
        ww, hh = max(2, int(w * cam[2])), max(2, int(h * cam[2]))
        m = Image.new('L', (ww * 2, hh * 3), 0)
        ImageDraw.Draw(m).ellipse((ww * 0.5, hh, ww * 1.5, hh * 2), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(2, hh * 0.35)))
        dark = Image.new('RGBA', m.size, (60, 30, 18, 0))
        dark.putalpha(m.point(lambda v: int(v * strength)))
        frame.alpha_composite(dark, (int(x - m.width / 2), int(y - m.height / 2)))

    # ---- frame -------------------------------------------------------------------------------------
    def frame(self, t: float) -> Image.Image:
        T, cam = self.T, self.camera(t)
        frame = self.background(cam)
        pose = self.perf.pose(t)
        # Calendar on the wall (beat 1 shot).
        marks = 7 * ease_out((t - (T['so'] + 0.02)) / (T['week'] - T['so']))
        circle = ease((t - T['payday']) / 0.35)
        self.place(frame, self.calendar.render(max(0.0, marks), circle), cam, CAL_XY, CAL_W, anchor=(0, 0))
        # Chair behind the lead, contact shadows on the floor.
        ch = self.chair
        chair_w = 1.25 * D
        chair_x = LEAD_X - 0.12 * D                  # set back a little; its backrest shows beside the torso
        self.shadow(frame, cam, (chair_x, FLOOR - 26), chair_w * 1.0, 26, 0.35)
        self.place(frame, ch, cam, (chair_x, FLOOR - 30), chair_w, anchor=(0.5, 1.0))
        for fx in (0.50, 0.72):
            self.shadow(frame, cam, (LEAD_X + (fx + 0.12) * D, FLOOR - 4), 0.50 * D, 16, 0.6)
        self.shadow(frame, cam, (TABLE_X, FLOOR - 2), 1.0 * D, 30, 0.6)
        out = self.lead.render(pose, (LEAD_X, SEAT), cam[2], cam[:2], held=self.held(t), table_y=TABLE_TOP)
        layer, xy = out['body']
        frame.alpha_composite(layer, xy)
        # The table in front of the lead's body.
        self.place(frame, self.table, cam, (TABLE_X, FLOOR + 2), TABLE_W, anchor=(0.5, 1.0))
        if t >= self.cut:   # beat 2: the jar on the table, coins falling into it
            self.shadow(frame, cam, (JAR_X + 6, TABLE_TOP + 4), JAR_W * 1.05, 16, 0.45)
            jar_h = JAR_W * self.jar.height / self.jar.width
            level = self.jar_level(t)
            self.place(frame, props.coin_pile(self.coin, self.jar, level), cam, (JAR_X, TABLE_TOP + 2), JAR_W, anchor=(0.5, 1.0))
            for c in self.coins:
                at = c.at(t)
                if at:
                    self.place(frame, self.coin, cam, (at[0], at[1]), 0.15 * D, angle=at[2])
            self.place(frame, self.jar, cam, (JAR_X, TABLE_TOP + 2), JAR_W, anchor=(0.5, 1.0))
        if out['front']:
            frame.alpha_composite(*out['front'])
        if t < self.cut:
            for b in self.bill_flights:   # thrown up and out, then falling; the same size as in the hand
                u = t - b['t0']
                if 0 <= u < 1.3:
                    x = b['origin'][0] + b['vx'] * u
                    y = b['origin'][1] + b['vy'] * u + 0.5 * 1500 * u * u
                    self.place(frame, self.bill, cam, (x, y), self.bill_w, angle=-30 + b['spin'] * u, alpha=1 - ease((u - 0.9) / 0.4))
            u = t - self.moth_t
            if 0 <= u < 2.2:              # the moth flutters out of the upturned wallet
                x = self.moth_origin[0] - 0.10 * D - 90 * u + 18 * math.sin(u * 6)      # up and away to the left
                y = self.moth_origin[1] + 0.50 * D - 300 * u + 26 * math.sin(u * 8.5)
                flap = 0.45 + 0.55 * abs(math.sin(u * 40))
                self.place(frame, self.moth, cam, (x, y), 0.20 * D, sx=flap, alpha=1 - ease((u - 1.7) / 0.5))
        return frame


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


CHOICES = {'prop-chair': 'prop-chair-s4.png', 'prop-table': 'prop-table-s2.png', 'prop-wallet-empty': 'prop-wallet-empty-s21.png',
           'prop-moth': 'prop-moth-s8.png', 'prop-jar': 'prop-jar2-s17.png', 'prop-bill': 'prop-bill2-s19-clean.png'}


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
    im.save(dst)   # seeds picked after review


FRIEND_X = 645.0   # across the table from the lead


def two_shot(scene: 'Scene', t: float = 1.0) -> Image.Image:
    """The lead and the scarf friend at the table, framed like The Empty Chair's 03-together (for review)."""
    friend = Character(D=D, light=LIGHT, accessory='scarf')
    cw = 700.0
    cw = scene.bg.height * W / H          # the widest 9:16 crop of the room
    cam = (0.0, scene.bg.height - cw * H / W, W / cw)
    frame = scene.background(cam)
    floor = (FLOOR - 6 - SEAT) / D
    lead_pose = replace(scene.perf.idle(t, lead_base_pose()), turn=0.6, gaze_x=0.7, smile=0.6, brow_worry=0.3, lean=3,
                        lh=table_hand(-0.15), l_shape='resting', l_rot=-20, rh=table_hand(0.40), r_shape='resting', r_rot=25)
    friend_pose = replace(Idle(seed=3)(t, Pose()), seated=1.0, knees=-1.0, lf=(-0.72, floor), rf=(-0.50, floor - 0.02),
                          turn=-0.55, gaze_x=-0.7, smile=0.75, mouth_open=0.25, brow_raise=0.3,
                          lh=(-0.42, -0.80), l_shape='open_palm', l_rot=-35, l_bend=1, lean=-3,
                          rh=table_hand(0.30), r_shape='resting', r_rot=25)
    scene.shadow(frame, cam, (TABLE_X, FLOOR - 2), 1.0 * D, 30, 0.6)
    chair_x = LEAD_X - 0.12 * D
    scene.place(frame, scene.chair, cam, (chair_x, FLOOR - 30), 1.25 * D, anchor=(0.5, 1.0))
    outs = [c.render(p, (x, SEAT), cam[2], cam[:2], table_y=TABLE_TOP)
            for c, p, x in ((scene.lead, lead_pose, LEAD_X), (friend, friend_pose, FRIEND_X))]
    for o in outs:
        frame.alpha_composite(*o['body'])
    scene.place(frame, scene.table, cam, ((LEAD_X + FRIEND_X) / 2 - 20, FLOOR + 2), TABLE_W, anchor=(0.5, 1.0))
    for o in outs:
        if o['front']:
            frame.alpha_composite(*o['front'])
    return frame.convert('RGB')
