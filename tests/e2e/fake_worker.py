"""A stand-in for the Mac worker (docs/WEBAPP.md, "Worker API"), for the end-to-end tests. No FLUX, no voice, no
real render: paint jobs upload two Pillow preview JPEGs; render jobs make a 2 s ffmpeg `testsrc` MP4 with a sine tone,
an SRT, a qa.json and a contact sheet.

    python tests/e2e/fake_worker.py --server http://127.0.0.1:8000 [--once] [--fail-render] [--crash-after-claim]

The bearer token comes from `--token` or the environment (`STICK_WORKER_TOKEN`); it is never printed.

Options:
  --once               handle at most one job, then exit (with --wait N, poll up to N s for one; default: one claim)
  --fail-render        render jobs write a failing qa.json and report qa_pass false (episode -> done_qa_failed)
  --fail-kind KIND     jobs of KIND (paint or render) call /fail instead of /done (episode -> failed)
  --crash-after-claim  claim one job, then exit at once without progress or done (to test lease expiry)
  --journal PATH       append one JSON line per claimed job (id, kind, slug, payload, outcome) for the tests

Exit codes: 0 normal, 2 bad usage or refused token, 17 crashed on purpose (--crash-after-claim).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

VERSION = 'fake-e2e-1'
FFMPEG = os.getenv('FFMPEG', '/opt/homebrew/bin/ffmpeg')
FFPROBE = os.getenv('FFPROBE', '/opt/homebrew/bin/ffprobe')
NAME_OK = re.compile(r'^[a-z0-9._-]+$')
CRASH_CODE = 17


def log(*parts):
    print('[fake-worker]', *parts, file=sys.stderr, flush=True)


def safe_name(text: str) -> str:
    """A file name the worker API accepts: [a-z0-9._-]+ only."""
    out = re.sub(r'[^a-z0-9._-]+', '-', str(text).lower()).strip('-.') or 'item'
    return out[:80]


# ---------------------------------------------------------------------------------------------------- canned media

def preview_jpeg(path: Path, label: str, colour=(214, 190, 150)):
    """A 720px-wide portrait preview, like the Mac's scene previews."""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (720, 1280), colour)
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 680, 1240), outline=(60, 40, 30), width=8)
    d.ellipse((300, 380, 420, 500), outline=(20, 20, 20), width=8)            # a stick figure, so it looks the part
    d.line((360, 500, 360, 760), fill=(20, 20, 20), width=8)
    d.line((360, 560, 260, 660), fill=(20, 20, 20), width=8)
    d.line((360, 560, 460, 660), fill=(20, 20, 20), width=8)
    d.line((360, 760, 280, 920), fill=(20, 20, 20), width=8)
    d.line((360, 760, 440, 920), fill=(20, 20, 20), width=8)
    d.text((80, 1100), f'FAKE PREVIEW: {label}'[:60], fill=(20, 20, 20))
    img.save(path, 'JPEG', quality=80)


def contact_sheet(path: Path, slug: str, cols=5, rows=2):
    from PIL import Image, ImageDraw
    w, h = 216, 384
    sheet = Image.new('RGB', (w * cols, h * rows), (250, 248, 240))
    d = ImageDraw.Draw(sheet)
    for i in range(cols * rows):
        x, y = (i % cols) * w, (i // cols) * h
        d.rectangle((x + 6, y + 6, x + w - 6, y + h - 6), fill=(200 + i * 5 % 55, 180, 150), outline=(40, 40, 40), width=3)
        d.text((x + 16, y + 16), f'b{i + 1:02d}', fill=(20, 20, 20))
    d.text((12, h * rows - 20), f'fake contact sheet: {slug}'[:80], fill=(20, 20, 20))
    sheet.save(path, 'JPEG', quality=80)


def test_mp4(path: Path, seconds=2.0, size='1080x1920', fps=10):
    cmd = [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
           '-f', 'lavfi', '-i', f'testsrc=size={size}:rate={fps}:duration={seconds}',
           '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=44100:duration={seconds}',
           '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '64k', '-shortest', '-movflags', '+faststart', str(path)]
    subprocess.run(cmd, check=True, timeout=60, capture_output=True)


def probe_duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         check=True, timeout=30, capture_output=True, text=True).stdout.strip()
    return round(float(out), 2)


def srt_text(lines):
    def ts(t):
        ms = int(round(t * 1000))
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    blocks = []
    for i, (a, b, text) in enumerate(lines, 1):
        blocks.append(f'{i}\n{ts(a)} --> {ts(b)}\n{text}\n')
    return '\n'.join(blocks)


QA_CHECKS = ('duration', 'freeze', 'captions', 'words', 'text', 'fill', 'host', 'loudness', 'characters', 'library',
             'subject', 'character', 'hook', 'layout')


def qa_report(slug: str, duration: float, passing: bool) -> dict:
    """The shape of pipeline.qa.run's qa.json (each check with pass_, then summary{check: bool, all}), plus a
    top-level `all` as the contract reads it (qa.json['all'])."""
    report = {k: {'pass_': True} for k in QA_CHECKS}
    report['duration'] = {'seconds': duration, 'target': duration, 'pass_': True}
    report['words'] = {'missing': [], 'pass_': True}
    if not passing:
        report['words'] = {'missing': ['leaked', 'jar'], 'pass_': False}
        report['loudness'] = {'integrated_lufs': -21.0, 'pass_': False}
    report['video'] = f'outputs/{slug}/{slug}.mp4'
    report['voice'] = 'fake'
    report['fake'] = True
    report['summary'] = {k: v['pass_'] for k, v in report.items() if isinstance(v, dict) and 'pass_' in v}
    report['summary']['all'] = all(report['summary'].values())
    report['all'] = report['summary']['all']
    return report


# ---------------------------------------------------------------------------------------------------- the worker

class Refused(Exception):
    """The server refused the token (401/403)."""


class FakeWorker:
    def __init__(self, server, token, *, fail_render=False, fail_kind=None, journal=None, poll=1.0, timeout=30.0):
        self.server = server.rstrip('/')
        self.http = httpx.Client(base_url=self.server, timeout=timeout,
                                 headers={'Authorization': f'Bearer {token}', 'User-Agent': f'stick-fake-worker/{VERSION}'})
        self.fail_render = fail_render
        self.fail_kind = fail_kind
        self.journal = Path(journal) if journal else None
        self.poll = poll
        self.host = 'fake-worker.local'

    # -- transport
    def post(self, path, **kw):
        r = self.http.post(path, **kw)
        if r.status_code in (401, 403):
            raise Refused(f'{path}: {r.status_code}')
        return r

    def heartbeat(self, busy=None):
        body = {'host': self.host, 'version': VERSION}
        if busy is not None:
            body['busy_job_id'] = busy
        r = self.post('/api/worker/heartbeat', json=body)
        r.raise_for_status()
        return r.json()

    def claim(self):
        r = self.post('/api/worker/claim', json={'kinds': ['paint', 'render']})
        if r.status_code == 204 or not r.content.strip():
            return None
        r.raise_for_status()
        job = r.json()
        return job or None

    def progress(self, job_id, stage, pct, message=''):
        r = self.post(f'/api/worker/jobs/{job_id}/progress', json={'stage': stage, 'pct': pct, 'message': message})
        r.raise_for_status()

    def upload(self, job_id, path: Path, name: str, kind: str):
        assert NAME_OK.match(name), name
        ctype = {'.jpg': 'image/jpeg', '.mp4': 'video/mp4', '.srt': 'application/x-subrip', '.json': 'application/json'}
        with open(path, 'rb') as fh:
            r = self.post(f'/api/worker/jobs/{job_id}/files', data={'name': name, 'kind': kind},
                          files={'file': (name, fh, ctype.get(path.suffix, 'application/octet-stream'))})
        r.raise_for_status()

    def done(self, job_id, result):
        r = self.post(f'/api/worker/jobs/{job_id}/done', json={'result': result})
        r.raise_for_status()

    def fail(self, job_id, error):
        r = self.post(f'/api/worker/jobs/{job_id}/fail', json={'error': error})
        r.raise_for_status()

    def note(self, job, outcome, **extra):
        if not self.journal:
            return
        row = dict(job_id=job.get('job_id'), kind=job.get('kind'), slug=job.get('slug'), payload=job.get('payload'),
                   has_episode=isinstance(job.get('episode'), dict), has_ledger=isinstance(job.get('ledger'), dict),
                   outcome=outcome, at=time.time(), **extra)
        with open(self.journal, 'a') as fh:
            fh.write(json.dumps(row) + '\n')

    # -- jobs
    def paint_keys(self, job):
        """Keys to 'paint': the redo keys when asked, else the episode's first two insert scenes, else two canned."""
        redo = (job.get('payload') or {}).get('redo') or {}
        if redo:
            return [(k, 'scene', note) for k, note in redo.items()]
        found = []
        for b in (job.get('episode') or {}).get('beats') or []:
            ins = ((b.get('shot') or {}).get('insert')) or {}
            key = ins.get('key') if isinstance(ins, dict) else None
            if key and key not in [k for k, _, _ in found]:
                found.append((key, 'scene', ins.get('scene') or ''))
            if len(found) == 2:
                break
        if len(found) < 2:
            found += [('fake-scene-1', 'scene', 'A painted café table with a jar of coins'),
                      ('fake-prop-1', 'prop', 'A glass jar with a crack')][len(found):]
        return found

    def beats_for(self, job, key):
        out = []
        for b in (job.get('episode') or {}).get('beats') or []:
            ins = ((b.get('shot') or {}).get('insert')) or {}
            if isinstance(ins, dict) and ins.get('key') == key and b.get('id'):
                out.append(b['id'])
        return out or ['b01']

    def do_paint(self, job, tmp: Path):
        jid = job['job_id']
        self.progress(jid, 'paint', 5, 'fake paint: starting')
        new = []
        keys = self.paint_keys(job)
        for i, (key, kind, scene) in enumerate(keys):
            name = safe_name(key) + '.jpg'
            path = tmp / name
            preview_jpeg(path, key, colour=(214, 190 - 30 * i, 150 + 20 * i))
            self.upload(jid, path, name, 'scene_preview')
            self.progress(jid, 'paint', int(10 + 80 * (i + 1) / len(keys)), f'fake paint: {key}')
            new.append({'key': key, 'kind': kind, 'name': name, 'scene': scene or f'fake scene for {key}',
                        'beats': self.beats_for(job, key)})
        return {'new': new, 'failed': []}

    def do_render(self, job, tmp: Path):
        jid, slug = job['job_id'], job['slug']
        self.progress(jid, 'voice', 5, 'fake voice')
        mp4, srt, qa, sheet = tmp / f'{slug}.mp4', tmp / f'{slug}.srt', tmp / 'qa.json', tmp / 'contact_sheet.jpg'
        for n in range(1, 4):
            self.progress(jid, 'compose', 10 + 20 * n, f'b{n:02d}: frames 20 0.1 s')
        test_mp4(mp4)
        duration = probe_duration(mp4)
        srt.write_text(srt_text([(0.0, 1.0, "Your salary didn't shrink."), (1.0, duration, 'It leaked.')]))
        self.progress(jid, 'qa', 85, 'fake QA')
        report = qa_report(slug, duration, passing=not self.fail_render)
        qa.write_text(json.dumps(report, indent=1))
        contact_sheet(sheet, slug)
        for path, kind in ((mp4, 'mp4'), (srt, 'srt'), (qa, 'qa'), (sheet, 'contact_sheet')):
            self.upload(jid, path, path.name, kind)
        self.progress(jid, 'upload', 100, 'fake render: uploaded')
        return {'duration': duration, 'qa_pass': bool(report['all']), 'qa_summary': report['summary']}

    def handle(self, job):
        jid, kind = job['job_id'], job['kind']
        log(f'claimed job {jid} ({kind}) for {job.get("slug")}')
        self.heartbeat(busy=jid)
        if self.fail_kind == kind:
            self.progress(jid, kind, 10, 'fake: about to fail on purpose')
            self.fail(jid, f'fake worker: {kind} failed on purpose (--fail-kind)')
            self.note(job, 'fail')
            return 'fail'
        with tempfile.TemporaryDirectory(prefix='fake-worker-') as d:
            try:
                if kind == 'paint':
                    result = self.do_paint(job, Path(d))
                elif kind == 'render':
                    result = self.do_render(job, Path(d))
                else:
                    raise ValueError(f'unknown job kind {kind!r}')
            except Refused:
                raise
            except Exception as exc:  # report it like the Mac would, then carry on
                log(f'job {jid} failed: {exc!r}')
                self.fail(jid, f'fake worker error: {exc}')
                self.note(job, 'fail', error=str(exc))
                return 'fail'
        self.done(jid, result)
        self.note(job, 'done', result=result)
        log(f'job {jid} done')
        return 'done'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--server', default=os.getenv('STICK_SERVER', 'http://127.0.0.1:8000'))
    ap.add_argument('--token', default=None, help='bearer token (default: $STICK_WORKER_TOKEN)')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--wait', type=float, default=0.0, help='with --once: poll up to this many seconds for a job')
    ap.add_argument('--poll', type=float, default=0.5, help='seconds between claims while idle')
    ap.add_argument('--fail-render', action='store_true')
    ap.add_argument('--fail-kind', choices=('paint', 'render'))
    ap.add_argument('--crash-after-claim', action='store_true')
    ap.add_argument('--journal')
    args = ap.parse_args(argv)
    token = args.token or os.getenv('STICK_WORKER_TOKEN', '')
    if not token:
        log('no token: pass --token or set STICK_WORKER_TOKEN')
        return 2
    w = FakeWorker(args.server, token, fail_render=args.fail_render, fail_kind=args.fail_kind, journal=args.journal,
                   poll=args.poll)
    deadline = time.monotonic() + args.wait
    try:
        while True:
            try:
                w.heartbeat()
                job = w.claim()
            except Refused:
                raise
            except httpx.HTTPError as exc:
                log(f'server unreachable: {exc!r}')
                job = None
            if job:
                if args.crash_after_claim:
                    w.note(job, 'crash')
                    log(f'claimed job {job.get("job_id")} and crashing on purpose (--crash-after-claim)')
                    os._exit(CRASH_CODE)
                w.handle(job)
                if args.once:
                    return 0
            elif args.once and time.monotonic() >= deadline:
                log('no job queued')
                return 0
            time.sleep(args.poll)
    except Refused as exc:
        log(f'token refused ({exc})')
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
