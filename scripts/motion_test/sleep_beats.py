"""Video 2, "Why you're always tired even after 8 hours of sleep": nine beats on the painted bases.

Each beat realises the script's motion recipes (text_slam, pose_swap, punch_in, impact_shake, push_in, icon_pop,
checklist_tick, split_compare) with the action library where it fits, plus the props the script asks for that
no base has (pipeline/rig/drawn.py). What came from the library and what was written by hand is listed per beat
in LIBRARY_REPORT at the bottom, for the render report.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from pathlib import Path

from PIL import Image

from pipeline import overlays as O
from pipeline.rig import drawn
from pipeline.rig.actions import Action, Idle, Performance, back_out, ease, ease_out, keys

from pipeline.rig.skeleton import Held

from beat_base import PROPS, Beat, W, H, lerp, punch, shake, word_times  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PROPS['clock'] = Image.open(ROOT / 'styles/cozy/bases/lead-bed-awake-bedroom/prop-clock.png').convert('RGBA')   # one brass clock in every bedroom base
TEXT_TOP = (540, 300)
TEXT_LOW = (540, 1130)             # above the caption band (captions centre on y 1348)


def tk(T: dict, *keys, default=None):
    """The time of the first key present (word timings can split a hyphenated word)."""
    for k in keys:
        if k in T:
            return T[k]
    return default


# ---- b01: hook (bed edge) -----------------------------------------------------------------------------
class B01(Beat):
    """'Eight hours in bed. Still exhausted?' Slumped on the bed edge, back of the hand to the forehead; on
    'exhausted' sits up with one palm turned up, eyes wide in weary disbelief. Text slams on 'Eight hours'."""
    base_name = 'lead-bed-edge-bedroom'

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        head, D = lead.s['head'], lead.D
        base = self.rest_pose('lead', turn=0.8, gaze_x=0.2, gaze_y=0.5, smile=-0.3, eye_open=0.5, brow_worry=0.8)
        brow = B((head[0] + 0.02 * D, head[1] - 0.08 * D))

        def slump(t, p):
            return replace(p, lh=brow, l_shape='back', l_rot=-50, l_bend=1, extra={'l_front': 1.0, 'l_droop': 0.0},
                           head_tilt=8, head_dy=0.03, lean=2.0, eye_open=0.45, brow_worry=1.0, smile=-0.4, gaze_y=0.6)

        def disbelief(t, p):
            q = back_out((t - T['exhausted']) / 0.3)
            return replace(p, lh=lead.rest('l'), l_shape='resting', l_rot=-15, rh=lerp(lead.rest('r'), B((600, 830)), q),
                           r_shape='open_palm', r_rot=40, extra={'r_droop': 0.6}, head_tilt=-4 * q, head_dy=-0.03 * q, lean=-1.5 * q,
                           eye_open=1.0, eye_wide=0.8 * q, brow_raise=0.9 * q, brow_worry=0.5, smile=-0.6, mouth_open=0.0,
                           turn=0.6, gaze_x=0.0, gaze_y=0.0)
        acts = [Action(self.t0 - 0.1, T['exhausted'] - 0.05, slump, 0.01, 0.2, 'hand on forehead, slumped'),
                Action(T['exhausted'] - 0.05, self.t1, disbelief, 0.25, 0.2, 'sit up: palm up, eyes wide')]
        self.events += [(T['eight'], 'pop', 0.5), (T['exhausted'], 'whoosh', 0.5)]
        self.slam_im = O.stacked(('Eight hours.', 'Still tired.'), (104, 84))
        return {'lead': Performance(base, Idle(seed=21), acts)}

    def camera(self, t):
        return self.stage.camera(450, 700, 880)

    def on_table(self, t):
        def draw(frame, cam):
            self.prop(frame, cam, 'clock')
        return draw

    def overlay(self, frame, t):
        O.slam(frame, self.slam_im, t, self.T['eight'], centre=TEXT_TOP)


# ---- b02: setup (asleep, top-down) --------------------------------------------------------------------
class B02(Beat):
    """'Sleep isn't just time. Quality can break without you ever waking up.' Asleep under the quilt from
    above; on 'break' holes open in the quilt with light pouring through and the camera punches in."""
    base_name = 'lead-bed-asleep-top-bedroom'

    def build(self):
        T = self.T
        B = self.p['lead'].body
        base = self.rest_pose('lead', turn=0.0, gaze_x=0.0, gaze_y=0.0, smile=0.5, eye_open=0.0,
                              lh=B((330, 720)), rh=B((560, 720)), extra={'l_under': 1.0, 'r_under': 1.0})   # arms well under the quilt

        def asleep(t, p):
            return replace(p, eye_open=0.0, smile=0.55, head_tilt=-4)

        def twitch(t, p):
            u = t - T['break']
            k = math.exp(-u * 6) * math.sin(u * 30) if u > 0 else 0.0
            return replace(p, eye_open=0.0, head_tilt=-4 + 3 * k, brow_worry=0.6 * ease(u / 0.2), smile=0.2)
        acts = [Action(self.t0 - 0.1, T['break'], asleep, 0.01, 0.1, 'asleep'),
                Action(T['break'], self.t1, twitch, 0.05, 0.3, 'a twitch under the quilt')]
        self.holes = [(T['break'] + 0.05 * k, xy, s) for k, (xy, s) in
                      enumerate((((380, 1000), 1), ((590, 1180), 2), ((300, 1340), 3), ((650, 1440), 4)))]
        self.events += [(self.t0 + 0.3, 'snore', 0.6), (self.t0 + 3.0, 'snore', 0.5), (T['quality'], 'pop', 0.5),
                        (T['break'], 'whoosh', 0.6), (T['break'] + 0.1, 'crack', 0.4)]
        self.slam_im = O.text_layer("Time isn't quality", 86)
        return {'lead': Performance(base, Idle(seed=22, breath_period=4.4), acts)}

    def camera(self, t):
        return self.stage.camera(470, 760, 941 - 141 * punch(t, self.T['break'], 1.0, 0.25))

    def on_table(self, t):
        def draw(frame, cam):
            D = self.p['lead'].D
            for t0, xy, seed in self.holes:
                if t < t0:
                    continue
                q = back_out((t - t0) / 0.3, 1.8)
                self.place(frame, drawn.hole(70, seed), cam, xy, 0.22 * D * q, lit=False)
                beam = drawn.light_shaft(90, 260, seed)
                a = ease((t - t0) / 0.4) * (0.75 + 0.25 * math.sin(t * 5 + seed))
                self.place(frame, beam, cam, (xy[0], xy[1] - 0.05 * D), 0.26 * D, anchor=(0.5, 1.0), alpha=a, lit=False)
        return draw

    def overlay(self, frame, t):
        O.slam(frame, self.slam_im, t, self.T['quality'], centre=TEXT_LOW)


# ---- b03: setup (airway, close on the pillow) ---------------------------------------------------------
class B03(Beat):
    """'Your airway muscles relax and block airflow. Your brain jolts you half-awake, again and again.'
    Close on the head on the pillow, eyes shut; a ribbon of air flows by the face and kinks shut on 'block';
    on 'jolts' and each 'again' the eyes snap half-open and the picture shakes."""
    base_name = 'lead-bed-awake-bedroom'

    def build(self):
        T = self.T
        base = self.rest_pose('lead', turn=0.2, gaze_x=0.0, gaze_y=-0.5, smile=0.4, eye_open=0.0,
                              extra={'l_under': 0.0, 'r_under': 0.0})
        self.jolts = [T['jolts'], T['again'], tk(T, 'again#2', default=T['again'] + 0.6)]

        def relax(t, p):
            return replace(p, eye_open=0.0, smile=0.45, mouth_open=0.08 + 0.06 * math.sin(t * 4))

        def jolt(t, p):
            k = min(1.0, sum(math.exp(-(t - x) * 4.5) for x in self.jolts if t >= x))
            return replace(p, eye_open=0.08 + 0.6 * k, eye_wide=0.7 * k, brow_raise=0.9 * k, brow_worry=0.3,
                           head_dy=-0.06 * k, head_tilt=-6 * k, mouth_open=0.0, smile=-0.8, gaze_y=-0.4)
        acts = [Action(self.t0 - 0.1, T['jolts'] - 0.05, relax, 0.01, 0.08, 'asleep, breathing'),
                Action(T['jolts'] - 0.05, self.t1, jolt, 0.06, 0.3, 'jolted half-awake')]
        self.events += [(self.t0 + 0.2, 'snore', 0.5), (T['block'], 'pop', 0.4)]
        self.events += [(x, 'rumble', 0.8) for x in self.jolts] + [(x + 0.02, 'thud', 0.35) for x in self.jolts[1:]]
        return {'lead': Performance(base, Idle(seed=23, breath_period=3.0), acts)}

    def camera(self, t):
        x0, y0, s = self.stage.camera(330, 720, 640)
        for x in self.jolts[1:]:
            dx, dy = shake(t, x, 1.0)
            x0 += dx
            y0 += dy
        return (x0, y0, s)

    def flying(self, t):
        T = self.T

        def draw(frame, cam):
            # the airway as a diagram above the head: a tube with air flowing through, pinching shut on 'block'
            head, D = self.p['lead'].s['head'], self.p['lead'].D
            kink = ease((t - T['block']) / 0.3) * (1 - ease((t - T['jolts']) / 0.25))
            phase = (t - self.t0) * 5.0 * (1 - 0.85 * kink)
            tube = drawn.ribbon(420, 120, kink, phase)
            self.place(frame, tube, cam, (head[0] + 0.12 * D, head[1] - 0.78 * D), 1.15 * D, alpha=0.95, lit=False)
        return draw


# ---- b04: reveal (café, the street through the window) -----------------------------------------------
class B04(Beat):
    """'That's sleep apnea. Tens of millions of Americans have it.' Both look out at the street, where small
    figures walk by yawning and slumped; the lead gestures at them on 'apnea'. The camera pushes in; the
    on-screen line slams on 'have it' (above the bottom quarter)."""
    base_name = 'together'
    cast = ('lead', 'friend')

    def build(self):
        T = self.T
        lead, fr = self.p['lead'], self.p['friend']
        B = lead.body
        lead_base = self.rest_pose('lead', turn=-0.5, gaze_x=-0.8, gaze_y=-0.9, smile=0.2, head_tilt=-4)
        fr_base = self.rest_pose('friend', turn=0.5, gaze_x=0.8, gaze_y=-0.9, smile=0.3, head_tilt=4)

        def gesture(t, p):
            q = back_out((t - T['apnea'] + 0.1) / 0.35)
            return replace(p, lh=lerp(lead.rest('l'), B((120, 690)), q), l_shape='open_palm', l_rot=-45,
                           extra={'l_droop': 0.5}, turn=-0.6, gaze_x=-0.8, gaze_y=-0.8, brow_raise=0.5 * q, mouth_open=0.2 * q)

        def nod(t, p):
            u = t - T['millions']
            return replace(p, head_dy=0.03 * math.sin(u * 7) * math.exp(-u * 2) if u > 0 else 0.0, smile=0.1, brow_worry=0.6)
        acts_l = [Action(T['apnea'] - 0.1, T['have'] - 0.1, gesture, 0.2, 0.3, 'palm at the window')]
        acts_f = [Action(T['millions'], self.t1, nod, 0.1, 0.3, 'slow nod')]
        rng = random.Random(44)                      # passers-by close to the glass, big enough to read as tired people
        self.walkers = [dict(x0=rng.uniform(20, 330), y=rng.choice((630, 650, 670)), v=rng.uniform(14, 24), h=rng.uniform(150, 185),
                             yawn=rng.random() < 0.6, slump=rng.uniform(0.2, 0.9), band=(10, 340), seed=k) for k in range(4)]
        self.walkers += [dict(x0=rng.uniform(735, 925), y=rng.choice((660, 680)), v=rng.uniform(14, 22), h=rng.uniform(140, 170),
                              yawn=rng.random() < 0.6, slump=rng.uniform(0.2, 0.9), band=(725, 935), seed=10 + k) for k in range(2)]
        self._fig_cache = {}
        self.events += [(T['apnea'], 'pop', 0.4), (T['have'], 'whoosh', 0.7), (T['have'] + 0.05, 'pop', 0.5)]
        self.slam_im = O.stacked(('Snoring + tired?', 'Ask a doctor.'), (72, 64))
        return {'lead': Performance(lead_base, Idle(seed=24), acts_l), 'friend': Performance(fr_base, Idle(seed=25), acts_f)}

    def camera(self, t):
        q = ease((t - self.t0) / max(0.1, self.t1 - self.t0))
        return self.stage.camera(470, 700, 941 - 80 * q)

    def figure(self, yawn, slump, step, seed):
        key = (yawn, round(slump, 1), round(step * 8) / 8, seed)
        if key not in self._fig_cache:
            self._fig_cache[key] = drawn.figure(180, 1.0 if yawn else 0.0, slump, key[2], seed)
        return self._fig_cache[key]

    def wall(self, t):
        def draw(frame, cam):
            u = t - self.t0
            for wk in self.walkers:
                lo, hi = wk['band']
                x = lo + (wk['x0'] - lo + wk['v'] * u) % (hi - lo)
                yawn = wk['yawn'] and (math.sin(u * 1.3 + wk['seed']) > -0.2)
                fig = self.figure(yawn, wk['slump'], (u * wk['v'] / 40) % 1.0, wk['seed'])
                bob = 3 * abs(math.sin(u * wk['v'] / 40 * math.pi))
                self.place(frame, fig, cam, (x, wk['y'] - bob), wk['h'] * 0.7, anchor=(0.5, 1.0), alpha=0.92, lit=False)
        return draw

    def overlay(self, frame, t):
        O.slam(frame, self.slam_im, t, self.T['have'], centre=TEXT_TOP)      # the clear wall/ceiling area, off the characters


# ---- b05: tension (the alarm) --------------------------------------------------------------------------
class B05(Beat):
    """'But maybe your night was fine. Then the trouble starts at the alarm.' Asleep, calm; on 'trouble' a
    squint; on 'alarm' the clock on the nightstand pops and rings, the far hand reaches over and slaps it."""
    base_name = 'lead-bed-awake-bedroom'

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        place = self.base.meta['places']['clock']
        cx, cy = place['xy']
        D = lead.D
        self.clock_top = (cx, cy - 0.40 * D)
        self.ring_at = T['alarm'] - 0.05
        self.slap_at = T['alarm'] + 0.5
        base = self.rest_pose('lead', turn=0.2, gaze_x=0.0, gaze_y=-0.5, smile=0.6, eye_open=0.0)

        def calm(t, p):
            return replace(p, eye_open=0.0, smile=0.6)

        def squint(t, p):
            q = ease((t - T['trouble']) / 0.3)
            return replace(p, eye_open=0.3 * q, brow_worry=1.0 * q, smile=0.4 - 0.7 * q, gaze_x=0.6 * q, turn=0.2 + 0.5 * q)

        def slap(t, p):
            u = t - self.ring_at
            reach = ease(u / 0.45)
            down = keys(u, [(0.45, 0.0), (0.55, 1.0), (0.7, 0.85)], ease_out)
            hand = lerp(lead.rest('r'), B((self.clock_top[0] - 0.04 * D, self.clock_top[1] - 0.12 * D)), reach)
            hand = (hand[0], hand[1] + 0.10 * down)
            return replace(p, rh=hand, r_shape='open_palm' if reach < 0.9 else 'back', r_rot=-20, r_bend=1,
                           extra={'r_droop': 0.3, 'r_front': 1.0}, eye_open=0.35 - 0.3 * down, brow_worry=1.0, smile=-0.5,
                           turn=0.8, gaze_x=0.8, head_tilt=4 * reach, mouth_open=0.0)
        acts = [Action(self.t0 - 0.1, T['trouble'] - 0.1, calm, 0.01, 0.2, 'asleep, calm'),
                Action(T['trouble'] - 0.1, self.ring_at, squint, 0.25, 0.1, 'squint'),
                Action(self.ring_at, self.t1, slap, 0.1, 0.3, 'reach over and slap the clock')]
        self.events += [(T['trouble'], 'pop', 0.3), (self.ring_at, 'alarm', 1.0), (self.slap_at, 'tap', 1.0), (self.slap_at + 0.05, 'thud', 0.3)]
        return {'lead': Performance(base, Idle(seed=26, breath_period=3.2), acts)}

    def camera(self, t):
        return self.stage.camera(440, 760, 900 - 90 * punch(t, self.ring_at, 1.0, 0.3))

    def on_table(self, t):
        def draw(frame, cam):
            ringing = self.ring_at <= t < self.slap_at
            u = t - self.ring_at
            pop = 1 + 0.25 * math.exp(-max(0.0, u) * 6) if u >= 0 else 1.0
            ang = 9 * math.sin(u * 2 * math.pi * 18) if ringing else 0.0
            self.prop(frame, cam, 'clock', width=0.33 * pop, angle=ang, dy=-6 * abs(math.sin(u * 2 * math.pi * 18)) if ringing else 0)
        return draw


# ---- b06: tension (sleep inertia, lamps) ---------------------------------------------------------------
class B06(Beat):
    """'That's sleep inertia. Parts of your brain wake up slower than others, so you start the day groggy.'
    On the bed edge, head heavy to one side, eyes nearly shut, hands limp; 'Sleep inertia' slams in, then on
    'slower' a row of small lamps pops in above the head, some lit, some dark."""
    base_name = 'lead-bed-edge-bedroom'

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=0.7, gaze_x=0.1, gaze_y=0.4, smile=-0.2, eye_open=0.25, head_tilt=5)

        def groggy(t, p):
            # the painted head is a wide oval (367 x 330 px): a big tilt skews it, so the heaviness is a small
            # tilt plus a slow sway and a sag of the head (user: the head looked oversized and misshapen)
            sway = math.sin(2 * math.pi * (t - self.t0) * 0.45)
            blink = 1 - 0.8 * math.exp(-((t - T['groggy'] - 0.2) / 0.25) ** 2)
            return replace(p, lh=B((400, 1060)), rh=B((572, 1050)), l_shape='resting', r_shape='resting', l_rot=-10, r_rot=10,
                           extra={'l_droop': 1.5, 'r_droop': 1.5}, lean=1.5 * sway, head_tilt=5 + 3 * sway, eye_open=0.25 * blink,
                           brow_worry=0.5, smile=-0.2, head_dy=0.035)
        acts = [Action(self.t0 - 0.1, self.t1, groggy, 0.01, 0.1, 'heavy head, limp hands, swaying')]
        self.lamps = [(T['slower'] + 0.11 * k, x, lit, cord) for k, (x, lit, cord) in
                      enumerate(((150, True, 170), (290, False, 230), (430, True, 150), (570, True, 210), (710, False, 180)))]
        self.events += [(T['sleep'], 'pop', 0.5)]
        self.events += [(t0, 'click' if lit else 'pop', 0.8 if lit else 0.3) for t0, _, lit, _ in self.lamps]
        self.slam_im = O.text_layer('Sleep inertia', 96)
        return {'lead': Performance(base, Idle(seed=27), acts)}

    def camera(self, t):
        return self.stage.camera(430, 640, 760)

    def wall(self, t):
        def draw(frame, cam):
            D = self.p['lead'].D
            for t0, x, lit, cord in self.lamps:
                if t < t0:
                    continue
                q = back_out((t - t0) / 0.35, 1.6)
                sprite = drawn.lamp(160, lit, cord, 30 + x)
                self.place(frame, sprite, cam, (x, -10), 0.42 * D * (0.6 + 0.4 * q), anchor=(0.5, 0.0), lit=False)
        return draw

    def on_table(self, t):
        def draw(frame, cam):
            self.prop(frame, cam, 'clock')
        return draw

    def overlay(self, frame, t):
        O.slam(frame, self.slam_im, t, self.T['sleep'], centre=TEXT_TOP, until=self.T['slower'] - 0.25)


# ---- b07: tension (stress) -----------------------------------------------------------------------------
class B07(Beat):
    """'And stress? It keeps your nervous system alert, even while you sleep.' Lying awake, eyes wide at the
    ceiling; a tangled knot of red thread bobs above the face from 'stress'; on 'alert' the head turns, brows
    tense and both hands grip the quilt."""
    base_name = 'lead-bed-awake-bedroom'

    def build(self):
        T, lead = self.T, self.p['lead']
        B = lead.body
        base = self.rest_pose('lead', turn=0.15, gaze_x=0.0, gaze_y=-0.8, smile=-0.1, eye_open=1.0, eye_wide=0.5, brow_worry=0.3)

        def stare(t, p):
            return replace(p, eye_wide=0.5 + 0.2 * math.sin(t * 3), gaze_x=0.15 * math.sin(t * 1.7))

        def alert(t, p):
            q = ease((t - T['alert'] + 0.05) / 0.3)
            return replace(p, turn=0.15 + 0.75 * q, gaze_x=0.8 * q, gaze_y=-0.8 + 0.6 * q, head_tilt=9 * q, brow_worry=1.0,
                           eye_wide=0.8, smile=-0.5, lh=lerp(lead.rest('l'), B((400, 900)), q), rh=lerp(lead.rest('r'), B((600, 815)), q),
                           l_shape='fist' if q > 0.5 else 'resting', r_shape='fist' if q > 0.5 else 'resting', l_rot=-10, r_rot=10)
        acts = [Action(self.t0 - 0.1, T['alert'] - 0.05, stare, 0.01, 0.1, 'stare at the ceiling'),
                Action(T['alert'] - 0.05, self.t1, alert, 0.25, 0.2, 'turn, tense, grip the quilt')]
        self.events += [(T['stress'], 'pop', 0.6), (T['alert'], 'whoosh', 0.5), (T['keeps'], 'pop', 0.4)]
        self.slam_im = O.text_layer('Stress keeps you alert', 78)
        return {'lead': Performance(base, Idle(seed=28), acts)}

    def camera(self, t):
        return self.stage.camera(430, 760, 880)

    def flying(self, t):
        T = self.T

        def draw(frame, cam):
            if t < T['stress']:
                return
            head, D = self.p['lead'].s['head'], self.p['lead'].D
            u = t - T['stress']
            q = back_out(u / 0.4, 1.7)
            tangle = min(1.0, 0.4 + u / 0.6)
            knot = drawn.knot(220, 3, tangle)
            bob = 0.03 * D * math.sin(u * 2.4)
            self.place(frame, knot, cam, (head[0] + 0.1 * D, head[1] - 0.80 * D + bob), 0.9 * D * q, angle=10 * math.sin(u * 1.3), lit=False)
        return draw

    def on_table(self, t):
        def draw(frame, cam):
            self.prop(frame, cam, 'clock')
        return draw

    def overlay(self, frame, t):
        O.slam(frame, self.slam_im, t, self.T['keeps'], centre=TEXT_LOW)


# ---- b08: payoff (bedroom, the three causes big) ------------------------------------------------------
class B08(Beat):
    """'So eight hours can still be broken sleep, a slow brain reboot, or a body that never powered down.'
    The lead on the bed edge, counting the causes: above the bed three big pictures pop in on their words
    (a holed quilt scrap, a dim lamp, a coiled spring), each with a two-line label; the lead looks up at each
    as it lands and sags on 'down'."""
    base_name = 'lead-bed-edge-bedroom'
    COLS = (175, 470, 745)                       # screen x of the three columns, inside the safe area (right edge 930)
    PIC_Y, LABEL_Y = 185, 362                   # picture centre and label-block centre (screen px), above the head

    def build(self):
        T, lead = self.T, self.p['lead']
        self.items = [(T['broken'], 'scrap', ('Broken', 'sleep')), (T['slow'], 'lamp', ('Slow', 'reboot')),
                      (T['never'], 'spring', ('Never', 'powered down'))]
        self.labels = [O.stacked(lines, (px, px)) for (_, _, lines), px in zip(self.items, (48, 48, 44))]   # the long label a little smaller so the columns stay apart
        base = self.rest_pose('lead', turn=0.7, gaze_x=0.1, gaze_y=0.0, smile=-0.35, eye_open=0.75, head_tilt=3)

        def count(t, p):
            # look up at the column that just landed (gaze and a small turn), then sag on 'down'
            gx, k = 0.0, 0.0
            for i, (t0, _, _) in enumerate(self.items):
                if t >= t0 - 0.05:
                    k = ease((t - t0 + 0.05) / 0.25)
                    gx = (-0.7, 0.0, 0.7)[i]
            sag = ease((t - T['down']) / 0.4)
            return replace(p, gaze_y=-0.9 * k + 0.9 * sag, gaze_x=gx * k * (1 - sag), turn=0.7 + 0.2 * gx * k, head_tilt=3 - 6 * gx * k + 10 * sag,
                           head_dy=-0.02 * k + 0.05 * sag, brow_raise=0.6 * k * (1 - sag), brow_worry=0.3 + 0.6 * sag, eye_open=0.75 - 0.45 * sag,
                           smile=-0.35 - 0.4 * sag, mouth_open=0.0, lean=-1.0 * k + 2.0 * sag)
        self.events += [(t0, 'pop', 0.8) for t0, _, _ in self.items] + [(T['so'], 'paper', 0.4), (T['down'] + 0.1, 'thud', 0.4)]
        return {'lead': Performance(base, Idle(seed=31), [Action(self.t0 - 0.1, self.t1, count, 0.01, 0.1, 'look up at each cause, then sag')])}

    def camera(self, t):
        return self.stage.camera(470, 782, 940)

    def on_table(self, t):
        def draw(frame, cam):
            self.prop(frame, cam, 'clock')
        return draw

    def overlay(self, frame, t):
        # the three causes: picture pops in (overshoot), its label slams in just after
        for i, (t0, kind, _) in enumerate(self.items):
            if t < t0:
                continue
            q = back_out((t - t0) / 0.35, 1.7)
            x = self.COLS[i]
            if kind == 'scrap':
                im = drawn.scrap_holed(200)
                w = int(250 * q)
            elif kind == 'lamp':
                im = drawn.lamp(160, False, 30)
                w = int(240 * q)
            else:
                sq = 0.3 * max(0.0, math.sin((t - t0) * 6)) * math.exp(-(t - t0) * 2)
                im = drawn.spring(110, 240, 7, sq)
                w = int(130 * q)
            if w < 2:
                continue
            im = im.resize((w, max(1, int(im.height * w / im.width))), Image.BICUBIC)
            frame.alpha_composite(im, (int(x - im.width / 2), int(self.PIC_Y - im.height / 2)))
            O.slam(frame, self.labels[i], t, t0 + 0.12, centre=(x, self.LABEL_Y))


# ---- b09: ending (café two-shot) -----------------------------------------------------------------------
class B09(Beat):
    """'Next time you're wiped out, ask: was that eight hours, or eight good ones?' The two labels of the
    comparison slide in on each 'eight'; on 'good ones' the lead lifts their cup toward the friend with a
    smile and the friend nods, both hands around the drained cup."""
    base_name = 'together'
    cast = ('lead', 'friend')

    def build(self):
        T = self.T
        lead, fr = self.p['lead'], self.p['friend']
        cups = self.base.meta['cups']
        lead_base = self.rest_pose('lead', turn=0.8, gaze_x=0.6, smile=0.4)
        fr_base = self.rest_pose('friend', turn=-0.8, gaze_x=-0.6, smile=0.5)

        def cup_centre(c):
            x, y = c['xy']
            w, h = c['size']
            return (x + w * 0.5, y + h * 0.45)
        self.lift_from = T['good'] - 0.2
        side = lead.far
        home = cup_centre(cups[0])
        sh = lead.shoulders[side]
        raised = (sh[0] + 0.24 * lead.D * lead.facing, sh[1] - 0.06 * lead.D)
        Bl, Bf = lead.body, fr.body

        def toast(t, p):
            xy = keys(t, [(self.lift_from, home), (T['good'] + 0.25, raised), (self.t1, raised)])
            q = ease((t - T['good']) / 0.3)
            kw = {side + 'h': Bl(xy), side + '_shape': 'grip', side + '_rot': 0.0, 'extra': {side + '_droop': 1.2, side + '_front': 1.0}}
            return replace(p, smile=0.95, brow_raise=0.4 * q, mouth_open=0.1 * q, lean=1.5 * q, gaze_x=0.6, **kw)

        def wiped(t, p):
            q = ease((t - T['wiped']) / 0.3) * (1 - ease((t - T['ask']) / 0.3))
            return replace(p, eye_open=1 - 0.5 * q, head_tilt=-6 * q, smile=0.4 - 0.5 * q, brow_worry=0.6 * q)
        fc = cup_centre(cups[1])

        def hands_on_cup(t, p):
            nod = 0.035 * math.sin((t - T['good']) * 9) * math.exp(-(t - T['good']) * 2.5) if t > T['good'] else 0.0
            return replace(p, lh=Bf((fc[0] - 0.13 * fr.D, fc[1] + 0.02 * fr.D)), rh=Bf((fc[0] + 0.13 * fr.D, fc[1] + 0.02 * fr.D)),
                           l_shape='grip', r_shape='grip', l_rot=-20, r_rot=20, head_dy=nod, smile=0.6 + 0.3 * ease((t - T['good']) / 0.3),
                           gaze_x=-0.6, turn=-0.75)
        perf = {'lead': Performance(lead_base, Idle(seed=29), [Action(T['wiped'] - 0.1, T['ask'] + 0.2, wiped, 0.2, 0.3, 'wiped out'),
                                                                 Action(self.lift_from - 0.2, self.t1, toast, 0.15, 0.1, 'lift the cup to the friend')]),
                'friend': Performance(fr_base, Idle(seed=30), [Action(self.t0 - 0.1, self.t1, hands_on_cup, 0.01, 0.1, 'hands around the cup, nod')])}
        self.events += [(T['eight'], 'pop', 0.5), (tk(T, 'eight#2', default=T['good'] - 0.3), 'pop', 0.5), (T['good'], 'sparkle', 0.7)]
        return perf

    def holds(self, name, t):
        if name == 'lead':
            return {self.p['lead'].far} if t >= self.lift_from else set()
        return {'l', 'r'}

    def held(self, name, t):
        if name != 'lead' or t < self.lift_from:
            return {}
        cup = self.base.cups[0][0]
        return {self.p['lead'].far: Held(cup, (cup.width * 0.5, cup.height * 0.45), cup.width / self.p['lead'].D)}

    def cups_away(self, t):
        return {0} if t >= self.lift_from else set()

    def camera(self, t):
        q = ease((t - self.t0) / max(0.1, self.t1 - self.t0))
        return self.stage.camera(470, 836, 941 - 50 * q)

    def overlay(self, frame, t):
        T = self.T
        O.split_compare(frame, t, 'eight hours', 'eight good ones?', T['eight'], tk(T, 'eight#2', default=T['good'] - 0.3), centre=(540, 330))


BEATS = {'b01': B01, 'b02': B02, 'b03': B03, 'b04': B04, 'b05': B05, 'b06': B06, 'b07': B07, 'b08': B08, 'b09': B09}

# What each beat took from the library and what had to be written for this script.
LIBRARY_REPORT = {
    'b01': dict(library=['text_slam (overlays.slam/stacked)', 'hand-to-forehead pose (as actions.facepalm)', 'Idle'],
                hand_written=['sit-up palm-up pose_swap'], props=['clock (base prop)']),
    'b02': dict(library=['text_slam', 'punch_in (beat_base.punch)', 'Idle breathing'],
                hand_written=['asleep/twitch poses'], props=['quilt holes + light shafts (drawn.hole, drawn.light_shaft)']),
    'b03': dict(library=['impact_shake (beat_base.shake)', 'Idle'], hand_written=['relax/jolt poses'],
                props=['airway ribbon (drawn.ribbon)']),
    'b04': dict(library=['together base', 'push_in camera', 'text_slam', 'Idle'], hand_written=['window gaze + palm gesture', 'friend nod'],
                props=['street figures (drawn.figure)']),
    'b05': dict(library=['push_in', 'icon_pop (scale pop)', 'Idle'], hand_written=['squint + reach-and-slap'],
                props=['clock (base prop, rings and shakes)']),
    'b06': dict(library=['text_slam', 'icon_pop'], hand_written=['heavy-head sway pose'], props=['lamp row (drawn.lamp)', 'clock']),
    'b07': dict(library=['text_slam', 'icon_pop', 'Idle'], hand_written=['stare + grip-the-quilt pose_swap'], props=['thread knot (drawn.knot)', 'clock']),
    'b08': dict(library=['bed-edge base', 'icon_pop (scale pop) x3', 'text_slam labels', 'Idle'], hand_written=['look up at each cause, then sag'],
                props=['holed quilt scrap, dark lamp, coil spring (drawn.scrap_holed/lamp/spring)', 'clock']),
    'b09': dict(library=['together base', 'cup lift (as salary b09 toast)', 'split_compare (overlays.split_compare, new)'],
                hand_written=['friend: hands around the cup + nod'], props=['base cups']),
}
