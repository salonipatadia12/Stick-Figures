"""Animated test clip: beats 1-2 of the salary episode with the cut-out rig and Kokoro narration.

Every action is keyed to a narration word (Kokoro word timings), so the same choreography re-times
itself for another voice. The staging, choreography and props live in scene_salary.py.

Run with .venv/bin/python from the repository root:
  scripts/motion_test/beats_1_2.py [--voice am_michael] [--still 3.0 ...] [--compare] [--hands] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
import scene_salary as S  # noqa: E402
from pipeline import styles  # noqa: E402
from pipeline.rig.actions import back_out  # noqa: E402

W, H, FPS = S.W, S.H, 30
OUT_DIR = ROOT / 'renders/motion-test'
VOICE_DIR = OUT_DIR / 'voice'
LEAD, GAP, TAIL = 0.45, 0.40, 0.95
SR = 24000

ap = argparse.ArgumentParser()
ap.add_argument('--voice', default=styles.load('cozy').default_voice)
ap.add_argument('--still', type=float, nargs='*')
ap.add_argument('--compare', action='store_true', help='side-by-side frame with The Empty Chair')
ap.add_argument('--hands', action='store_true', help='hand pose library contact sheet')
ap.add_argument('--out')
args = ap.parse_args()


def load_beat(beat):
    words = json.loads((VOICE_DIR / f'{args.voice}-{beat}.words.json').read_text())
    with wave.open(str(VOICE_DIR / f'{args.voice}-{beat}.wav')) as f:
        audio = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
    return words, audio


def word(words, start, text):
    for w in words:
        if w['word'].lower().strip('.,?!') == text.lower():
            return start + w['start']
    raise KeyError(text)


w1, a1 = load_beat('b01')
w2, a2 = load_beat('b02')
B1 = LEAD
B2 = B1 + len(a1) / SR + GAP
END = B2 + len(a2) / SR + TAIL
CUT = B2 - GAP / 2
T = dict(payday=word(w1, B1, 'payday'), feels=word(w1, B1, 'feels'), rich=word(w1, B1, 'rich'), so=word(w1, B1, 'so'),
         broke=word(w1, B1, 'broke'), a=word(w1, B1, 'a'), week=word(w1, B1, 'week'), later=word(w1, B1, 'later'),
         say=word(w2, B2, 'say'), amount=word(w2, B2, '1,500'), lands=word(w2, B2, 'lands'), every=word(w2, B2, 'every'),
         other=word(w2, B2, 'other'), friday=word(w2, B2, 'friday'), it=word(w2, B2, 'it'), huge=word(w2, B2, 'huge'))
print('voice', args.voice, 'timeline', {k: round(v, 2) for k, v in T.items()}, 'end', round(END, 2), flush=True)
scene = S.Scene(T, CUT, END)


# ---- text ------------------------------------------------------------------------------------------
def text_layer(text, size, fill=(255, 255, 255), stroke=(22, 22, 46)):
    font = ImageFont.truetype(S.FONT, size)
    l, t_, r, b = font.getbbox(text, stroke_width=size // 9)
    im = Image.new('RGBA', (r - l + 24, b - t_ + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((12 - l + 4, 12 - t_ + 6), text, font=font, fill=(22, 22, 46, 110), stroke_width=size // 9, stroke_fill=(22, 22, 46, 110))
    d.text((12 - l, 12 - t_), text, font=font, fill=fill, stroke_width=size // 9, stroke_fill=stroke)
    return im


def slam(frame, im, t, t0, centre):
    if t < t0:
        return
    u = t - t0
    s = 1.0 + 0.45 * (1 - back_out(u / 0.2, 2.2))
    shake = 10 * math.sin(u * 70) * math.exp(-u * 12)
    sized = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.BICUBIC)
    frame.alpha_composite(sized, (int(centre[0] - sized.width / 2 + shake), int(centre[1] - sized.height / 2)))


def two_lines(top, bottom, size_top, size_bottom, fill=(255, 255, 255)):
    a, b = text_layer(top, size_top, fill), text_layer(bottom, size_bottom, fill)
    im = Image.new('RGBA', (max(a.width, b.width), a.height + b.height - 18), (0, 0, 0, 0))
    im.alpha_composite(a, ((im.width - a.width) // 2, 0))
    im.alpha_composite(b, ((im.width - b.width) // 2, a.height - 18))
    return im


# On-screen text stays in the upper-middle: clear of the bottom quarter and the right edge, where the
# Shorts title and buttons sit, and clear of the calendar on the wall and the lead's head.
SAFE = dict(bottom=int(H * 0.75), right=W - 150)
TEXT_C = (318, 350)
BROKE = two_lines('Broke', 'a week later', 118, 70)
EXAMPLE = text_layer('Example', 50, fill=(255, 236, 190))


def counter(frame, t):
    if t < T['amount'] - 0.05:
        return
    value = 1500 * scene.jar_level(t)
    pop = math.exp(-max(0.0, t - T['lands']) * 9) if t >= T['lands'] else 0
    im = text_layer(f'${int(round(value / 10) * 10):,}', int(128 * (1 + 0.16 * pop)), fill=(255, 226, 120))
    frame.alpha_composite(EXAMPLE, (int(TEXT_C[0] - EXAMPLE.width / 2), TEXT_C[1] - 128))
    frame.alpha_composite(im, (int(TEXT_C[0] - im.width / 2), TEXT_C[1] - 66 - int(8 * pop)))


def frame_at(t, cam=None):
    frame = scene.frame(t, cam)
    if t < CUT:
        slam(frame, BROKE, t, T['week'], TEXT_C)
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
    return out * np.minimum(1, tt / 0.002) * (0.55 if big else 0.22)


def sfx_whoosh(rng):
    n = int(0.35 * SR)
    noise = rng.normal(0, 1, n)
    noise = np.convolve(noise, np.ones(12) / 12, 'same') - np.convolve(noise, np.ones(80) / 80, 'same')
    return 0.12 * noise * np.sin(np.pi * np.arange(n) / n) ** 2


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
    for b in scene.bill_flights:
        add(sfx_whoosh(rng), b['t0'])
    for c in scene.coins[:-1]:
        add(sfx_coin(rng), c.landed_at(), 0.8)
    add(sfx_coin(rng, big=True), T['lands'])
    for x in (T['every'], T['other']):
        add(sfx_coin(rng) * 0.5, x, 0.6)
    peak = np.abs(mix).max() or 1
    mix = (np.tanh(mix / peak * 1.2) * 0.9)[: int(END * SR)]
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((mix * 32767).astype(np.int16).tobytes())


# ---- review sheets -----------------------------------------------------------------------------------
EC = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'


def side_by_side(path, left, right, left_label, right_label):
    font = ImageFont.truetype(S.FONT, 40)
    sheet = Image.new('RGB', (W * 2 + 30, H + 90), (246, 238, 222))
    sheet.paste(left, (0, 90))
    sheet.paste(right, (W + 30, 90))
    d = ImageDraw.Draw(sheet)
    d.text((20, 25), left_label, font=font, fill=(30, 30, 50))
    d.text((W + 50, 25), right_label, font=font, fill=(30, 30, 50))
    sheet.save(path, quality=90)
    print(path)


def compare_sheets():
    """Beat 1 on its painted base next to the Empty Chair frame it was cut from, at the same (full) framing,
    and the two-shot base next to 03-together."""
    full = scene.stage.camera(470, 836, 941)
    t = T['rich'] + 0.1
    side_by_side(OUT_DIR / 'compare-empty-chair.jpg', Image.open(EC / '04-waiting.png').convert('RGB').resize((W, H), Image.LANCZOS),
                 frame_at(t, full), 'The Empty Chair (04-waiting)', f'Base pose + rig, beat 1 at {t:.2f} s')
    side_by_side(OUT_DIR / 'compare-two-shot.jpg', Image.open(EC / '03-together.png').convert('RGB').resize((W, H), Image.LANCZOS),
                 S.two_shot(), 'The Empty Chair (03-together)', 'Base pose + rigs: the lead and the friend')


OUT_DIR.mkdir(parents=True, exist_ok=True)
if args.hands:
    from pipeline.rig import hands
    A = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'
    refs = [Image.open(A / f).convert('RGB').crop(b) for f, b in [
        ('03-together.png', (545, 765, 680, 870)), ('03-together.png', (220, 800, 330, 900)), ('06-return-final.png', (430, 600, 510, 700)),
        ('06-return-final.png', (630, 650, 720, 740)), ('06-return-final.png', (750, 720, 860, 800)), ('03-together.png', (400, 720, 500, 790))]]
    print(hands.contact_sheet(OUT_DIR / 'hand-library.jpg', refs))
if args.compare:
    compare_sheets()
if args.still is not None:
    for s in args.still:
        p = OUT_DIR / f'still-{args.voice}-{s:05.2f}.jpg'
        frame_at(s).save(p, quality=90)
        print(p, scene.perf.active(s))
if args.still is not None or args.compare or args.hands:
    sys.exit()

out = Path(args.out) if args.out else OUT_DIR / f'beats-1-2-{args.voice}.mp4'
audio_path = OUT_DIR / f'beats-1-2-{args.voice}.wav'
mix_audio(audio_path)
cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
       '-i', str(audio_path), '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
       '-af', 'loudnorm=I=-15:TP=-1.5:LRA=11', '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2',
       '-shortest', '-movflags', '+faststart', str(out)]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
n = int(round(END * FPS))
t0 = time.perf_counter()
for i in range(n):
    t = i / FPS
    proc.stdin.write(frame_at(t).tobytes())
    if i % 60 == 0:
        print(f'{i}/{n} {t:.1f}s {scene.perf.active(t)}  {time.perf_counter() - t0:.0f}s', flush=True)
proc.stdin.close()
proc.wait()
(OUT_DIR / f'beats-1-2-{args.voice}.timeline.json').write_text(json.dumps(dict(
    voice=args.voice, seconds=round(END, 2), words=T,
    actions=[dict(name=a.name, start=round(a.start, 2), end=round(a.end, 2)) for a in scene.perf.actions]), indent=1))
print(out, f'{END:.2f}s', f'render {time.perf_counter() - t0:.0f}s')
