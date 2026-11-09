"""Sound for a Short: synthesized SFX (no samples, so no licences), a quiet plucked music bed ducked
under the voice, the mix, and a two-pass loudnorm master (-14 LUFS integrated, true peak <= -1 dBTP).

Every effect is generated from a seed, so a re-render is bit-identical.
"""
from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48000


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def _noise(rng, n, lo=None, hi=None):
    x = rng.normal(0, 1, n)
    if hi:                                   # crude low-pass: moving average
        k = max(1, int(SR / hi))
        x = np.convolve(x, np.ones(k) / k, 'same')
    if lo:                                   # crude high-pass: subtract a slower average
        k = max(1, int(SR / lo))
        x = x - np.convolve(x, np.ones(k) / k, 'same')
    return x / (np.abs(x).max() or 1)


def thud(rng):
    t = _t(0.45)
    f = 75 * np.exp(-t * 4) + 38
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
    return 0.8 * body + 0.3 * _noise(rng, len(t), hi=500) * np.exp(-t * 30)


def coin(rng, big=False):
    t = _t(0.7 if big else 0.35)
    out = np.zeros(len(t))
    for f, dcy, amp in ((2620, 7, 0.5), (3950, 9, 0.35), (5480, 12, 0.25), (7030, 16, 0.15)):
        f *= rng.uniform(0.96, 1.04)
        out += amp * np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) * np.exp(-t * dcy * (0.6 if big else 1.0))
    return out * np.minimum(1, t / 0.002) * (0.55 if big else 0.22)


def trickle(rng, seconds=1.2, n=10):
    out = np.zeros(int(seconds * SR) + SR // 2)
    for k in range(n):
        at = int(rng.uniform(0, seconds) * SR)
        c = coin(rng) * rng.uniform(0.35, 0.8)
        out[at:at + len(c)] += c[:len(out) - at]
    return out


def whoosh(rng, seconds=0.35):
    n = int(seconds * SR)
    return 0.14 * _noise(rng, n, lo=300, hi=4000) * np.sin(np.pi * np.arange(n) / n) ** 2


def pop(rng):
    t = _t(0.12)
    f = 900 * np.exp(-t * 30) + 300
    return 0.35 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 35)


def tick(rng):
    """A soft check-mark tick: a short wooden click."""
    t = _t(0.08)
    return 0.35 * np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 80) + 0.12 * _noise(rng, len(t), lo=2000) * np.exp(-t * 120)


def tap(rng):
    """A fingertip on a phone screen."""
    t = _t(0.05)
    return 0.18 * _noise(rng, len(t), lo=1500, hi=9000) * np.exp(-t * 140)


def paper(rng, seconds=0.9):
    """Paper unrolling: crackly filtered noise with an uneven envelope."""
    n = int(seconds * SR)
    env = np.abs(_noise(rng, n, hi=18)) ** 0.7 * np.sin(np.pi * np.arange(n) / n)
    return 0.16 * _noise(rng, n, lo=1200, hi=7000) * env


def crack(rng):
    """A thin glass crack: a bright click and a tiny ring."""
    t = _t(0.25)
    ring = sum(np.sin(2 * np.pi * f * t) * np.exp(-t * 30) for f in (3100, 4700, 6900)) / 3
    return 0.25 * ring + 0.2 * _noise(rng, len(t), lo=3000) * np.exp(-t * 90)


def clink(rng):
    """Two ceramic cups touching."""
    t = _t(0.9)
    out = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d) for f, d, a in ((1760, 6, 0.5), (2637, 8, 0.35), (3520, 11, 0.2)))
    return 0.45 * out * np.minimum(1, t / 0.001)


def slide(rng, seconds=0.5):
    """Cardboard sliding on wood."""
    n = int(seconds * SR)
    return 0.12 * _noise(rng, n, lo=200, hi=2500) * np.sin(np.pi * np.arange(n) / n) ** 1.5


def sparkle(rng):
    t = _t(0.8)
    out = np.zeros(len(t))
    for k, f in enumerate((2093, 2637, 3136, 4186)):
        s = int(k * 0.06 * SR)
        out[s:] += np.sin(2 * np.pi * f * t[:len(t) - s]) * np.exp(-t[:len(t) - s] * 7)
    return 0.07 * out


def alarm(rng, seconds=1.1):
    """A small twin-bell alarm clock: a fast hammer on two bells, fading."""
    t = _t(seconds)
    hammer = (np.sin(2 * np.pi * 26 * t) > 0.2).astype(float)
    bells = 0.6 * np.sin(2 * np.pi * 3150 * t) + 0.4 * np.sin(2 * np.pi * 4420 * t) + 0.2 * np.sin(2 * np.pi * 6100 * t)
    ring = hammer * bells * np.exp(-((t * 26) % 1.0) * 2.2)
    return 0.22 * ring * np.minimum(1, t / 0.005) * np.exp(-t * 1.2)


def click(rng):
    """A lamp switch."""
    t = _t(0.08)
    return 0.35 * _noise(rng, len(t), lo=1200, hi=6000) * np.exp(-t * 90)


def snore(rng, seconds=0.9):
    """A soft in-breath snore: low noise with a slow swell."""
    t = _t(seconds)
    env = np.sin(np.pi * t / seconds) ** 2
    return 0.10 * _noise(rng, len(t), lo=90, hi=600) * env * (1 + 0.6 * np.sin(2 * np.pi * 24 * t))


def rumble(rng, seconds=0.5):
    """A low jolt for an impact shake."""
    t = _t(seconds)
    return 0.3 * np.sin(2 * np.pi * (48 - 20 * t) * t) * np.exp(-t * 7) + 0.1 * _noise(rng, len(t), hi=300) * np.exp(-t * 9)


def ding(rng):
    """A small bright bell: the clink's ring with a longer tail (the script vocabulary's 'ding')."""
    t = _t(0.9)
    sig = np.zeros_like(t)
    for k, (f, a) in enumerate(((1760.0, 1.0), (2637.0, 0.45), (3520.0, 0.2))):
        sig += a * np.sin(2 * np.pi * f * t) * np.exp(-t * (5.5 + 2 * k))
    return sig * 0.5


def typing(rng, seconds=0.9, n=9):
    """A short burst of key taps (the script vocabulary's 'typing')."""
    out = np.zeros(int(seconds * SR))
    for k in range(n):
        at = int((0.04 + k * (seconds - 0.1) / n + rng.uniform(-0.015, 0.015)) * SR)
        c = tap(rng)
        out[at:at + len(c)] += c[:max(0, len(out) - at)] * rng.uniform(0.6, 1.0)
    return out


SFX = dict(thud=thud, alarm=alarm, click=click, snore=snore, rumble=rumble, coin=coin, coin_big=lambda r: coin(r, True), trickle=trickle, whoosh=whoosh, pop=pop, tick=tick,
           tap=tap, paper=paper, crack=crack, clink=clink, slide=slide, sparkle=sparkle, ding=ding, typing=typing)


def music_bed(seconds: float, seed: int = 3) -> np.ndarray:
    """A quiet original plucked harmony (the same idea as The Empty Chair's bed): warm chords, one pluck
    per beat of a slow bar, soft attack and long decay."""
    chords = [(130.81, 164.81, 196.0), (110.0, 130.81, 164.81), (87.31, 130.81, 174.61), (98.0, 146.83, 196.0)]
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * SR) + SR * 3)
    bar = 3.2
    for b in range(int(seconds / bar) + 1):
        for n, f in enumerate(chords[b % 4]):
            at = 1.0 + b * bar + n * 0.55 + rng.uniform(-0.02, 0.02)
            t = _t(3.0)
            env = np.minimum(1, t / 0.012) * np.exp(-t * 1.6)
            note = env * (np.sin(2 * np.pi * 2 * f * t) + 0.18 * np.sin(2 * np.pi * 4 * f * t))
            i = int(at * SR)
            out[i:i + len(note)] += note[:len(out) - i]
    out = out[:int(seconds * SR)]
    fade = np.minimum(1, _t(seconds) / 1.5) * np.minimum(1, (seconds - _t(seconds)) / 1.5)
    return 0.05 * out * fade


def duck(music: np.ndarray, voice: np.ndarray, depth: float = 0.55) -> np.ndarray:
    """Lower the music under the voice (smooth envelope follower)."""
    k = int(0.05 * SR)
    env = np.sqrt(np.convolve(voice ** 2, np.ones(k) / k, 'same'))
    on = np.clip(env / 0.02, 0, 1)
    k2 = int(0.25 * SR)
    on = np.convolve(on, np.ones(k2) / k2, 'same')
    return music * (1 - depth * np.clip(on, 0, 1))


def mix(seconds: float, voice: list, events: list, music: bool = True, seed: int = 5) -> np.ndarray:
    """voice: [(start_s, samples at SR)], events: [(time_s, sfx name, gain)]."""
    n = int(seconds * SR)
    v = np.zeros(n)
    for at, clip in voice:
        i = int(at * SR)
        clip = clip[:max(0, n - i)]
        v[i:i + len(clip)] += clip
    fx = np.zeros(n)
    rng = np.random.default_rng(seed)
    for at, name, gain in sorted(events, key=lambda e: e[0]):
        sig = SFX.get(name, pop)(rng) * gain                       # an unknown name is a soft pop, never a crash
        i = int(max(0.0, at) * SR)
        sig = sig[:max(0, n - i)]
        fx[i:i + len(sig)] += sig
    out = v + 0.55 * fx
    if music:
        out += duck(music_bed(seconds), v)
    return out


def write_wav(path: Path, samples: np.ndarray):
    peak = np.abs(samples).max() or 1
    x = samples / max(1.0, peak / 0.95)
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((x * 32767).astype(np.int16).tobytes())


def master(src: Path, dst: Path, lufs: float = -14.0, tp: float = -1.5, lra: float = 11.0) -> dict:
    """Two-pass loudnorm: measure, then normalise linearly to the target (48 kHz stereo WAV out)."""
    first = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(src), '-af',
                            f'loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json', '-f', 'null', '-'],
                           capture_output=True, text=True).stderr
    m = json.loads(first[first.rindex('{'):first.rindex('}') + 1])
    af = (f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(src), '-af', af, '-ar', '48000', '-ac', '2', str(dst)], check=True)
    # loudnorm's linear mode can land short of the target; trim the gain, never past the true-peak ceiling
    got = loudness(dst)
    gain = min(lufs - got['integrated_lufs'], tp - got['true_peak_dbtp'])
    if abs(gain) > 0.2:
        tmp = dst.with_suffix('.trim.wav')
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(dst), '-af', f'volume={gain:.2f}dB', str(tmp)], check=True)
        tmp.replace(dst)
    return dict(measured=m, trim_db=round(gain, 2) if abs(gain) > 0.2 else 0.0, result=loudness(dst))


def loudness(path: Path) -> dict:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's audio, via ffmpeg's EBU R128 meter."""
    err = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    summary = err[err.rindex('Summary:'):]
    def grab(label):
        line = next(l for l in summary.splitlines() if l.strip().startswith(label))
        return float(line.split(':')[1].split()[0])
    return dict(integrated_lufs=grab('I:'), true_peak_dbtp=grab('Peak:'), lra=grab('LRA:'))
