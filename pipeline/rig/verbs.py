"""Parameterised body verbs: the action library a script is composed from (docs/PLAN.md §7 B).

Every verb takes the character's rig geometry (a `Puppet`: head diameter D, shoulders, rest hands, which way
it faces) and absolute times, and returns a `Move`: an `Action` for the performance plus, where a prop is
involved, when each hand holds it and when it flies. Hand targets are in body space (head diameters from
the shoulder midpoint, y down), so the same verb works on any base. Nothing here draws; the composer
(scripts/motion_test/compose_beats.py) turns Moves into frames.

The vocabulary matches `pipeline.models.Verb`. Posture and travel verbs (lie_down, sit_up, stand_up, walk)
are not here: a painted torso cannot do them, so the composer stages them as a cut on the anchor word.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .actions import Action, back_out, ease, ease_out, keys
from .skeleton import Pose

IN_PLACE = ('reach', 'point', 'shrug', 'facepalm', 'pick_up', 'put_down', 'throw', 'drink', 'type', 'write', 'wave', 'nod',
            'shake_head', 'slump', 'jolt', 'gaze', 'shiver', 'look_up', 'snap', 'freeze', 'tickle', 'laugh')
STAGED = ('lie_down', 'sit_up', 'stand_up', 'walk')


@dataclass
class Move:
    action: Action
    holds: list = field(default_factory=list)      # (t_from, t_to, side, prop name): the hand grips the prop
    flights: list = field(default_factory=list)    # (t_from, side, prop name, (dx, dy) body-space velocity): the prop leaves the hand
    lands: list = field(default_factory=list)      # (t, prop name, xy body space): the prop is set down here
    label: str = ''


def _side(p, side: str, target=None) -> str:
    """'near', 'far', 'l', 'r' or 'auto' (the arm whose shoulder is nearer the target, else the far arm, which
    crosses the body less and reads best from the side view)."""
    if side in ('l', 'r'):
        return side
    if side == 'near':
        return p.near
    if side == 'far' or target is None:
        return p.far
    sl, sr = p.body(p.shoulders['l']), p.body(p.shoulders['r'])
    return 'l' if math.hypot(target[0] - sl[0], target[1] - sl[1]) < math.hypot(target[0] - sr[0], target[1] - sr[1]) else 'r'


def _toward(p, target) -> dict:
    """Face channels that look at a body-space point."""
    head = p.body(p.s['head'])
    dx, dy = target[0] - head[0], target[1] - head[1]
    dist = math.hypot(dx, dy) or 1e-6
    return dict(turn=max(-1.0, min(1.0, dx / max(dist, 0.6) * 1.2)), gaze_x=max(-1.0, min(1.0, dx / dist)),
                gaze_y=max(-1.0, min(1.0, dy / dist)))


def _lerp(a, b, q):
    return (a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q)


def _hand(side, xy, shape, rot=0.0, bend=1, **extra):
    kw = {side + 'h': xy, side + '_shape': shape, side + '_rot': rot, side + '_bend': bend}
    if extra:
        kw['extra'] = {side + '_' + k: v for k, v in extra.items()}
    return kw


# ---- arm verbs -------------------------------------------------------------------------------------------

def reach(p, t0: float, target, side: str = 'auto', dur: float = 0.45, hold: float = 0.8, shape: str = 'open_palm',
          face: bool = True) -> Move:
    """One hand goes out to `target` (body space) and stays there for `hold` seconds."""
    s = _side(p, side, target)
    rest = p.rest(s)
    look = _toward(p, target) if face else {}

    def fn(t, pose):
        q = ease((t - t0) / dur)
        return replace(pose, **_hand(s, _lerp(rest, target, q), shape, rot=-20 * p.facing, droop=0.6, front=1.0),
                       **look, brow_raise=0.3 * q)
    return Move(Action(t0 - 0.05, t0 + dur + hold, fn, 0.1, 0.3, f'reach {s}'), label='reach')


def point(p, t0: float, target, side: str = 'auto', dur: float = 0.35, hold: float = 1.2) -> Move:
    """Point at `target`: the hand stops at 80 % of the arm's reach along the line, index out, eyes on it."""
    s = _side(p, side, target)
    sh = p.body(p.shoulders[s])
    dx, dy = target[0] - sh[0], target[1] - sh[1]
    dist = math.hypot(dx, dy) or 1e-6
    r = min(dist, 0.85)
    tip = (sh[0] + dx / dist * r, sh[1] + dy / dist * r)
    rest = p.rest(s)
    look = _toward(p, target)
    ang = math.degrees(math.atan2(dy, dx))

    def fn(t, pose):
        q = back_out((t - t0) / dur, 1.4)
        return replace(pose, **_hand(s, _lerp(rest, tip, q), 'point', rot=max(-60, min(60, -ang * 0.3)), droop=0.8, front=1.0),
                       **look, brow_raise=0.4, mouth_open=0.1 * q)
    return Move(Action(t0 - 0.05, t0 + dur + hold, fn, 0.1, 0.3, f'point {s}'), label='point')


def shrug(p, t0: float, dur: float = 1.0) -> Move:
    """Both palms up and out, shoulders up, a small frown."""
    def fn(t, pose):
        q = back_out((t - t0) / 0.35)
        return replace(pose, lh=(-0.78, -0.62 - 0.08 * q), rh=(0.78, -0.62 - 0.08 * q), l_shape='open_palm', r_shape='open_palm',
                       l_rot=-75, r_rot=75, l_bend=1, r_bend=1, head_tilt=9 * q * p.facing, brow_worry=0.8, brow_raise=0.45,
                       smile=-0.35, head_dy=0.03 * q)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.12, 0.3, 'shrug'), label='shrug')


def facepalm(p, t0: float, dur: float = 1.2, side: str = 'near') -> Move:
    """Hand to the forehead, eyes shut, head drops."""
    s = _side(p, side)
    head = p.body(p.s['head'])
    brow = (head[0] + 0.02 * p.facing, head[1] - 0.08)

    def fn(t, pose):
        q = ease((t - t0) / 0.3)
        return replace(pose, **_hand(s, brow, 'back', rot=-50 * p.facing, front=1.0, droop=0.0), head_tilt=8 * q,
                       head_dy=0.03 * q, lean=2.0 * q, eye_open=1 - 0.6 * q, brow_worry=1.0, smile=-0.4)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.12, 0.3, 'facepalm'), label='facepalm')


def pick_up(p, t0: float, prop: str, at, side: str = 'auto', dur: float = 0.45, carry_to=None, hold: float = 1.5) -> Move:
    """Reach to a prop at `at` (body space), close the hand on it and lift it to `carry_to` (default: chest height
    on that side). The prop is held from the moment of contact."""
    s = _side(p, side, at)
    rest = p.rest(s)
    carry = carry_to or (at[0] * 0.6, -0.55)
    contact = t0 + dur

    def fn(t, pose):
        u = t - t0
        xy = keys(u, [(0, rest), (dur, at), (dur + 0.35, carry)], ease)
        return replace(pose, **_hand(s, xy, 'grip' if u >= dur - 0.12 else 'open_palm', rot=-15 * p.facing, droop=0.9, front=1.0),
                       **_toward(p, at if u < dur + 0.2 else carry))
    return Move(Action(t0 - 0.05, contact + 0.35 + hold, fn, 0.1, 0.3, f'pick up {prop}'),
                holds=[(contact, contact + 0.35 + hold, s, prop)], label='pick_up')


def put_down(p, t0: float, prop: str, at, side: str = 'auto', dur: float = 0.45, start=None) -> Move:
    """Carry a held prop to `at` (body space) and let go there. `start` is where the hand begins (default chest)."""
    s = _side(p, side, at)
    begin = start or (at[0] * 0.6, -0.55)
    rest = p.rest(s)
    release = t0 + dur

    def fn(t, pose):
        u = t - t0
        xy = keys(u, [(0, begin), (dur, at), (dur + 0.35, rest)], ease)
        return replace(pose, **_hand(s, xy, 'grip' if u <= dur + 0.02 else 'open_palm', rot=-15 * p.facing, droop=0.9, front=1.0),
                       **_toward(p, at))
    return Move(Action(t0 - 0.05, release + 0.4, fn, 0.1, 0.3, f'put down {prop}'),
                holds=[(t0 + 0.02, release, s, prop)], lands=[(release, prop, at)], label='put_down')


def throw(p, t0: float, prop: str, direction: int | None = None, side: str = 'far', windup: float = 0.3) -> Move:
    """Wind up and fling a held prop off toward `direction` (+1 screen-right, -1 left; default: the facing)."""
    s = _side(p, side)
    d = direction if direction is not None else p.facing
    release = t0 + windup
    back = (-0.45 * d, -0.95)
    out = (0.95 * d, -0.95)

    def fn(t, pose):
        u = t - t0
        xy = keys(u, [(0, p.rest(s)), (windup * 0.7, back), (windup, out), (windup + 0.4, (0.6 * d, -0.6))], ease_out)
        return replace(pose, **_hand(s, xy, 'grip' if u <= windup + 0.02 else 'open_palm', rot=0.0, droop=0.3, front=1.0),
                       lean=-2.0 * ease(u / windup) + 3.0 * ease((u - windup) / 0.25), turn=0.8 * d, gaze_x=0.9 * d,
                       brow_worry=0.6, smile=-0.3, mouth_open=0.3 * ease((u - windup + 0.1) / 0.2))
    return Move(Action(t0 - 0.05, release + 0.6, fn, 0.1, 0.3, f'throw {prop}'),
                holds=[(t0 + 0.02, release, s, prop)], flights=[(release, s, prop, (2.6 * d, -1.6))], label='throw')


def drink(p, t0: float, prop: str, at, side: str = 'auto', dur: float = 1.4) -> Move:
    """Lift a cup from `at` (body space) to the mouth, sip with eyes closing, set it back."""
    s = _side(p, side, at)
    head = p.body(p.s['head'])
    sip = (head[0] + 0.26 * p.facing, head[1] + 0.42)
    rest = p.rest(s)
    lift, hold_at, back = t0, t0 + 0.4, t0 + dur - 0.35

    def fn(t, pose):
        u = t - t0
        xy = keys(u, [(0, rest), (0.25, at), (0.4, sip), (dur - 0.35, sip), (dur, at), (dur + 0.3, rest)], ease)
        sipping = ease((u - 0.4) / 0.25) * (1 - ease((u - (dur - 0.35)) / 0.25))
        return replace(pose, **_hand(s, xy, 'grip' if 0.15 <= u <= dur + 0.05 else 'open_palm', rot=0.0, droop=1.2, front=1.0),
                       eye_open=1 - 0.7 * sipping, smile=0.5 + 0.3 * sipping, head_tilt=-6 * sipping * p.facing,
                       mouth_open=0.0, gaze_y=0.4 * sipping)
    return Move(Action(lift - 0.05, t0 + dur + 0.3, fn, 0.1, 0.3, f'drink from {prop}'),
                holds=[(t0 + 0.2, t0 + dur, s, prop)], lands=[(t0 + dur, prop, at)], label='drink')


def type_(p, t0: float, t1: float, at=None) -> Move:
    """Both hands on a keyboard at `at` (body space, default on the table in front), tapping."""
    at = at or (0.15 * p.facing, 0.22)
    l_home, r_home = (at[0] - 0.22, at[1]), (at[0] + 0.22, at[1])

    def fn(t, pose):
        u = t - t0
        bl = 0.03 * max(0.0, math.sin(u * 17.0))
        br = 0.03 * max(0.0, math.sin(u * 17.0 + 2.1))
        return replace(pose, lh=(l_home[0], l_home[1] - bl), rh=(r_home[0], r_home[1] - br), l_shape='resting', r_shape='resting',
                       l_rot=-30, r_rot=30, l_bend=1, r_bend=1, extra={'l_droop': 1.4, 'r_droop': 1.4}, gaze_y=0.7, turn=0.0,
                       lean=1.5, head_dy=0.02)
    return Move(Action(t0 - 0.1, t1, fn, 0.2, 0.3, 'type'), label='type')


def write(p, t0: float, t1: float, at=None, side: str = 'near') -> Move:
    """One hand writes on a page at `at` (body space) in small loops; the other hand rests on the page."""
    s = _side(p, side)
    o = 'l' if s == 'r' else 'r'
    at = at or (0.25 * p.facing, 0.24)

    def fn(t, pose):
        u = t - t0
        loop = (at[0] + 0.05 * math.sin(u * 9) + 0.08 * ((u * 0.25) % 1.0), at[1] + 0.02 * math.sin(u * 18))
        a = _hand(s, loop, 'grip', rot=-35 * p.facing, droop=1.2, front=1.0)
        b = _hand(o, (at[0] - 0.35 * p.facing, at[1]), 'resting', rot=0.0, droop=1.2)
        a['extra'] = a['extra'] | b.pop('extra')
        return replace(pose, **a, **b, gaze_y=0.8, turn=0.2 * p.facing, lean=2.0, head_dy=0.03)
    return Move(Action(t0 - 0.1, t1, fn, 0.2, 0.3, 'write'), label='write')


def wave(p, t0: float, dur: float = 1.1, side: str = 'far') -> Move:
    """Hand up beside the head, waving from the wrist; a smile."""
    s = _side(p, side)
    head = p.body(p.s['head'])
    up = (head[0] + (0.75 if s == 'r' else -0.75), head[1] + 0.05)

    def fn(t, pose):
        u = t - t0
        q = back_out(u / 0.3, 1.5)
        rot = 35 * math.sin((u - 0.3) * 11) * ease((u - 0.25) / 0.1) if u > 0.25 else 0.0
        return replace(pose, **_hand(s, _lerp(p.rest(s), up, q), 'open_palm', rot=rot, bend=1, droop=0.0, front=1.0), smile=0.9,
                       brow_raise=0.5 * q, eye_wide=0.2)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.1, 0.35, 'wave'), label='wave')


# ---- head and body verbs -------------------------------------------------------------------------------

def nod(p, t0: float, times: int = 2, dur: float = 0.9) -> Move:
    def fn(t, pose):
        u = t - t0
        return replace(pose, head_dy=0.035 * math.sin(u * 2 * math.pi * times / dur) * (1 - ease((u - dur + 0.2) / 0.2)),
                       smile=max(pose.smile, 0.3))
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.05, 0.2, 'nod'), label='nod')


def shake_head(p, t0: float, times: int = 2, dur: float = 0.9) -> Move:
    def fn(t, pose):
        u = t - t0
        k = math.sin(u * 2 * math.pi * times / dur) * (1 - ease((u - dur + 0.2) / 0.2))
        return replace(pose, turn=pose.turn + 0.45 * k, gaze_x=pose.gaze_x + 0.3 * k, smile=min(pose.smile, -0.2), brow_worry=0.5)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.05, 0.2, 'shake head'), label='shake_head')


def slump(p, t0: float, dur: float = 2.0, hands_to=None) -> Move:
    """Shoulders sag, head drops forward, eyes half shut, hands fall to the lap (or `hands_to`, body space)."""
    lh, rh = hands_to or ((-0.30, 0.55), (0.30, 0.55))

    def fn(t, pose):
        q = ease((t - t0) / 0.5)
        return replace(pose, lean=3.0 * q, head_dy=0.06 * q, head_tilt=7 * q * p.facing, eye_open=1 - 0.55 * q, smile=-0.4,
                       brow_worry=0.6, lh=_lerp(pose.lh, lh, q), rh=_lerp(pose.rh, rh, q), l_shape='resting', r_shape='resting',
                       extra={'l_droop': 2.0, 'r_droop': 2.0})
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.15, 0.4, 'slump'), label='slump')


def jolt(p, t0: float, dur: float = 0.9) -> Move:
    """A start: head snaps up and back, eyes wide, brows up, then settles."""
    def fn(t, pose):
        u = t - t0
        k = math.exp(-u * 4.5) if u >= 0 else 0.0
        return replace(pose, head_dy=-0.06 * k, head_tilt=-6 * k * p.facing, eye_open=1.0, eye_wide=0.8 * k, brow_raise=0.9 * k,
                       mouth_open=0.0, smile=-0.6, lean=-2.0 * k)
    return Move(Action(t0 - 0.02, t0 + dur, fn, 0.03, 0.3, 'jolt'), label='jolt')


def gaze(p, t0: float, target=None, direction: str = '', dur: float = 1.5) -> Move:
    """Look at a body-space point, or 'camera' (straight out), 'up', 'down', 'away' (off the facing side)."""
    if target is not None:
        look = _toward(p, target)
    elif direction == 'camera':
        look = dict(turn=0.0, gaze_x=0.0, gaze_y=0.0)
    elif direction == 'up':
        look = dict(gaze_y=-0.9, head_dy=-0.02)
    elif direction == 'down':
        look = dict(gaze_y=0.9, head_dy=0.03)
    else:                                          # away: over the shoulder, off the facing side
        look = dict(turn=-0.8 * p.facing, gaze_x=-0.9 * p.facing, gaze_y=-0.3)

    def fn(t, pose):
        return replace(pose, **look)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.2, 0.3, 'gaze'), label='gaze')


# ---- host verbs (script v5): the host acts the story out inside the scenes -----------------------------

def shiver(p, t0: float, dur: float = 2.2) -> Move:
    """Arms hugged across the chest, the body trembling, brows worried, mouth tight."""
    lh, rh = (-0.28, 0.32), (0.28, 0.32)

    def fn(t, pose):
        u = t - t0
        q = ease(u / 0.35)
        k = q * (1 - ease((u - dur + 0.4) / 0.4))
        return replace(pose, lean=1.6 * k * math.sin(u * 2 * math.pi * 9), head_dy=0.012 * k * math.sin(u * 2 * math.pi * 11 + 1),
                       lh=_lerp(pose.lh, lh, q), rh=_lerp(pose.rh, rh, q), l_shape='fist', r_shape='fist', smile=-0.5,
                       brow_worry=0.8 * q, eye_wide=0.2 * q)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.15, 0.35, 'shiver'), label='shiver')


def look_up(p, t0: float, dur: float = 2.0) -> Move:
    """Head tipped back, eyes to the sky, mouth a little open in wonder."""
    def fn(t, pose):
        q = ease((t - t0) / 0.4)
        return replace(pose, gaze_y=-1.0 * q, gaze_x=pose.gaze_x * (1 - q), head_dy=-0.03 * q, head_tilt=-8 * q * p.facing,
                       brow_raise=0.5 * q, mouth_open=max(pose.mouth_open, 0.15 * q))
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.2, 0.35, 'look up'), label='look_up')


def snap(p, t0: float, dur: float = 1.0, side: str = 'far') -> Move:
    """The hand comes up beside the shoulder and flicks: a finger snap on the anchor word."""
    s = _side(p, side)
    head = p.body(p.s['head'])
    up = (head[0] + (0.62 if s == 'r' else -0.62), head[1] + 0.55)

    def fn(t, pose):
        u = t - t0
        q = back_out((u + 0.2) / 0.25, 1.4)
        flick = 30 * math.exp(-max(0.0, u) * 9) if u >= 0 else 0.0
        return replace(pose, **_hand(s, _lerp(p.rest(s), up, q), 'fist' if u < 0 else 'point', rot=flick, bend=1, front=1.0),
                       smile=0.6, brow_raise=0.4 * q)
    return Move(Action(t0 - 0.25, t0 + dur, fn, 0.1, 0.3, 'snap'), label='snap')


def freeze(p, t0: float, dur: float = 1.8) -> Move:
    """Stopped dead: eyes wide, a hand up at the side of the head, mouth open in a blank 'wait...'."""
    s = _side(p, 'far')
    head = p.body(p.s['head'])
    temple = (head[0] + (0.55 if s == 'r' else -0.55), head[1] + 0.1)

    def fn(t, pose):
        q = ease((t - t0) / 0.3)
        return replace(pose, **_hand(s, _lerp(p.rest(s), temple, q), 'open_palm', rot=-20, bend=1, front=1.0),
                       eye_wide=0.8 * q, brow_raise=0.7 * q, brow_worry=0.4 * q, smile=-0.3, mouth_open=max(pose.mouth_open, 0.25 * q))
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.12, 0.35, 'freeze'), label='freeze')


# ---- two characters in the host format: the friend tickles the host, the host laughs ---------------------

TICKLE_DUR = 1.2              # s: the wiggle at the target
TICKLE_REACH = 0.35           # s: the hand going out to it


def tickle(p, t0: float, target=None, side: str = 'auto', dur: float = TICKLE_DUR, reach_in: float = TICKLE_REACH,
           shape: str = 'grip') -> Move:
    """The hand (with a held feather) goes out to `target` (body space; default ahead at the waist) and wiggles there for
    `dur` seconds, eyes on it and a grin; the held prop flutters with it (pose.extra '<side>_prop_rot', degrees, read by
    the composer). Contact is at t0 + reach_in."""
    target = target or (0.95 * p.facing, 0.55)
    s = _side(p, side, target)
    look = _toward(p, target)

    def fn(t, pose):
        u = t - t0
        q = ease(u / reach_in)
        w = ease((u - 0.8 * reach_in) / 0.15) * (1 - ease((u - reach_in - dur + 0.15) / 0.15))
        start = getattr(pose, s + 'h')
        xy = _lerp(start, target, q)
        xy = (xy[0] + 0.035 * w * math.sin(u * 2 * math.pi * 5.5), xy[1] + 0.03 * w * math.sin(u * 2 * math.pi * 7.3 + 0.7))
        kw = _hand(s, xy, shape, rot=-10 * p.facing, droop=0.7, front=1.0, prop_rot=5.0 * w * math.sin(u * 2 * math.pi * 6.2))
        return replace(pose, **kw, **look, smile=0.85, brow_raise=0.35 * q, mouth_open=max(pose.mouth_open, 0.15 * w))
    return Move(Action(t0 - 0.05, t0 + reach_in + dur, fn, 0.1, 0.4, f'tickle {s}'), label='tickle')


def laugh(p, t0: float, dur: float = 0.8) -> Move:
    """A burst of laughter (a flinch from a tickle): shoulders squeezed up and shaking, head tilted back, eyes shut in a
    grin, mouth open, hands pulled in to the belly."""
    def fn(t, pose):
        u = t - t0
        q = ease(u / 0.15) * (1 - ease((u - dur + 0.2) / 0.2))
        shake = abs(math.sin(u * 2 * math.pi * 6.5))
        return replace(pose, lift=q * (0.05 + 0.05 * shake), head_tilt=-9 * q * p.facing, head_dy=-0.02 * q,
                       eye_open=max(0.05, 1 - 0.95 * q), eye_wide=0.0, smile=1.0, mouth_open=q * (0.45 + 0.25 * shake),
                       brow_raise=0.5 * q, brow_worry=0.0, lh=_lerp(pose.lh, (-0.40, 0.60), q), rh=_lerp(pose.rh, (0.40, 0.60), q),
                       l_shape='fist', r_shape='fist', l_rot=-30, r_rot=30, l_bend=1, r_bend=1)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.06, 0.3, 'laugh'), label='laugh')


# ---- presenter gestures (user 2026-11-09: the host reacts to every line, never hands-on-hips for a whole beat) ------
# The composer adds these line by line (compose_beats.acting); they are not script verbs.

def _window(t, t0, dur, rise=0.3, fall=0.35):
    return ease((t - t0) / rise) * (1 - ease((t - t0 - dur + fall) / fall))


def explain(p, t0: float, dur: float = 1.4, side: str = 'far') -> Move:
    """Talking hands: one hand comes up in front of the chest, palm up, and beats with the words."""
    s = _side(p, side)
    sgn = 1 if s == 'r' else -1
    spot = (0.40 * sgn, 0.36)

    def fn(t, pose):
        u = t - t0
        q = back_out(u / 0.3, 1.3)
        beat = 0.05 * math.sin(max(0.0, u - 0.3) * 2 * math.pi * 1.7) * (1 - ease((u - dur + 0.3) / 0.3))
        return replace(pose, **_hand(s, _lerp(p.rest(s), (spot[0], spot[1] + beat), q), 'open_palm', rot=70 * sgn, bend=1,
                                     droop=1.0, front=1.0), brow_raise=0.25 * q, smile=max(pose.smile, 0.35))
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.12, 0.35, 'explain'), label='explain')


def present(p, t0: float, dur: float = 1.5) -> Move:
    """An open palm out toward the thing (the side the host faces), eyes on it, then back to the viewer."""
    s = 'r' if p.facing > 0 else 'l'
    target = (0.95 * p.facing, 0.12)
    look = _toward(p, (2.0 * p.facing, -0.3))

    def fn(t, pose):
        u = t - t0
        q = back_out(u / 0.32, 1.3)
        eyes = 1 - ease((u - 0.8) / 0.3)
        return replace(pose, **_hand(s, _lerp(p.rest(s), target, q), 'open_palm', rot=55 * p.facing, bend=1, droop=1.2, front=1.0),
                       turn=pose.turn + (look['turn'] - pose.turn) * eyes, gaze_x=pose.gaze_x + (look['gaze_x'] - pose.gaze_x) * eyes,
                       brow_raise=0.35 * q, smile=max(pose.smile, 0.4))
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.12, 0.35, 'present'), label='present')


def lean(p, t0: float, dur: float = 1.6) -> Move:
    """Lean in to the viewer (the composer tilts the whole host about the feet): a smile, brows up."""
    def fn(t, pose):
        q = _window(t, t0, dur)
        return replace(pose, lean=4.0 * q, head_tilt=5 * q * p.facing, smile=max(pose.smile, 0.55), brow_raise=0.35 * q, eye_wide=0.1 * q)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.15, 0.35, 'lean'), label='lean')


def step_back(p, t0: float, dur: float = 1.5) -> Move:
    """A small step back in surprise (the composer moves the host back): both palms up and out, brows up."""
    lh, rh = (-0.50, 0.12), (0.50, 0.12)

    def fn(t, pose):
        q = back_out((t - t0) / 0.3, 1.3)
        return replace(pose, lh=_lerp(pose.lh, lh, q), rh=_lerp(pose.rh, rh, q), l_shape='open_palm', r_shape='open_palm',
                       l_rot=-20, r_rot=20, l_bend=1, r_bend=1, eye_wide=0.6 * q, brow_raise=0.8 * q, smile=-0.05, head_dy=-0.02 * q)
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.1, 0.35, 'step back'), label='step_back')


def open_hands(p, t0: float, dur: float = 1.4) -> Move:
    """'This much': both hands apart at chest height, palms in, for a big number or a big difference."""
    lh, rh = (-0.80, 0.22), (0.80, 0.22)

    def fn(t, pose):
        q = back_out((t - t0) / 0.32, 1.3)
        return replace(pose, lh=_lerp(pose.lh, lh, q), rh=_lerp(pose.rh, rh, q), l_shape='open_palm', r_shape='open_palm',
                       l_rot=10, r_rot=-10, l_bend=1, r_bend=1, eye_wide=0.3 * q, brow_raise=0.5 * q, smile=max(pose.smile, 0.3))
    return Move(Action(t0 - 0.05, t0 + dur, fn, 0.12, 0.35, 'open hands'), label='open_hands')


VERBS = dict(reach=reach, point=point, shrug=shrug, facepalm=facepalm, pick_up=pick_up, put_down=put_down, throw=throw,
             drink=drink, type=type_, write=write, wave=wave, nod=nod, shake_head=shake_head, slump=slump, jolt=jolt, gaze=gaze,
             shiver=shiver, look_up=look_up, snap=snap, freeze=freeze, tickle=tickle, laugh=laugh)
GESTURES = dict(explain=explain, present=present, lean=lean, step_back=step_back, open_hands=open_hands, nod=nod, shrug=shrug,
                gaze=gaze, point=point)
