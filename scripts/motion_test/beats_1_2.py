"""Animated test clip: beats 1-2 of the salary episode with the cut-out rig and Kokoro narration.

Every action is keyed to a narration word (Kokoro word timings), so the same choreography
re-times itself for another voice. Backgrounds and the wallet/coin art come from SDXL + the
cozy LoRA (empty scenes, no people); the character is the jointed rig in pipeline/rig.

Run with .venv/bin/python from the repository root:
  scripts/motion_test/beats_1_2.py --voice am_michael [--still 3.0] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pipeline.rig import props  # noqa: E402
from pipeline.rig.actions import Action, Idle, Performance, back_out, ease, ease_out, keys  # noqa: E402
from pipeline.rig.skeleton import Character, Held, Pose  # noqa: E402

W, H, FPS = 1080, 1920, 30
OUT_DIR = ROOT / 'renders/motion-test'
ASSETS = OUT_DIR / 'assets'
VOICE_DIR = OUT_DIR / 'voice'
FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'
LEAD, GAP, TAIL = 0.45, 0.40, 0.95
SR = 24000

# World = the SDXL background's pixel space (768x1344).
BG_FILE = ASSETS / 'bg-cafe-s11.png'
D = 200.0                      # head diameter, world px
BASE = (500.0, 1042.0)         # hip root when standing; feet land near y=1290
CAL_BOX = (452, 362, 150)      # calendar top-left x, y and width (world)
JAR_FOOT, JAR_W = (300.0, 905.0), 112.0

ap = argparse.ArgumentParser()
ap.add_argument('--voice', default='am_michael')
ap.add_argument('--still', type=float, nargs='*', help='render stills at these times instead of the video')
ap.add_argument('--wallet', default='prop-wallet-s5.png')
ap.add_argument('--out')
args = ap.parse_args()


# ---- narration timing ----------------------------------------------------------------------------
def load_beat(beat):
    words = json.loads((VOICE_DIR / f'{args.voice}-{beat}.words.json').read_text())
    with wave.open(str(VOICE_DIR / f'{args.voice}-{beat}.wav')) as f:
        audio = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
    return words, audio


w1, a1 = load_beat('b01')
w2, a2 = load_beat('b02')
B1 = LEAD
B2 = B1 + len(a1) / SR + GAP
END = B2 + len(a2) / SR + TAIL
CUT = B2 - GAP / 2


def word(words, start, text):
    for w in words:
        if w['word'].lower().strip('.,?!') == text.lower():
            return start + w['start']
    raise KeyError(text)


T = dict(payday=word(w1, B1, 'payday'), feels=word(w1, B1, 'feels'), rich=word(w1, B1, 'rich'), so=word(w1, B1, 'so'),
         broke=word(w1, B1, 'broke'), a=word(w1, B1, 'a'), week=word(w1, B1, 'week'), later=word(w1, B1, 'later'),
         say=word(w2, B2, 'say'), amount=word(w2, B2, '1,500'), lands=word(w2, B2, 'lands'), every=word(w2, B2, 'every'),
         other=word(w2, B2, 'other'), friday=word(w2, B2, 'friday'), it=word(w2, B2, 'it'), feels2=w2[-2]['start'] + B2,
         huge=word(w2, B2, 'huge'))
print('timeline', {k: round(v, 2) for k, v in T.items()}, 'end', round(END, 2), flush=True)

# ---- performance ---------------------------------------------------------------------------------
base = Pose(turn=0.15, smile=0.55)


def cash_fan(t, p):
    """'Payday feels rich': fanning yourself with cash, hand on hip, chin up, eyes happily shut."""
    raise_ = ease((t - (T['payday'] - 0.25)) / 0.3)          # cash at the chest, then up beside the face
    fan = math.sin(2 * math.pi * (t - T['payday']) * 2.6) * raise_
    rich = keys(t - T['rich'], [(-0.1, 0), (0.0, 0.0), (0.08, 0.07), (0.22, 0.0)])
    bliss = ease((t - T['feels']) / 0.2)
    hand = (0.58 + 0.22 * raise_, -1.05 - 0.47 * raise_)
    return replace(p, rh=hand, r_shape='grip', r_rot=-8 + 22 * fan, r_bend=1,
                   lh=(-0.50, -0.28), l_shape='fist', l_rot=60, l_bend=1,
                   lean=-3.5, head_tilt=-7, smile=0.95, mouth_open=0.25 * bliss, eye_open=1 - 0.8 * bliss,
                   brow_raise=0.5, lift=rich, turn=0.25)


def calendar_glance(t, p):
    """'So why are you broke...': the fun stops; eyes go up to the calendar as the X marks land."""
    shock = ease((t - T['so']) / 0.25)
    flinch = sum(0.012 * math.exp(-((t - (T['so'] + 0.15 * k)) / 0.05) ** 2) for k in range(7))
    return replace(p, rh=(0.55, -1.15), r_shape='grip', r_rot=10, lh=(-0.42, -0.15), l_shape='relaxed',
                   turn=-0.15, gaze_x=0.1, gaze_y=-1.0, head_tilt=-9, head_dy=-flinch,
                   eye_wide=0.7 * shock, smile=0.55 - 0.9 * shock, brow_raise=0.7 * shock, mouth_open=0.0)


def slump(t, p):
    """'broke': a thud - knees give, shoulders drop, head sinks."""
    u = t - T['broke']
    drop = keys(u, [(0, 0), (0.08, 1.0), (0.25, 0.75)], ease_out)
    return replace(p, crouch=0.10 * drop, stretch=-0.05 * drop, head_dy=0.05 * drop, lean=2 * drop,
                   rh=(0.45, -0.25), r_shape='relaxed', lh=(-0.50, -0.30), l_shape='grip', l_rot=0,
                   smile=-0.6, brow_worry=1.0, brow_raise=0.2, eye_wide=0.2, turn=0.1, gaze_y=0.2)


def wallet_shake(t, p):
    """'a week later': wallet up, turned upside down, shaken - a moth flutters out; eyes follow it."""
    u = t - T['a']
    up = ease(u / 0.22)
    shake = math.sin(2 * math.pi * u * 7.5) * ease((u - 0.22) / 0.1) * (1 - ease((t - T['later'] - 0.45) / 0.2))
    follow = ease((t - (T['later'] - 0.05)) / 0.5)
    return replace(p, lh=(-0.30 - 0.48 * up + 0.05 * shake, -1.55 * up - 0.3 * (1 - up) - 0.04 * abs(shake)), l_shape='grip', l_rot=-20 + 10 * shake,
                   l_bend=1, rh=(0.40, -0.25), r_shape='relaxed', crouch=0.04, stretch=-0.01,
                   smile=-0.45, brow_worry=1.0, brow_raise=0.3, eye_wide=0.25,
                   turn=-0.2 + 0.45 * follow, gaze_x=-0.6 + 1.3 * follow, gaze_y=-0.2 - 0.6 * follow, head_tilt=-4 * follow)


def facepalm(t, p):
    """'...later?': free hand to the forehead, eyes shut, head drops back a touch."""
    q = ease((t - (T['later'] + 0.25)) / 0.3)
    return replace(p, rh=(0.06, -1.80), r_shape='open', r_rot=0, r_bend=1, head_tilt=-5 * q, eye_open=1 - 0.85 * q,
                   brow_worry=1.0, smile=-0.55, lean=-2.5 * q, lh=(-0.55, -0.45), l_shape='grip', l_rot=-10)


def cut_turn(t, p):
    """Beat 2 opens on the jar: turned toward it, rubbing hands."""
    k = math.sin(2 * math.pi * (t - CUT) * 4.5) * 0.05
    return replace(p, lh=(-0.12 + k, -0.80), rh=(0.10 + k, -0.78), l_shape='relaxed', r_shape='relaxed', l_rot=-70, r_rot=70,
                   l_bend=1, r_bend=1, turn=-0.75, gaze_x=-0.7, gaze_y=0.4, smile=0.8, brow_raise=0.35, lean=-2.5)


def coin_rain(t, p):
    """'Say $1,500 lands': hands clasped under the chin, bouncing, eyes tracking the falling coins."""
    bounce = abs(math.sin(2 * math.pi * (t - T['amount']) * 2.2)) * 0.035
    track = 0.5 + 0.5 * math.sin(2 * math.pi * (t - T['amount']) * 1.6)
    return replace(p, lh=(-0.11, -0.98), rh=(0.11, -0.96), l_shape='fist', r_shape='fist', l_rot=-80, r_rot=80, l_bend=1, r_bend=1,
                   lift=bounce, turn=-0.7, gaze_x=-0.8, gaze_y=-0.9 + 1.4 * track, eye_wide=0.55, smile=0.95, mouth_open=0.35,
                   brow_raise=0.7, lean=-2)


def fist_pump(t, p):
    """'lands': a quick 'yes!' fist pump."""
    u = t - T['lands']
    pump = keys(u, [(0, 0), (0.12, 1.0), (0.22, 0.7), (0.32, 1.0)], ease_out)
    return replace(p, rh=(0.55, -1.0 - 0.75 * pump), r_shape='fist', r_rot=-80, r_bend=1, lh=(-0.42, -0.2), l_shape='relaxed',
                   crouch=0.06 * (1 - pump), smile=1.0, mouth_open=0.6 * pump, eye_open=1 - 0.75 * pump, turn=-0.3)


def jar_pats(t, p):
    """'every other Friday': two proud pats on the jar lid, then a satisfied nod."""
    pats = [T['every'], T['other']]
    lift = min(min(abs(t - x) for x in pats) / 0.12, 1.0)
    jar_lid = ((JAR_FOOT[0] - BASE[0]) / D + 0.05, (JAR_FOOT[1] - JAR_W * 1.25 - BASE[1]) / D - 0.06)
    hand = (jar_lid[0], jar_lid[1] - 0.22 * lift)
    nod = math.exp(-((t - T['friday'] - 0.15) / 0.12) ** 2) * 0.06
    return replace(p, lh=hand, l_shape='open', l_rot=-70, l_bend=1, rh=(0.44, -0.14), r_shape='relaxed', r_rot=0, r_bend=1,
                   turn=-0.55, gaze_x=-0.6, gaze_y=0.5, head_dy=nod, smile=0.85, brow_raise=0.3, lean=-5)


def feels_huge(t, p):
    """'It feels huge': anticipation dip on 'It', arms flung wide with a hop that peaks on 'huge', squash on landing."""
    u = t - T['huge']
    pre = ease((t - T['it']) / 0.2) * (1 - ease((u + 0.12) / 0.1))
    up = back_out((u + 0.14) / 0.28)
    lift = keys(u, [(-0.14, 0.0), (0.0, 0.22), (0.18, 0.0), (0.26, 0.0)])
    land = keys(u, [(0.1, 0.0), (0.2, 0.09), (0.45, 0.0)])
    squash = keys(u, [(-0.3, 0.0), (-0.16, -0.05), (-0.05, 0.07), (0.16, 0.0), (0.22, -0.06), (0.45, 0.0)])
    lx, ly = -0.48 - 0.37 * up, -0.05 - 1.95 * up             # wound down at the hips, then flung up and out
    return replace(p, lh=(lx, ly), rh=(-lx, ly),
                   l_shape='open' if up > 0.3 else 'fist', r_shape='open' if up > 0.3 else 'fist', l_rot=-15, r_rot=15,
                   l_bend=1, r_bend=1, crouch=0.12 * pre + land, lift=max(0.0, lift), stretch=squash,
                   turn=0.0, gaze_x=0, gaze_y=-0.2, smile=1.0, mouth_open=0.3 * pre + 0.9 * ease((u + 0.1) / 0.12),
                   eye_wide=0.7, brow_raise=0.9)


acts = [
    Action(0.0, T['so'] - 0.05, cash_fan, 0.0, 0.2, 'fan self with cash'),
    Action(T['so'], T['broke'], calendar_glance, 0.15, 0.08, 'look up at the calendar'),
    Action(T['broke'], T['a'] + 0.05, slump, 0.06, 0.1, 'slump on "broke"'),
    Action(T['a'], T['later'] + 0.35, wallet_shake, 0.12, 0.25, 'wallet upside down, shake'),
    Action(T['later'] + 0.25, CUT, facepalm, 0.25, 0.01, 'hand to forehead'),
    Action(CUT, T['amount'] + 0.1, cut_turn, 0.01, 0.2, 'rub hands at the jar'),
    Action(T['amount'], T['lands'] - 0.02, coin_rain, 0.2, 0.08, 'watch the coins, bouncing'),
    Action(T['lands'], T['every'] - 0.1, fist_pump, 0.06, 0.15, 'fist pump'),
    Action(T['every'] - 0.25, T['it'] - 0.05, jar_pats, 0.2, 0.2, 'pat the jar twice, nod'),
    Action(T['it'], END, feels_huge, 0.12, 0.3, 'arms wide, hop'),
]
lead = Performance(base, Idle(seed=11), acts)
char = Character(D=D, shade_side='right')

# ---- props & effects -------------------------------------------------------------------------------
wallet_img = props.key_out(str(ASSETS / args.wallet))
wallet = Held(wallet_img, (wallet_img.width * 0.5, wallet_img.height * 0.88), 0.62)
coin_path = next(iter(sorted(ASSETS.glob('prop-coin-*.png'))), None)
coin = props.coin_sprite(str(coin_path) if coin_path else None, 120)
calendar = props.Calendar(width=600)
jar = props.Jar(width=420)
N_BILLS = 4
bill = props.bill_sprite(300)
rng = random.Random(4)
bill_flights = [dict(t0=T['so'] + 0.06 + 0.17 * k, vx=rng.uniform(260, 420) * (1 if k % 2 else -0.6), vy=rng.uniform(-520, -380),
                     spin=rng.uniform(-420, 420)) for k in range(N_BILLS)]
N_COINS = 16
jar_h = JAR_W * 1.25
jar_top = JAR_FOOT[1] - jar_h
drop_times = [T['amount'] + 0.05 + (T['lands'] - T['amount'] - 0.4) * (i / (N_COINS - 1)) ** 0.85 for i in range(N_COINS)]
coins = []
for i, td in enumerate(drop_times):
    level = (i + 1) / N_COINS
    y1 = jar_top + jar_h * props.Jar.surface_y(level) - 6
    f = props.Falling(t0=0, x=JAR_FOOT[0] + rng.uniform(-28, 28), y0=jar_top - 520, y1=y1, spin=rng.uniform(-600, 600))
    land = td if i < N_COINS - 1 else T['lands']   # the last coin lands exactly on "lands"
    f.t0 = land - math.sqrt(2 * (f.y1 - f.y0) / f.g)
    coins.append(f)


def jar_level(t):
    return sum(1 for c in coins if c.landed_at() <= t) / N_COINS


def world_joint(t, key):
    """Where a joint was at time t, in world px (used to launch things that leave the hand)."""
    j = char.joints(lead.pose(t), BASE, 1.0, (0.0, 0.0))
    return j[key]


for b in bill_flights:
    b['origin'] = world_joint(b['t0'], 'r_wr')
MOTH_T = T['later'] - 0.02
MOTH_ORIGIN = world_joint(MOTH_T, 'l_wr')


def held_props(t, pose):
    out = {}
    if t < T['broke']:
        flown = sum(1 for b in bill_flights if t >= b['t0'])
        if flown < N_BILLS:
            fan, grip = props.bill_fan(N_BILLS - flown, 15, 300)
            out['r'] = Held(fan, grip, 0.85, angle=-10, follow_hand=0.55)
    if T['broke'] + 0.05 <= t < CUT:
        u = t - T['a']
        flip = 180 * ease((u - 0.05) / 0.22)
        shake = 14 * math.sin(2 * math.pi * u * 7.5) * ease((u - 0.22) / 0.1) * (1 - ease((t - T['later'] - 0.45) / 0.2))
        grow = 0.6 + 0.4 * ease((t - T['broke'] - 0.05) / 0.12)  # slides out of the pocket
        out['l'] = replace(wallet, angle=flip + shake, size_d=wallet.size_d * grow)
    return out


# ---- camera ------------------------------------------------------------------------------------------
BG = Image.open(BG_FILE).convert('RGB')
BG2 = BG.resize((BG.width * 2, BG.height * 2), Image.LANCZOS)


def camera(t):
    """(origin_x, origin_y, scale). Shot 1 pushes in slowly; shot 2 punches in on 'huge'."""
    if t < CUT:
        q = ease((t - 0) / CUT)
        cw, cx, cy = 600 - 50 * q, 470, 818
    else:
        punch = ease_out((t - T['huge'] + 0.02) / 0.16)
        cw, cx, cy = 560 - 45 * punch, 420, 846 - 10 * punch
    ch = cw * H / W
    x0 = min(max(cx - cw / 2, 0), BG.width - cw)
    y0 = min(max(cy - ch / 2, 0), BG.height - ch)
    return x0, y0, W / cw


def background(cam):
    x0, y0, s = cam
    box = (x0 * 2, y0 * 2, (x0 + W / s) * 2, (y0 + H / s) * 2)
    return BG2.resize((W, H), Image.BICUBIC, box=box).convert('RGBA')


def to_screen(cam, p):
    return ((p[0] - cam[0]) * cam[2], (p[1] - cam[1]) * cam[2])


def place(frame, sprite, cam, world_xy, world_w, anchor=(0.5, 0.5), angle=0.0, alpha=1.0):
    s = world_w * cam[2] / sprite.width
    im = sprite.resize((max(1, round(sprite.width * s)), max(1, round(sprite.height * s))), Image.BICUBIC)
    if angle:
        im = im.rotate(-angle, Image.BICUBIC, expand=True)
    if alpha < 1:
        im.putalpha(im.getchannel('A').point(lambda v: int(v * alpha)))
    x, y = to_screen(cam, world_xy)
    frame.alpha_composite(im, (round(x - im.width * anchor[0]), round(y - im.height * anchor[1])))


SHADOW = Image.new('L', (400, 120), 0)
ImageDraw.Draw(SHADOW).ellipse((40, 30, 360, 90), fill=150)
SHADOW = SHADOW.filter(ImageFilter.GaussianBlur(18))


def shadow(frame, cam, pose):
    x, y = to_screen(cam, (BASE[0], BASE[1] + 1.30 * D))
    w = 1.25 * D * cam[2] * (1 - 0.35 * pose.lift)
    m = SHADOW.resize((max(1, int(w)), max(1, int(w * 0.3))))
    dark = Image.new('RGBA', m.size, (70, 36, 20, 0))
    dark.putalpha(m.point(lambda v: int(v * (0.75 - 0.4 * pose.lift))))
    frame.alpha_composite(dark, (int(x - m.width / 2), int(y - m.height / 2)))


# ---- text --------------------------------------------------------------------------------------------
def text_layer(text, size, fill=(255, 255, 255), stroke=(22, 22, 46)):
    font = ImageFont.truetype(FONT, size)
    l, t_, r, b = font.getbbox(text, stroke_width=size // 9)
    im = Image.new('RGBA', (r - l + 24, b - t_ + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((12 - l + 4, 12 - t_ + 6), text, font=font, fill=(22, 22, 46, 110), stroke_width=size // 9, stroke_fill=(22, 22, 46, 110))
    d.text((12 - l, 12 - t_), text, font=font, fill=fill, stroke_width=size // 9, stroke_fill=stroke)
    return im


def slam(frame, im, t, t0, y):
    if t < t0:
        return
    u = t - t0
    s = 1.0 + 0.45 * (1 - back_out(u / 0.2, 2.2))
    shake = 10 * math.sin(u * 70) * math.exp(-u * 12)
    sized = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BICUBIC)
    frame.alpha_composite(sized, (int(W / 2 - sized.width / 2 + shake), int(y - sized.height / 2)))


BROKE = text_layer('Broke a week later', 92)
EXAMPLE = text_layer('Example', 54, fill=(255, 236, 190))


def counter(frame, t):
    if t < T['amount'] - 0.05:
        return
    value = 1500 * jar_level(t)
    label = f'${int(round(value / 10) * 10):,}'
    pop = math.exp(-max(0.0, t - T['lands']) * 9) if t >= T['lands'] else 0
    im = text_layer(label, int(150 * (1 + 0.18 * pop)), fill=(255, 226, 120))
    frame.alpha_composite(EXAMPLE, (int(W / 2 - EXAMPLE.width / 2), 120))
    frame.alpha_composite(im, (int(W / 2 - im.width / 2), 180 - int(10 * pop)))


# ---- frame -------------------------------------------------------------------------------------------
def frame_at(t: float) -> Image.Image:
    cam = camera(t)
    frame = background(cam)
    pose = lead.pose(t)
    if t < CUT:
        marks = 7 * ease_out((t - (T['so'] + 0.02)) / (T['week'] - T['so']))
        circle = ease((t - T['payday']) / 0.35)
        cal = calendar.render(max(0.0, marks), circle)
        place(frame, cal, cam, CAL_BOX[:2], CAL_BOX[2], anchor=(0, 0))
    else:
        level = jar_level(t)
        back, front = jar.render(level, coin)
        place(frame, back, cam, JAR_FOOT, JAR_W, anchor=(0.5, 1.0))
        for c in coins:
            at = c.at(t)
            if at:
                place(frame, coin, cam, (at[0], at[1]), 34, angle=at[2])
        place(frame, front, cam, JAR_FOOT, JAR_W, anchor=(0.5, 1.0))
    shadow(frame, cam, pose)
    layer, xy = char.render(pose, BASE, cam[2], cam[:2], held=held_props(t, pose))
    frame.alpha_composite(layer, xy)
    if t < CUT:
        for k, b in enumerate(bill_flights):  # bills leave the fan one by one and flutter off
            u = t - b['t0']
            if 0 <= u < 1.2:
                gx, gy = b['origin'][0], b['origin'][1] - 0.3 * D
                x = gx + b['vx'] * u
                y = gy + b['vy'] * u + 0.5 * 700 * u * u
                place(frame, bill, cam, (x, y), 0.55 * D, angle=b['spin'] * u + 30 * math.sin(u * 9), alpha=1 - ease((u - 0.7) / 0.5))
        u = t - MOTH_T
        if 0 <= u < 2.0:  # the moth leaves the upturned wallet
            wx, wy = MOTH_ORIGIN[0], MOTH_ORIGIN[1] + 0.35 * D
            x = wx + 160 * u + 22 * math.sin(u * 6)
            y = wy - 230 * u + 30 * math.sin(u * 8.5)
            place(frame, props.moth_sprite(u * 38), cam, (x, y), 0.30 * D * min(1, u * 4 + 0.3), alpha=1 - ease((u - 1.5) / 0.5))
        slam(frame, BROKE, t, T['week'], 1470)
    else:
        counter(frame, t)
    return frame.convert('RGB')


# ---- audio -------------------------------------------------------------------------------------------
def sfx_thud():
    n = int(0.45 * SR)
    tt = np.arange(n) / SR
    f = 75 * np.exp(-tt * 4) + 38
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 9)
    noise = np.convolve(np.random.default_rng(1).normal(0, 1, n), np.ones(40) / 40, 'same') * np.exp(-tt * 30)
    return 0.8 * body + 0.35 * noise


def sfx_coin(rng, big=False):
    n = int((0.7 if big else 0.35) * SR)
    tt = np.arange(n) / SR
    out = np.zeros(n)
    for f, dcy, amp in ((2620, 7, 0.5), (3950, 9, 0.35), (5480, 12, 0.25), (7030, 16, 0.15)):
        f *= rng.uniform(0.96, 1.04)
        out += amp * np.sin(2 * np.pi * f * tt + rng.uniform(0, 6)) * np.exp(-tt * dcy * (0.6 if big else 1.0))
    out *= np.minimum(1, tt / 0.002)
    return out * (0.55 if big else 0.22)


def sfx_whoosh(rng):
    n = int(0.35 * SR)
    noise = rng.normal(0, 1, n)
    k = np.ones(12) / 12
    noise = np.convolve(noise, k, 'same') - np.convolve(noise, np.ones(80) / 80, 'same')
    env = np.sin(np.pi * np.arange(n) / n) ** 2
    return 0.12 * noise * env


def mix_audio(path):
    total = int(math.ceil(END * SR)) + SR
    mix = np.zeros(total, np.float32)

    def add(sig, at, gain=1.0):
        i = int(at * SR)
        sig = sig[: max(0, total - i)]
        mix[i:i + len(sig)] += gain * sig

    add(a1, B1)
    add(a2, B2)
    rng = np.random.default_rng(5)
    add(sfx_thud(), T['broke'], 0.7)
    for b in bill_flights:
        add(sfx_whoosh(rng), b['t0'], 1.0)
    for c in coins[:-1]:
        add(sfx_coin(rng), c.landed_at(), 0.8)
    add(sfx_coin(rng, big=True), T['lands'], 1.0)
    for x in (T['every'], T['other']):
        add(sfx_coin(rng) * 0.5, x, 0.6)
    peak = np.abs(mix).max() or 1
    mix = np.tanh(mix / peak * 1.2) * 0.9
    mix = mix[: int(END * SR)]
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((mix * 32767).astype(np.int16).tobytes())


# ---- main --------------------------------------------------------------------------------------------
OUT_DIR.mkdir(parents=True, exist_ok=True)
if args.still is not None:
    for s in args.still:
        p = OUT_DIR / f'still-{args.voice}-{s:05.2f}.jpg'
        frame_at(s).save(p, quality=90)
        print(p, lead.active(s))
    sys.exit()

out = Path(args.out) if args.out else OUT_DIR / f'beats-1-2-{args.voice}.mp4'
audio_path = OUT_DIR / f'beats-1-2-{args.voice}.wav'
mix_audio(audio_path)
cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
       '-i', str(audio_path), '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
       '-af', 'loudnorm=I=-15:TP=-1.5:LRA=11', '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', str(out)]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
n = int(round(END * FPS))
t0 = time.perf_counter()
log = []
for i in range(n):
    t = i / FPS
    proc.stdin.write(frame_at(t).tobytes())
    if i % 30 == 0:
        log.append(dict(t=round(t, 2), actions=lead.active(t)))
        print(f'{i}/{n} {t:.1f}s {lead.active(t)}  {time.perf_counter() - t0:.0f}s', flush=True)
proc.stdin.close()
proc.wait()
(OUT_DIR / f'beats-1-2-{args.voice}.timeline.json').write_text(json.dumps(dict(voice=args.voice, seconds=round(END, 2), words=T, actions=[
    dict(name=a.name, start=round(a.start, 2), end=round(a.end, 2)) for a in acts]), indent=1))
print(out, f'{END:.2f}s', f'render {time.perf_counter() - t0:.0f}s')
