"""The automatic props step (docs/PLAN.md §7 C): `stick props <slug>`.

A script (schema v3) lists the props each beat needs. Most come from a library: the base's own cut-outs, the
shared sprites, the prop library (`styles/cozy/props/library.json`) or the code-drawn set
(`pipeline/rig/drawn.py`). For each prop no library has, this step:

1. renders two FLUX.2 klein text-to-image candidates of the prop alone on a plain light ground (local, free,
   about 25 s each at 512 px),
2. cuts the object out (`props.key_neutral` + `props.clean_alpha`),
3. checks the sprite: one object, a sensible share of the canvas, not touching the border, in the palette,
4. on the first pass, enters it in the prop library with its provenance. If both seeds fail (2026-11-14, see
   `pipeline/paintsafe.py`) it is painted again on a new seed, then from a simplified name, and if that fails too
   it gets a placeholder sprite (a card with its name) entered with `placeholder: true`, so the video still
   renders and its QA report names the beat. A prop never stops the episode.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, paintsafe

ROOT = config.ROOT
LIBRARY = ROOT / 'styles/cozy/props/library.json'
SPRITES = ROOT / 'styles/cozy/props'
FLUX_OUT = ROOT / 'renders/props/flux'
SHEETS = ROOT / 'renders/props'
PROMPT = ('A single {name}, alone, centred, seen from slightly above, on a plain flat light grey background and nothing '
          'else. Children\'s-book painterly illustration: soft brush texture, simple shapes, a thin dark navy outline, '
          'warm palette of rust, cream, teal and wood brown, like a cozy hand-painted cartoon. No text, no letters, '
          'no people, no hands, no table, no ground shadow.')
# the code-drawn and shared props the composer already knows (keys after compose_beats.prop_key)
BUILT_IN = {'lamp', 'clock', 'spring', 'knot', 'thread', 'quilt', 'scrap', 'steam', 'figure', 'person', 'hole', 'receipt', 'phone', 'box', 'cup',
            'sunbeam', 'specks', 'photo'}


def library() -> dict:
    return json.loads(LIBRARY.read_text()) if LIBRARY.exists() else {}


def save_library(lib: dict):
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY.write_text(json.dumps(lib, indent=1, ensure_ascii=False) + '\n')


def prop_key(name: str) -> str:
    import sys
    sys.path.insert(0, str(ROOT / 'scripts/motion_test'))
    from compose_beats import prop_key as pk
    return pk(name)


def needed(episode, lib: dict | None = None) -> dict:
    """Prop key -> dict(name, beats) for every script prop that no library provides."""
    from . import bases as B
    lib = library() if lib is None else lib
    rows = {r['beat']: r for r in B.plan(episode)}
    out = {}
    for b in episode.beats:
        st = b.shot.staging
        if st is None:
            continue
        base = rows[b.id]['base'] or ''
        base_props = set()
        for name in base.split(' + '):
            meta = ROOT / 'styles/cozy/bases' / name / 'base.json'
            if meta.exists():
                m = json.loads(meta.read_text())
                base_props |= set(m.get('props', {})) | ({'cup'} if m.get('cups') else set())
        for p in st.props:
            key = prop_key(p.name)
            if b.shot.visual != 'character' and not p.held_by:
                continue          # a painted insert/split/graphic shows its own things; only held props are drawn on it
            stale = (lib.get(key) or {}).get('placeholder') and lib[key].get('episode') != episode.slug
            if key in base_props or key in BUILT_IN or (key in lib and not stale):
                continue
            out.setdefault(key, dict(name=p.name, beats=[]))['beats'].append(b.id)
    return out


def jobs(keys: dict, rnd: int = 1) -> dict:
    out = {}
    for key, d in keys.items():
        job = paintsafe.safe_stem(f'prop-{key}' + ('' if rnd == 1 else f'-r{rnd}'))
        s0 = sum(ord(c) for c in key) % 700 + 100 + 50 * (rnd - 1)
        out[key] = dict(job=job, seeds=[s0, s0 + 1], size=512, prompt=PROMPT.format(name=d['name']), round=rnd)
    return out


def render(job_map: dict, slug: str) -> dict:
    spec = {j['job']: dict(prompt=j['prompt'], seeds=j['seeds'], size=j['size']) for j in job_map.values()}
    todo = [n for n, j in spec.items() if not all(paintsafe.is_image(paintsafe.flux_png(FLUX_OUT, n, s)) for s in j['seeds'])]
    if todo:
        SHEETS.mkdir(parents=True, exist_ok=True)
        jobs_file = SHEETS / f'{paintsafe.safe_stem(slug)}-prop-jobs.json'
        jobs_file.write_text(json.dumps(spec, indent=1))
        rc = subprocess.run([str(ROOT / '.venvs/mlx/bin/python'), str(ROOT / 'scripts/motion_test/flux_props.py'), str(jobs_file), *todo]).returncode
        if rc:
            print(f'WARNING: FLUX (flux_props.py) stopped with exit {rc}; the props it did not paint go to the fallback', flush=True)
    return {k: [paintsafe.flux_png(FLUX_OUT, j['job'], s) for s in j['seeds']] for k, j in job_map.items()}


def cut(path: Path) -> Image.Image:
    from .rig.props import clean_alpha, key_neutral
    sprite = key_neutral(str(path), sat_max=30, lum_min=120)
    return clean_alpha(sprite, solid=90, soft=1.0)


def check(sprite: Image.Image, canvas: int = 512) -> dict:
    """One object of a sensible size, clear of the border, with some colour: pass/fail with reasons."""
    box = sprite.getchannel('A').point(lambda v: 255 if v > 128 else 0).getbbox()
    if box is None:
        return dict(pass_=False, reasons=['nothing cut out'], size=[0, 0], coverage=0.0, pieces=0)
    sprite = sprite.crop(box)
    a = np.asarray(sprite.getchannel('A')) > 128
    reasons = []
    cover = a.mean() if a.size else 0.0
    w, h = sprite.size
    if max(w, h) < 100 or min(w, h) < 40:          # thin things (a test strip, a pencil) are judged on the long side
        reasons.append(f'too small ({w}x{h} px)')
    if w > canvas * 0.96 or h > canvas * 0.96:
        reasons.append('fills the canvas: the backdrop was not separated')
    sizes = _pieces(a)
    n = len(sizes)
    if n == 0:
        reasons.append('nothing cut out')
    elif max(sizes) / a.sum() < 0.9:
        reasons.append(f'{n} separate pieces: not one object')
    if 0.0 < cover < 0.22:
        reasons.append(f'sprite is mostly empty ({cover:.0%} solid): a thin or broken object')
    rgb = np.asarray(sprite.convert('RGB')).astype(int)
    sat = (rgb.max(-1) - rgb.min(-1))[a] if a.any() else np.array([0])
    if sat.mean() < 18:
        reasons.append('grey: no colour from the palette')
    lum = rgb.mean(-1)[a] if a.any() else np.array([255])
    if (lum > 235).mean() > 0.6:
        reasons.append('mostly white: the object merged with the backdrop')
    return dict(pass_=not reasons, reasons=reasons, size=[w, h], coverage=round(float(cover), 3), pieces=int(n))


def _pieces(mask: np.ndarray, min_px: int = 30) -> list:
    """Pixel counts of the connected solid pieces (flood fills on a PIL image; no SciPy in this env)."""
    from PIL import ImageDraw
    m = Image.fromarray((mask * 255).astype(np.uint8)).convert('I')
    arr = np.asarray(m)
    sizes, label = [], 1
    ys, xs = np.nonzero(arr == 255)
    for y, x in zip(ys[::7], xs[::7]):                      # sample seeds; every piece over a few dozen px is hit
        if np.asarray(m)[y, x] != 255:
            continue
        ImageDraw.floodfill(m, (int(x), int(y)), label, thresh=0)
        size = int((np.asarray(m) == label).sum())
        if size >= min_px:
            sizes.append(size)
        label += 1
    return sizes


def sheet(rows: list, slug: str) -> Path:
    """rows = [(key, name, [(png, sprite, check)])]: the failed props, for the user."""
    from PIL import ImageDraw, ImageFont
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf', 20)
    tw, pad = 256, 40
    cols = max(len(r[2]) for r in rows) * 2
    im = Image.new('RGB', (cols * (tw + 12) + 12, len(rows) * (tw + pad * 2)), (246, 238, 222))
    d = ImageDraw.Draw(im)
    for r, (key, name, cands) in enumerate(rows):
        y = r * (tw + pad * 2)
        d.text((12, y + 8), f'{name}  (failed QA on both seeds; tell me how it should look, or name a stand-in)', font=font, fill=(30, 30, 50))
        for c, (png, sprite, chk) in enumerate(cands):
            x = 12 + c * 2 * (tw + 12)
            if paintsafe.is_image(png):
                im.paste(Image.open(png).convert('RGB').resize((tw, tw)), (x, y + pad))
            tile = Image.new('RGB', (tw, tw), (200, 230, 200))
            sp = sprite.copy()
            sp.thumbnail((tw - 8, tw - 8))
            tile.paste(sp, ((tw - sp.width) // 2, (tw - sp.height) // 2), sp)
            im.paste(tile, (x + tw + 12, y + pad))
            d.text((x, y + pad + tw + 6), '; '.join(chk['reasons'])[:60], font=font, fill=(150, 40, 40))
    SHEETS.mkdir(parents=True, exist_ok=True)
    out = SHEETS / f'{slug}-props.jpg'
    im.save(out, quality=90)
    return out


def _try(png) -> tuple:
    """(png, sprite, check) for one FLUX file; a missing or unreadable file fails the check without cutting."""
    if not paintsafe.is_image(png):
        return png, Image.new('RGBA', (8, 8)), dict(pass_=False, reasons=['FLUX did not make a readable image'])
    try:
        sprite = cut(png)
    except Exception as e:                           # a cut that crashes is a failed candidate, not a failed episode
        return png, Image.new('RGBA', (8, 8)), dict(pass_=False, reasons=[f'cut failed: {type(e).__name__}: {e}'])
    return png, sprite, check(sprite)


RETRIES = (('new seed', False), ('simplified prompt', True))


def run(episode, lib: dict | None = None, generate: bool = True) -> dict:
    """Generate, cut and check every prop the script needs and no library has. Returns what was added, what passed
    on a retry and what became a placeholder (`failed` stays empty: a prop never stops the episode)."""
    lib = library() if lib is None else lib
    need = needed(episode, lib)
    result = dict(needed=need, added={}, failed={}, placeholders={}, retried={}, sheet=None)
    if not need or not generate:
        return result
    SPRITES.mkdir(parents=True, exist_ok=True)
    tried = {k: [] for k in need}
    passed = {}                                      # key -> (png, sprite, check, prompt)
    for n, (how, simple) in enumerate(((None, False),) + RETRIES, 1):
        left = {k: d for k, d in need.items() if k not in passed}
        if not left:
            break
        want = {k: dict(d, name=paintsafe.simplify(d['name'])) for k, d in left.items()} if simple else left
        jm = jobs(want, rnd=n)
        if n > 1:
            for j in jm.values():
                j['seeds'] = j['seeds'][:1]
            print(f'Retry {n - 1} ({how}) for {len(left)} prop(s): ' + ', '.join(
                k + (f' as "{want[k]["name"]}"' if simple else '') for k in left), flush=True)
        found = render(jm, episode.slug if n == 1 else f'{episode.slug}-retry{n - 1}')
        for key, pngs in found.items():
            for png in pngs:
                cand = _try(png)
                tried[key].append(cand)
                if cand[2]['pass_']:
                    passed[key] = cand + (jm[key]['prompt'],)
                    if how:
                        result['retried'][key] = how
                    break
    for key, d in need.items():
        dst = paintsafe.png_path(SPRITES, key)
        if key in passed:
            png, sprite, chk, prompt = passed[key]
            sprite.save(dst)
            lib[key] = dict(sprite=str(dst.relative_to(ROOT)), width=0.4, name=d['name'], source=str(Path(png).relative_to(ROOT)),
                            qa=chk, made=time.strftime('%Y-%m-%d'), licence='apache-2.0 (FLUX.2 klein output)', episode=episode.slug,
                            note='generated by the props step; width is the default 0.4 head diameters')
            result['added'][key] = lib[key]
            continue
        why = ' | '.join('; '.join(c[2]['reasons']) for c in tried[key]) or 'not painted'
        paintsafe.placeholder_prop(d['name']).save(dst)
        lib[key] = dict(sprite=str(dst.relative_to(ROOT)), width=0.4, name=d['name'], placeholder=True, why=why[:500],
                        made=time.strftime('%Y-%m-%d'), episode=episode.slug,
                        tried=[str(Path(c[0]).relative_to(ROOT)) for c in tried[key] if Path(c[0]).exists()])
        result['placeholders'][key] = why
        print(f'WARNING: prop {d["name"]} would not paint after 2 seeds and 2 retries; using a placeholder ({why[:160]})', flush=True)
    if result['added'] or result['placeholders']:
        save_library(lib)
    return result


def view(result: dict) -> str:
    lines = []
    need = result['needed']
    if not need:
        return 'Props: every script prop comes from a library (base cut-outs, shared sprites, prop library, code-drawn).'
    lines.append(f'Props to make ({len(need)}): ' + ', '.join(f"{d['name']} [{', '.join(d['beats'])}]" for d in need.values()))
    for k, e in result['added'].items():
        lines.append(f'  + {k}: {e["sprite"]} (passed QA, from {Path(e["source"]).name})')
    for k, how in result.get('retried', {}).items():
        lines.append(f'    ({k} passed on a retry: {how})')
    for k, why in result.get('placeholders', {}).items():
        lines.append(f'  ~ {k}: PLACEHOLDER (would not paint after 2 seeds and 2 retries: {why[:160]})')
    for k, f in result['failed'].items():
        lines.append(f'  ! {k} ({f["name"]}): failed QA on both seeds: ' + ' | '.join('; '.join(r) for r in f['reasons']))
    if result['sheet']:
        lines.append(f'Sheet of the failed props: {result["sheet"]}')
    return '\n'.join(lines)
