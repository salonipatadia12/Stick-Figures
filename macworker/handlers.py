"""Job handlers: `paint` and `render`, and `pitch`/`script` when the server runs SCRIPT_PROVIDER=ollama (docs/WEBAPP.md,
"What the Mac worker runs").

A handler gets a Ctx and returns an Outcome: the result for `done` plus the files to upload first. It raises
JobError for a failure the server should hear about (`fail`). Handlers never talk to the server themselves.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import bridge
from .progress import PaintProgress, RenderProgress, failed_reasons
from .util import NAME_OK, check_key, check_slug, read_json, safe_name, write_json

log = logging.getLogger('stick-worker')

INSERTS_LIB = 'styles/cozy/inserts/library.json'
PROPS_LIB = 'styles/cozy/props/library.json'
EXIT_GATE = 3                                # `stick inserts|props` when some candidates failed QA on both seeds
PREVIEW_W = 720
CREAM = (246, 238, 222)
LLM_KINDS = ('pitch', 'script')
CLAIM_KINDS = LLM_KINDS + ('paint', 'render')   # the server hands out pitch/script only with SCRIPT_PROVIDER=ollama
LLM_WHAT = dict(pitch='Research/storylines', script='Script')
DEFAULT_OLLAMA_URL = 'http://localhost:11434'
OLLAMA_DOWN = 'Ollama is not running on the Mac: brew services start ollama'


class JobError(Exception):
    pass


@dataclass
class Outcome:
    result: dict
    files: list = field(default_factory=list)       # [{path, name, kind}]


@dataclass
class Ctx:
    repo: Path
    job: dict
    workdir: Path                                    # this job's folder in the worker state dir
    report: callable                                 # report(stage, pct, message)
    run: callable                                    # run(cmd, on_line=None) -> (rc, output tail)
    render_workers: int = 4
    ollama: callable = None                          # ollama() -> version, or JobError(OLLAMA_DOWN)

    @property
    def slug(self) -> str:
        return check_slug(self.job.get('slug'))

    @property
    def payload(self) -> dict:
        return self.job.get('payload') or {}


# ---- shared ----------------------------------------------------------------------------------------
def write_episode(repo: Path, job: dict) -> Path:
    """Write the received episode.json / ledger.json into <repo>/episodes/<slug>/. A different local copy is kept
    under episodes/_archive/macworker/<slug>/<time>/ first (never deleted)."""
    slug = check_slug(job.get('slug'))
    ep = job.get('episode')
    if not isinstance(ep, dict) or not ep:
        raise JobError('the claim carried no episode.json')
    d = repo / 'episodes' / slug
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for name, data in (('episode.json', ep), ('ledger.json', job.get('ledger'))):
        if data is None:
            continue
        path = d / name
        old = read_json(path)
        if old is not None and old != data:
            arch = repo / 'episodes/_archive/macworker' / slug / stamp
            arch.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, arch / name)
        write_json(path, data)
    return d


def keep(src: Path, workdir: Path, name: str) -> Path:
    """A copy of a finished output inside the job folder (a hard link: no extra disk), so a later re-render of
    the same slug cannot change what is still waiting to upload."""
    dst = workdir / 'files' / safe_name(name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def preview(src: Path, dst: Path, width: int = PREVIEW_W) -> Path:
    """A 720 px wide JPEG; transparent sprites are laid on the cozy cream background."""
    from PIL import Image
    with Image.open(src) as f:
        im = f.copy()
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGBA', im.size, CREAM + (255,))
        bg.alpha_composite(im)
        im = bg
    im = im.convert('RGB')
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, 'JPEG', quality=85, optimize=True)
    return dst


def _bridge(ctx: Ctx, *args, on_line=None) -> dict | None:
    rc, out = ctx.run([ctx.repo / '.venv/bin/python', Path(bridge.__file__).resolve(), *args], on_line=on_line)
    ans = bridge.parse(out)
    if rc != 0 or ans is None:
        tail = '\n'.join(out.splitlines()[-15:])
        raise JobError(f'pipeline bridge {args[0]} failed (exit {rc}):\n{tail}')
    return ans


# ---- paint -----------------------------------------------------------------------------------------
def paint(ctx: Ctx) -> Outcome:
    slug = ctx.slug
    repo = ctx.repo
    redo = ctx.payload.get('redo') or {}
    if not isinstance(redo, dict):
        raise JobError('payload.redo must be {key: note}')
    for k, note in redo.items():
        check_key(k)
        if not isinstance(note, str) or not note.strip():
            raise JobError(f'redo {k}: the note is empty')
    write_episode(repo, ctx.job)
    ctx.report('prepare', 2, 'episode written; checking what to paint')
    before = dict(scene=read_json(repo / INSERTS_LIB, {}) or {}, prop=read_json(repo / PROPS_LIB, {}) or {})
    info = _bridge(ctx, 'needed', slug)
    uses = dict(scene=info.get('uses_inserts') or {}, prop=info.get('uses_props') or {})
    need = dict(scene=info.get('inserts') or {}, prop=info.get('props') or {})
    output, failed, made = '', {}, {}             # made: key -> kind

    if redo:
        notes_file = ctx.workdir / 'redo.json'
        write_json(notes_file, redo)
        pp = PaintProgress(2 * len(redo), 5, 90, 'repainting')
        ans = _bridge(ctx, 'redo', slug, notes_file, on_line=lambda l: _emit(ctx, pp.update(l)))
        for k, m in (ans.get('made') or {}).items():
            made[k] = m.get('kind', 'scene')
        failed.update(ans.get('failed') or {})
    else:
        steps = [('scene', 'inserts', 5, 50, 'painting scenes'), ('prop', 'props', 50, 90, 'painting props')]
        for kind, cmd, lo, hi, what in steps:
            n = len(need[kind])
            ctx.report('paint', lo, f'{what}: {n} to make' if n else f'{what}: nothing new')
            if not n:
                continue
            pp = PaintProgress(2 * n, lo, hi, what)
            rc, out = ctx.run([repo / '.venv/bin/stick', cmd, slug, '--generate'], on_line=lambda l, pp=pp: _emit(ctx, pp.update(l)))
            output += out + '\n'
            if rc not in (0, EXIT_GATE):
                tail = '\n'.join(out.splitlines()[-15:])
                raise JobError(f'stick {cmd} {slug} --generate failed (exit {rc}):\n{tail}')
        after = dict(scene=read_json(repo / INSERTS_LIB, {}) or {}, prop=read_json(repo / PROPS_LIB, {}) or {})
        for kind in ('scene', 'prop'):
            for k in after[kind]:
                if k not in before[kind]:
                    made[k] = kind
        why = failed_reasons(output)
        for kind in ('scene', 'prop'):
            for k in need[kind]:
                if k not in after[kind]:
                    failed[k] = why.get(k, 'not made (failed QA on both seeds)')

    ctx.report('previews', 92, f'{len(made)} new, {len(failed)} failed: making previews')
    libs = dict(scene=read_json(repo / INSERTS_LIB, {}) or {}, prop=read_json(repo / PROPS_LIB, {}) or {})
    new, files, names = [], [], set()
    for key, kind in made.items():
        entry = libs[kind].get(key) or {}
        rel = entry.get('file') if kind == 'scene' else entry.get('sprite')
        src = repo / rel if rel else repo / ('styles/cozy/inserts' if kind == 'scene' else 'styles/cozy/props') / f'{key}.png'
        name = safe_name(f'{key}.jpg')
        if name in names:                         # a prop and a scene with the same key
            name = safe_name(f'{kind}-{key}.jpg')
        names.add(name)
        try:
            path = preview(src, ctx.workdir / 'files' / name)
        except Exception as e:
            failed[key] = f'made, but no preview: {type(e).__name__}: {e}'
            continue
        beats = (uses[kind].get(key) or need[kind].get(key) or {}).get('beats', [])
        new.append(dict(key=key, kind=kind, name=name, scene=entry.get('scene') if kind == 'scene' else entry.get('name', key),
                        beats=beats, **({'placeholder': True} if entry.get('placeholder') else {})))
        files.append(dict(path=str(path), name=name, kind='scene_preview'))
    result = dict(new=new, failed=[dict(key=k, why=w) for k, w in failed.items()])
    held = [n['key'] for n in new if n.get('placeholder')]
    if held:                                  # pipeline/paintsafe.py: would not paint after 2 seeds and 2 retries
        result['note'] = ('placeholders (would not paint after two seeds and two retries; repaint with a note): '
                          + ', '.join(held))
    if redo:
        stale = sorted({v for k, m in (ans.get('made') or {}).items() for v in m.get('stale_variants', [])})
        if stale:
            result['note'] = 'crossfade variants painted from the old scene: ' + ', '.join(stale)
    return Outcome(result, files)


def _emit(ctx: Ctx, update):
    if update:
        ctx.report(*update)


# ---- render ----------------------------------------------------------------------------------------
RENDER_FILES = (('{slug}.mp4', 'mp4'), ('{slug}.srt', 'srt'), ('qa.json', 'qa'), ('contact_sheet.jpg', 'contact_sheet'))


def qa_summary(qa: dict) -> dict:
    """{check: bool, ..., all: bool}: qa.json['summary'] when present, else its top-level booleans."""
    s = qa.get('summary')
    if isinstance(s, dict) and s:
        out = dict(s)
    else:
        out = {k: v for k, v in qa.items() if isinstance(v, bool) and k != 'all'}
        if not out:                               # e.g. {"duration": {"pass_": true}, ...}
            out = {k: bool(v['pass_']) for k, v in qa.items() if isinstance(v, dict) and isinstance(v.get('pass_'), bool)}
    if not isinstance(out.get('all'), bool):
        out['all'] = qa['all'] if isinstance(qa.get('all'), bool) else (bool(out) and all(v for v in out.values() if isinstance(v, bool)))
    return out


def qa_pass(qa: dict) -> bool:
    """The contract's qa.json['all']; the pipeline writes it as qa.json['summary']['all']."""
    return bool(qa_summary(qa)['all'])


def render(ctx: Ctx) -> Outcome:
    slug = ctx.slug
    repo = ctx.repo
    write_episode(repo, ctx.job)
    started = time.time()
    rp = RenderProgress()
    ctx.report(rp.stage, rp.pct, rp.message)
    cmd = [repo / '.venv/bin/python', 'scripts/motion_test/episode_compose.py', slug, '--workers', str(ctx.render_workers)]
    rc, out = ctx.run(cmd, on_line=lambda l: _emit(ctx, rp.update(l)))
    if rc != 0:
        tail = '\n'.join(out.splitlines()[-20:])
        raise JobError(f'render failed (exit {rc}):\n{tail}')
    out_dir = repo / 'renders/sample' / slug
    files = []
    for pattern, kind in RENDER_FILES:
        name = pattern.format(slug=slug)
        src = out_dir / name
        if not src.is_file():
            raise JobError(f'render finished but {src.relative_to(repo)} is missing')
        if src.stat().st_mtime < started - 5:
            raise JobError(f'render finished but {name} is older than this job (stale output)')
        files.append(dict(path=str(keep(src, ctx.workdir, name)), name=safe_name(name), kind=kind))
    qa = read_json(out_dir / 'qa.json', {}) or {}
    duration = rp.duration
    if duration is None:
        duration = (read_json(out_dir / 'timeline.json', {}) or {}).get('duration')
    ctx.report('upload', 96, 'uploading the video')
    summary = qa_summary(qa)
    result = dict(duration=duration, qa_pass=bool(summary['all']), qa_summary=summary,
                  repairs=qa.get('repairs') or [], still_wrong=qa.get('still_wrong') or [])
    # the self-repair loop may have lengthened the narration (pipeline/repair.py): the server keeps that script
    sent = (ctx.job.get('episode') or {}).get('beats') or []
    now = read_json(repo / 'episodes' / slug / 'episode.json', {}) or {}
    if sent and [b.get('narration') for b in now.get('beats') or []] != [b.get('narration') for b in sent]:
        result['episode'] = now
    return Outcome(result, files)


# ---- pitch / script (SCRIPT_PROVIDER=ollama) -----------------------------------------------------------
def ollama_version(url: str = DEFAULT_OLLAMA_URL, timeout: float = 5.0, transport=None) -> str:
    """Ollama's version from GET <url>/api/version, or JobError(OLLAMA_DOWN) when nothing answers."""
    import httpx
    try:
        with httpx.Client(timeout=timeout, transport=transport) as c:
            r = c.get(url.rstrip('/') + '/api/version')
            r.raise_for_status()
            return str((r.json() or {}).get('version') or 'unknown')
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        raise JobError(f'{OLLAMA_DOWN} (no answer at {url}: {type(e).__name__}). Press Retry once it runs.') from None


def _cache_name(name) -> bool:
    return isinstance(name, str) and bool(NAME_OK.match(name)) and not name.startswith('.') and len(name) <= 128


def llm(ctx: Ctx) -> Outcome:
    """Run story.pitch / script.generate here with the local model, in a scratch episodes folder inside the job
    folder: the episode, its ledger and cache/ from the claim, plus the server's other episodes as stubs (the
    storyline rotation reads them). Result: the updated episode.json and ledger.json; the new or changed cache/
    files go up first (kind `cache`), so a retry or the next step reuses them."""
    kind, slug = ctx.job.get('kind'), ctx.slug
    if kind not in LLM_KINDS:
        raise JobError(f'not a pitch/script job: {kind!r}')
    what = LLM_WHAT[kind]
    ep = ctx.job.get('episode')
    if not isinstance(ep, dict) or not ep:
        raise JobError('the claim carried no episode.json')
    ctx.report('ollama', 2, 'Checking Ollama on the Mac')
    version = (ctx.ollama or ollama_version)()
    edir = ctx.workdir / 'episodes'
    if edir.exists():
        shutil.rmtree(edir)                       # a fresh copy for every run of this job
    d = edir / slug
    write_json(d / 'episode.json', ep)
    ledger = ctx.job.get('ledger')
    if isinstance(ledger, dict) and isinstance(ledger.get('entries'), list):
        write_json(d / 'ledger.json', ledger)
    (d / 'cache').mkdir(parents=True, exist_ok=True)
    received = {}
    for name, text in (ctx.job.get('cache') or {}).items():
        if _cache_name(name) and isinstance(text, str):
            (d / 'cache' / name).write_text(text, encoding='utf-8')
            received[name] = text
    for other, data in (ctx.job.get('others') or {}).items():
        try:
            check_slug(other)
        except ValueError:
            continue
        if other != slug and isinstance(data, dict):
            write_json(edir / other / 'episode.json', data)
    ctx.report(kind, 10, ('Researching the topic and pitching three storylines' if kind == 'pitch' else
                          'Writing the script') + f' (Ollama {version} on the Mac, free)')
    rc, out = ctx.run([ctx.repo / '.venv/bin/python', Path(bridge.__file__).resolve(), kind, slug, edir])
    ans = bridge.parse(out)
    if isinstance(ans, dict) and ans.get('error'):
        raise JobError(f'{what} failed: {ans["error"]} (press Retry to run it again)')
    if rc != 0 or not isinstance(ans, dict):
        tail = '\n'.join(out.splitlines()[-15:])
        raise JobError(f'{what} failed on the Mac (exit {rc}):\n{tail}')
    episode = read_json(d / 'episode.json')
    if not isinstance(episode, dict):
        raise JobError(f'{what} finished but episode.json is unreadable')
    files = []
    for p in sorted((d / 'cache').iterdir()):
        if not p.is_file() or not _cache_name(p.name):
            continue
        try:
            if received.get(p.name) == p.read_text(encoding='utf-8'):
                continue                          # the server has it already
        except (OSError, UnicodeDecodeError):
            continue
        files.append(dict(path=str(p), name=p.name, kind='cache'))
    ctx.report('upload', 95, f'{what} done: sending it back')
    return Outcome(dict(notes=[str(n) for n in (ans.get('notes') or [])], episode=episode,
                        ledger=read_json(d / 'ledger.json'), provider='ollama', ollama_version=version), files)


HANDLERS = dict(paint=paint, render=render, pitch=llm, script=llm)
