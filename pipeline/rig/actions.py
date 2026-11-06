"""Idle life and body actions for the cut-out rig.

An Action changes a pose over a time window and is blended in and out with eased
envelopes, on top of idle life (breathing, blinks, weight shift) that never stops.
Times are absolute seconds on the video timeline, normally anchored to narration words.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from typing import Callable

from .skeleton import Pose, blend


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease(x: float) -> float:
    """Smoothstep in/out."""
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def back_out(x: float, s: float = 1.6) -> float:
    """Overshoot then settle: the snap of a cartoon pose."""
    x = clamp01(x) - 1
    return x * x * ((s + 1) * x + s) + 1


def keys(t: float, frames: list, fn=ease):
    """Interpolate [(time, value), ...]; values may be floats or tuples."""
    if t <= frames[0][0]:
        return frames[0][1]
    for (t0, v0), (t1, v1) in zip(frames, frames[1:]):
        if t <= t1:
            q = fn((t - t0) / max(t1 - t0, 1e-6))
            if isinstance(v0, tuple):
                return tuple(a + (b - a) * q for a, b in zip(v0, v1))
            return v0 + (v1 - v0) * q
    return frames[-1][1]


@dataclass
class Idle:
    """Breathing, blinks on an irregular rhythm, slow weight shift and head drift."""
    seed: int = 7
    breath_period: float = 3.6

    def __post_init__(self):
        rng = random.Random(self.seed)
        self.blinks, t = [], rng.uniform(0.6, 1.4)
        while t < 600:
            self.blinks.append(t)
            t += rng.choice([2.2, 2.9, 3.4, 4.1]) + rng.uniform(-0.3, 0.3)
            if rng.random() < 0.18:  # an occasional double blink
                self.blinks.append(t - 0.0)
                t += 0.32

    def blink(self, t: float) -> float:
        for b in self.blinks:
            if b <= t < b + 0.16:
                return 1 - math.sin(math.pi * (t - b) / 0.16)
            if b > t:
                break
        return 1.0

    def __call__(self, t: float, base: Pose) -> Pose:
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * t / self.breath_period)
        sway = math.sin(2 * math.pi * t / 5.3)
        drift = math.sin(2 * math.pi * t / 4.1 + 1.3)
        lx, ly = base.lh
        rx, ry = base.rh
        return replace(base, breath=breath, lean=base.lean + 0.8 * sway, head_tilt=base.head_tilt + 1.6 * drift,
                       eye_open=base.eye_open * self.blink(t),
                       lh=(lx - 0.012 * sway, ly - 0.02 * breath), rh=(rx - 0.012 * sway, ry - 0.02 * breath))


@dataclass
class Action:
    """A body action on [start, end], faded in/out over fade_in/fade_out seconds."""
    start: float
    end: float
    fn: Callable[[float, Pose], Pose]   # (absolute t, current pose) -> target pose
    fade_in: float = 0.18
    fade_out: float = 0.25
    name: str = ''

    def weight(self, t: float) -> float:
        if t < self.start or t > self.end + self.fade_out:
            return 0.0
        w_in = ease((t - self.start) / self.fade_in) if self.fade_in > 0 else 1.0
        w_out = 1 - ease((t - self.end) / self.fade_out) if t > self.end and self.fade_out > 0 else (0.0 if t > self.end else 1.0)
        return min(w_in, w_out)


@dataclass
class Performance:
    """Base pose + idle + ordered actions. pose(t) is deterministic, so any frame can be rendered alone."""
    base: Pose
    idle: Idle
    actions: list

    def pose(self, t: float) -> Pose:
        p = self.base
        for a in self.actions:
            w = a.weight(t)
            if w > 0:
                p = blend(p, a.fn(t, p), w)
        return self.idle(t, p)

    def active(self, t: float) -> list:
        return [a.name for a in self.actions if a.weight(t) > 0.5]


# ---- reusable actions: each returns fn(t, pose) -> pose for a window [t0, t1] ------------------------

def hold(**channels):
    """Hold a pose (a held gesture between bigger moves)."""
    return lambda t, p: replace(p, **channels)


def shrug(t0: float, t1: float, sign: int = 1):
    """Palms up and out, shoulders up, head tilt, small frown."""
    def fn(t, p):
        q = back_out((t - t0) / 0.35)
        return replace(p, lh=(-0.78, -0.62 - 0.08 * q), rh=(0.78, -0.62 - 0.08 * q), l_shape='open', r_shape='open',
                       l_rot=-75, r_rot=75, l_bend=1, r_bend=1, head_tilt=sign * 9 * q, brow_worry=0.8, brow_raise=0.45,
                       smile=-0.35, stretch=0.02 * q, head_dy=0.03 * q)
    return fn


def facepalm(t0: float, side: str = 'r'):
    """Hand to forehead from the side, head drops back a little, eyes close."""
    def fn(t, p):
        q = ease((t - t0) / 0.3)
        hand = (0.30 if side == 'r' else -0.30, -1.95)
        kw = dict(rh=hand, r_shape='open', r_rot=-105, r_bend=1) if side == 'r' else dict(lh=hand, l_shape='open', l_rot=105, l_bend=1)
        return replace(p, head_tilt=-6 * q if side == 'r' else 6 * q, eye_open=1 - 0.8 * q, brow_worry=1, smile=-0.5,
                       lean=-2 * q, **kw)
    return fn


def arms_wide(t0: float):
    """Throw both arms up and out with a hop, squash on landing: something feels big."""
    def fn(t, p):
        u = t - t0
        up = back_out(u / 0.28)
        lift = keys(u, [(0, 0), (0.05, -0.06), (0.22, 0.20), (0.42, 0.0), (0.5, -0.05), (0.62, 0.0)])
        crouch = keys(u, [(0, 0), (0.08, 0.10), (0.18, 0.0), (0.42, 0.0), (0.5, 0.08), (0.7, 0.0)])
        squash = keys(u, [(0, 0), (0.08, -0.05), (0.2, 0.06), (0.42, 0.0), (0.5, -0.06), (0.7, 0.0)])
        return replace(p, lh=(-1.05, -2.15 * up + (-0.3) * (1 - up)), rh=(1.05, -2.15 * up - 0.3 * (1 - up)),
                       l_shape='open', r_shape='open', l_rot=-20, r_rot=20, lift=max(0.0, lift), crouch=max(0.0, crouch),
                       stretch=squash, smile=1.0, mouth_open=0.85, eye_wide=0.6, brow_raise=0.8)
    return fn


def rub_hands(t0: float, t1: float, height: float = -0.78):
    """Both hands together in front of the chest, rubbing in anticipation."""
    def fn(t, p):
        k = math.sin(2 * math.pi * (t - t0) * 4.5) * 0.05
        return replace(p, lh=(-0.10 + k, height), rh=(0.10 + k, height + 0.02), l_shape='relaxed', r_shape='relaxed',
                       l_rot=-60, r_rot=60, l_bend=1, r_bend=1, smile=0.8, brow_raise=0.4, lean=-1.5)
    return fn


def look(t0: float, turn: float, gaze=(0.0, 0.0), tilt: float = 0.0):
    """Turn the face and eyes toward something."""
    def fn(t, p):
        return replace(p, turn=turn, gaze_x=gaze[0], gaze_y=gaze[1], head_tilt=p.head_tilt + tilt)
    return fn
