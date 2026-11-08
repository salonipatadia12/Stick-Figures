"""The starter base pack (docs/PLAN.md §7 D): `stick pack starter`.

Eight common settings, three bases each (lead alone, friend alone, the two together), rendered as FLUX.2 klein
candidates from the Empty Chair character references, laid out on one sheet per setting for the user's single
approval round. Picks enter `styles/cozy/bases/library.json` like any other base (status approved, to build);
rejections with a direction get a redo round, as the per-episode bases gate does. State lives in
`styles/cozy/bases/pack-<name>.json` (candidates, rejections, rounds).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import bases as B
from . import config

ROOT = config.ROOT
PACK_DIR = ROOT / 'styles/cozy/bases'

PLACES = {
    'office': ('a small home office: a wooden desk with a closed laptop and a mug, a desk lamp, a window with soft daylight, '
               'a shelf with books and a plant'),
    'kitchen': ('a cozy kitchen: a wooden counter with a kettle and a bowl of fruit, open shelves with jars, a window over the '
                'sink with morning light, a plant on the sill'),
    'living-room': ('a warm living room: a soft rust-coloured sofa with a knitted cushion, a low wooden coffee table with a '
                    'mug, a floor lamp, a window with dappled light, a plant'),
    'street': ('a quiet painted street corner: a brick shopfront, a lamp post, a bench, a tree, long golden light and '
               'dappled shadows on the pavement'),
    'park': ('a park: a wooden bench on a path, trees with dappled light, a lamp post, a distant pond, warm late light'),
    'classroom': ('a classroom: a wooden school desk and chair, a green chalkboard with nothing written on it, a window '
                  'with daylight, a shelf with books and a globe'),
    'gym': ('a small gym: a wooden bench, a rack of dumbbells, a yoga mat rolled on the floor, a big window with '
            'daylight, a plant'),
    'car': ("the inside of a small car seen from the passenger side: the driver's seat, the steering wheel, the dashboard, "
            'the windscreen with a sunny road and trees outside, soft light'),
}
# pose per setting and cast
POSES = {
    'office': dict(lead='seated-desk', friend='seated-desk', together='seated-desk'),
    'kitchen': dict(lead='standing', friend='standing', together='standing'),
    'living-room': dict(lead='seated-sofa', friend='seated-sofa', together='seated-sofa'),
    'street': dict(lead='standing', friend='standing', together='standing'),
    'park': dict(lead='seated-bench', friend='seated-bench', together='seated-bench'),
    'classroom': dict(lead='seated-desk', friend='standing', together='mixed'),
    'gym': dict(lead='seated-bench', friend='standing', together='mixed'),
    'car': dict(lead='driving', friend='passenger', together='driving'),
}
POSE_TEXT = {
    'seated-desk': 'now SEATED AT THE DESK in three-quarter view facing right, both hands resting on the desktop, full upper body and the chair visible',
    'seated-sofa': 'now SITTING ON THE SOFA in three-quarter view facing right, relaxed, both hands resting in the lap, full body visible',
    'seated-bench': 'now SITTING ON THE BENCH seen from the side in three-quarter view facing right, both hands resting on the knees, feet on the ground',
    'standing': 'now STANDING, full body visible from head to feet, three-quarter view facing right, arms relaxed at the sides',
    'driving': "now SITTING IN THE DRIVER'S SEAT seen from the passenger side in three-quarter view, both navy mitten hands on the steering wheel, head turned a little toward the viewer",
    'passenger': "now ALONE IN THE PASSENGER SEAT of the car, seen from the side in three-quarter view, hands resting in the lap, the driver's seat beside them EMPTY with nobody at the steering wheel",
}
TOGETHER = {
    'seated-desk': 'both now SEATED SIDE BY SIDE AT THE DESK, facing each other a little, hands on the desktop',
    'seated-sofa': 'both now SITTING ON THE SOFA side by side, turned a little toward each other, hands in their laps',
    'seated-bench': 'both now SITTING ON THE BENCH side by side, turned a little toward each other, hands on their knees',
    'standing': 'both now STANDING side by side, full bodies visible from head to feet, turned a little toward each other, arms relaxed',
    'driving': "now in the car: one in the DRIVER'S SEAT with both hands on the steering wheel, the other in the PASSENGER SEAT beside, seen from the side, both visible",
    'classroom': 'one now SEATED AT THE SCHOOL DESK facing right, the other STANDING at the chalkboard beside it, full bodies visible',
    'gym': 'one now SITTING ON THE BENCH, the other STANDING beside it holding nothing, full bodies visible',
}
CHARACTERS = {
    'lead': ('Using the character from these images: ' + B.CHARACTER['lead'], B.REFERENCES['lead']),
    'friend': ('Using the character from these images: ' + B.CHARACTER['friend'], ['cand-friend-seated-s52.png', '03-together.png']),
    'together': ('Using the two characters from this image (the one with the teal scarf and the one without): '
                 'the same white round-headed characters with dark navy outlines, white bulb-shaped torsos, dark navy limbs and mitten hands',
                 ['03-together.png']),
}
CAST = {'lead': ['lead'], 'friend': ['friend'], 'together': ['friend', 'lead']}


def base_name(setting: str, who: str) -> str:
    pose = POSES[setting][who]
    if who == 'together':
        return f'together-{setting}'
    return f'{who}-{pose}-{setting}'


def state_path(pack: str) -> Path:
    return PACK_DIR / f'pack-{pack}.json'


def load_state(pack: str) -> dict:
    p = state_path(pack)
    return json.loads(p.read_text()) if p.exists() else dict(pack=pack, candidates={}, rejected={}, rejections=[], picks={})


def save_state(state: dict):
    state_path(state['pack']).write_text(json.dumps(state, indent=1, ensure_ascii=False) + '\n')


def wanted(settings: list | None = None) -> dict:
    """Base name -> description (setting, pose, view, cast, who) for the pack."""
    out = {}
    for setting in (settings or list(PLACES)):
        for who in ('lead', 'friend', 'together'):
            pose = POSES[setting][who]
            out[base_name(setting, who)] = dict(setting=setting, pose=pose, view='eye-level', cast=CAST[who], who=who,
                                                poses={c: (POSES[setting]['lead' if c == 'lead' else 'friend'] if pose == 'mixed' else pose) for c in CAST[who]})
    return out


def covers(desc: dict, want: dict | None = None) -> str | None:
    """The pack base (name) that serves this base description once picked: same setting, pose and cast, or an
    insert of a pack setting (every pack base is built with an empty plate)."""
    want = want or wanted()
    for name, d in want.items():
        if d['setting'] != desc['setting']:
            continue
        if desc['pose'] == 'insert':
            return name
        if d['pose'] == desc['pose'] and sorted(d['cast']) == sorted(desc['cast']):
            return name
    return None


def jobs(want: dict, state: dict, direction: dict | None = None) -> dict:
    """FLUX edit jobs (flux_bases.py format) for every wanted base, with redo rounds for rejected ones."""
    direction = direction or {}
    out = {}
    for name, d in want.items():
        who, setting, pose = d['who'], d['setting'], d['pose']
        rnd = 1 + len({Path(f).name.rsplit('-s', 1)[0] for f in state.get('rejected', {}).get(name, [])})
        job = f'pack-{setting}-{who}' + ('' if rnd == 1 else f'-r{rnd}')
        s0 = (sum(ord(c) for c in name) % 500) + 400 + 100 * (rnd - 1)
        seeds = [s0, s0 + 1] if rnd == 1 else [s0, s0 + 1, s0 + 2]
        lead_in, images = CHARACTERS[who]
        if name in direction:
            pose_text = direction[name].strip().rstrip('.') + '.'
        elif who == 'together':
            pose_text = TOGETHER.get(setting if pose == 'mixed' else pose) + f', in {PLACES[setting]}.'
        else:
            pose_text = POSE_TEXT[pose] + f', in {PLACES[setting]}.'
        prompt = f'{lead_in}, {pose_text} {B.STYLE}'
        out[name] = dict(job=job, images=images, seeds=seeds, prompt=prompt, round=rnd, setting=setting)
    return out


def render(job_map: dict, pack: str) -> dict:
    return B.render_candidates(job_map, f'pack-{pack}')


def sheets(found: dict, job_map: dict, pack: str) -> dict:
    """One sheet per setting: setting -> sheet path."""
    out = {}
    by_setting = {}
    for name, files in found.items():
        by_setting.setdefault(job_map[name]['setting'], {})[name] = files
    for setting, rows in by_setting.items():
        rnd = max(job_map[n]['round'] for n in rows)
        path = B.sheet(rows, f'pack-{pack}-{setting}', rnd)
        out[setting] = str(path.relative_to(ROOT))
    return out


def approve(state: dict, picks: dict, rejects: dict, by: str, want: dict) -> list:
    """Record picks (into the base library) and rejections (into the pack state). Returns messages."""
    lib = B.library()
    msgs = []
    for name, reason in rejects.items():
        files = state['candidates'].get(name, [])
        state.setdefault('rejected', {}).setdefault(name, []).extend(f for f in files if f not in state.get('rejected', {}).get(name, []))
        state.setdefault('rejections', []).append(dict(base=name, files=files, reason=reason, by=by, at=time.strftime('%Y-%m-%dT%H:%M:%S')))
        msgs.append(f'  {name}: rejected' + (f' ({reason})' if reason else ''))
    for name, cand in picks.items():
        path = Path(cand)
        if not path.is_absolute():
            path = ROOT / (cand if '/' in cand else f'renders/bases/flux/{cand}')
        if not path.exists():
            raise FileNotFoundError(f'no such candidate: {path}')
        d = want[name]
        entry = B.approve(lib, name, str(path.relative_to(ROOT)), d, by)
        entry['pack'] = state['pack']
        entry['inserts'] = True            # every pack base is built with an empty plate, so it serves object-only inserts
        if d['pose'] == 'mixed':
            entry['poses'] = d['poses']
        state.setdefault('picks', {})[name] = str(path.relative_to(ROOT))
        msgs.append(f'  {name} <- {path.name} (approved, to build)')
    if picks:
        B.save_library(lib)
    save_state(state)
    return msgs


def status(state: dict, want: dict) -> str:
    lib = B.library()
    lines = [f"Starter pack: {len(want)} bases in {len({d['setting'] for d in want.values()})} settings"]
    for name, d in want.items():
        e = lib.get(name)
        st = e['status'] if e else ('candidates ready' if name in state.get('candidates', {}) else 'not rendered')
        lines.append(f'  {name:34} {d["setting"]:12} {d["pose"]:13} {st}')
    return '\n'.join(lines)
