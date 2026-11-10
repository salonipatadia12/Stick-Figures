"""Explainer inserts (docs/PLAN.md §7 H): `stick inserts <slug> [--generate]`.

An insert beat SHOWS the subject: a full-frame painted scene with no characters (a volcano erupting, a frosted
farm in May, a street at sunset), generated once with local FLUX.2 klein and kept in the insert library
(`styles/cozy/inserts/library.json`, scenes as `styles/cozy/inserts/<key>.png`) for every later video. Code
animates it (`pipeline/rig/inserts.py`): particles, light and colour shifts, a moving sun, crossfades, camera
moves, labels and arrows.

A crossfade effect names a changed version of the same place ("the same street at sunset"); it is made as a
FLUX edit of the first scene so the two line up, and stored under `<key>--<slug of the change>`.

QA as for props: two seeds per scene, `scripts/motion_test/scene_qa.py` (painted detail, exposure, no character,
no text block) picks the better passing seed; a sheet and a question only when both fail.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from . import config

ROOT = config.ROOT
DIR = ROOT / 'styles/cozy/inserts'
LIBRARY = DIR / 'library.json'
FLUX_OUT = ROOT / 'renders/inserts/flux'
# Scenes are painted as FLUX edits against an Empty Chair frame used only as a style reference: tested 2026-11-08
# against plain text-to-image, the edit keeps the reference's brushwork, warm light and palette far better.
STYLE_REF = 'legacy/bespoke/the-empty-chair-source/assets/01-alone.png'
STYLE = ('Paint a completely new picture in exactly the painting style of this image (its brushwork, warm light, palette '
         'and texture), but with none of its content and NO people, characters or animals: {scene}. Vertical portrait '
         'composition with open sky or open space in the top third and clear, level open ground across the lower third (room '
         'for a person to stand there later, eye-level camera). No text, no letters, no signs, no logos.')
EDIT = ('Edit this painting: show the very same place, from the same viewpoint, with the same composition, but {change}. '
        'Keep every building, hill, tree and object exactly where it is. Same painterly style. No people, no text.')


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40]


def library() -> dict:
    return json.loads(LIBRARY.read_text()) if LIBRARY.exists() else {}


def save_library(lib: dict):
    DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY.write_text(json.dumps(lib, indent=1, ensure_ascii=False) + '\n')


# A close-up of things (a jar on a counter, a germ, a test strip) versus a place (a street, a tower, a kitchen seen
# whole). In an object close-up a full-body host standing beside the object looks dwarfed by a giant prop (user
# 2026-11-10), so the composer keeps the host in the foreground there. A library entry may set "scale" itself.
OBJECT_WORDS = re.compile(r'\b(jars?|bottles?|glass(es)?|cups?|mugs?|strips?|dippers?|spoons?|lemons?|plates?|bowls?|cells?|'
                          r'crystals?|drops?|droplets?|coins?|books?|phones?|tabletop|surface|microscope|macro|petri)\b')
FIRST_CLAUSE = re.compile(r'[,:;]| in | with | at | beside | under | on a | on the ')


def scene_scale(key: str, scene: str = '', lib: dict | None = None) -> str:
    """'object' or 'place' for an insert scene: the library entry's own "scale", else read from the opening phrase of
    its description (what the picture IS, not what stands in it)."""
    entry = (library() if lib is None else lib).get(key) or {}
    if entry.get('scale') in ('object', 'place'):
        return entry['scale']
    head = FIRST_CLAUSE.split((scene or entry.get('scene', '')).lower(), maxsplit=1)[0]
    return 'object' if OBJECT_WORDS.search(head) else 'place'


def variant_key(key: str, change: str) -> str:
    return f'{key}--{slug(change)}'


def needed(episode, lib: dict | None = None) -> dict:
    """Scenes the episode's insert beats need that the library does not have: key -> dict(scene, beats, base).
    `base` is set for crossfade variants (made as an edit of that scene)."""
    lib = library() if lib is None else lib
    out = {}
    for b in episode.beats:
        sp = b.shot.split if b.shot.visual == 'split' else None
        if sp and sp.key and sp.key not in lib:                # a split's bottom scene
            out.setdefault(sp.key, dict(scene=sp.scene, beats=[], base=None))['beats'].append(b.id)
        ins = b.shot.insert if b.shot.visual in ('insert', 'split') else None
        if ins is None:
            continue
        if ins.key not in lib:
            out.setdefault(ins.key, dict(scene=ins.scene, beats=[], base=None))['beats'].append(b.id)
        for e in ins.effects:
            if e.kind == 'crossfade' and e.detail:
                k = variant_key(ins.key, e.detail)
                if k not in lib:
                    out.setdefault(k, dict(scene=e.detail, beats=[], base=ins.key))['beats'].append(b.id)
    return out


def jobs(want: dict, rnd: int = 1, direction: dict | None = None) -> dict:
    """FLUX jobs: text-to-image for scenes, an edit of the base scene for crossfade variants (after the base exists)."""
    direction = direction or {}
    out = {}
    for key, d in want.items():
        s0 = sum(ord(c) for c in key) % 700 + 100 + 1000 * (rnd - 1)
        name = f'scene-{key}' + ('' if rnd == 1 else f'-r{rnd}')
        scene = direction.get(key, d['scene'])
        if d.get('base'):
            out[key] = dict(job=name, seeds=[s0, s0 + 1], prompt=EDIT.format(change=scene), images=[str(DIR / f"{d['base']}.png")])
        else:
            out[key] = dict(job=name, seeds=[s0, s0 + 1], prompt=STYLE.format(scene=scene.rstrip('.')), images=[STYLE_REF])
    return out


def render(job_map: dict, tag: str) -> dict:
    """Run the jobs with no output yet; key -> [png paths]."""
    spec = {j['job']: {k: v for k, v in j.items() if k != 'job'} for j in job_map.values()}
    todo = [n for n, j in spec.items() if not all((FLUX_OUT / f'{n}-s{s}.png').exists() for s in j['seeds'])]
    if todo:
        FLUX_OUT.mkdir(parents=True, exist_ok=True)
        jobs_file = FLUX_OUT.parent / f'{tag}-scene-jobs.json'
        jobs_file.write_text(json.dumps(spec, indent=1))
        subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/flux_scenes.py'), str(jobs_file), *todo], check=True)
    return {k: [str(FLUX_OUT / f"{j['job']}-s{s}.png") for s in j['seeds']] for k, j in job_map.items()}


def qa(paths: list) -> dict:
    """path -> QA dict, from scripts/motion_test/scene_qa.py (OpenCV env)."""
    if not paths:
        return {}
    out = subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/scene_qa.py'), *paths],
                         capture_output=True, text=True)
    res = {}
    for line in out.stdout.splitlines():
        if line.startswith('{'):
            d = json.loads(line)
            res[d['path']] = d
    for p in paths:
        res.setdefault(p, dict(path=p, ok=False, reasons=['QA did not run: ' + out.stderr.strip()[-200:]], score=0))
    return res


def sheet(failed: dict, results: dict, tag: str) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf', 20)
    tw, th = 300, 533
    rows = list(failed.items())
    im = Image.new('RGB', (2 * (tw + 12) + 12, len(rows) * (th + 90)), (246, 238, 222))
    d = ImageDraw.Draw(im)
    for r, (key, files) in enumerate(rows):
        y = r * (th + 90)
        d.text((12, y + 8), f'{key} (failed QA on both seeds)', font=font, fill=(30, 30, 50))
        for c, f in enumerate(files):
            x = 12 + c * (tw + 12)
            im.paste(Image.open(f).convert('RGB').resize((tw, th)), (x, y + 36))
            d.text((x, y + 40 + th), '; '.join(results[f]['reasons'])[:60], font=font, fill=(150, 30, 30))
    out = ROOT / 'renders/inserts' / f'{tag}-inserts-failed.jpg'
    im.save(out, quality=88)
    return out


def run(episode, generate: bool = False, lib: dict | None = None) -> dict:
    lib = library() if lib is None else lib
    want = needed(episode, lib)
    result = dict(needed=want, made={}, failed={}, sheet=None)
    if not generate or not want:
        return result
    DIR.mkdir(parents=True, exist_ok=True)
    for stage in ('scenes', 'variants'):             # variants are edits of scenes, so scenes go first
        part = {k: d for k, d in want.items() if bool(d.get('base')) == (stage == 'variants')
                and (stage == 'scenes' or d['base'] in lib)}   # a variant waits for its scene to exist
        if not part:
            continue
        jm = jobs(part)
        found = render(jm, episode.slug)
        results = qa([f for fs in found.values() for f in fs])
        for key, files in found.items():
            ok = [f for f in files if results[f]['ok']]
            if not ok:
                result['failed'][key] = files
                continue
            best = max(ok, key=lambda f: results[f]['score'])
            dst = DIR / f'{key}.png'
            from PIL import Image
            Image.open(best).convert('RGB').save(dst, optimize=True)
            lib[key] = dict(scene=part[key]['scene'], source=str(Path(best).relative_to(ROOT)), file=str(dst.relative_to(ROOT)),
                            prompt=jm[key]['prompt'], base=part[key].get('base'), qa=results[best]['stats'],
                            made=time.strftime('%Y-%m-%d'), episode=episode.slug)
            result['made'][key] = str(dst.relative_to(ROOT))
        save_library(lib)
        if result['failed']:
            result['sheet'] = str(sheet(result['failed'], results, episode.slug).relative_to(ROOT))
    return result


def view(result: dict) -> str:
    lines = []
    want = result['needed']
    if not want:
        return 'Every insert scene is in the library.'
    lines.append(f'Insert scenes to make ({len(want)}): ' + ', '.join(f"{k} [{', '.join(d['beats'])}]" for k, d in want.items()))
    for k, f in result['made'].items():
        lines.append(f'  + {k}: {f} (passed QA)')
    for k in result['failed']:
        lines.append(f'  ! {k}: failed QA on both seeds')
    if result.get('sheet'):
        lines.append(f"Sheet of the failed scenes: {result['sheet']}")
    return '\n'.join(lines)
