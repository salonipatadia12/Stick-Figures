"""Pipeline calls the worker makes in a child process (the repo's .venv, the repo's current `pipeline/`), so a
long-running worker never holds stale pipeline code. Run by file path, not as part of the macworker package:

  .venv/bin/python macworker/bridge.py needed <slug>
  .venv/bin/python macworker/bridge.py redo <slug> <redo.json>      # {key: note}: repaint as the next round

The answer is one line on stdout: `BRIDGE_JSON {...}`. Everything else (FLUX progress) passes through.
Uses only the pipeline's public helpers: inserts.jobs/render/qa/library/save_library and the propgen equivalents.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

MARK = 'BRIDGE_JSON '


def _pipeline(repo: Path):
    sys.path.insert(0, str(repo))
    from pipeline import inserts, propgen, store
    return inserts, propgen, store


def needed(slug: str, ins, pg, store) -> dict:
    """What the libraries lack (`inserts`, `props`) and every key the episode uses with its beats (`uses_*`,
    computed against an empty library)."""
    episode, _ = store.load(slug)
    return dict(inserts=ins.needed(episode), props=pg.needed(episode),
                uses_inserts=ins.needed(episode, {}), uses_props=pg.needed(episode, {}))


def _archive(src: Path, lib_dir: Path, key: str, entry: dict, prev_round: int, today: str) -> str | None:
    """Copy the current picture and its library entry under <lib_dir>/archive/round<N>-<date>/ (never delete,
    never overwrite an earlier archive). Returns the archived picture's path."""
    adir = lib_dir / 'archive' / f'round{prev_round}-{today}'
    adir.mkdir(parents=True, exist_ok=True)
    dst = adir / src.name
    if dst.exists():
        dst = adir / f'{src.stem}-{time.strftime("%H%M%S")}{src.suffix}'
    if src.exists():
        shutil.copy2(src, dst)
    entries_file = adir / 'library-entries.json'
    entries = json.loads(entries_file.read_text()) if entries_file.exists() else {}
    name = key if key not in entries else f'{key}@{time.strftime("%H%M%S")}'
    entries[name] = dict(entry, archived_file=str(dst))
    entries_file.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + '\n')
    return str(dst) if src.exists() else None


def _rel(path, root) -> str | None:
    if path is None:
        return None
    p, r = Path(path).resolve(), Path(root).resolve()
    try:
        return str(p.relative_to(r))
    except ValueError:
        return str(p)


def _next_round(entry: dict, exists) -> int:
    rnd = int(entry.get('round') or 1) + 1
    while exists(rnd):                        # never reuse a round whose FLUX output is already on disk
        rnd += 1
    return rnd


def redo_insert(key: str, note: str, slug: str, ins, today: str) -> tuple[str | None, str | None, dict]:
    """Repaint one insert scene as the next round with the note as art direction. (path, why_failed, info)."""
    from PIL import Image
    lib = ins.library()
    entry = lib[key]
    base = entry.get('base')
    scene = entry.get('scene', '')
    direction = f"{scene.rstrip('. ')}; {note.strip()}" if base else f"{scene.rstrip('. ')}. Art direction: {note.strip()}"
    want = {key: dict(scene=scene, beats=[], base=base)}

    def exists(r):
        jm = ins.jobs(want, rnd=r, direction={key: direction})[key]
        return any((ins.FLUX_OUT / f"{jm['job']}-s{s}.png").exists() for s in jm['seeds'])

    rnd = _next_round(entry, exists)
    jm = ins.jobs(want, rnd=rnd, direction={key: direction})
    found = ins.render(jm, f'{slug}-redo')
    results = ins.qa([f for fs in found.values() for f in fs])
    ok = [f for f in found[key] if results[f]['ok']]
    if not ok:
        why = ' | '.join('; '.join(results[f].get('reasons', [])) for f in found[key]) or 'failed QA'
        return None, f'round {rnd} failed QA on both seeds ({why}); round {rnd - 1} kept', {}
    best = max(ok, key=lambda f: results[f]['score'])
    dst = ins.DIR / f'{key}.png'
    archived = _archive(dst, ins.DIR, key, entry, rnd - 1, today)
    Image.open(best).convert('RGB').save(dst, optimize=True)
    rel = lambda p: _rel(p, ins.ROOT)
    lib = ins.library()                       # re-read: nothing else should have changed it, but keep other edits
    lib[key] = dict(entry, source=rel(best), file=rel(dst), prompt=jm[key]['prompt'], qa=results[best].get('stats'),
                    made=time.strftime('%Y-%m-%d'), round=rnd, direction=note.strip(), episode=slug,
                    history=list(entry.get('history', [])) + ([dict(round=rnd - 1, file=rel(archived))] if archived else []))
    ins.save_library(lib)
    variants = sorted(k for k, e in lib.items() if (e or {}).get('base') == key)
    return str(dst), None, dict(round=rnd, scene=scene, stale_variants=variants)


def redo_prop(key: str, note: str, slug: str, pg, today: str) -> tuple[str | None, str | None, dict]:
    lib = pg.library()
    entry = lib[key]
    name = entry.get('name', key)
    want = {key: dict(name=f'{name} ({note.strip()})', beats=[])}

    def exists(r):
        jm = pg.jobs(want, rnd=r)[key]
        return any((pg.FLUX_OUT / f"{jm['job']}-s{s}.png").exists() for s in jm['seeds'])

    rnd = _next_round(entry, exists)
    jm = pg.jobs(want, rnd=rnd)
    found = pg.render(jm, f'{slug}-redo')
    reasons = []
    for png in found[key]:
        sprite = pg.cut(png)
        chk = pg.check(sprite)
        if chk.get('pass_'):
            dst = pg.SPRITES / f'{key}.png'
            archived = _archive(dst, pg.SPRITES, key, entry, rnd - 1, today)
            sprite.save(dst)
            rel = lambda p: _rel(p, pg.ROOT)
            lib = pg.library()
            lib[key] = dict(entry, sprite=rel(dst), source=rel(png), qa=chk, made=time.strftime('%Y-%m-%d'), round=rnd,
                            direction=note.strip(), history=list(entry.get('history', [])) +
                            ([dict(round=rnd - 1, file=rel(archived))] if archived else []))
            pg.save_library(lib)
            return str(dst), None, dict(round=rnd, scene=name)
        reasons.append('; '.join(chk.get('reasons', [])))
    return None, f'round {rnd} failed QA on both seeds ({" | ".join(reasons)}); round {rnd - 1} kept', {}


def redo(slug: str, notes: dict, ins, pg, today: str | None = None) -> dict:
    """{made: {key: {file, kind, round, scene}}, failed: {key: why}}."""
    today = today or time.strftime('%Y%m%d')
    out = dict(made={}, failed={})
    ilib, plib = ins.library(), pg.library()
    for key, note in notes.items():
        try:
            if key in ilib:
                path, why, info = redo_insert(key, note, slug, ins, today)
                kind = 'scene'
            elif key in plib:
                path, why, info = redo_prop(key, note, slug, pg, today)
                kind = 'prop'
            else:
                out['failed'][key] = 'not in the insert or prop library'
                continue
        except Exception as e:               # one key failing does not stop the others
            out['failed'][key] = f'{type(e).__name__}: {e}'
            continue
        if path:
            out['made'][key] = dict(file=path, kind=kind, **info)
        else:
            out['failed'][key] = why
    return out


def main(argv: list) -> int:
    repo = Path(os.environ.get('STICK_REPO') or Path(__file__).resolve().parents[1])
    ins, pg, store = _pipeline(repo)
    cmd, slug = argv[0], argv[1]
    if cmd == 'needed':
        ans = needed(slug, ins, pg, store)
    elif cmd == 'redo':
        ans = redo(slug, json.loads(Path(argv[2]).read_text()), ins, pg)
    else:
        print(f'unknown command {cmd}', file=sys.stderr)
        return 2
    print(MARK + json.dumps(ans, ensure_ascii=False, default=str), flush=True)
    return 0


def parse(output: str) -> dict | None:
    for line in reversed(output.splitlines()):
        if line.startswith(MARK):
            return json.loads(line[len(MARK):])
    return None


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
