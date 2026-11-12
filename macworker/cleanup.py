"""After each job (user 2026-11-11): stale per-beat clips go, and the Ollama model leaves memory.

The renderer writes every beat as its own clip, renders/sample/<slug>/shots/<beat>-<digest>.{mp4,captions.json,
text.json}, then lists the clips of the finished video in renders/sample/<slug>/shots.txt. The clips that list names
are the render cache (edit one beat and only that beat is drawn again), so they stay. A clip it no longer names is
an intermediate from an earlier version of that beat: after a finished render those are deleted, and only those
older than the listing (a render still running writes its new clips before its listing).

Never touched: the final video, its subtitles, QA, contact sheet, timeline and audio, the clips the listing names,
episodes/, .env, keys, and anything outside renders/sample/<slug>/shots/. Every deletion is logged, with the space
freed, to <repo>/logs/cleanup.log.

The model is unloaded only when Ollama reports it loaded (GET /api/ps): a keep_alive=0 request for a model that is
not in memory would load it first.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .util import check_slug

log = logging.getLogger('stick-worker')

SHOT = re.compile(r'^(b\d{2}-[0-9a-f]{8,64})\.(mp4|captions\.json|text\.json)$')
LISTED = re.compile(r"^\s*file\s+'(.+)'\s*$")


def current_shots(out_dir: Path) -> set | None:
    """The clip stems (b01-<digest>) the finished video is made of, from shots.txt; None when there is no usable
    listing (then nothing is deleted)."""
    listing = out_dir / 'shots.txt'
    if not listing.is_file():
        return None
    stems = set()
    for line in listing.read_text(errors='replace').splitlines():
        m = LISTED.match(line)
        if m:
            s = SHOT.match(Path(m.group(1)).name)   # by name: the listing may hold the repo's old absolute path
            if s:
                stems.add(s.group(1))
    return stems or None


def stale_shots(out_dir: Path) -> list:
    """Clip files in out_dir/shots that the current listing does not name and that are older than it."""
    keep = current_shots(out_dir)
    shots = out_dir / 'shots'
    if not keep or not shots.is_dir() or shots.is_symlink():
        return []
    listed_at = (out_dir / 'shots.txt').stat().st_mtime
    out = []
    for p in sorted(shots.iterdir()):
        m = SHOT.match(p.name)
        if (m and m.group(1) not in keep and p.is_file() and not p.is_symlink()
                and p.stat().st_mtime < listed_at):
            out.append(p)
    return out


def remove_stale_shots(repo: Path, slug: str) -> tuple:
    """Delete the stale clips of one episode's render. Returns (files removed, bytes freed)."""
    out_dir = repo / 'renders' / 'sample' / check_slug(slug)
    removed = freed = 0
    for p in stale_shots(out_dir):
        try:
            size = p.stat().st_size
            p.unlink()
        except OSError as e:
            log.warning('cleanup: could not remove %s: %s', p, e)
            continue
        removed, freed = removed + 1, freed + size
    return removed, freed


def loaded_models(ollama_url: str, timeout: float = 3.0, transport=None) -> list:
    """Names of the models Ollama has in memory, [] when it does not answer."""
    import httpx
    try:
        with httpx.Client(timeout=timeout, transport=transport) as c:
            r = c.get(ollama_url.rstrip('/') + '/api/ps')
            r.raise_for_status()
            return [str(m.get('name') or m.get('model') or '') for m in (r.json() or {}).get('models') or []]
    except (httpx.HTTPError, ValueError, AttributeError):
        return []


def unload_model(ollama_url: str, model: str, timeout: float = 10.0, transport=None) -> bool:
    """keep_alive=0 for `model` when it is loaded. True when it was loaded and Ollama confirmed the unload."""
    import httpx
    if model not in loaded_models(ollama_url, transport=transport):
        return False
    try:
        with httpx.Client(timeout=timeout, transport=transport) as c:
            r = c.post(ollama_url.rstrip('/') + '/api/generate', json=dict(model=model, keep_alive=0))
            return r.status_code == 200
    except httpx.HTTPError:
        return False


def write_log(repo: Path, line: str) -> None:
    try:
        d = repo / 'logs'
        d.mkdir(exist_ok=True)
        with open(d / 'cleanup.log', 'a') as f:
            f.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")} {line}\n')
    except OSError as e:
        log.warning('cleanup: could not write logs/cleanup.log: %s', e)


def after_job(repo: Path, job: dict, status: str, ollama_url: str, model: str, transport=None) -> dict:
    """The post-job cleanup. Never raises: a cleanup problem must not fail or delay a job's report."""
    kind, slug = job.get('kind'), job.get('slug')
    out = dict(removed=0, freed_bytes=0, unloaded=False)
    try:
        if status == 'finished' and kind == 'render' and slug:
            out['removed'], out['freed_bytes'] = remove_stale_shots(repo, slug)
        out['unloaded'] = unload_model(ollama_url, model, transport=transport)
    except Exception as e:                       # noqa: BLE001: best effort by design
        log.warning('cleanup after job %s failed: %s: %s', job.get('job_id'), type(e).__name__, e)
        out['error'] = f'{type(e).__name__}: {e}'
    mb = out['freed_bytes'] / 1e6
    line = (f'job {job.get("job_id")} {kind} {slug} ({status}): removed {out["removed"]} stale clip file(s), '
            f'freed {mb:.1f} MB; {model} ' + ('unloaded' if out['unloaded'] else 'not in memory'))
    log.info('cleanup: %s', line)
    if out['removed'] or out['unloaded'] or out.get('error'):
        write_log(repo, line + (f'; error {out["error"]}' if out.get('error') else ''))
    return out
