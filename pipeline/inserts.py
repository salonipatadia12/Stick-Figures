"""Explainer inserts (docs/PLAN.md §7 H): `stick inserts <slug> [--generate]`.

An insert beat SHOWS the subject: a full-frame painted scene with no characters (a volcano erupting, a frosted
farm in May, a street at sunset), generated once with local FLUX.2 klein and kept in the insert library
(`styles/cozy/inserts/library.json`, scenes as `styles/cozy/inserts/<key>.png`) for every later video. Code
animates it (`pipeline/rig/inserts.py`): particles, light and colour shifts, a moving sun, crossfades, camera
moves, labels and arrows.

A crossfade effect names a changed version of the same place ("the same street at sunset"); it is made as a
FLUX edit of the first scene so the two line up, and stored under `<key>--<slug of the change>`.

QA as for props: two seeds per scene, `scripts/motion_test/scene_qa.py` (painted detail, exposure, no character,
no text block) picks the better passing seed. A scene with no passing seed is never a dead end (2026-11-14, see
`pipeline/paintsafe.py`): it is painted again on a new seed, then from a simplified prompt, and if that fails too it
gets a placeholder (a gradient in the scene's mood with what it would show written on it), entered in the library
with `placeholder: true` so the video renders and its QA report names the beat.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from . import config, paintsafe

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
# A scene in open space has no ground to stand on: the style prompt drops its "open ground" clause there (a lunar
# module standing on sand, or a capsule parked on a desert, would mislead).
SPACE = re.compile(r'\b(in (open |black |deep |outer )?space|outer space|in orbit|open space)\b', re.I)
GROUND = (' and clear, level open ground across the lower third (room for a person to stand there later, eye-level '
          'camera)')
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
                          r'crystals?|drops?|droplets?|coins?|books?|phones?|tabletop|surface|microscope|macro|petri|brains?|hearts?|lungs?|'
                          r'eyes?|feathers?|germs?|bacteria|molecules?|neurons?)\b')
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


def lacks(lib: dict, key: str, slug: str = '') -> bool:
    """True when the library cannot serve this scene: no entry, or a placeholder made for another episode (a
    placeholder is painted again for every new video that wants the scene)."""
    e = lib.get(key)
    return e is None or bool(isinstance(e, dict) and e.get('placeholder') and e.get('episode') != slug)


def split_top_missing(shot) -> bool:
    """A split whose top scene (the insert) has no key: the composer shows the bottom scene alone
    (compose_beats.split_fallback), so there is nothing to paint for the top."""
    return shot.visual == 'split' and (shot.insert is None or not (shot.insert.key or '').strip())


def needed(episode, lib: dict | None = None) -> dict:
    """Scenes the episode's insert beats need that the library does not have: key -> dict(scene, beats, base).
    `base` is set for crossfade variants (made as an edit of that scene). A scene with no key or no description is
    never queued (2026-11-14: an empty split-top key was painted from an empty prompt and saved as `.png`)."""
    lib = library() if lib is None else lib
    slug = getattr(episode, 'slug', '')
    out = {}
    for b in episode.beats:
        sp = b.shot.split if b.shot.visual == 'split' else None
        if sp and sp.key and sp.key.strip() and lacks(lib, sp.key, slug):       # a split's bottom scene
            out.setdefault(sp.key, dict(scene=sp.scene or sp.key.replace('-', ' '), beats=[], base=None))['beats'].append(b.id)
        ins = b.shot.insert if b.shot.visual in ('insert', 'split') else None
        if ins is None or not (ins.key or '').strip():
            continue
        if lacks(lib, ins.key, slug):
            out.setdefault(ins.key, dict(scene=ins.scene or ins.key.replace('-', ' '), beats=[], base=None))['beats'].append(b.id)
        for e in ins.effects:
            if e.kind == 'crossfade' and e.detail:
                k = variant_key(ins.key, e.detail)
                if lacks(lib, k, slug):
                    out.setdefault(k, dict(scene=e.detail, beats=[], base=ins.key))['beats'].append(b.id)
    return out


def jobs(want: dict, rnd: int = 1, direction: dict | None = None) -> dict:
    """FLUX jobs: text-to-image for scenes, an edit of the base scene for crossfade variants (after the base exists)."""
    direction = direction or {}
    out = {}
    for key, d in want.items():
        s0 = sum(ord(c) for c in key) % 700 + 100 + 1000 * (rnd - 1)
        name = paintsafe.safe_stem(f'scene-{key}' + ('' if rnd == 1 else f'-r{rnd}'))
        scene = direction.get(key, d['scene'])
        if d.get('base'):
            out[key] = dict(job=name, seeds=[s0, s0 + 1], prompt=EDIT.format(change=scene), images=[str(DIR / f"{d['base']}.png")])
        else:
            prompt = STYLE.format(scene=scene.rstrip('.'))
            if SPACE.search(scene):
                prompt = prompt.replace(GROUND, ', nothing below it: no ground, no horizon, no land')
            out[key] = dict(job=name, seeds=[s0, s0 + 1], prompt=prompt, images=[STYLE_REF])
    return out


def render(job_map: dict, tag: str) -> dict:
    """Run the jobs with no good output yet; key -> [png paths] (a path may be missing if FLUX could not paint it:
    the caller treats that seed as failed). A FLUX crash is a warning here, never an exception."""
    spec = {j['job']: {k: v for k, v in j.items() if k != 'job'} for j in job_map.values()}
    todo = [n for n, j in spec.items() if not all(paintsafe.is_image(paintsafe.flux_png(FLUX_OUT, n, s)) for s in j['seeds'])]
    if todo:
        FLUX_OUT.mkdir(parents=True, exist_ok=True)
        jobs_file = FLUX_OUT.parent / f'{paintsafe.safe_stem(tag)}-scene-jobs.json'
        jobs_file.write_text(json.dumps(spec, indent=1))
        rc = subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/flux_scenes.py'), str(jobs_file), *todo]).returncode
        if rc:
            print(f'WARNING: FLUX (flux_scenes.py) stopped with exit {rc}; the scenes it did not paint go to the fallback', flush=True)
    return {k: [str(paintsafe.flux_png(FLUX_OUT, j['job'], s)) for s in j['seeds']] for k, j in job_map.items()}


def qa(paths: list) -> dict:
    """path -> QA dict, from scripts/motion_test/scene_qa.py (OpenCV env). A missing or unreadable file fails
    without being sent to the checker."""
    if not paths:
        return {}
    res = {p: dict(path=p, ok=False, reasons=['FLUX did not make a readable image'], score=0)
           for p in paths if not paintsafe.is_image(p)}
    paths = [p for p in paths if p not in res]
    if not paths:
        return res
    out = subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/scene_qa.py'), *paths],
                         capture_output=True, text=True)
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
            if paintsafe.is_image(f):
                im.paste(Image.open(f).convert('RGB').resize((tw, th)), (x, y + 36))
            d.text((x, y + 40 + th), '; '.join(results[f]['reasons'])[:60], font=font, fill=(150, 30, 30))
    out = ROOT / 'renders/inserts' / f'{tag}-inserts-failed.jpg'
    im.save(out, quality=88)
    return out


RETRIES = (('new seed', False), ('simplified prompt', True))     # after the first two seeds, in this order


def _best(found: dict, results: dict) -> dict:
    """key -> the best passing file, for the keys that have one."""
    out = {}
    for key, files in found.items():
        ok = [f for f in files if results.get(f, {}).get('ok') and paintsafe.is_image(f)]
        if ok:
            out[key] = max(ok, key=lambda f: results[f]['score'])
    return out


def run(episode, generate: bool = False, lib: dict | None = None) -> dict:
    """Paint every scene the episode needs. Never stops the episode over one scene: a scene that fails both seeds is
    retried on a new seed, then from a simplified prompt, then becomes a placeholder (`result['placeholders']`)."""
    lib = library() if lib is None else lib
    want = needed(episode, lib)
    if not generate or not want:
        return dict(needed=want, made={}, failed={}, placeholders={}, retried={}, sheet=None)
    return paint(want, episode.slug, lib)


def paint(want: dict, slug: str, lib: dict | None = None) -> dict:
    """Paint these scenes (key -> dict(scene, beats, base)) with the fallback chain and enter them in the library.
    `run` paints what an episode needs; the self-repair loop paints a scene again from its beat (pipeline/repair.py)."""
    lib = library() if lib is None else lib
    episode = type('E', (), dict(slug=slug))
    result = dict(needed=want, made={}, failed={}, placeholders={}, retried={}, sheet=None)
    DIR.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    for stage in ('scenes', 'variants'):             # variants are edits of scenes, so scenes go first
        part = {k: d for k, d in want.items() if bool(d.get('base')) == (stage == 'variants')
                and (stage == 'scenes' or d['base'] in lib)}   # a variant waits for its scene to exist
        if not part:
            continue
        on_placeholder = {k for k, d in part.items() if d.get('base') and (lib.get(d['base']) or {}).get('placeholder')}
        part = {k: d for k, d in part.items() if k not in on_placeholder}
        for key in sorted(on_placeholder):           # an edit of a placeholder is no picture: a placeholder too
            dst = paintsafe.placeholder_scene(paintsafe.png_path(DIR, key), want[key]['scene'])
            lib[key] = dict(scene=want[key]['scene'], base=want[key]['base'], file=str(dst.relative_to(ROOT)), placeholder=True,
                            why='its base scene is a placeholder', made=time.strftime('%Y-%m-%d'), episode=episode.slug)
            result['placeholders'][key] = 'its base scene is a placeholder'
        if not part:
            save_library(lib)
            continue
        jm = jobs(part)
        found = render(jm, episode.slug)
        results = qa([f for fs in found.values() for f in fs])
        best = _best(found, results)
        prompts = {k: jm[k]['prompt'] for k in best}
        for n, (how, simple) in enumerate(RETRIES, 2):
            left = {k: d for k, d in part.items() if k not in best}
            if not left:
                break
            direction = {k: paintsafe.simplify(d['scene']) for k, d in left.items()} if simple else None
            jr = jobs(left, rnd=n, direction=direction)
            for j in jr.values():
                j['seeds'] = j['seeds'][:1]          # one image per retry
            print(f'Retry {n - 1} ({how}) for {len(left)} scene(s): ' + ', '.join(
                f'{k}' + (f' as "{direction[k]}"' if direction else '') for k in left), flush=True)
            fr = render(jr, f'{episode.slug}-retry{n - 1}')
            rr = qa([f for fs in fr.values() for f in fs])
            results.update(rr)
            for k, f in _best(fr, rr).items():
                best[k], prompts[k] = f, jr[k]['prompt']
                result['retried'][k] = how
            for k in left:
                found[k] = list(found[k]) + fr[k]
        for key, d in part.items():
            dst = paintsafe.png_path(DIR, key)
            entry = dict(scene=d['scene'], base=d.get('base'), made=time.strftime('%Y-%m-%d'), episode=episode.slug)
            if key in best:
                Image.open(best[key]).convert('RGB').save(dst, optimize=True)
                lib[key] = dict(entry, source=str(Path(best[key]).relative_to(ROOT)), file=str(dst.relative_to(ROOT)),
                                prompt=prompts[key], qa=results[best[key]].get('stats'))
                result['made'][key] = str(dst.relative_to(ROOT))
                continue
            why = ' | '.join('; '.join(results.get(f, {}).get('reasons') or ['no result']) for f in found[key]) or 'not painted'
            paintsafe.placeholder_scene(dst, d['scene'])
            lib[key] = dict(entry, file=str(dst.relative_to(ROOT)), placeholder=True, why=why[:500],
                            tried=[str(Path(f).relative_to(ROOT)) for f in found[key] if Path(f).exists()])
            result['placeholders'][key] = why
            print(f'WARNING: scene {key} would not paint after 2 seeds and 2 retries; using a placeholder ({why[:160]})', flush=True)
        save_library(lib)                            # after each stage: the variants need their scene in the library
    return result


def view(result: dict) -> str:
    lines = []
    want = result['needed']
    if not want:
        return 'Every insert scene is in the library.'
    lines.append(f'Insert scenes to make ({len(want)}): ' + ', '.join(f"{k} [{', '.join(d['beats'])}]" for k, d in want.items()))
    for k, f in result['made'].items():
        lines.append(f'  + {k}: {f} (passed QA)')
    for k, how in result.get('retried', {}).items():
        lines.append(f'    ({k} passed on a retry: {how})')
    for k, why in result.get('placeholders', {}).items():
        lines.append(f'  ~ {k}: PLACEHOLDER (would not paint after 2 seeds and 2 retries: {why[:160]})')
    for k in result['failed']:
        lines.append(f'  ! {k}: failed QA on both seeds')
    if result.get('sheet'):
        lines.append(f"Sheet of the failed scenes: {result['sheet']}")
    return '\n'.join(lines)
