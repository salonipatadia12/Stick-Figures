"""New base poses as a pipeline step.

A script may need a setting or a pose the painted-base library doesn't have (a bedroom, a character
lying down). This module:

1. describes what each beat needs (setting, pose, view, cast) from its shot,
2. matches that against the library (`styles/cozy/bases/library.json`, one entry per approved base),
3. lists the missing bases, writes FLUX.2 klein candidate jobs for them (two seeds each), renders them
   in `.venvs/mlx` and lays out a candidate sheet for the user,
4. stops at the `bases` approval gate. Nothing is used until the user picks a candidate per base; the
   pick is recorded in the library with its provenance, and the base is then built into layers
   (`scripts/motion_test/build_bases.py`) for this and every later video. The user may also reject a
   base's candidates (`stick approve --gate bases --reject <base>`): the next `stick bases --candidates`
   round renders new seeds, with the user's direction (`--direction <base>="..."`) replacing the pose
   template, and with the approved bases of the same setting as reference images for continuity.

The description step reads the shot's free text with keywords, so it is a first pass: the base plan is
printed for review and can be corrected by hand in `episode.json` (`shot.layers` may name a base).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from . import config

ROOT = config.ROOT
LIBRARY = ROOT / 'styles/cozy/bases/library.json'
FLUX_OUT = ROOT / 'renders/bases/flux'
CANDIDATES = ROOT / 'renders/bases/candidates'
EC = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'

BED_WORDS = ('bed ', 'bed,', 'bed.', 'quilt', 'pillow', 'nightstand', 'alarm clock', 'bedroom', 'duvet', 'blanket')
LYING_WORDS = ('asleep', 'sleeping', 'lying', 'lies in', 'lies on', 'on their back', 'under the quilt', 'under a quilt')
EDGE_RE = re.compile(r'(edge of (a|the) (\w+ )?bed)|bed edge|sits? on the bed|sitting on the bed')
STANDING_WORDS = ('standing', 'stands ')
TOP_WORDS = ('top-down', 'top down', 'from above', 'from directly above', 'overhead')
STREET_WORDS = ('on the street', 'street corner', 'bus stop', 'sidewalk')


def library() -> dict:
    return json.loads(LIBRARY.read_text()) if LIBRARY.exists() else {}


def save_library(lib: dict):
    LIBRARY.write_text(json.dumps(lib, indent=1, ensure_ascii=False) + '\n')


def describe(beat) -> dict:
    """What a beat needs from a base: setting, pose, view and cast, read from the shot text."""
    text = f'{beat.visual_intent} {beat.shot.image_prompt}'.lower()
    cast = sorted(beat.shot.cast)
    view = 'top-down' if beat.shot.framing == 'top_down' or any(w in text for w in TOP_WORDS) else 'eye-level'
    bed_hits = sum(w in text for w in BED_WORDS)
    if bed_hits >= 2 or (bed_hits and 'caf' not in text):          # "a quilt scrap on the café table" is the café
        setting = 'bedroom'
    elif any(w in text for w in STREET_WORDS) and 'caf' not in text:
        setting = 'street'
    else:
        setting = 'cafe'
    if not cast:
        pose = 'insert'
    elif setting == 'bedroom' and EDGE_RE.search(text):
        pose = 'bed-edge'
    elif setting == 'bedroom' and (any(w in text for w in LYING_WORDS) or 'alarm' in text):
        pose = 'bed-asleep-top' if view == 'top-down' else 'bed-awake'
    elif any(w in text for w in STANDING_WORDS):
        pose = 'standing'
    else:
        pose = 'seated-at-table'
    return dict(setting=setting, pose=pose, view=view, cast=cast)


def wanted_name(desc: dict) -> str:
    who = '-'.join(desc['cast']) or 'empty'
    if desc['pose'] == 'insert':
        return f'{desc["setting"]}-insert'
    return f'{who}-{desc["pose"]}' + ('' if desc['setting'] == 'cafe' else f'-{desc["setting"]}')


def match(desc: dict, lib: dict):
    """The library base that serves this description, or None.

    An exact match first (setting, pose, view, cast). Then the same base at eye level for a top-down
    request, since a seated top-down shot is framed from the eye-level base at a closer angle (the
    salary sample did this). Then, for two characters, one base per character in the same setting,
    cut as two shots ('friend-at-table + lead-standing')."""
    live = {n: e for n, e in lib.items() if e.get('status') in ('approved', 'built')}

    def find(setting, pose, view, cast):
        for name, e in live.items():
            if (e.get('setting') == setting and e.get('pose') == pose and e.get('view', 'eye-level') == view
                    and sorted(e.get('cast', [])) == sorted(cast)):
                return name
        return None
    if desc['pose'] == 'insert':
        return next((n for n, e in live.items() if e.get('setting') == desc['setting'] and e.get('inserts')), None)
    hit = find(desc['setting'], desc['pose'], desc['view'], desc['cast'])
    if hit is None and desc['view'] == 'top-down' and not desc['pose'].endswith('-top'):
        hit = find(desc['setting'], desc['pose'], 'eye-level', desc['cast'])
    if hit is None and len(desc['cast']) > 1:
        parts = []
        for who in desc['cast']:
            one = find(desc['setting'], desc['pose'], 'eye-level', [who]) or next(
                (n for n, e in live.items() if e.get('setting') == desc['setting'] and e.get('cast') == [who]), None)
            if one is None:
                return None
            parts.append(one)
        hit = ' + '.join(parts)
    return hit


def plan(episode, lib: dict | None = None) -> list:
    """Per beat: the description, the matching base (or None) and the wanted base name."""
    lib = library() if lib is None else lib
    out = []
    for b in episode.beats:
        forced = next((l[5:] for l in b.shot.layers if l.startswith('base:')), None)
        d = describe(b)
        base = forced or match(d, lib)
        out.append(dict(beat=b.id, **d, base=base, wanted=wanted_name(d)))
    return out


def missing(rows: list) -> dict:
    """Wanted base name -> description, for every beat with no library base (deduplicated)."""
    out = {}
    for r in rows:
        if r['base'] is None:
            out.setdefault(r['wanted'], dict(setting=r['setting'], pose=r['pose'], view=r['view'], cast=r['cast'],
                                              beats=[])).setdefault('beats', []).append(r['beat'])
    return out


def plan_digest(rows: list) -> str:
    return hashlib.sha256(json.dumps(sorted(r['wanted'] for r in rows if r['base'] is None)).encode()).hexdigest()


def plan_view(rows: list) -> str:
    lines = ['Base plan (setting · pose · view · cast -> library base)']
    for r in rows:
        who = ', '.join(r['cast']) or 'no characters'
        base = r['base'] or f'MISSING -> {r["wanted"]}'
        lines.append(f"  {r['beat']}  {r['setting']:8} {r['pose']:16} {r['view']:9} [{who}]  {base}")
    miss = missing(rows)
    if miss:
        lines.append(f'\n{len(miss)} base pose(s) to make and approve: ' + ', '.join(miss))
    else:
        lines.append('\nEvery beat has an approved base.')
    return '\n'.join(lines)


# ---- candidates ---------------------------------------------------------------------------------------
CHARACTER = {
    'lead': 'the same white round-headed character with a dark navy outline, white bulb-shaped torso, dark navy limbs and mitten hands',
    'friend': 'the same white round-headed character with a dark navy outline and a teal scarf, white bulb-shaped torso, dark navy limbs and mitten hands',
}
STYLE = ('Same proportions as the reference (very large round head, short torso, long thin navy legs), same painterly '
         'illustration style, portrait framing. No text.')
BEDROOM = ('a cozy bedroom with a rumpled patchwork quilt in rust, cream and teal squares, a small wooden nightstand with a '
           'round alarm clock and a lamp, a window with golden light and dappled shadows, a plant')
POSES = {
    'bed-edge': 'now SITTING ON THE EDGE OF A BED, seen from the side in three-quarter view facing right, feet on a wooden floor, arms relaxed at the sides, in {setting}.',
    'bed-asleep-top': 'now ASLEEP IN BED seen from DIRECTLY ABOVE (a top-down view): the bed fills the frame, the round white head rests on a pale pillow at the top, the body is hidden under the quilt, one navy mitten hand on top of the quilt, in {setting}.',
    'bed-awake': 'now LYING IN BED ON THEIR BACK at night, seen from the side at the level of the bed, head on a pale pillow on the left, body under the quilt pulled up to the chest, both navy mitten hands resting on top of the quilt, the lamp glowing warm, a dark blue window with moonlight, in {setting}.',
    'standing': 'now STANDING, full body visible from head to feet, three-quarter view facing right, arms relaxed at the sides, in {setting}.',
    'seated-at-table': 'now seated at a wooden table in three-quarter view, hands resting on the tabletop, in {setting}.',
}
SETTINGS = {'bedroom': BEDROOM, 'cafe': 'the sunlit cafe from the reference with ivy, tall windows and dappled shadows',
            'street': 'a warm painted street corner in golden light with dappled shadows'}
REFERENCES = {'lead': ['04-waiting.png', '01-alone.png'], 'friend': ['03-together.png']}
SEEDS = {'bed-edge': [101, 102], 'bed-asleep-top': [111, 112], 'bed-awake': [121, 122]}


def references_for(desc: dict, lib: dict, who: str) -> list:
    """Reference images for a candidate: the approved bases of the same setting (so a new pose keeps the same
    room, bed and props), else the character references from The Empty Chair."""
    same = [e['source'] for e in lib.values() if e.get('status') in ('approved', 'built') and e.get('setting') == desc['setting']
            and who in e.get('cast', []) and e.get('source')]
    return [Path(src).name for src in same[:2]] or REFERENCES.get(who, REFERENCES['lead'])


def candidate_jobs(miss: dict, lib: dict | None = None, rejected: dict | None = None, direction: dict | None = None) -> dict:
    """FLUX.2 klein edit jobs for the missing bases, in flux_bases.py's job format.

    Two seeds on the first round. When a base's candidates were rejected (`rejected[wanted]` lists them), the job
    gets a round suffix, three new seeds and, if given, the user's `direction[wanted]` in place of the pose template.
    """
    lib = library() if lib is None else lib
    rejected, direction = rejected or {}, direction or {}
    jobs = {}
    for wanted, d in miss.items():
        if d['pose'] == 'insert':
            continue                       # an insert is an empty plate of an existing setting: no character to design
        who = d['cast'][0] if len(d['cast']) == 1 else 'lead'
        job = 'cand-' + d['pose'] if who == 'lead' else f'cand-{who}-{d["pose"]}'
        if d['setting'] != 'cafe' and d['pose'] in ('standing', 'seated-at-table'):
            job += f'-{d["setting"]}'
        rnd = 1 + len({Path(f).name.rsplit('-s', 1)[0] for f in rejected.get(wanted, [])})
        base_seed = sum(ord(c) for c in job) % 900 + 100
        seeds = SEEDS.get(d['pose'], [base_seed, base_seed + 1])
        if rnd > 1:
            job += f'-r{rnd}'
            seeds = [seeds[0] + 100 * (rnd - 1) + i for i in range(3)]
        images = references_for(d, lib, who)
        setting = SETTINGS.get(d['setting'], d['setting'])
        if wanted in direction:
            pose = direction[wanted].strip().rstrip('.') + '.'
            lead_in = ('Using the character, the room, the bed and the furniture from these images (keep the same bed, '
                       'headboard, quilt pattern, pillow, nightstand and lamp): ' if images != REFERENCES.get(who, REFERENCES['lead'])
                       else 'Using the character from these images: ')
        else:
            pose = POSES.get(d['pose'], 'in a new pose that fits the scene, in {setting}.').format(setting=setting)
            lead_in = 'Using the character from these images: '
        prompt = f'{lead_in}{CHARACTER.get(who, CHARACTER["lead"])}, {pose} {STYLE}'
        jobs[wanted] = dict(job=job, images=images, seeds=seeds, prompt=prompt, round=rnd)
    return jobs


def render_candidates(jobs: dict, slug: str) -> dict:
    """Run the jobs that have no output yet (FLUX in .venvs/mlx), then return wanted -> [png paths]."""
    spec = {j['job']: dict(images=j['images'], seeds=j['seeds'], prompt=j['prompt']) for j in jobs.values()}
    todo = [name for name, j in spec.items() if not all((FLUX_OUT / f'{name}-s{s}.png').exists() for s in j['seeds'])]
    if todo:
        CANDIDATES.mkdir(parents=True, exist_ok=True)
        rnd = max(j.get('round', 1) for j in jobs.values())
        jobs_file = CANDIDATES / (f'{slug}-jobs.json' if rnd == 1 else f'{slug}-jobs-r{rnd}.json')
        jobs_file.write_text(json.dumps(spec, indent=1))
        subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/flux_bases.py'), str(jobs_file), *todo], check=True)
    return {w: [str((FLUX_OUT / f"{j['job']}-s{s}.png").relative_to(ROOT)) for s in j['seeds']] for w, j in jobs.items()}


def sheet(found: dict, slug: str, rnd: int = 1) -> Path:
    """One row per wanted base, one labelled thumbnail per candidate, for the approval. Later rounds get their
    own file, so earlier sheets stay on disk."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf', 22)
    tw, th, pad, gap = 360, 640, 44, 12
    cols = max(len(v) for v in found.values())
    row_h = th + pad * 2
    im = Image.new('RGB', (cols * (tw + gap) + gap, len(found) * row_h), (246, 238, 222))
    d = ImageDraw.Draw(im)
    for r, (wanted, files) in enumerate(found.items()):
        y = r * row_h
        title = wanted if ('pick' in wanted or wanted.startswith('approved')) else f'{wanted}  (pick one)'
        d.text((gap, y + 10), title, font=font, fill=(30, 30, 50))
        for c, f in enumerate(files):
            x = gap + c * (tw + gap)
            tile = Image.open(ROOT / f).convert('RGB').resize((tw, th))
            im.paste(tile, (x, y + pad))
            d.text((x, y + pad + th + 10), Path(f).name, font=font, fill=(30, 30, 50))
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    out = CANDIDATES / (f'{slug}-candidates.jpg' if rnd == 1 else f'{slug}-candidates-r{rnd}.jpg')
    im.save(out, quality=90)
    return out


def approve(lib: dict, wanted: str, candidate: str, desc: dict, by: str) -> dict:
    """Record the user's pick: the base enters the library as approved, to be built into layers."""
    entry = dict(status='approved', setting=desc['setting'], pose=desc['pose'], view=desc['view'], cast=desc['cast'],
                 source=candidate, approved=time.strftime('%Y-%m-%d'), by=by,
                 note='approved candidate; build its layers with scripts/motion_test/build_bases.py (a spec per base)')
    lib[wanted] = entry
    return entry
