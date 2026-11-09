"""Build approved bases without hand-written specs (docs/PLAN.md §7 D): `stick build-bases`.

For every base in the library with status 'approved' (a picked FLUX candidate), two clean plates are made with
FLUX.2 klein edits of the candidate: one with the characters' arms and hands removed, one with the characters
removed entirely. `scripts/motion_test/spec_from_plates.py` then reads the head, torso, shoulders and resting
hands off the three images and `build_bases.build` cuts the layers into styles/cozy/bases/<name>/. The library
entry becomes 'built' with a pointer to the debug overlay (renders/bases/debug-<name>.jpg), which is what to look
at when a base animates oddly.

Plates are picked automatically: the arms plate with the fewest navy pixels left where the arms were, the empty
plate with the fewest character pixels left. Jobs and prompts are logged like every FLUX call
(styles/cozy/bases/flux-prompts.json).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import bases as B
from . import config

ROOT = config.ROOT
FLUX_OUT = ROOT / 'renders/bases/flux'
KEEP = ('Same painterly illustration style, same camera and framing, same light and shadows. Do not redraw or move '
        'anything else.')
# what stands behind the arms, per pose: named so the edit fills the right thing in
BEHIND = {
    'standing': 'the scene behind them', 'seated-at-table': 'the chair back behind them and the bare tabletop',
    'seated-desk': 'the chair back behind them and the bare desktop', 'seated-sofa': 'the sofa cushions behind and beside them',
    'seated-bench': 'the bench slats behind and beside them', 'driving': 'the seat back behind them and the steering wheel',
    'passenger': 'the seat back behind them and their lap', 'bed-edge': 'the quilt and headboard behind them',
}
MOVING = {'seated-sofa', 'seated-bench'}          # a seated torso that leans; at a table or desk it stays painted


def _who(entry: dict) -> tuple[str, str, str]:
    """('the standing character', 'their', 'Their') style words for one or two characters."""
    cast = entry.get('cast', ['lead'])
    pose = entry.get('pose', 'standing')
    stance = {'standing': 'standing', 'driving': 'seated', 'passenger': 'seated'}.get(pose, 'seated')
    if len(cast) > 1:
        return f'both {stance} characters', 'their', 'Each character\'s'
    if 'friend' in cast:
        return f'the {stance} character with the teal scarf', 'their', 'Their'
    return f'the {stance} character', 'their', 'Their'


def plate_jobs(name: str, entry: dict) -> dict:
    """Two FLUX edit jobs for a library base: plate-<name>-arms and plate-<name>-empty."""
    subject, their, their_cap = _who(entry)
    poses = entry.get('poses') or {c: entry.get('pose') for c in entry.get('cast', ['lead'])}
    behind = ' and '.join(sorted({BEHIND.get(p, 'the scene behind them') for p in poses.values()}))
    two = len(entry.get('cast', [])) > 1
    scarf = 'friend' in entry.get('cast', [])
    arms = (f"Edit this illustration. Delete {subject}' navy arms and navy mitten hands entirely. " if two else
            f"Edit this illustration. Delete {subject}'s navy arms and both navy mitten hands entirely. ")
    arms += (f"{their_cap} torso becomes one smooth white bulb shape with a single dark navy outline along each side: no arms, "
             f"no sleeves, no shoulders sticking out and no lines inside the white torso. Show {behind} where the arms were. "
             f"Keep {their} round head{'s' if two else ''} and face{'s' if two else ''}, {'the teal scarf, ' if scarf else ''}"
             f"the navy legs and feet, the furniture and the scene exactly the same. " + KEEP)
    empty = (f"Edit this illustration. Remove {subject} completely (head{'s' if two else ''}, torso{'s' if two else ''}, "
             f"{'the teal scarf, ' if scarf else ''}arms, hands, legs and feet), so the scene looks empty: show what is behind "
             f"them, the furniture and the floor, exactly as painted. Keep the furniture, the scene and the light exactly the same. "
             + KEEP)
    src = Path(entry['source']).name
    seed = sum(ord(c) for c in name) % 80 + 10
    return {f'plate-{name}-arms': dict(images=[src], seeds=[seed, seed + 1], prompt=arms),
            f'plate-{name}-empty': dict(images=[src], seeds=[seed + 2], prompt=empty)}


def to_build(lib: dict | None = None, only: list | None = None) -> dict:
    lib = B.library() if lib is None else lib
    return {n: e for n, e in lib.items() if e.get('status') == 'approved' and (not only or n in only)}


def render_plates(names: list, lib: dict | None = None) -> dict:
    """Render the plates (cached by file name) and return name -> {'arms': [paths], 'empty': [paths]}."""
    lib = B.library() if lib is None else lib
    jobs = {}
    for n in names:
        jobs.update(plate_jobs(n, lib[n]))
    todo = [j for j, d in jobs.items() if not all((FLUX_OUT / f'{j}-s{s}.png').exists() for s in d['seeds'])]
    if todo:
        B.CANDIDATES.mkdir(parents=True, exist_ok=True)
        jobs_file = B.CANDIDATES / 'plate-jobs.json'
        old = json.loads(jobs_file.read_text()) if jobs_file.exists() else {}
        old.update(jobs)
        jobs_file.write_text(json.dumps(old, indent=1))
        subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/flux_bases.py'), str(jobs_file), *todo], check=True)
    return {n: dict(arms=[str(FLUX_OUT / f'plate-{n}-arms-s{s}.png') for s in jobs[f'plate-{n}-arms']['seeds']],
                    empty=[str(FLUX_OUT / f'plate-{n}-empty-s{s}.png') for s in jobs[f'plate-{n}-empty']['seeds']]) for n in names}


def build(names: list, lib: dict | None = None) -> list:
    """Derive specs and build the layers for these approved bases (runs scripts/motion_test/build_pack.py in the
    OpenCV env). Returns the printed report lines; the library entries become 'built'."""
    lib = B.library() if lib is None else lib
    plates = render_plates(names, lib)
    args = []
    for n in names:
        e = lib[n]
        poses = e.get('poses') or {c: e.get('pose') for c in e.get('cast', ['lead'])}
        moving = any(p in MOVING for p in poses.values())
        args.append(json.dumps(dict(name=n, source=str(ROOT / e['source']), arms=plates[n]['arms'], empty=plates[n]['empty'],
                                    cast=e.get('cast', ['lead']), moving=moving, poses=poses, head_hints=e.get('head_hints'))))
    proc = subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/build_pack.py'), *args],
                          capture_output=True, text=True)
    lines = (proc.stdout + proc.stderr).splitlines()
    built = {}
    for line in lines:
        if line.startswith('BUILT '):
            _, n, arms, empty = line.split(' ', 3)
            built[n] = (arms, empty)
    for n, (arms, empty) in built.items():
        lib[n].update(status='built', plates=dict(arms=arms, empty=empty), debug=f'renders/bases/debug-{n}.jpg',
                      spec='derived (scripts/motion_test/spec_from_plates.py)')
    if built:
        B.save_library(lib)
    if proc.returncode:
        lines.append(f'build_pack.py exited {proc.returncode}')
    return lines
