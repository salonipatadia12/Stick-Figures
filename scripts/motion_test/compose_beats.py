"""Compose an episode's beats from its script, with no per-beat code (docs/PLAN.md §7 B and G).

For every beat the script (schema v3) says which setting and poses it needs (`shot.staging`), which verbs
happen on which words, which props are in the frame, and which motion recipes land the idea. This module
turns that into `beat_base.Beat` scenes:

- the base comes from the base plan (`pipeline.bases.plan`), so only approved bases are used;
- each staged action is a verb from `pipeline.rig.verbs`, timed on its anchor word;
- props come, in order, from the base's own cut-outs, the shared sprites, the prop library
  (`styles/cozy/props/library.json`) and the code-drawn set (`pipeline.rig.drawn`);
- motion recipes map to the camera and overlay helpers (slam, counter, checklist, split, arrow, icon pop);
- posture verbs (lie_down, sit_up, stand_up) split the beat into two shots on the anchor word when the
  library has a base for the new pose; walk becomes a pan.

`for_episode(slug)` returns what the runner needs: BEATS, shots(tl), word_times and LIBRARY_REPORT. An episode
module may override single beats with hand-written classes (`overrides={'b03': MyB03}`); those count as
hand-written in the report. Everything else counts as built from the libraries.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from pipeline import bases as BASES
from pipeline import overlays as O
from pipeline import store
from pipeline.rig import drawn
from pipeline.rig import verbs as V
from pipeline.rig.actions import Action, Idle, Performance, back_out, ease, keys
from pipeline.rig.skeleton import Held

from beat_base import PROPS, Beat, punch, shake, word_times  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PROP_LIBRARY = ROOT / 'styles/cozy/props/library.json'
TEXT_TOP, TEXT_LOW = (540, 300), (540, 1130)
FRAMING_WIDTH = {'wide': 940, 'medium': 760, 'close': 560, 'insert': 620, 'top_down': 760}
MOOD = {'hook': dict(smile=-0.1, brow_worry=0.4), 'setup': dict(smile=0.3), 'tension': dict(smile=-0.15, brow_worry=0.5),
        'reveal': dict(smile=0.3, brow_raise=0.2), 'payoff': dict(smile=0.6), 'ending': dict(smile=0.6)}
POSE_AFTER = {'lie_down': 'bed-awake', 'sit_up': 'bed-edge', 'stand_up': 'standing'}
# code-drawn props by keyword: name -> (factory, width in D)
DRAWN = {
    'lamp': (lambda: drawn.lamp(160, True, 40), 0.42), 'clock': (lambda: PROPS['clock'] if 'clock' in PROPS else drawn.lamp(160, False, 20), 0.33),
    'spring': (lambda: drawn.spring(110, 240, 7, 0.0), 0.3), 'knot': (lambda: drawn.knot(220, 3, 1.0), 0.7), 'thread': (lambda: drawn.knot(220, 3, 1.0), 0.7),
    'quilt': (lambda: drawn.scrap_holed(200), 0.55), 'scrap': (lambda: drawn.scrap(200), 0.55), 'steam': (lambda: drawn.steam(80, 140), 0.3),
    'figure': (lambda: drawn.figure(180), 0.5), 'person': (lambda: drawn.figure(180), 0.5), 'hole': (lambda: drawn.hole(70), 0.25),
    'receipt': (lambda: O.receipt_paper(('item', 'item', 'item')), 0.5), 'phone': (lambda: O.phone(300), 0.35), 'box': (lambda: O.takeout_box(300), 0.5),
    'sunbeam': (lambda: drawn.sunbeam(260, 420), 0.0), 'specks': (lambda: drawn.specks(220, 160), 0.0),
    'photo': (lambda: drawn.photo(240, 300), 0.4),
    'cup': (None, 0.0),                                  # the bases carry their own cups
}
SYNONYMS = {'alarm clock': 'clock', 'alarm': 'clock', 'coffee': 'cup', 'coffee cup': 'cup', 'mug': 'cup', 'tea': 'cup',
            'bedside lamp': 'lamp', 'desk lamp': 'lamp', 'light': 'lamp', 'bulb': 'lamp', 'blanket': 'quilt', 'duvet': 'quilt',
            'coil': 'spring', 'tangle': 'knot', 'yarn': 'knot', 'string': 'knot', 'bill': 'receipt', 'invoice': 'receipt',
            'mobile': 'phone', 'smartphone': 'phone', 'parcel': 'box', 'package': 'box', 'takeout': 'box',
            'beam': 'sunbeam', 'sun beam': 'sunbeam', 'ray of light': 'sunbeam', 'shaft of light': 'sunbeam', 'sunlight': 'sunbeam',
            'dust': 'specks', 'dust specks': 'specks', 'dust motes': 'specks', 'motes': 'specks',
            'photograph': 'photo', 'picture': 'photo', 'snapshot': 'photo', 'polaroid': 'photo'}


def prop_key(name: str) -> str:
    n = name.lower().strip()
    if n in SYNONYMS:
        return SYNONYMS[n]
    for k in list(DRAWN) + list(SYNONYMS):
        if re.search(rf'\b{re.escape(k)}\b', n):
            return SYNONYMS.get(k, k)
    return n


def prop_library() -> dict:
    return json.loads(PROP_LIBRARY.read_text()) if PROP_LIBRARY.exists() else {}


def counter(frame, t, t0, a: float, b: float, centre=TEXT_TOP, size: int = 110, dur: float = 1.2, prefix: str = '', suffix: str = ''):
    """A number counting from a to b over dur seconds, slammed in at t0 (number_counter recipe)."""
    if t < t0:
        return
    q = ease((t - t0) / dur)
    v = a + (b - a) * q
    txt = f'{prefix}{v:,.0f}{suffix}'
    O.slam(frame, O.text_layer(txt, size), t, t0, centre=centre)


def parse_counter(detail: str):
    nums = re.findall(r'-?[\d][\d,]*\.?\d*', detail)
    if len(nums) < 2:
        return None
    a, b = (float(n.replace(',', '')) for n in nums[:2])
    pre = re.match(r'\s*([$€£₹¥])', detail)
    return a, b, (pre.group(1) if pre else ''), ('%' if '%' in detail else '')


def word_time(T: dict, anchor: str, default: float) -> float:
    if not anchor:
        return default
    first = ''.join(c for c in anchor.split()[0].lower() if c.isalnum() or c in "'$,").strip(',')
    return T.get(first, default)


def text_items(spec: dict, tw) -> list:
    """(kind, time, payload) for the on-screen text a beat's motion recipes ask for."""
    out = []
    for m in spec['shot'].get('motion', []):
        r, anchor, detail = m['recipe'], m.get('anchor', ''), m.get('detail', '')
        at = tw(anchor)
        if r == 'text_slam' and (spec.get('on_screen_text') or detail):
            out.append(('slam', at, spec.get('on_screen_text') or detail))
        elif r == 'number_counter' and parse_counter(detail):
            out.append(('counter', at, parse_counter(detail)))
        elif r == 'checklist_tick' and detail:
            out.append(('checklist', at, [s.strip() for s in detail.split('|') if s.strip()]))
        elif r == 'split_compare' and '|' in detail:
            out.append(('split', at, [s.strip() for s in detail.split('|', 1)]))
        elif r == 'underline_draw':
            out.append((r, at, detail))
    return out


def draw_text_items(frame, t, items, centre, spec, t_min=None):
    """The text slot shows one item at a time: each one stays until the next one arrives (no two texts ever stack
    on each other, user 2026-11-08). Items before t_min (the first half of a split beat) are not repeated."""
    items = sorted((it for it in items if t_min is None or it[1] >= t_min - 0.05), key=lambda it: it[1])
    for i, (kind, at, payload) in enumerate(items):
        until = items[i + 1][1] - 0.2 if i + 1 < len(items) else None
        if t < at or (until is not None and t > until + 0.25):
            continue
        if kind == 'slam':
            words = payload.split()
            if len(words) > 3:
                im = O.stacked((' '.join(words[:len(words) // 2]), ' '.join(words[len(words) // 2:])), (88, 78))
            else:
                im = O.text_layer(payload, 96 if len(payload) <= 12 else 80)
            O.slam(frame, im, t, at, centre=centre, until=until)
        elif kind == 'counter':
            a, b, pre, suf = payload
            if until is None or t <= until:
                counter(frame, t, at, a, b, centre=centre, prefix=pre, suffix=suf)
        elif kind == 'checklist':
            if until is None or t <= until:
                rows = [(item, '', at + 0.5 * k) for k, item in enumerate(payload)]
                O.checklist(frame, t, rows, at - 0.1, centre=centre)
        elif kind == 'split':
            if until is None or t <= until:
                O.split_compare(frame, t, payload[0], payload[1], at, at + 0.8, centre=centre)
        elif kind == 'underline_draw' and spec.get('on_screen_text') and t >= at:
            q = ease((t - at) / 0.35)
            O._ink(O.ImageDraw.Draw(frame), [(centre[0] - 300, centre[1] + 70), (centre[0] - 300 + 600 * q, centre[1] + 74)], 10)


def boxes_overlap(a, b) -> float:
    """Intersection area over the area of a."""
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0) / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


class Composed(Beat):
    """A beat built from the script. Class attributes are set per beat by `make_class`."""
    spec: dict = {}          # beat dict from episode.json
    plan_row: dict = {}
    after_cut: bool = False  # second shot of a beat split by a posture verb

    def __init__(self, T, t0, t1, load_art=True):
        self.notes = []
        self.props_in_frame = {}          # name -> dict(sprite, xy plate px, width D, foot anchor)
        self.hold_schedule = {}           # cast -> [(from, to, side, prop)]
        self.flights = []                 # (t, side, prop, velocity, start xy)
        self.lands = []                   # (t, prop, xy)
        self.pops = []                    # (t, prop)
        self.cam_moves = []               # (kind, anchor time)
        self.text_items = []              # (kind, t, payload)
        super().__init__(T, t0, t1, load_art)
        if not self.cast:                 # an object-only insert: the setting without its painted character
            from dataclasses import replace as dc_replace
            from PIL import ImageFilter
            from pipeline.rig.puppet import Stage
            empty = ROOT / 'styles/cozy/bases' / self.base_name / 'empty.png'
            if empty.exists():
                soft = Image.open(empty).convert('RGB').filter(ImageFilter.GaussianBlur(1.2))
                self.stage = Stage(dc_replace(self.base, plate=soft, bodies={}, occluders={}), (self.stage.W, self.stage.H))
            else:
                self.notes.append(f'insert on {self.base_name}: no empty plate, the painted character stays in frame')

    # -- helpers -----------------------------------------------------------------------------------
    def lead(self):
        return self.p[self.cast[0]] if self.cast else None

    def tw(self, anchor: str, default=None):
        """Time of an anchor word (first word of a phrase), else default (the beat start + 0.4)."""
        if not anchor:
            return default if default is not None else self.t0 + 0.4
        first = ''.join(c for c in anchor.split()[0].lower() if c.isalnum() or c in "'$,").strip(',')
        return self.T.get(first, default if default is not None else self.t0 + 0.4)

    def table_point(self, p, dx: float = 0.5) -> tuple:
        """A spot on the surface in front of a character (plate px): at the rest hands' height."""
        ry = sum(p.s['rest'][s][1] for s in ('l', 'r')) / 2
        return (p.mid[0] + dx * p.D * p.facing, ry - 0.02 * p.D)

    def place_prop(self, cue: dict):
        """Find a sprite for a script prop and decide where it stands. Returns the entry or None (noted)."""
        key = prop_key(cue['name'])
        base_props = self.base.props
        places = self.base.meta.get('places', {})
        width = None
        sprite = None
        if key in base_props:
            sprite = base_props[key][0]
            width = places.get(key, {}).get('width', 0.33)
        elif key == 'cup' and self.base.cups:
            return None                                         # already on the table, part of the base
        elif key in PROPS:
            sprite, width = PROPS[key], 0.33
        else:
            lib = prop_library().get(key)
            if lib and (ROOT / lib['sprite']).exists():
                sprite, width = Image.open(ROOT / lib['sprite']).convert('RGBA'), lib.get('width', 0.4)
            elif key in DRAWN and DRAWN[key][0] is not None:
                sprite, width = DRAWN[key][0](), DRAWN[key][1]
        if sprite is None:
            self.notes.append(f'prop "{cue["name"]}" not in any library (base, shared, props library, drawn)')
            return None
        p = self.lead()
        where = cue.get('where', '').lower()
        if key in places:
            xy = tuple(places[key]['xy'])
        elif p is None:
            xy = (self.base.size[0] / 2, self.base.size[1] * 0.6)
        elif any(k in where for k in ('sky', 'above', 'ceiling', 'air', 'overhead')):
            xy = (p.s['head'][0] + 1.0 * p.D * p.facing, p.s['head'][1] - 1.1 * p.D)      # high in the frame, ahead
        elif 'wall' in where or 'window' in where:
            xy = (p.s['head'][0] + 0.9 * p.D * p.facing, p.s['head'][1] - 0.3 * p.D)
        elif 'floor' in where:
            xy = (p.mid[0] + 0.9 * p.D * p.facing, p.mid[1] + 1.4 * p.D)
        elif 'hand' in where or cue.get('held_by'):
            xy = self.table_point(p, 0.45)
        elif 'table' in where and 'table' not in self.base.layers and p.s.get('hip') is None:
            n = len(self.props_in_frame)                        # a standing scene with no table: the thing sits low, ahead
            xy = (p.mid[0] + (0.9 + 0.35 * n) * p.D * p.facing, p.mid[1] + 0.5 * p.D)
        else:
            n = len(self.props_in_frame)
            xy = self.table_point(p, 0.45 + 0.35 * n)
        entry = dict(sprite=sprite, xy=xy, width=width, key=key, name=cue['name'])
        self.props_in_frame[key] = entry
        return entry

    def body_of(self, who, xy):
        return self.p[who].body(xy)

    def target_for(self, who: str, target: str):
        """Resolve an action target to a body-space point for `who`, a prop key, or a direction word."""
        t = (target or '').lower().strip()
        key = prop_key(t) if t else ''
        if key in self.props_in_frame:
            return 'prop', key, self.body_of(who, self.props_in_frame[key]['xy'])
        if key == 'cup' and self.base.cups:
            idx = 0 if self.p[who].facing > 0 or len(self.base.cups) == 1 else 1
            c = self.base.meta['cups'][idx]
            return 'cup', idx, self.body_of(who, (c['xy'][0] + c['size'][0] / 2, c['xy'][1] + c['size'][1] * 0.45))
        if t in self.cast and t != who:
            return 'cast', t, self.body_of(who, self.p[t].s['head'])
        if t in ('camera', 'viewer'):
            return 'direction', 'camera', None
        if t in ('window', 'door', 'street', 'outside', 'away'):
            return 'direction', 'away', None
        if t in ('ceiling', 'sky', 'up'):
            return 'direction', 'up', None
        if t in ('floor', 'ground', 'down', 'table'):
            return 'direction', 'down', None
        p = self.p[who]
        return 'direction', 'ahead', (0.85 * p.facing, -0.45)

    # -- build -------------------------------------------------------------------------------------
    def build(self):
        spec, T = self.spec, self.T
        st = spec['shot'].get('staging') or {}
        perfs = {}
        for cue in st.get('props', []):
            self.place_prop(cue)
        moves = {who: [] for who in self.cast}
        for i, cue in enumerate(st.get('actions', [])):
            who, verb = cue['who'], cue['verb']
            if who not in self.cast or verb in V.STAGED:
                continue
            at = self.tw(cue['anchor'])
            kind, ref, point = self.target_for(who, cue.get('target', ''))
            p = self.p[who]
            mv = None
            if verb in ('reach', 'point'):
                if point is None:
                    point = (0.85 * p.facing, -0.6)
                mv = V.VERBS[verb](p, at, point)
            elif verb in ('pick_up', 'drink', 'put_down', 'throw'):
                prop = ref if kind in ('prop', 'cup') else prop_key(cue.get('target', '') or 'thing')
                if kind not in ('prop', 'cup'):
                    self.notes.append(f'{verb} "{cue.get("target")}": no such prop in the frame; staged with the hands only')
                    pt = (0.5 * p.facing, 0.28)
                else:
                    pt = point
                if verb == 'pick_up':
                    mv = V.pick_up(p, at, prop, pt)
                elif verb == 'drink':
                    mv = V.drink(p, at, prop, pt)
                elif verb == 'put_down':
                    mv = V.put_down(p, at, prop, pt)
                else:
                    mv = V.throw(p, at, prop)
                if kind in ('prop', 'cup'):
                    for h in mv.holds:
                        self.hold_schedule.setdefault(who, []).append(h + (kind,))
                    for f in mv.flights:
                        self.flights.append(f + (kind, pt))
                    self.lands += [l + (kind,) for l in mv.lands]
            elif verb in ('type', 'write'):
                mv = V.VERBS[verb](p, at, min(self.t1, at + 2.5))
            elif verb == 'gaze':
                mv = V.gaze(p, at, point if kind in ('prop', 'cup', 'cast') else None,
                            direction=ref if kind == 'direction' else '')
            else:
                mv = V.VERBS[verb](p, at)
            moves[who].append(mv)
        # motion recipes
        for m in spec['shot'].get('motion', []):
            r, anchor, detail = m['recipe'], m.get('anchor', ''), m.get('detail', '')
            at = self.tw(anchor)
            if r in ('push_in', 'pan_left', 'pan_right', 'punch_in', 'impact_shake', 'enter_left', 'enter_right', 'exit_left', 'exit_right'):
                self.cam_moves.append((r, at))
            elif r == 'text_slam' and (spec.get('on_screen_text') or detail):
                self.text_items.append(('slam', at, spec.get('on_screen_text') or detail))
            elif r == 'number_counter' and parse_counter(detail):
                self.text_items.append(('counter', at, parse_counter(detail)))
            elif r == 'checklist_tick' and detail:
                self.text_items.append(('checklist', at, [s.strip() for s in detail.split('|') if s.strip()]))
            elif r == 'split_compare' and '|' in detail:
                self.text_items.append(('split', at, [s.strip() for s in detail.split('|', 1)]))
            elif r == 'icon_pop':
                key = prop_key(detail) if detail else next(iter(self.props_in_frame), None)
                if key in self.props_in_frame:
                    self.pops.append((at, key))
                elif detail:
                    entry = self.place_prop(dict(name=detail, where='table', held_by=''))
                    if entry:
                        self.pops.append((at, entry['key']))
            elif r in ('arrow_draw', 'underline_draw'):
                self.text_items.append((r, at, detail))
            elif r == 'pose_swap' and self.cast:
                who = self.cast[0]
                moves[who].append(self.reaction(who, at, ' '.join(spec['shot'].get('pose_variants', []))))
        # performances
        mood = dict(MOOD.get(spec.get('role', 'setup'), {}))
        for k, who in enumerate(self.cast):
            p = self.p[who]
            base = self.rest_pose(who, **mood)
            if len(self.cast) == 2:                     # the two look at each other
                other = self.p[self.cast[1 - k]]
                base = replace(base, **V._toward(p, p.body(other.s['head'])))
            acts = [mv.action for mv in moves[who]]
            perfs[who] = Performance(base, Idle(seed=40 + k + len(spec['id'])), acts)
        # sound
        for s in spec.get('sfx', []):
            self.events.append((self.tw(s['anchor']), s['name'], 0.6))
        for kind, at, _ in self.text_items:
            self.events.append((at, 'pop' if kind != 'counter' else 'ding', 0.5))
        for at, _ in self.pops:
            self.events.append((at, 'pop', 0.5))
        for r, at in self.cam_moves:
            if r in ('punch_in', 'impact_shake'):
                self.events.append((at, 'thud' if r == 'impact_shake' else 'whoosh', 0.5))
        for who, mvs in moves.items():
            for mv in mvs:
                if mv.label == 'throw':
                    self.events.append((mv.action.start + 0.3, 'whoosh', 0.6))
        return perfs

    def reaction(self, who, at, text: str):
        """pose_swap: a reaction verb picked from the pose variant's words."""
        p = self.p[who]
        t = text.lower()
        if re.search(r'arms? (up|wide|out)|throws? (both )?arms|cheer', t):
            return V.wave(p, at, dur=1.2)
        if re.search(r'shrug|palms? up|open hands', t):
            return V.shrug(p, at)
        if re.search(r'nod', t):
            return V.nod(p, at)
        if re.search(r'point', t):
            return V.point(p, at, (0.85 * p.facing, -0.6))
        if re.search(r'slump|sigh|droop|sag|tired|head in hands|facepalm', t):
            return V.facepalm(p, at) if 'hand' in t or 'palm' in t else V.slump(p, at)
        if re.search(r'wave', t):
            return V.wave(p, at)
        if re.search(r'look|turn|glance|stare', t):
            return V.gaze(p, at, direction='camera' if 'camera' in t or 'viewer' in t else 'away')
        return V.jolt(p, at)

    # -- holds and props ---------------------------------------------------------------------------
    def holds(self, name, t):
        return {side for (a, b, side, prop, kind) in self.hold_schedule.get(name, []) if a <= t <= b}

    def held(self, name, t):
        out = {}
        for (a, b, side, prop, kind) in self.hold_schedule.get(name, []):
            if a <= t <= b:
                if kind == 'cup':
                    cup = self.base.cups[prop][0]
                    out[side] = Held(cup, (cup.width * 0.5, cup.height * 0.45), cup.width / self.p[name].D)
                else:
                    e = self.props_in_frame[prop]
                    out[side] = Held(e['sprite'], (e['sprite'].width * 0.5, e['sprite'].height * 0.6), e['width'])
        return out

    def away(self, t) -> set:
        """Props (keys) and cups (indices) that are off their place this frame: in a hand, flying, or thrown."""
        gone = set()
        for who, sched in self.hold_schedule.items():
            for (a, b, side, prop, kind) in sched:
                if a <= t:
                    gone.add((kind, prop))
        for (tf, side, prop, vel, kind, start) in self.flights:
            if t >= tf:
                gone.add((kind, prop))
        for (tl_, prop, xy, kind) in self.lands:
            if t >= tl_ and not any(a <= t <= b and pr == prop for s in self.hold_schedule.values() for (a, b, _, pr, _) in s):
                gone.discard((kind, prop))
        return gone

    def cups_away(self, t):
        return {prop for kind, prop in self.away(t) if kind == 'cup'}

    def standing(self) -> bool:
        p = self.lead()
        return p is not None and not p.s.get('hip') and 'table' not in self.base.layers

    def camera(self, t):
        p = self.lead()
        framing = self.spec['shot'].get('framing', 'medium')
        w = FRAMING_WIDTH.get(framing, 760)
        if p is None:
            cx, cy = self.base.size[0] / 2, self.base.size[1] * 0.55
        else:
            heads = [self.p[n].s['head'] for n in self.cast]
            cx = sum(h[0] for h in heads) / len(heads)
            cy = sum(h[1] for h in heads) / len(heads) + (0.35 if framing == 'close' else 0.9) * p.D
            if self.standing():                          # never a body-filling close-up of a standing character
                w = max(w, 860)
                cy = sum(h[1] for h in heads) / len(heads) + 0.85 * p.D
            if len(self.cast) == 2:
                w = max(w, 900)
        u = (t - self.t0) / max(0.1, self.t1 - self.t0)
        dx = 0.0
        for r, at in self.cam_moves:
            if r == 'push_in':
                w -= 0.09 * w * ease(u)
            elif r == 'pan_left' or r == 'enter_left':
                dx -= 70 * ease(u) if r == 'pan_left' else -70 * (1 - ease((t - self.t0) / 0.6))
            elif r == 'pan_right' or r == 'enter_right':
                dx += 70 * ease(u) if r == 'pan_right' else 70 * (1 - ease((t - self.t0) / 0.6))
            elif r in ('exit_left', 'exit_right'):
                dx += (-1 if r == 'exit_left' else 1) * 60 * ease((t - at) / 0.5)
            elif r == 'punch_in':
                w -= w * punch(t, at, 0.1, 0.2)
        x0, y0, s = self.stage.camera(cx + dx, cy, w)
        for r, at in self.cam_moves:
            if r == 'impact_shake':
                sx, sy = shake(t, at, 1.0)
                x0, y0 = x0 + sx, y0 + sy
        return (x0, y0, s)

    def on_table(self, t):
        if not self.props_in_frame and not self.flights:
            return None

        def draw(frame, cam):
            gone = self.away(t)
            for key, e in self.props_in_frame.items():
                if ('prop', key) in gone:
                    continue
                scale = 1.0
                for at, k in self.pops:
                    if k == key:
                        if t < at:
                            scale = 0.0
                        else:
                            scale = back_out((t - at) / 0.3, 1.7)
                if scale <= 0.01:
                    continue
                xy = e['xy']
                for (tl_, prop, bxy, kind) in self.lands:          # put down somewhere else
                    if prop == key and t >= tl_ and self.cast:
                        xy = self.p[self.cast[0]].world(bxy)
                D = self.lead().D if self.lead() else 300
                self.place(frame, e['sprite'], cam, xy, e['width'] * D * scale, anchor=(0.5, 1.0))
        return draw

    def flying(self, t):
        if not self.flights:
            return None

        def draw(frame, cam):
            for (tf, side, prop, vel, kind, start) in self.flights:
                u = t - tf
                if u < 0 or u > 1.4:
                    continue
                p = self.lead()
                bx = start[0] + vel[0] * u
                by = start[1] + vel[1] * u + 4.5 * u * u
                sprite = self.base.cups[prop][0] if kind == 'cup' else self.props_in_frame[prop]['sprite']
                width = (sprite.width / p.D) if kind == 'cup' else self.props_in_frame[prop]['width']
                self.place(frame, sprite, cam, p.world((bx, by)), width * p.D, angle=240 * u)
        return draw

    def char_boxes(self, t, cam=None) -> list:
        """Each character's head and body on screen (frame px): text must never sit on these."""
        cam = cam or self.camera(t)
        out = []
        for n in self.cast:
            p = self.p[n]
            hx, hy = p.s['head']
            rest_y = max(p.s['rest'][k][1] for k in ('l', 'r'))
            bottom = (p.s['hip'][1] + 0.15 * p.D) if p.s.get('hip') else (hy + 3.3 * p.D if self.standing() else rest_y + 0.25 * p.D)
            a = self.to_screen(cam, (hx - 0.62 * p.D, hy - 0.56 * p.D))
            b = self.to_screen(cam, (hx + 0.62 * p.D, bottom))
            out.append((a[0], a[1], b[0], b[1]))
        return out

    def text_centre(self):
        """The text slot for this shot: the first of top, upper-middle, low that no character's head or body covers."""
        if getattr(self, '_centre', None) is None:
            mid = (self.t0 + self.t1) / 2
            boxes = self.char_boxes(mid)
            longest = max((len(str(p)) for _, _, p in self.text_items), default=12)
            half = min(470, 28 * longest + 60)
            self._centre = TEXT_TOP
            for c in (TEXT_TOP, (540, 420), TEXT_LOW):
                tb = (c[0] - half, c[1] - 62, c[0] + half, c[1] + 62)
                if all(boxes_overlap(tb, b) < 0.04 for b in boxes):
                    self._centre = c
                    break
        return self._centre

    def frame(self, t):
        frame = super().frame(t)
        cam = self.camera(t)
        for box in self.char_boxes(t, cam):
            O.note_box('character', box)
        return frame

    def overlay(self, frame, t):
        centre = self.text_centre()
        draw_text_items(frame, t, [it for it in self.text_items if it[0] != 'arrow_draw'], centre, self.spec,
                        t_min=self.t0 if self.after_cut else None)
        for kind, at, payload in self.text_items:
            if kind == 'arrow_draw' and self.cast and (not self.after_cut or at >= self.t0):
                cam = self.camera(t)
                head = self.to_screen(cam, self.lead().s['head'])
                O.arrow(frame, t, at, (centre[0], centre[1] + 80), (head[0], head[1] - 0.6 * self.lead().D * cam[2]))


class VisualShot:
    """A beat (or the first part of one) that SHOWS the subject: a painted insert or a drawn graphic, full frame, no
    characters. Same interface as a Beat for the runner: events, notes and frame(t)."""
    spec: dict = {}
    visual = 'insert'

    def __init__(self, T, t0, t1, load_art=True):
        self.T, self.t0, self.t1 = T, t0, t1
        self.events, self.notes = [], []
        self.text_items = text_items(self.spec, self.tw)
        for snd in self.spec.get('sfx', []):
            at = self.tw(snd['anchor'])
            if at <= t1:
                self.events.append((at, snd['name'], 0.6))
        for kind, at, _ in self.text_items:
            self.events.append((at, 'pop' if kind != 'counter' else 'ding', 0.5))
        self.setup(load_art)

    def tw(self, anchor, default=None):
        return word_time(self.T, anchor, self.t0 + 0.4 if default is None else default)

    def frame(self, t):
        frame = self.draw(t)
        draw_text_items(frame, t, self.text_items, TEXT_TOP, self.spec)
        return frame


class InsertShot(VisualShot):
    visual = 'insert'

    def setup(self, load_art):
        from pipeline import inserts as INS
        from pipeline.rig.inserts import Effect, InsertScene
        ins = self.spec['shot']['insert']
        effects = []
        for k, e in enumerate(ins.get('effects', [])):
            at = self.tw(e['anchor']) if e.get('anchor') else None
            effects.append(Effect(e['kind'], at, e.get('at', ''), e.get('detail', ''), seed=11 + k))
            if e['kind'] == 'shake' and at is not None:
                self.events.append((at, 'thud', 0.6))
            elif e['kind'] in ('label', 'arrow') and at is not None:
                self.events.append((at, 'pop', 0.45))
            elif e['kind'] == 'crossfade' and at is not None:
                self.events.append((at, 'whoosh', 0.4))
        for m in self.spec['shot'].get('motion', []):               # camera recipes act on the painting
            r, at = m['recipe'], self.tw(m.get('anchor', ''))
            if r == 'punch_in':
                effects.append(Effect('push', at))
                self.events.append((at, 'whoosh', 0.5))
            elif r == 'push_in':
                effects.append(Effect('push', None))
            elif r in ('pan_left', 'pan_right'):
                effects.append(Effect('pan', None, detail=r.split('_')[1]))
            elif r == 'impact_shake':
                effects.append(Effect('shake', at))
                self.events.append((at, 'thud', 0.6))
        self.key = ins['key']
        if not load_art:
            return
        lib = INS.library()
        if self.key not in lib:
            raise RuntimeError(f'{self.spec["id"]}: insert scene "{self.key}" is not in the library; run stick inserts --generate')
        scene = Image.open(ROOT / lib[self.key]['file'])
        variants = {}
        for e in ins.get('effects', []):
            if e['kind'] == 'crossfade' and e.get('detail'):
                vk = INS.variant_key(self.key, e['detail'])
                if vk in lib:
                    variants[e['detail']] = Image.open(ROOT / lib[vk]['file'])
                else:
                    self.notes.append(f'crossfade "{e["detail"]}" has no painted variant; it is skipped')
        self.scene = InsertScene(scene, effects, self.t0, self.t1, variants, seed=sum(map(ord, self.spec['id'])))

    def draw(self, t):
        return self.scene.frame(t)


class GraphicShot(VisualShot):
    visual = 'graphic'

    def setup(self, load_art):
        from pipeline.rig.graphics import Graphic
        g = self.spec['shot']['graphic']
        items = g.get('items', [])
        anchors = g.get('anchors') or []
        span = max(0.6, self.t1 - self.t0)
        times = []
        for k, item in enumerate(items):
            a = anchors[k] if k < len(anchors) else ''
            default = self.t0 + 0.35 + k * (span * 0.6 / max(1, len(items)))
            times.append(self.tw(a, default) if a else default)
        for at in times:
            self.events.append((at, 'pop', 0.45))
        self.graphic = Graphic(g['kind'], items, times, g.get('detail', ''), self.t0, self.t1, seed=sum(map(ord, self.spec['id'])))

    def draw(self, t):
        return self.graphic.frame(t)


def make_visual_class(spec: dict):
    cls = InsertShot if spec['shot'].get('visual') == 'insert' else GraphicShot
    return type(f'{cls.__name__}_{spec["id"]}', (cls,), dict(spec=spec))


def make_class(spec: dict, row: dict, base_name: str, after_cut: bool = False):
    cast = tuple(w for w in spec['shot'].get('cast', []))
    meta = json.loads((ROOT / 'styles/cozy/bases' / base_name / 'base.json').read_text())
    seats = {s['name'] for s in meta['seats']}
    cast = tuple(w for w in cast if w in seats)
    return type(f'Composed_{spec["id"]}{"b" if after_cut else ""}', (Composed,),
                dict(base_name=base_name, cast=cast, spec=spec, plan_row=row, after_cut=after_cut))


def for_episode(slug: str, overrides: dict | None = None, lib: dict | None = None):
    """BEATS, shots(tl), word_times and LIBRARY_REPORT for the runner, composed from the episode's script."""
    episode, _ = store.load(slug)
    raw = json.loads((ROOT / 'episodes' / slug / 'episode.json').read_text())
    specs = {b['id']: b for b in raw['beats']}
    rows = {r['beat']: r for r in BASES.plan(episode, lib)}
    lib = BASES.library() if lib is None else lib
    overrides = overrides or {}
    beats, report, splits = {}, {}, {}
    visuals = {}                                         # beat id -> 'insert' | 'graphic' (shown full frame)
    for b in episode.beats:
        row = rows[b.id]
        spec = specs[b.id]
        if b.id in overrides:
            beats[b.id] = overrides[b.id]
            report[b.id] = dict(library=[], hand_written=[overrides[b.id].__name__], props=[], notes=['hand-written override'])
            continue
        visual = b.shot.visual
        if visual in ('insert', 'graphic'):
            visuals[b.id] = visual
            used = [m.recipe for m in b.shot.motion]
            what = f'insert {b.shot.insert.key}' if visual == 'insert' else f'graphic {b.shot.graphic.kind}'
            effects = [e.kind for e in b.shot.insert.effects] if visual == 'insert' else []
            notes = []
            base = row['base'] if not str(row['base']).startswith('(') else None
            if b.shot.reaction_anchor and b.shot.cast:
                if base is None:
                    raise RuntimeError(f'{b.id}: no approved base for the reaction ({row["wanted"]}); run the bases gate first')
                if ' + ' in base:
                    base = base.split(' + ')[0]
                splits[b.id] = (b.shot.reaction_anchor, base)
                beats[b.id + 'a'] = make_visual_class(spec)
                beats[b.id + 'b'] = make_class(spec, row, base, after_cut=True)
                notes.append(f'cut to the reaction on "{b.shot.reaction_anchor}" ({base})')
                verbs = [f'{a.who} {a.verb}' for a in b.shot.staging.actions] if b.shot.staging else []
                report[b.id] = dict(library=[what] + effects + used + [f'base {base}'] + verbs, hand_written=[], props=[], notes=notes, visual=visual)
            else:
                beats[b.id] = make_visual_class(spec)
                report[b.id] = dict(library=[what] + effects + used, hand_written=[], props=[], notes=notes, visual=visual)
            continue
        base = row['base']
        notes = []
        if base is None:
            raise RuntimeError(f'{b.id}: no approved base for {row["wanted"]}; run the bases gate first')
        if ' + ' in base:                                   # two characters, one base each: the first carries the beat
            base = base.split(' + ')[0]
            notes.append(f'two-character beat on one base ({base}); the other character is not in frame')
        st = b.shot.staging
        used = [m.recipe for m in b.shot.motion]
        verbs_used, props_used = [], []
        if st:
            verbs_used = [f'{a.who} {a.verb}' for a in st.actions]
            props_used = [p.name for p in st.props]
            posture = [a for a in st.actions if a.verb in POSE_AFTER]
            if posture:
                a = posture[0]
                d = dict(setting=row['setting'], pose=POSE_AFTER[a.verb], view='eye-level', cast=[a.who], poses={a.who: POSE_AFTER[a.verb]})
                second = BASES.match(d, lib)
                if second:
                    splits[b.id] = (a.anchor, second)
                    beats[b.id + 'a'] = make_class(spec, row, base)
                    beats[b.id + 'b'] = make_class(spec, row, second, after_cut=True)
                    notes.append(f'{a.verb} on "{a.anchor}": cut from {base} to {second}')
                else:
                    notes.append(f'{a.verb} on "{a.anchor}": no base for {POSE_AFTER[a.verb]} in this setting; staged as a punch-in')
                    spec['shot'].setdefault('motion', []).append(dict(recipe='punch_in', anchor=a.anchor, detail=''))
        if b.id not in splits:
            beats[b.id] = make_class(spec, row, base)
        report[b.id] = dict(library=[f'base {base}'] + used + verbs_used, hand_written=[], props=props_used, notes=notes)

    subject = {}                                         # beat id -> (seconds the subject fills the frame, beat seconds)

    def shots(tl):
        out = []
        for bt in tl['beats']:
            dur = bt['shot_end'] - bt['shot_start']
            if bt['id'] in splits:
                anchor, _ = splits[bt['id']]
                T = word_times(bt['words'])
                first = ''.join(c for c in anchor.split()[0].lower() if c.isalnum() or c in "'$,").strip(',')
                cut = T.get(first, (bt['shot_start'] + bt['shot_end']) / 2) - 0.05
                out.append((bt['id'] + 'a', bt['shot_start'], cut))
                out.append((bt['id'] + 'b', cut, bt['shot_end']))
                shown = cut - bt['shot_start'] if bt['id'] in visuals else bt['shot_end'] - cut
            else:
                out.append((bt['id'], bt['shot_start'], bt['shot_end']))
                shown = dur if bt['id'] in visuals else 0.0
            subject[bt['id']] = (round(shown, 3), round(dur, 3))
        return out

    def summary():
        total = len(report)
        lib_beats = sum(1 for r in report.values() if not r['hand_written'])
        out = dict(beats=total, library_beats=lib_beats, library_pct=round(100 * lib_beats / total) if total else 0, per_beat=report)
        if subject:                                    # the topic itself on screen (an insert or a graphic), user 2026-11-08
            on = [k for k, (s_, d_) in subject.items() if d_ and s_ / d_ >= 0.5]
            secs = sum(s_ for s_, _ in subject.values())
            dur = sum(d_ for _, d_ in subject.values())
            out['subject'] = dict(beats=len(subject), subject_beats=len(on), subject_pct=round(100 * len(on) / len(subject)),
                                  subject_time_pct=round(100 * secs / dur) if dur else 0,
                                  per_beat={k: dict(shown_s=s_, beat_s=d_, visual=visuals.get(k, 'character')) for k, (s_, d_) in subject.items()})
        return out
    return SimpleNamespace(BEATS=beats, shots=shots, word_times=word_times, LIBRARY_REPORT=report, library_summary=summary,
                           slug=slug)
