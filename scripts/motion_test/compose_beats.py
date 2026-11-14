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

import copy
import json
import math
import re
from bisect import bisect_right
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

from beat_base import H, PROPS, W, Beat, punch, shake, word_times  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PROP_LIBRARY = ROOT / 'styles/cozy/props/library.json'
TEXT_TOP, TEXT_LOW = (540, 300), (540, 1130)
HEAD_CLEAR = 395                         # screen y a head may not rise above while the top text slot is in use
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
    n = re.sub(r'^(a|an|the|some)\s+', '', name.lower().strip())
    if n in SYNONYMS:
        return SYNONYMS[n]
    for k in list(DRAWN) + list(SYNONYMS):
        if re.search(rf'\b{re.escape(k)}\b', n):
            return SYNONYMS.get(k, k)
    return n


def prop_library() -> dict:
    return json.loads(PROP_LIBRARY.read_text()) if PROP_LIBRARY.exists() else {}


def find_prop(name: str, library: dict):
    """The library entry for a held prop as the script words it: the exact key first ("a test strip" -> "test
    strip"), else the longest key whose words all appear in the name ("a jar of honey" -> "honey jar")."""
    key = prop_key(name)
    if key in library:
        return library[key]
    words = set(re.findall(r'[a-z]+', key))
    fits = [k for k in library if set(k.split()) <= words]
    return library[max(fits, key=len)] if fits else None


def counter(frame, t, t0, a: float, b: float, centre=TEXT_TOP, size: int = 110, dur: float = 1.2, prefix: str = '', suffix: str = ''):
    """A number counting from a to b over dur seconds, slammed in at t0 (number_counter recipe)."""
    if t < t0:
        return
    q = ease((t - t0) / dur)
    v = a + (b - a) * q
    txt = f'{prefix}{v:,.0f}{suffix}'
    O.slam(frame, O.text_layer(txt, size), t, t0, centre=centre)


def parse_counter(detail: str):
    """'0 -> 35' / '$0 -> $3,000' / '0 -> 40%' / '0 -> 35 seconds': (from, to, prefix, suffix). A word after the last
    number is shown after the counted number ("35 seconds", user 2026-11-10)."""
    found = list(re.finditer(r'-?[\d][\d,]*\.?\d*', detail))
    if len(found) < 2:
        return None
    a, b = (float(m.group(0).replace(',', '')) for m in found[:2])
    pre = re.match(r'\s*([$€£₹¥])', detail)
    tail = detail[found[1].end():].strip()
    suffix = '%' if '%' in detail else (' ' + tail if re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,20}", tail) else '')
    return a, b, (pre.group(1) if pre else ''), suffix


def word_time(T: dict, anchor: str, default: float) -> float:
    if not anchor:
        return default
    first = ''.join(c for c in anchor.split()[0].lower() if c.isalnum() or c in "'$,").strip(',')
    return T.get(first, default)


def slams(spec: dict) -> bool:
    """The beat slams its on-screen text at the top of the frame."""
    return any(m['recipe'] == 'text_slam' for m in spec['shot'].get('motion', [])) and bool(spec.get('on_screen_text'))


def same_text(text: str) -> str:
    return ' '.join(re.findall(r'[a-z0-9.]+', (text or '').lower()))


def held_width(entry: dict, sprite) -> float:
    """A held prop's width in head diameters: 1.5x the library width (at least 0.6), but a long thin thing (a test strip,
    a pencil) keeps its long side near one head so it reads as hand-sized, not a sign board."""
    w, h = sprite.size
    return min(max(0.6, 1.5 * entry.get('width', 0.4)), 1.1 * w / max(w, h, 1))


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
            sp = spec['shot'].get('split') if spec['shot'].get('visual') == 'split' else None
            if sp and sp.get('labels'):                 # a split screen already labels its halves: no second pair
                continue
            out.append(('split', at, [s.strip() for s in detail.split('|', 1)]))
        elif r == 'underline_draw':
            out.append((r, at, detail))
    return out


TEXT_KINDS = ('slam', 'counter', 'checklist', 'split')
STOPWORDS = {'the', 'and', 'for', 'not', 'but', 'you', 'your', 'are', 'was', 'its', "it's", 'with', 'when', 'than', 'then',
             'that', 'this', 'what', 'why', 'how', 'who', 'all', 'one', 'out', 'off', 'into', 'from', 'about'}


def finish_text_items(spec: dict, tw, items: list, t0: float, t1: float) -> list:
    """The beat's text items, completed and timed for its shot:
    - a beat whose script sets on_screen_text but no recipe that draws it (text_slam, number_counter, split_compare,
      checklist_tick) still shows it, slammed on the first narration word it shares with the text, else a third of
      the way in (user review 2026-11-08: doorway b08's "Only when overloaded" never appeared);
    - every item lands at least overlays.MIN_HOLD before the cut (O.settle)."""
    items = list(items)
    ost = (spec.get('on_screen_text') or '').strip()
    if ost and not any(k in TEXT_KINDS for k, _, _ in items):
        keys = {w for w in re.findall(r"[\w'-]+", ost.lower()) if len(w) > 2 and w not in STOPWORDS}
        word = next((w for w in re.findall(r"[\w'-]+", spec.get('narration', '').lower()) if w in keys), None)
        at = tw(word) if word else t0 + 0.33 * (t1 - t0)
        items.append(('slam', at, ost))
    times = O.settle([at for _, at, _ in items], t0, t1)
    out = []
    for (k, _, payload), at in zip(items, times):
        if k == 'checklist':                         # each row ticks on the first narrated word it shares with its label
            said = re.findall(r"[\w'-]+", spec.get('narration', '').lower())
            rows, last = [], at
            for i, label in enumerate(payload):
                keys = [w for w in re.findall(r"[\w'-]+", label.lower()) if len(w) > 2 and w not in STOPWORDS]
                hit = next((w for w in said if w in keys), None)
                when = tw(hit) if hit else None
                when = when if when is not None and when >= last else last + (0.5 if i else 0.0)
                rows.append((label, min(when, t1 - 0.4)))
                last = rows[-1][1]
            payload = rows
        out.append((k, at, payload))
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
                rows = [(item[0], '', item[1]) if isinstance(item, tuple) else (item, '', at + 0.5 * k)
                        for k, item in enumerate(payload)]
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
    host_format: bool = False  # script v5: the lead is the host (lip-sync, eyes to camera, script sounds only)

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

    def pose(self, name, t):
        pose = super().pose(name, t)
        cue = self.spec['shot'].get('host') or {}
        if self.host_format and name == 'lead' and cue.get('where', 'off') != 'off':
            if not hasattr(self, '_starts'):
                self._starts = sorted(v for v in self.T.values() if self.t0 - 0.05 <= v <= self.t1)
            pose = replace(pose, mouth_open=max(pose.mouth_open, talk_curve(self._starts, t)))
            if cue.get('to_camera'):
                pose = replace(pose, gaze_x=pose.gaze_x * 0.2, turn=pose.turn * 0.55)
        return pose

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
        self.text_items = finish_text_items(spec, self.tw, self.text_items, self.t0, self.t1)
        for s in spec.get('sfx', []):
            self.events.append((self.tw(s['anchor']), s['name'], 0.6))
        for kind, at, _ in self.text_items:
            self.events.append((at, 'pop' if kind != 'counter' else 'ding', 0.5))
        for at, _ in self.pops:
            self.events.append((at, 'pop', 0.5))
        for r, at in self.cam_moves:
            if r in ('punch_in', 'impact_shake'):
                self.events.append((at, 'thud' if r == 'impact_shake' else 'whoosh', 0.5))
        if self.host_format:
            self.events = [(self.tw(x['anchor']), x['name'], 0.6) for x in spec.get('sfx', [])]
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
        if p is not None and any(it[0] != 'arrow_draw' for it in self.text_items):
            # the text slot at the top must stay clear of heads: frame lower, or wider when the plate ends (user 2026-11-08)
            top = min(self.p[n].s['head'][1] - 0.58 * self.p[n].D for n in self.cast)
            for _ in range(6):
                head_top = (top - y0) * s
                if head_top >= HEAD_CLEAR:
                    break
                cy -= (HEAD_CLEAR - head_top) / s
                x0, y0, s = self.stage.camera(cx + dx, cy, w)
                if (top - y0) * s < HEAD_CLEAR:
                    w = min(self.base.size[0], w * 1.08)
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
            def chars(kind, p):
                return len(p) if isinstance(p, str) else (9 if kind == 'counter' else max((len(str(x)) for x in p), default=10) * (2 if kind == 'split' else 1))
            longest = max((chars(k, p) for k, _, p in self.text_items if k != 'arrow_draw'), default=12)
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


HOST_DIR = ROOT / 'styles/cozy/host/lead-standing'
HOST_HEAD_BASE = 'lead-standing-doorway'
HOST_H = 0.44                 # the host's height (head top to feet) as a share of the frame, standing in a scene
POP_H = 0.40                  # popping in under a diagram: head and shoulders below the captions, the rest off-frame
HOST_X = {'left': 0.15, 'centre': 0.50, 'right': 0.85}   # off-centre by default: the captions run across the middle
HOST_SIZES = {'small': (0.30, 0.86), 'medium': (0.44, 0.93), 'large': (0.62, 1.06), 'close': (0.83, 1.23)}
# (height as a share of the frame, feet line): small stands further back; large and close are cut by the bottom edge on
# purpose (knees up; head and shoulders), like a pop-in (user 2026-11-09: vary the host's position and size per beat)
HOST_MARGIN = 14              # px: the whole host, arms included, stays this far inside the frame (a pop-in excepted)
HOST_REST = {'l': (-0.47, 0.93), 'r': (0.47, 0.93)}   # body space: arms relaxed at the sides, not hands on the hips
TINT_MAX = 0.06               # the scene's colour on the host, per channel (user 2026-11-09: white everywhere, tint <= 10-15 %)
LIGHT_RANGE = (0.88, 1.02)    # and the whole light match within 12 % of the host's own white
RIM = 0.4                     # rim light strength on the lit edge
MAX_SFX = 6                   # user 2026-11-08: at most 5-6 meaningful sounds per video
ACT_GAP = 1.0                 # s: at most one new gesture this often
FILL_GAP = 1.6                # s: a longer still stretch gets a small gesture of its own
LOOKS = ('present', 'gaze', 'point', 'look')   # while these play the host looks at the thing, not at the viewer
FRIEND_DIR = ROOT / 'styles/cozy/host/friend-standing'
FRIEND_REACH = 2.0            # D of empty canvas on the friend's facing side: room for an arm and a feather reaching out
FRIEND_MIN_GAP = 1.9          # D between the two head lines at least (no overlap of the two bodies)
FEATHER_D = 1.35              # a feather on a stick, end to tip, in head diameters
TICKLE_DROP = 1.3             # the host's side is tickled this many D below its head centre (its widest, flat side)
TICKLE_PRESS = 0.08           # D: the feather tip sits this far into the host's outline (it touches)
FRIEND_HOLD = {'feather': ((0.40, 0.34), -74.0)}   # held up beside the body, the plume up and toward the host


def talk_curve(starts: list, t: float) -> float:
    """Lip-sync from word start times: the mouth opens and closes about every 0.17 s while a word is spoken."""
    i = bisect_right(starts, t) - 1
    if i < 0:
        return 0.0
    w = starts[i]
    nxt = starts[i + 1] if i + 1 < len(starts) else w + 0.45
    dur = min(0.5, max(0.12, nxt - w - 0.05))
    u = t - w
    return 0.0 if u > dur else 0.12 + 0.5 * abs(math.sin(math.pi * u / 0.17))


def host_spec(spec: dict) -> dict:
    """The beat as the host performs it: lead alone, standing, its verbs; no text, no camera moves, no sounds."""
    s = copy.deepcopy(spec)
    st = s['shot'].get('staging') or {}
    s['shot']['cast'] = ['lead']
    s['shot']['staging'] = dict(setting=st.get('setting', 'other'), setting_detail='', poses=[dict(who='lead', pose='standing')],
                                actions=[a for a in st.get('actions', []) if a['who'] == 'lead' and a['verb'] not in V.STAGED],
                                props=[])
    s['shot']['motion'], s['on_screen_text'], s['sfx'] = [], '', []
    return s


SURPRISE = re.compile(r"\b(but|even|wait|except|actually|forgot|failed|warning|no effect|erupted|cancell?ed)\b")
BIG = re.compile(r"\d|\b(more|most|far|times|huge|whole|every|worldwide|around the world|all|soared|millions?|largest|three)\b")
YOU = re.compile(r"\b(you|your|you're|we|let me|let's|me)\b")
CONCLUDE = re.compile(r"^(so|that's|here's|this is)\b|\banswer\b|\bthat's why\b|\bthat's how\b")
THING = re.compile(r"\b(there|this|that|it|sky|sun|light|door\w*|room|volcano|air|snow|veil|scene|kitchen|field|farm)\b")


def clauses(narration: str, T: dict) -> list:
    """(text, start time) per clause of the narration: split at punctuation, at most 7 words. T is word -> time in
    spoken order (beat_base.word_times); when the script's words and the spoken ones differ in count, 4-word chunks."""
    toks, items = narration.split(), list(T.items())
    if len(toks) != len(items):
        toks = [k.split('#')[0] + (',' if (i + 1) % 4 == 0 else '') for i, (k, _) in enumerate(items)]
    out, cur, start = [], [], None
    for tok, (_, t) in zip(toks, items):
        if not cur:
            start = t
        cur.append(tok)
        if re.search(r"[,.;:?!—…]$", tok) or len(cur) >= 7:
            out.append((' '.join(cur), start))
            cur = []
    if cur:
        out.append((' '.join(cur), start))
    return out


def acting(narration: str, T: dict, t0: float, t1: float, busy: list, where: str = 'scene') -> list:
    """The host reacts to every line (user 2026-11-09): one gesture per clause that the script's own verbs leave free.
    A question shrugs, a surprise steps back, a conclusion nods, a big number opens the hands, 'you' leans in, a thing
    in the scene is presented with an open palm; otherwise talking hands, a look, a nod or a lean, never twice running.
    Returns [(gesture, at, dur)]."""
    cycle = ['explain', 'present', 'nod', 'lean'] if where == 'scene' else ['explain', 'nod', 'open_hands', 'lean']
    cl = clauses(narration, T)
    out, last, last_t, k = [], None, -9.0, 0
    for i, (text, at) in enumerate(cl):
        nxt = cl[i + 1][1] if i + 1 < len(cl) else t1
        at = max(at, last_t + ACT_GAP)                  # right after another gesture: later in the same clause, if it fits
        if at < t0 - 0.05 or at > t1 - 0.7 or at > nxt - 0.4:
            continue
        if any(a - 0.5 <= at <= b + 0.2 for a, b in busy):
            continue
        low = text.lower()
        if '?' in text:
            verb = 'shrug'
        elif SURPRISE.search(low):
            verb = 'step_back'
        elif CONCLUDE.search(low):
            verb = 'nod'
        elif BIG.search(low):
            verb = 'open_hands'
        elif YOU.search(low):
            verb = 'lean'
        elif THING.search(low):
            verb = 'present'
        else:
            verb = cycle[k % len(cycle)]
            k += 1
        if where != 'scene' and verb in ('step_back', 'present'):
            verb = 'explain'
        while verb == last:
            verb = cycle[k % len(cycle)]
            k += 1
        out.append((verb, at, max(0.9, min(2.2, nxt - at - 0.1))))
        last, last_t = verb, at
    # second pass: no still stretch of FILL_GAP or more inside a long clause or between the script's own verbs
    spans = sorted([(a, b) for a, b in busy] + [(at, at + dur) for _, at, dur in out])
    cursor, gaps = t0 + 0.3, []
    for a, b in spans:
        if a - cursor >= FILL_GAP:
            gaps.append((cursor, a))
        cursor = max(cursor, b + 0.35)
    if t1 - 0.6 - cursor >= FILL_GAP:
        gaps.append((cursor, t1 - 0.6))
    for a, b in gaps:
        verb = cycle[k % len(cycle)]
        k += 1
        prev = max((x for x in out if x[1] < a), key=lambda x: x[1], default=None)
        if prev and prev[0] == verb:
            verb = cycle[k % len(cycle)]
            k += 1
        out.append((verb, a + 0.1, min(1.6, b - a - 0.2)))
    return sorted(out, key=lambda x: x[1])


class HostLayer:
    """The host, full body on a transparent canvas, animated by the same verbs, idle motion and live face as a painted
    base (its body cut once from a standing base: scripts/motion_test/host_cutout.py), with lip-sync and a gesture on
    every line (`acting`). `draw` puts it into a scene frame: standing on the ground, fully inside the frame, with a
    contact shadow, a light tint (at most 15 %) and a rim light from the scene; or popping in at the bottom-left edge
    of a diagram. `hit(box)` counts the host's pixels inside a box (captions keep off the host)."""

    def __init__(self, spec: dict, T: dict, t0: float, t1: float):
        from dataclasses import replace as dc_replace
        from pipeline.rig.puppet import Puppet, Stage, load_base
        meta = json.loads((HOST_DIR / 'host.json').read_text())
        base_cls = make_class(host_spec(spec), {}, meta['base'])

        class Host(base_cls):
            def build(self):                            # arms relaxed at the sides, not on the hips
                lead = self.p['lead']
                lead.s = dict(lead.s, rest={k: lead.world(v) for k, v in HOST_REST.items()})
                return super().build()

        self.c = Host(T, t0, t1)
        body = Image.open(HOST_DIR / 'body.png').convert('RGBA')
        self.c.base = dc_replace(self.c.base, plate=body)
        self.c.stage = Stage(self.c.base, (W, H))
        self.cam = self.c.stage.camera(body.width / 2, body.height / 2, body.width)
        lead = self.c.p['lead']                         # the head painted on the approved doorway base (s426): a clean rim, no
        clean = Puppet(load_base(HOST_HEAD_BASE), 'lead')      # ring of brick from the street base the body was cut from
        k = lead.D / clean.D
        lead.head_sprite = clean.head_sprite.resize((round(clean.head_sprite.width * k), round(clean.head_sprite.height * k)), Image.LANCZOS)
        lead.head_centre = (clean.head_centre[0] * k, clean.head_centre[1] * k)
        lead._head_cache.clear()
        s = self.cam[2]
        D = meta['D']
        self.top, self.feet, self.cx, self.D = (meta['head'][1] - 0.62 * D) * s, meta['feet_y'] * s, meta['head'][0] * s, D * s
        cue = spec['shot'].get('host') or {}
        self.where, self.side, self.to_camera = cue.get('where', 'scene'), cue.get('side', 'centre'), cue.get('to_camera', True)
        self.size = cue.get('size') or 'medium'
        if self.side == 'centre' and not spec.get('v6'):  # v5 scripts: alternate sides beat to beat, facing in
            self.side = host_side(spec)
        self.note = ''
        shot = spec['shot']
        if self.where == 'scene' and self.size in ('small', 'medium') and shot.get('visual') in ('insert', 'split'):
            from pipeline.inserts import scene_scale
            ins = shot.get('insert') or {}
            if scene_scale(ins.get('key', ''), ins.get('scene', '')) == 'object':
                # never dwarfed by a giant prop (user 2026-11-10): in an object close-up the host is near the camera
                self.note = f'host {self.size} -> large: "{ins.get("key")}" is a close-up of objects'
                self.size = 'large'
        self.held, self.hold_side = None, lead.near
        if cue.get('holds'):                            # a prop from the props library, held up in the near hand
            entry = find_prop(cue['holds'], prop_library())
            if entry and (ROOT / entry['sprite']).exists():
                spr = Image.open(ROOT / entry['sprite']).convert('RGBA')
                self.held = Held(spr, (spr.width * 0.5, spr.height * 0.62), held_width(entry, spr))
            else:
                self.note = '; '.join(filter(None, [self.note, f'host holds "{cue["holds"]}": not in the props library (stick props --generate)']))
        self.cut_at = word_time(T, cue.get('cut_on', ''), None) if cue.get('cut_on') else None
        self.t0 = t0
        self.starts = sorted(v for v in T.values() if t0 - 0.05 <= v <= t1)
        self.shivers = [self.c.tw(a['anchor']) for a in (spec['shot'].get('staging') or {}).get('actions', []) if a['verb'] == 'shiver']
        self.facing = lead.facing
        perf = self.c.perfs['lead']
        staged = (spec['shot'].get('staging') or {}).get('actions', [])
        self.laughs = [(self.c.tw(a['anchor']), 0.8) for a in staged if a['who'] == 'lead' and a['verb'] == 'laugh']
        for anchor in tickle_anchors(spec) if self.where == 'scene' else []:
            at = self.c.tw(anchor) + V.TICKLE_REACH      # the friend's feather lands: the host laughs and flinches away
            perf.actions.append(V.laugh(lead, at, dur=V.TICKLE_DUR).action)
            self.laughs.append((at, V.TICKLE_DUR))
        busy = [(a.start, a.end) for a in perf.actions]
        self.acts = acting(spec.get('narration', ''), T, t0, t1, busy, self.where)
        for verb, at, dur in self.acts:
            fn = V.GESTURES[verb]
            mv = fn(lead, at, dur=min(dur, 1.0)) if verb == 'nod' else fn(lead, at, dur=min(dur, 1.4)) if verb == 'shrug' else fn(lead, at, dur=dur)
            perf.actions.append(mv.action)
        perf.actions.sort(key=lambda a: a.start)
        self.last = None                                # (x, y, alpha >= 128) of the last frame drawn

    def sprite(self, t: float) -> Image.Image:
        pose = self.c.pose('lead', t)
        pose = replace(pose, mouth_open=max(pose.mouth_open, talk_curve(self.starts, t)))
        if self.to_camera:                              # eyes to the viewer, except while looking at the thing
            look = max((a.weight(t) for a in self.c.perfs['lead'].actions if a.name.split()[0] in LOOKS), default=0.0)
            pose = replace(pose, gaze_x=pose.gaze_x * (0.2 + 0.8 * look), turn=pose.turn * (0.55 + 0.45 * look))
        held = {}
        if self.held is not None:                       # the holding hand stays up at the chest; the other one acts
            s = self.hold_side
            pose = replace(pose, **{s + 'h': (0.40 if s == 'r' else -0.40, 0.16), s + '_shape': 'grip', s + '_rot': 0.0})
            held = {s: self.held}
        frame = self.c.stage.compose(self.cam, [self.c.p['lead']], [pose], [held])
        x0, x1 = int(self.cx - 1.7 * self.D), int(self.cx + 1.7 * self.D)
        return frame.crop((max(0, x0), int(self.top), min(W, x1), int(self.feet) + 4)), self.cx - max(0, x0)

    def body_motion(self, t: float):
        """(tilt degrees toward the facing side, scale, rise as a share of H) from the lean and step-back gestures."""
        tilt, scale, rise = 0.0, 1.0, 0.0
        for verb, at, dur in self.acts:
            if verb not in ('lean', 'step_back') or not at - 0.05 <= t <= at + dur + 0.4:
                continue
            q = ease((t - at) / 0.35) * (1 - ease((t - at - dur) / 0.4))
            if verb == 'lean':
                tilt, scale = tilt + 3.5 * q, scale * (1 + 0.025 * q)
            else:
                scale, rise = scale * (1 - 0.06 * q), rise + 0.016 * q
        for at, dur in self.laughs:                     # a laugh bounces the whole body a little (no lean: a host at
            if at - 0.05 <= t <= at + dur + 0.3:        # the frame edge would be clamped and slide)
                q = ease((t - at) / 0.15) * (1 - ease((t - at - dur) / 0.3))
                rise += 0.004 * q * abs(math.sin((t - at) * 2 * math.pi * 6.5))
        return tilt, scale, rise

    def draw(self, frame: Image.Image, t: float, zoom: float = 1.0, dx: float = 0.0, dy: float = 0.0):
        import numpy as np
        from PIL import ImageDraw, ImageFilter
        sp, foot_x = self.sprite(t)
        side = self.side_at(t)
        tilt, grow, rise = self.body_motion(t)
        pop = self.where == 'pop_in'
        entering = self.entering(t)
        crop_ok = pop or self.size in ('large', 'close') or entering     # cut by the bottom edge on purpose
        k, fx, fy, flip = self.placement(sp, t, zoom, dx, dy, side, grow, rise, pop)
        for at in self.shivers:
            if 0 <= t - at <= 2.2:
                fx += 4 * math.sin((t - at) * 2 * math.pi * 9)
        sp = sp.resize((max(1, int(sp.width * k)), max(1, int(sp.height * k))), Image.BICUBIC)
        foot = foot_x * k
        if flip:
            sp = sp.transpose(Image.FLIP_LEFT_RIGHT)
            foot = sp.width - foot
        if tilt and not pop:                           # lean about the feet toward the side the host faces on screen
            pad = int(sp.height * 0.08)
            big = Image.new('RGBA', (sp.width + 2 * pad, sp.height), (0, 0, 0, 0))
            big.alpha_composite(sp, (pad, 0))
            faces_right = (self.facing > 0) != flip
            sp = big.rotate(-tilt if faces_right else tilt, Image.BICUBIC, center=(foot + pad, sp.height - 1))
            foot += pad
        bb = sp.getchannel('A').point(lambda v: 255 if v >= 128 else 0).getbbox() or (0, 0, sp.width, sp.height)
        x, y = int(fx - foot), int(fy - sp.height)
        if not pop and not entering:                   # fully inside the frame (user 2026-11-09), however the camera moves
            x = min(max(x, HOST_MARGIN - bb[0]), W - HOST_MARGIN - bb[2])
            if not crop_ok:                            # feet in frame too when the camera zooms
                y = min(y, H - HOST_MARGIN - bb[3])
            y = max(y, HOST_MARGIN - bb[1])
        fx = x + foot
        self.placed = dict(fx=fx, fy=fy, k=k / grow if not pop else k, flip=flip)
        rgb_ratio = [1.0, 1.0, 1.0]
        if not pop:                                    # the scene's light on the host: a capped tint, a rim light, a shadow
            box = (max(0, x), max(0, y), min(W, x + sp.width), min(H, y + sp.height))
            if box[2] > box[0] and box[3] > box[1]:
                sp, rgb_ratio = light_match(sp, frame, box)
            if fy < H:                                 # a contact shadow when the feet are in frame
                sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
                w0 = bb[2] - bb[0]
                ImageDraw.Draw(sh).ellipse((fx - 0.42 * w0, fy - 0.03 * sp.height, fx + 0.42 * w0, fy + 0.03 * sp.height),
                                           fill=(20, 16, 24, 90))
                frame.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
        frame.alpha_composite(sp, (x, y))
        a = np.asarray(sp.getchannel('A')) >= 128
        self.last = (x, y, a)
        ab = (x + bb[0], y + bb[1], x + bb[2], y + bb[3])
        in_frame = ab[0] >= 0 and ab[1] >= 0 and ab[2] <= W and ab[3] <= H
        O.note_box('character', (max(0, ab[0]), max(0, ab[1]), min(W, ab[2]), min(H, ab[3])), host=True, pop_in=pop,
                   crop_ok=bool(crop_ok), in_frame=bool(in_frame), body_rgb=[round(float(v), 3) for v in rgb_ratio], **self.log)

    log: dict = {}                                     # extra keys for the per-frame character log (the friend says who)

    def side_at(self, t: float) -> str:
        if self.cut_at is not None:                    # the host is at one spot, then (a cut) at the next one
            return 'left' if t < self.cut_at else 'right'
        return self.side

    def entering(self, t: float) -> bool:
        """True while the figure walks in from a frame edge (partly outside the frame on purpose)."""
        return False

    def placement(self, sp, t: float, zoom: float, dx: float, dy: float, side: str, grow: float, rise: float, pop: bool):
        """(scale, foot-line x, feet y, mirrored) of the sprite in the frame."""
        share, feet = HOST_SIZES.get(self.size, HOST_SIZES['medium'])
        if pop:                                        # from the bottom-left or bottom-right corner, facing in
            k = POP_H * H / sp.height
            fx = (0.8 if side == 'right' else 0.2) * W
            fy = 1.17 * H + 0.3 * H * (1 - ease((t - self.t0) / 0.35))
            flip = self.facing > 0 if side == 'right' else self.facing < 0
        else:
            k = share * H * zoom * grow / sp.height
            fx = W / 2 + (HOST_X.get(side, 0.5) * W - W / 2 - dx) * zoom
            fy = H / 2 + (feet * H - H / 2 - dy) * zoom - rise * H
            flip = (side == 'right' and self.facing > 0) or (side == 'left' and self.facing < 0)
        return k, fx, fy, flip

    def hit(self, box) -> int:
        """How many of the host's pixels (last frame drawn) fall inside `box` (x0, y0, x1, y1)."""
        if self.last is None:
            return 0
        x, y, a = self.last
        x0, y0 = max(0, int(box[0]) - x), max(0, int(box[1]) - y)
        x1, y1 = min(a.shape[1], int(box[2]) - x), min(a.shape[0], int(box[3]) - y)
        return int(a[y0:y1, x0:x1].sum()) if x1 > x0 and y1 > y0 else 0


def friend_spec(spec: dict) -> dict:
    """The beat as the friend plays it beside the host: standing, no verbs of its own from the composer (FriendLayer
    adds them, aimed at the host), no text, no camera moves, no sounds."""
    s = copy.deepcopy(spec)
    st = s['shot'].get('staging') or {}
    s['shot']['cast'] = ['friend']
    s['shot']['staging'] = dict(setting=st.get('setting', 'other'), setting_detail='', poses=[dict(who='friend', pose='standing')],
                                actions=[], props=[])
    s['shot']['motion'], s['on_screen_text'], s['sfx'] = [], '', []
    return s


def friend_prop(spec: dict) -> str:
    """The prop the friend holds in this beat (staging props held_by friend), or ''."""
    return next((p['name'] for p in (spec['shot'].get('staging') or {}).get('props', []) if p.get('held_by') == 'friend'), '')


def friend_actions(spec: dict) -> list:
    """The friend's verbs in a beat whose cast has the friend: [dict(verb, target, anchor)]. A reach toward the host
    while the friend holds a feather is a tickle."""
    if 'friend' not in spec['shot'].get('cast', []):
        return []
    feather = 'feather' in friend_prop(spec).lower()
    out = []
    for a in (spec['shot'].get('staging') or {}).get('actions', []):
        if a['who'] != 'friend' or a['verb'] in V.STAGED:
            continue
        verb = a['verb']
        if verb == 'reach' and feather and a.get('target', '') in ('lead', 'host', ''):
            verb = 'tickle'
        out.append(dict(verb=verb, target=a.get('target', ''), anchor=a.get('anchor', '')))
    return out


def tickle_anchors(spec: dict) -> list:
    """Anchor words on which the friend tickles the host (the host laughs on each)."""
    return [a['anchor'] for a in friend_actions(spec) if a['verb'] == 'tickle']


def held_prop(name: str):
    """(sprite, Held anchor, width in D) for a prop the friend holds: the props library first (find_prop), else a
    code-drawn one (a bill as a receipt). None when there is none. A feather is always the drawn feather on a stick:
    the tickle's aim is built on its grip at the stick's end, and a painted feather stands upright with no stick."""
    if 'feather' in name.lower():
        spr = drawn.feather_stick()
        return spr, (spr.width * drawn.FEATHER_GRIP, spr.height * 0.5), FEATHER_D
    entry = find_prop(name, prop_library())
    if entry and (ROOT / entry['sprite']).exists():
        spr = Image.open(ROOT / entry['sprite']).convert('RGBA')
        return spr, (spr.width * 0.5, spr.height * 0.62), held_width(entry, spr)
    if re.search(r'\b(bill|bills|invoice|statement|receipt)\b', name.lower()):
        spr = drawn.bill()
        return spr, (spr.width * 0.5, spr.height * 0.6), 0.55
    key = prop_key(name)
    if key in DRAWN and DRAWN[key][0] is not None:
        spr = DRAWN[key][0]()
        return spr, (spr.width * 0.5, spr.height * 0.62), held_width(dict(width=DRAWN[key][1]), spr)
    return None


class FriendLayer(HostLayer):
    """The friend beside the host in the host format (a beat whose cast has 'friend'): its body cut once from the
    friend-standing-street base (host_cutout.py -> styles/cozy/host/friend-standing), its puppet head and arms, drawn by
    HostLayer.draw with the same light match, contact shadow, clamp in frame and size rules. It stands on the side
    opposite the host, facing the host, with the host's head size and feet line; in a tickle it stands just close
    enough for its feather to touch the host's side. Its verbs are aimed at the host (reach, point, nod, shrug,
    tickle); a held prop comes from the props library (else a drawn one). It walks in from its edge on an
    enter_left / enter_right recipe. No lip-sync: the host is the one talking."""

    log = {'who': 'friend'}

    def __init__(self, spec: dict, T: dict, t0: float, t1: float, host: HostLayer):
        from dataclasses import replace as dc_replace
        from pipeline.rig.puppet import Puppet, Stage, load_base
        meta = json.loads((FRIEND_DIR / 'host.json').read_text())
        rest = meta['rest']
        base_cls = make_class(friend_spec(spec), {}, meta['base'])

        class Friend(base_cls):
            def build(self):                            # arms relaxed at the sides
                f = self.p['friend']
                f.s = dict(f.s, rest={k: f.world(v) for k, v in rest.items()})
                return super().build()

        self.c = Friend(T, t0, t1)
        body = Image.open(FRIEND_DIR / 'body.png').convert('RGBA')
        wide = Image.new('RGBA', (body.width + int(FRIEND_REACH * meta['D']), body.height), (0, 0, 0, 0))
        wide.paste(body, (0, 0))                        # room on the facing side (+x) for the reaching arm
        self.c.base = dc_replace(self.c.base, plate=wide, meta=dict(self.c.base.meta, size=[wide.width, wide.height]))
        self.c.stage = Stage(self.c.base, (round(W * wide.width / body.width), H))
        self.cam = self.c.stage.camera(wide.width / 2, wide.height / 2, wide.width)
        f = self.c.p['friend']
        if meta.get('head_base') and meta['head_base'] != meta['base']:
            clean = Puppet(load_base(meta['head_base']), 'friend')
            k = f.D / clean.D
            f.head_sprite = clean.head_sprite.resize((round(clean.head_sprite.width * k), round(clean.head_sprite.height * k)), Image.LANCZOS)
            f.head_centre = (clean.head_centre[0] * k, clean.head_centre[1] * k)
            f._head_cache.clear()
        x0c, _, s = self.cam
        D = meta['D']
        self.meta = meta
        self.top, self.feet, self.cx, self.D = (meta['head'][1] - 0.62 * D) * s, meta['feet_y'] * s, (meta['head'][0] - x0c) * s, D * s
        self.host = host
        self.where, self.size, self.to_camera, self.cut_at, self.t0 = 'scene', host.size, False, None, t0
        self.shivers, self.acts, self.laughs, self.starts = [], [], [], []
        self.facing = f.facing
        self.note = ''
        self.held, self.hold_side, self.hold_angle = None, f.far, 0.0   # the arm toward the host holds the prop
        name = friend_prop(spec)
        if name:
            found = held_prop(name)
            if found:
                self.held = found
                kind = next((k for k in FRIEND_HOLD if k in name.lower()), None)
                (hx, hy), self.hold_angle = FRIEND_HOLD[kind] if kind else ((0.46, 0.40), 0.0)
                perf = self.c.perfs['friend']
                sd = self.hold_side
                perf.base = replace(perf.base, **{sd + 'h': (hx * f.facing, hy), sd + '_shape': 'grip', sd + '_rot': 0.0})
            else:
                self.note = f'friend holds "{name}": not in the props library (stick props --generate)'
        self.gap, self.tickle_at, self.tickle_angle = None, [], 0.0
        self.host_side_x, self.host_drop = self.host_waist()
        perf = self.c.perfs['friend']
        for a in friend_actions(spec):
            at = self.c.tw(a['anchor'])
            mv = self.verb(f, a, at)
            if mv is not None:
                perf.actions.append(mv.action)
        perf.actions.sort(key=lambda a: a.start)
        motion = spec['shot'].get('motion', [])
        self.enter_at = next((self.c.tw(m.get('anchor', '')) for m in motion if m['recipe'] in ('enter_left', 'enter_right')), None)
        self.last = None

    # -- geometry: the friend's body space (D from its shoulder midpoint; the host is on its facing side) ---------------
    def host_waist(self):
        """(the host's side at the waist as D from the host's head line toward the friend, the waist's height as D above
        the feet), measured on the host's cut-out."""
        import numpy as np
        hm = json.loads((HOST_DIR / 'host.json').read_text())
        a = np.asarray(Image.open(HOST_DIR / 'body.png').getchannel('A'))
        y = int(hm['head'][1] + TICKLE_DROP * hm['D'])
        xs = np.where(a[y] >= 128)[0]
        side = (xs.max() - hm['head'][0]) / hm['D'] if len(xs) else 0.45
        return float(side), (hm['feet_y'] - y) / hm['D']

    def in_body(self, f, head_gap: float, up_from_feet: float):
        """The point head_gap D from the friend's head line toward the host, up_from_feet D above the feet."""
        hx, _ = f.body(f.s['head'])
        feet = (self.meta['feet_y'] - f.mid[1]) / f.D
        return (hx + head_gap * f.facing, feet - up_from_feet)

    def solve_tickle(self, f, side: str):
        """(wrist target, feather angle, gap between the two head lines in D) that put the feather's tip on the host's
        side at the waist."""
        from pipeline.rig.skeleton import Pose
        if self.held is None:
            return (0.95 * f.facing, 0.55), 0.0, 2.0
        spr, anchor, width = self.held
        tip = width * (spr.width - anchor[0]) / spr.width
        feet = (self.meta['feet_y'] - f.mid[1]) / f.D
        want_y = feet - self.host_drop                  # the waist's height in the friend's body space
        ang = 12.0                                      # the feather slants a little down toward the host
        sh = f.body(f.shoulders[side])
        lo, hi, best = -40.0, 85.0, None
        for _ in range(28):                             # the arm's angle that brings the tip to the waist's height
            th = (lo + hi) / 2
            wrist = (sh[0] + 0.82 * math.cos(math.radians(th)) * f.facing, sh[1] + 0.82 * math.sin(math.radians(th)))
            pose = Pose(**{side + 'h': wrist, side + '_shape': 'grip', side + '_rot': -10 * f.facing, side + '_bend': 1,
                           'extra': {side + '_droop': 0.7, side + '_front': 1.0}})
            j = f.joints(pose)
            grip = f.body(f.hand_placement(pose, j, side)['grip'])
            tip_xy = (grip[0] + tip * math.cos(math.radians(ang)) * f.facing, grip[1] + tip * math.sin(math.radians(ang)))
            best = (wrist, tip_xy)
            if tip_xy[1] < want_y:
                lo = th
            else:
                hi = th
        wrist, tip_xy = best
        hx, _ = f.body(f.s['head'])
        gap = (tip_xy[0] - hx) * f.facing + self.host_side_x - TICKLE_PRESS
        return wrist, ang, gap

    def nominal_gap(self) -> float:
        """D between the two head lines when each stands at its side's spot (camera at rest, before any clamp)."""
        share, _ = HOST_SIZES.get(self.size, HOST_SIZES['medium'])
        d_screen = self.host.D * share * H / (int(self.host.feet) + 4 - int(self.host.top))
        hs = self.host.side
        fs = 'left' if hs == 'right' else 'right'
        return max(FRIEND_MIN_GAP, abs(HOST_X.get(fs, 0.5) - HOST_X.get(hs, 0.5)) * W / d_screen)

    def verb(self, f, a: dict, at: float):
        target = a['target']
        to_host = target in ('lead', 'host', '')
        gap = self.gap or self.nominal_gap()
        head = self.in_body(f, gap, (self.meta['feet_y'] - self.meta['head'][1]) / self.meta['D'] - 0.2)
        chest = self.in_body(f, gap - self.host_side_x - 0.1, self.host_drop + 0.4)
        if a['verb'] == 'tickle':
            wrist, self.tickle_angle, self.gap = self.solve_tickle(f, self.hold_side)
            self.tickle_at.append(at)
            return V.tickle(f, at, wrist, side=self.hold_side)
        if a['verb'] == 'reach':
            return V.reach(f, at, chest if to_host else (0.85 * f.facing, -0.45))
        if a['verb'] == 'point':
            return V.point(f, at, head if to_host else (0.85 * f.facing, -0.6))
        if a['verb'] == 'gaze':
            return V.gaze(f, at, head) if to_host else V.gaze(f, at, direction=target)
        if a['verb'] in ('nod', 'shrug', 'shake_head', 'wave', 'jolt', 'laugh', 'facepalm', 'slump', 'snap', 'freeze', 'look_up'):
            return V.VERBS[a['verb']](f, at)
        self.note = '; '.join(filter(None, [self.note, f'friend {a["verb"]}: not staged in the host format']))
        return None

    # -- HostLayer hooks --------------------------------------------------------------------------------------------
    def side_at(self, t: float) -> str:
        return 'left' if self.host.side_at(t) == 'right' else 'right'

    def body_motion(self, t: float):
        return 0.0, 1.0, 0.0

    def enter_q(self, t: float) -> float:
        """1 while still outside the frame, 0 once in place (walking in on an enter recipe)."""
        if self.enter_at is None:
            return 0.0
        return 1 - ease((t - self.enter_at + 0.15) / 0.6)

    def entering(self, t: float) -> bool:
        return self.enter_q(t) > 0.001

    def placement(self, sp, t, zoom, dx, dy, side, grow, rise, pop):
        h = self.host.placed
        _, feet = HOST_SIZES.get(self.size, HOST_SIZES['medium'])
        k = h['k'] * self.host.D / self.D               # the host's head size on screen
        d_screen = self.D * k
        out = 1 if side == 'right' else -1
        if self.gap is not None:                        # a tickle: just close enough for the feather to touch
            tilt = self.host.body_motion(t)[0]           # the host's lean or flinch moves its waist: follow it
            fx = h['fx'] + out * (self.gap + math.sin(math.radians(tilt)) * self.host_drop) * d_screen
        else:
            fx = W / 2 + (HOST_X.get(side, 0.5) * W - W / 2 - dx) * zoom
            if (fx - h['fx']) * out < FRIEND_MIN_GAP * d_screen:
                fx = h['fx'] + out * FRIEND_MIN_GAP * d_screen
        fx += out * self.enter_q(t) * 0.8 * W
        fy = H / 2 + (feet * H - H / 2 - dy) * zoom
        flip = (side == 'right' and self.facing > 0) or (side == 'left' and self.facing < 0)
        return k, fx, fy, flip

    def sprite(self, t: float):
        from pipeline.rig.skeleton import GRIPPING
        pose = self.c.pose('friend', t)
        held = {}
        if self.held is not None:
            spr, anchor, width = self.held
            sd = self.hold_side
            if getattr(pose, sd + '_shape') not in GRIPPING:
                pose = replace(pose, **{sd + '_shape': 'grip'})
            w = max((a.weight(t) for a in self.c.perfs['friend'].actions if a.name.startswith('tickle')), default=0.0)
            angle = (self.hold_angle + (self.tickle_angle - self.hold_angle) * w) * self.facing
            angle += pose.extra.get(sd + '_prop_rot', 0.0)
            held = {sd: Held(spr, anchor, width, angle=angle)}
        frame = self.c.stage.compose(self.cam, [self.c.p['friend']], [pose], [held])
        x0, x1 = int(self.cx - 1.7 * self.D), int(self.cx + (FRIEND_REACH + 0.6) * self.D)
        return frame.crop((max(0, x0), int(self.top), min(frame.width, x1), int(self.feet) + 4)), self.cx - max(0, x0)

    def draw(self, frame, t: float, zoom: float = 1.0, dx: float = 0.0, dy: float = 0.0):
        if self.enter_q(t) >= 0.999 or getattr(self.host, 'placed', None) is None:
            self.last = None                            # not in the frame yet
            return
        super().draw(frame, t, zoom, dx, dy)


def host_side(spec: dict) -> str:
    """Where the host stands: the script's side (v6, marked by host_episode), or for v5 scripts left/right
    alternating by beat number."""
    cue = spec['shot'].get('host') or {}
    side = cue.get('side', 'centre')
    if side == 'centre' and not spec.get('v6'):
        return 'left' if int(re.sub(r'\D', '', spec['id']) or 0) % 2 else 'right'
    return side


def layout_token(spec: dict) -> str:
    """One beat's layout for the QA note on repeated patterns: picture kind | host where | side | size."""
    sh = spec['shot']
    kind = {'graphic': 'diagram', 'split': 'split'}.get(sh.get('visual'), 'scene')
    if kind == 'scene' and any(e.get('kind') == 'focus' for e in (sh.get('insert') or {}).get('effects', [])):
        kind = 'close-up'
    cue = sh.get('host') or {}
    where = cue.get('where', 'off')
    if where == 'off':
        return f'{kind}|off'
    return f'{kind}|{where}|{host_side(spec)}|{cue.get("size") or "medium"}'


def light_match(sp: Image.Image, frame: Image.Image, box) -> tuple:
    """Light the host by the scene without losing its white (user 2026-11-09: never pink, orange or brown): the scene's
    colour at most TINT_MAX per channel, the overall light within LIGHT_RANGE, and a rim of the scene's brightest
    colour along the edge facing the light. Returns (sprite, per-channel ratio of the body's white after/before)."""
    import numpy as np
    from PIL import ImageFilter
    env_img = np.asarray(frame.crop(box).convert('RGB')).reshape(-1, 3).astype(np.float32)
    env = env_img.mean(0)
    m = max(1.0, float(env.mean()))
    tint = 1 + TINT_MAX * np.clip(env / m - 1, -1, 1)
    shade = min(1.0, max(LIGHT_RANGE[0] + 0.03, m / 150))
    factor = np.clip(tint * shade, *LIGHT_RANGE)
    small = np.asarray(frame.convert('RGB').resize((54, 96))).astype(np.float32)
    lum = small.mean(-1)
    left, right = lum[:, :18].mean(), lum[:, -18:].mean()
    light = 'left' if left > right * 1.04 else 'right' if right > left * 1.04 else 'top'
    hi = small.reshape(-1, 3)[lum.reshape(-1) >= np.percentile(lum, 97)].mean(0)
    rim_col = 0.7 * hi + 0.3 * 255
    a = np.asarray(sp).astype(np.float32)
    alpha = a[..., 3] / 255
    r = max(2, round(0.004 * sp.height))
    shifted = np.zeros_like(alpha)
    if light == 'left':
        shifted[:, r:] = alpha[:, :-r]
    elif light == 'right':
        shifted[:, :-r] = alpha[:, r:]
    else:
        shifted[r:] = alpha[:-r]
    rim = np.clip(alpha - shifted, 0, 1)
    rim = np.asarray(Image.fromarray((rim * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1))).astype(np.float32) / 255
    body = (alpha > 0.99) & (a[..., :3].mean(-1) > 190) & (rim < 0.1)
    before = a[..., :3][body].mean(0) if body.any() else None
    a[..., :3] *= factor[None, None, :]
    a[..., :3] = a[..., :3] * (1 - RIM * rim[..., None]) + rim_col[None, None, :] * RIM * rim[..., None]
    after = a[..., :3][body].mean(0) if body.any() else None
    ratio = (after / np.maximum(before, 1)).tolist() if before is not None else [1.0, 1.0, 1.0]
    return Image.fromarray(a.clip(0, 255).astype(np.uint8), 'RGBA'), ratio


class VisualShot:
    """A beat (or the first part of one) that SHOWS the subject: a painted insert or a drawn graphic, full frame, no
    characters. Same interface as a Beat for the runner: events, notes and frame(t)."""
    spec: dict = {}
    visual = 'insert'

    host_format = False

    def __init__(self, T, t0, t1, load_art=True):
        self.T, self.t0, self.t1 = T, t0, t1
        self.events, self.notes = [], []
        cue = self.spec['shot'].get('host') or {}
        self.host = HostLayer(self.spec, T, t0, t1) if self.host_format and cue.get('where', 'off') != 'off' and load_art else None
        self.friend = (FriendLayer(self.spec, T, t0, t1, self.host) if self.host is not None and self.host.where == 'scene'
                       and 'friend' in self.spec['shot'].get('cast', []) and (FRIEND_DIR / 'host.json').exists() else None)
        self.text_items = finish_text_items(self.spec, self.tw, text_items(self.spec, self.tw), t0, t1)
        for snd in self.spec.get('sfx', []):
            at = self.tw(snd['anchor'])
            if at <= t1:
                self.events.append((at, snd['name'], 0.6))
        for kind, at, _ in self.text_items:
            self.events.append((at, 'pop' if kind != 'counter' else 'ding', 0.5))
        self.setup(load_art)
        if self.host_format:                           # only the script's own sounds (user: at most 5-6 per video)
            self.events = [(self.tw(x['anchor']), x['name'], 0.6) for x in self.spec.get('sfx', [])]

    def tw(self, anchor, default=None):
        return word_time(self.T, anchor, self.t0 + 0.4 if default is None else default)

    def host_hit(self, box) -> int:
        """The host's pixels inside `box` in the frame just drawn (captions keep off the host)."""
        if self.host is None:
            return 0
        return self.host.hit(box) + (self.friend.hit(box) if self.friend is not None else 0)

    def draw_people(self, frame, t, zoom=1.0, dx=0.0, dy=0.0):
        """The host, then the friend beside it (in front: its feather reaches the host's side)."""
        self.host.draw(frame, t, zoom, dx, dy)
        self.friend.draw(frame, t, zoom, dx, dy)

    def frame(self, t):
        frame = self.draw(t)
        if self.host is not None and self.visual == 'graphic':
            self.host.draw(frame, t)
        draw_text_items(frame, t, self.text_items, TEXT_TOP, self.spec)
        return frame


class InsertShot(VisualShot):
    visual = 'insert'

    def setup(self, load_art):
        from pipeline import inserts as INS
        from pipeline.rig.inserts import Effect, InsertScene
        ins = self.spec['shot']['insert']
        effects = []
        from pipeline.rig.inserts import settle_effects
        said = same_text(self.spec.get('on_screen_text', ''))
        for k, e in enumerate(ins.get('effects', [])):
            if e['kind'] == 'label' and said and same_text(e.get('detail', '')) == said:
                continue                                # the on-screen text already says it: never the same words twice
            at = self.tw(e['anchor']) if e.get('anchor') else None
            where = e.get('at', '')
            if e['kind'] == 'label' and said and where in ('top', 'sky') and slams(self.spec):
                where = 'peak'                          # the slam holds the top: the label sits below it (tickling b07)
            effects.append(Effect(e['kind'], at, where, e.get('detail', ''), seed=11 + k))
        settle_effects(effects, self.t0, self.t1)
        for e in effects:
            if e.at is None:
                continue
            if e.kind == 'shake':
                self.events.append((e.at, 'thud', 0.6))
            elif e.kind in ('label', 'arrow'):
                self.events.append((e.at, 'pop', 0.45))
            elif e.kind == 'crossfade':
                self.events.append((e.at, 'whoosh', 0.4))
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
        if self.key.startswith('base:'):               # an approved base's empty plate (its painted figure left out)
            scene = Image.open(ROOT / 'styles/cozy/bases' / self.key[5:] / 'empty.png')
        elif self.key not in lib:
            raise RuntimeError(f'{self.spec["id"]}: insert scene "{self.key}" is not in the library; run stick inserts --generate')
        else:
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
        if self.host is not None:
            self.scene.host = self.host.draw if self.friend is None else self.draw_people

    def draw(self, t):
        return self.scene.frame(t)


FRAMED_KINDS = ('thermometer',)   # diagrams whose drawn frame is itself the main element (the thermometer is there at once;
                                  # the atmosphere's layers too, but their first label still shows from the first frame)


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
        times = O.settle(times, self.t0, self.t1)
        if times and g['kind'] not in FRAMED_KINDS:     # the main element is on the paper from the first frame (user
            times[0] = self.t0 - 2.0                    # 2026-11-09: no second of blank paper); the rest land on their words
        for at in times:
            self.events.append((at, 'pop', 0.45))
        self.graphic = Graphic(g['kind'], items, times, g.get('detail', ''), self.t0, self.t1, seed=sum(map(ord, self.spec['id'])))

    def draw(self, t):
        return self.graphic.frame(t)


TOP_TEXT_BAND = int(H * 0.23)    # the on-screen text (slams) sits above this line; split labels stay below it


class SplitShot(VisualShot):
    """Two painted scenes stacked (myth vs fact, then vs now): the top scene fills the frame, then on the bottom
    label's word the frame splits and the bottom scene wipes up into the lower half. Labels sit at the seam."""
    visual = 'split'

    def setup(self, load_art):
        from pipeline import inserts as INS
        from pipeline.rig.inserts import Effect, InsertScene, settle_effects
        ins, sp = self.spec['shot']['insert'], self.spec['shot']['split']
        anchors = list(sp.get('anchors') or []) + ['', '']
        at = [self.tw(anchors[0]) if anchors[0] else self.t0 + 0.2,
              self.tw(anchors[1]) if anchors[1] else self.t0 + 0.45 * (self.t1 - self.t0)]
        self.label_at = O.settle(at, self.t0, self.t1)
        self.labels = list(sp.get('labels') or [])[:2]
        effects = [Effect(e['kind'], self.tw(e['anchor']) if e.get('anchor') else None, e.get('at', ''), e.get('detail', ''), seed=11 + k)
                   for k, e in enumerate(ins.get('effects', []))]
        settle_effects(effects, self.t0, self.t1)
        if not load_art:
            return
        lib = INS.library()
        for key in (ins['key'], sp['key']):
            if key not in lib:
                raise RuntimeError(f'{self.spec["id"]}: split scene "{key}" is not in the library; run stick inserts --generate')
        seed = sum(map(ord, self.spec['id']))
        self.top = InsertScene(Image.open(ROOT / lib[ins['key']]['file']), effects, self.t0, self.t1, seed=seed)
        self.bottom = InsertScene(Image.open(ROOT / lib[sp['key']]['file']), [Effect('push', None)], self.t0, self.t1, seed=seed + 1)

    def draw(self, t):
        from PIL import ImageDraw
        a = self.top.frame(t)
        q = ease((t - self.label_at[1] + 0.25) / 0.5) if len(self.label_at) > 1 else 0.0
        seam = H
        if q <= 0:
            frame = a
        else:
            b = self.bottom.frame(t)
            seam = int(H - q * H / 2)
            frame = Image.new('RGBA', (W, H))
            off = int(H * 0.25 * q)                     # the top scene slides up as the seam rises
            frame.paste(a.crop((0, off, W, off + seam)), (0, 0))
            frame.paste(b.crop((0, int(H * 0.25), W, int(H * 0.25) + H - seam)), (0, seam))
            d = ImageDraw.Draw(frame)
            d.rectangle((0, seam - 7, W, seam + 7), fill=(250, 244, 230), outline=(34, 30, 52), width=3)
        if self.host is not None:
            self.host.draw(frame, t)
            if self.friend is not None:
                self.friend.draw(frame, t)
        for k, (label, at) in enumerate(zip(self.labels, self.label_at)):
            if t < at or (k == 1 and q <= 0) or not label:      # a label the self-repair dropped is not drawn
                continue
            g = back_out(min(1.0, (t - at) / 0.32), 1.6)
            im = O.tag_label(label, 64)
            if g < 0.999:
                im = im.resize((max(1, int(im.width * g)), max(1, int(im.height * g))), Image.BICUBIC)
            y = (seam - 40 - im.height if k == 0 else seam + 40) if q > 0 else 0.55 * H
            y = min(y, int(H * 0.75) - im.height)            # above the safe bottom while the seam wipes up
            xy = self.label_spot(k, im, int(y))              # off the host (2026-11-13: a label sat on the host's head)
            frame.alpha_composite(im, xy)
            if g > 0.9:
                O.note_text(label, 64, (xy[0], xy[1], xy[0] + im.width, xy[1] + im.height))
        return frame

    def label_spot(self, k, im, y):
        """Where split label k goes: the right edge inside the safe area (the app's buttons sit further right) by
        default, else the left edge, else stepped away from the seam, whichever covers no host or friend pixels. The last
        clear spot is kept while it stays clear, so a label does not jump with every gesture."""
        import numpy as np
        from pipeline import captions as C
        full = O.tag_label(self.labels[k], 64)
        w, h = full.width, full.height

        bodies = []                                      # each character's whole box (arms included), as QA reads it
        for p in (self.host, self.friend):
            if p is not None and getattr(p, 'last', None) is not None:
                px, py, a = p.last
                rows, cols = np.flatnonzero(a.any(1)), np.flatnonzero(a.any(0))
                if rows.size:
                    bodies.append((px + cols[0], py + rows[0], px + cols[-1], py + rows[-1]))

        def hits(x, yy):
            box = (x - 12, yy - 12, x + w + 12, yy + h + 12)
            inside = sum(max(0, min(box[2], b[2]) - max(box[0], b[0])) * max(0, min(box[3], b[3]) - max(box[1], b[1]))
                         for b in bodies)
            return inside + sum(p.hit(box) for p in (self.host, self.friend) if p is not None)
        lo, hi = C.SAFE['left'], C.SAFE['right']
        step = -1 if k == 0 else 1                       # the top label moves up, the bottom one down

        def at(choice):                                  # (side, dy) -> (x, y), following the seam as it wipes
            side, dy = choice
            yy = int(min(max(y + step * dy, TOP_TEXT_BAND), int(H * 0.75) - h))   # below the on-screen text
            return (int(hi - w) if side == 'right' else int(lo)), yy
        spots = getattr(self, '_spots', None) or {}
        self._spots = spots
        keep = spots.get(k)
        if keep and hits(*at(keep)) == 0:
            return at(keep)
        options = [(side, dy) for dy in range(0, 991, 90) for side in ('right', 'left')]   # as far as above the head
        best = min(options, key=lambda o: (hits(*at(o)), options.index(o)))
        spots[k] = best
        return at(best)


def make_visual_class(spec: dict):
    cls = {'insert': InsertShot, 'split': SplitShot}.get(spec['shot'].get('visual'), GraphicShot)
    return type(f'{cls.__name__}_{spec["id"]}', (cls,), dict(spec=spec))


def make_class(spec: dict, row: dict, base_name: str, after_cut: bool = False):
    cast = tuple(w for w in spec['shot'].get('cast', []))
    meta = json.loads((ROOT / 'styles/cozy/bases' / base_name / 'base.json').read_text())
    seats = {s['name'] for s in meta['seats']}
    cast = tuple(w for w in cast if w in seats)
    return type(f'Composed_{spec["id"]}{"b" if after_cut else ""}', (Composed,),
                dict(base_name=base_name, cast=cast, spec=spec, plan_row=row, after_cut=after_cut))


def split_fallback(episode, specs: dict) -> list:
    """A split whose top scene (the insert) has no key is shown as its bottom scene alone, as an insert that keeps
    the top's effects; in memory only, episode.json is not touched. 2026-11-14: "Explain why sunsets are colorful"
    b06 had a split with an empty top key, which first broke the paint step and would then have stopped the render
    ("split scene '' is not in the library"). Returns notes for the log."""
    from pipeline.models import InsertCue
    notes = []
    for b in episode.beats:
        sh = b.shot
        if sh.visual != 'split' or (sh.insert is not None and (sh.insert.key or '').strip()):
            continue
        if sh.split is None or not (sh.split.key or '').strip():
            continue                                  # nothing to show at all: the gate's error, not ours
        effects = list(sh.insert.effects) if sh.insert else []
        sh.insert = InsertCue(key=sh.split.key, scene=sh.split.scene, effects=effects)
        sh.visual, sh.split = 'insert', None
        spec = specs[b.id]['shot']
        spec['insert'] = dict(key=sh.insert.key, scene=sh.insert.scene,
                              effects=list((spec.get('insert') or {}).get('effects') or []))
        spec['visual'], spec['split'] = 'insert', None
        notes.append(f'{b.id}: the split has no top scene; shown as its bottom scene "{sh.insert.key}" alone')
    return notes


def for_episode(slug: str, overrides: dict | None = None, lib: dict | None = None):
    """BEATS, shots(tl), word_times and LIBRARY_REPORT for the runner, composed from the episode's script."""
    episode, _ = store.load(slug)
    raw = json.loads((ROOT / 'episodes' / slug / 'episode.json').read_text())
    specs = {b['id']: b for b in raw['beats']}
    for note in split_fallback(episode, specs):
        print('note:', note)
    from pipeline import repair
    for note in repair.apply_hints(episode, specs, repair.load(slug)):     # the self-repair loop's fixes, in memory
        print('repair:', note)
    lib = BASES.library() if lib is None else lib
    if episode.host_format:
        return host_episode(episode, specs, lib, slug)
    rows = {r['beat']: r for r in BASES.plan(episode, lib)}
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


HOOK_STOP = {'the', 'a', 'an', 'and', 'or', 'but', 'so', 'is', 'it', 'its', "it's", 'you', 'your', 'to', 'of', 'in', 'on', 'at', 'why',
             'what', 'how', 'did', 'do', 'does', 'i', 'me', 'my', 'we', 'this', 'that', 'same', 'one', 'from', 'with', 'out'}


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in HOOK_STOP and len(w) > 2}


def host_episode(episode, specs: dict, lib: dict, slug: str):
    """Script v5, the host format: every beat is a painted scene or a diagram with the host in it (HostLayer), or the
    host on an approved standing base of the beat's setting (the doorway s426 base); a setting with no such base
    puts the host in the nearest painted scene of that setting in the same video. Scores: library, subject on screen,
    character (host) on screen, and the hook check."""
    beats, report, flags = {}, {}, {}
    scene_of = [(i, b.shot.staging.setting, b.shot.insert.key) for i, b in enumerate(episode.beats)
                if b.shot.visual == 'insert' and b.shot.insert and b.shot.staging]
    for i, b in enumerate(episode.beats):
        specs[b.id] = dict(specs[b.id], v6=bool(episode.storyline_pick))   # v6: the script's host side/size are literal
        spec, cue, st = specs[b.id], b.shot.host, b.shot.staging
        on = bool(cue and cue.where != 'off')
        used = [m.recipe for m in b.shot.motion]
        verbs = [f'{a.who} {a.verb}' for a in st.actions] if st else []
        notes = []
        subject = b.shot.visual in ('insert', 'graphic', 'split') or bool(st and st.setting in episode.subject_settings)
        if b.shot.visual in ('insert', 'graphic', 'split'):
            cls = make_visual_class(spec)
            what = (f'insert {b.shot.insert.key}' if b.shot.visual == 'insert' else f'graphic {b.shot.graphic.kind}'
                    if b.shot.visual == 'graphic' else f'split {b.shot.insert.key} / {b.shot.split.key}')
            effects = [e.kind for e in b.shot.insert.effects] if b.shot.visual in ('insert', 'split') else []
            lib_used = [what] + effects + used
        else:
            want = dict(setting=st.setting if st else 'other', pose='standing', view='eye-level', cast=['lead'], poses={'lead': 'standing'})
            base = BASES.match(want, lib) if st else None
            if base and not (ROOT / 'styles/cozy/bases' / base / 'empty.png').exists():
                base = None
            if base:                                    # one host look (user 2026-11-09): the composited host stands in the
                s2 = copy.deepcopy(spec)                # base's empty plate instead of the base's own painted figure
                s2['shot']['visual'], s2['shot']['insert'] = 'insert', dict(key=f'base:{base}', scene='', effects=[])
                cls = make_visual_class(s2)
                notes.append(f'the host stands in the {base} scene (empty plate; the painted figure is not used)')
                lib_used = [f'base {base}'] + used
            else:
                near = sorted(scene_of, key=lambda r: (r[1] != (st.setting if st else None), abs(r[0] - i), -r[0]))
                if not near:
                    raise RuntimeError(f'{b.id}: no standing base for {want["setting"]} and no painted scene to stand the host in')
                key = near[0][2]
                s2 = copy.deepcopy(spec)
                s2['shot']['visual'], s2['shot']['insert'] = 'insert', dict(key=key, scene='', effects=[])
                cls = make_visual_class(s2)
                notes.append(f'no standing {want["setting"]} base: the host stands in the {key} scene')
                lib_used = [f'insert {key}'] + used
        if on and cue.where == 'scene' and 'friend' in b.shot.cast:
            acts = ', '.join(f'{a["verb"]} on "{a["anchor"]}"' for a in friend_actions(spec)) or 'no verbs'
            held = friend_prop(spec)
            notes.append(f'the friend stands opposite the host ({acts}{"; holds " + held if held else ""})'
                         + ('; the host laughs on the tickle' if tickle_anchors(spec) else ''))
        cls.host_format = True
        beats[b.id] = cls
        report[b.id] = dict(library=lib_used + ([f'host {cue.where}'] if on else []) + verbs, hand_written=[], props=[],
                            notes=notes, visual=b.shot.visual)
        flags[b.id] = (subject, on)
    timing = {}

    def shots(tl):
        out = []
        for bt in tl['beats']:
            out.append((bt['id'], bt['shot_start'], bt['shot_end']))
            timing[bt['id']] = round(bt['shot_end'] - bt['shot_start'], 3)
        return out

    def score(index: int):
        ids = list(timing) or list(flags)
        on = [k for k in ids if flags[k][index]]
        secs = sum(timing.get(k, 0) for k in on)
        dur = sum(timing.get(k, 0) for k in ids)
        return dict(beats=len(ids), on_beats=len(on), pct=round(100 * len(on) / len(ids)) if ids else 0,
                    time_pct=round(100 * secs / dur) if dur else 0, off=[k for k in ids if not flags[k][index]])

    def summary():
        total = len(report)
        lib_beats = sum(1 for r in report.values() if not r['hand_written'])
        out = dict(beats=total, library_beats=lib_beats, library_pct=round(100 * lib_beats / total) if total else 0, per_beat=report)
        sub, cha = score(0), score(1)
        out['subject'] = dict(beats=sub['beats'], subject_beats=sub['on_beats'], subject_pct=sub['pct'], subject_time_pct=sub['time_pct'],
                              per_beat={k: dict(shown_s=timing.get(k, 0) if flags[k][0] else 0.0, beat_s=timing.get(k, 0),
                                                visual=report[k]['visual']) for k in flags})
        out['character'] = dict(beats=cha['beats'], host_beats=cha['on_beats'], host_pct=cha['pct'], host_time_pct=cha['time_pct'],
                                host_off_beats=cha['off'])
        first, payoff = episode.beats[0], next((b for b in episode.beats if b.role == 'payoff'), None)
        pick = episode.hook_options[(episode.hook_pick or 1) - 1] if episode.hook_options else None
        words = len(re.findall(r"[\w'’]+", first.narration))
        # the same rule as the story check at the script gate (Episode.story_report): the hook's answer and the picked
        # storyline's payoff (2026-11-13: QA failed a payoff that says the storyline's payoff word for word)
        answer = ((pick.answer if pick else '') + ' ' + (episode.storyline.payoff if episode.storyline else '')).strip()
        shared = sorted(_keywords(answer) & _keywords(payoff.narration)) if answer and payoff else []
        out['layout'] = [layout_token(specs[b.id]) for b in episode.beats]
        out['format'] = episode.story_format or 'explainer'
        out['hook'] = dict(line=first.narration, words=words, max_words=8, host_in_frame_one=flags[first.id][1],
                           answer=answer, payoff=payoff.narration if payoff else '', shared_words=shared,
                           answered_in_payoff=len(shared) >= 2,
                           pass_=words <= 8 and flags[first.id][1] and len(shared) >= 2)
        return out
    return SimpleNamespace(BEATS=beats, shots=shots, word_times=word_times, LIBRARY_REPORT=report, library_summary=summary,
                           slug=slug)
