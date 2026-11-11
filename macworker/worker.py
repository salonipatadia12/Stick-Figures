"""The worker loop: heartbeat, claim one job, run it, upload, done/fail; resume unreported results on start.

Every finished job is written to <state_dir>/jobs/<id>/outcome.json before anything is sent, with the files it
will upload; uploads are recorded one by one. A network error never loses work: the worker backs off and sends
what is left (on the next loop or the next start). Only after `done`/`fail` is acknowledged is the job reported.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import VERSION, config, proc
from .client import AuthError, Client, NetworkError, Rejected
from .handlers import CLAIM_KINDS, HANDLERS, LLM_KINDS, OLLAMA_DOWN, Ctx, JobError, ollama_version
from .util import Backoff, read_json, safe_name, write_json

log = logging.getLogger('stick-worker')
EXTRA_PATH = ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin')
KEEP_REPORTED = 30


class Stop(Exception):
    """Raised inside a running job when the worker is asked to stop (SIGTERM from launchd)."""


class Worker:
    def __init__(self, cfg: config.Config, client: Client, handlers: dict | None = None, sleep=time.sleep,
                 awake=proc.Awake, runner=proc.run, max_report_tries: int | None = None, backoff: Backoff | None = None):
        self.cfg, self.client = cfg, client
        self.handlers = HANDLERS if handlers is None else handlers
        self.sleep, self.awake, self.runner = sleep, awake, runner
        self.max_report_tries = max_report_tries
        self.backoff = backoff or Backoff()
        self.lock = threading.Lock()                 # one request at a time on the shared client
        self.jobs_dir = cfg.state_dir / 'jobs'
        self.busy = None
        self._last_progress = (None, 0.0)            # (body, time sent)
        self._last_pull = 0.0

    # ---- server calls ------------------------------------------------------------------------------
    def call(self, name, *args, **kw):
        with self.lock:
            return getattr(self.client, name)(*args, **kw)

    def progress(self, job_id, stage, pct, message=''):
        body, at = self._last_progress
        now = time.time()
        new = (stage, round(float(pct)), message)
        if body and body[:2] == new[:2] and now - at < 60:          # same stage and percent: no need to send
            self._last_progress = ((stage, pct, message), at)
            return
        self._last_progress = ((stage, pct, message), now)
        log.info('progress %s: %s %.0f%% %s', job_id, stage, float(pct), message)
        try:
            self.call('progress', job_id, stage, pct, message)
        except (NetworkError, Rejected, AuthError) as e:            # progress is best effort
            log.warning('progress not sent: %s', e)

    def _keepalive(self, job_id, stop: threading.Event):
        """While a job runs: a heartbeat every poll interval (busy) and the last progress again every 2 minutes,
        which extends the 15-minute lease during long FLUX or render steps."""
        while not stop.wait(self.cfg.poll_s):
            try:
                self.call('heartbeat', self.cfg.host, VERSION, busy_job_id=job_id)
            except Exception as e:
                log.warning('heartbeat (busy) failed: %s', e)
            body, at = self._last_progress
            if body and time.time() - at > 120:
                self._last_progress = (None, 0.0)
                self.progress(job_id, *body)

    # ---- one job -----------------------------------------------------------------------------------
    def job_dir(self, job_id) -> Path:
        return self.jobs_dir / safe_name(str(job_id), default='job')

    def child_env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ('STICK_WORKER_TOKEN',)}
        path = env.get('PATH', '').split(':')
        env['PATH'] = ':'.join([p for p in path if p] + [p for p in EXTRA_PATH if p not in path])
        env.update(PYTHONUNBUFFERED='1', STICK_REPO=str(self.cfg.repo), PYTHONPATH=str(self.cfg.repo),
                   STICK_EPISODES_DIR=str(self.cfg.repo / 'episodes'))
        return env

    def process(self, job: dict) -> str:
        """Run a claimed job and report it. Returns the local status ('reported', 'pending', 'rejected')."""
        job_id, kind = job.get('job_id'), job.get('kind')
        jdir = self.job_dir(job_id)
        jdir.mkdir(parents=True, exist_ok=True)
        write_json(jdir / 'job.json', dict(job_id=job_id, kind=kind, slug=job.get('slug'), payload=job.get('payload'),
                                           claimed_at=time.strftime('%Y-%m-%dT%H:%M:%S'), status='running'))
        log.info('job %s: %s %s', job_id, kind, job.get('slug'))
        self.busy, self._last_progress = job_id, (None, 0.0)
        stop = threading.Event()
        ka = threading.Thread(target=self._keepalive, args=(job_id, stop), daemon=True)
        try:
            with self.awake():
                ka.start()
                outcome = None
                try:
                    handler = self.handlers.get(kind)
                    if handler is None:
                        raise JobError(f'this worker does not run {kind!r} jobs')
                    ctx = Ctx(repo=self.cfg.repo, job=job, workdir=jdir,
                              report=lambda s, p, m='': self.progress(job_id, s, p, m),
                              run=lambda cmd, on_line=None: self.runner(cmd, cwd=self.cfg.repo, env=self.child_env(), on_line=on_line,
                                                                        log_path=jdir / 'output.log', timeout=self.cfg.job_timeout_s),
                              render_workers=self.cfg.workers, ollama=lambda: ollama_version(self.cfg.ollama_url))
                    outcome = handler(ctx)
                    state = dict(job_id=job_id, kind=kind, status='finished', result=outcome.result, files=outcome.files, uploaded=[])
                    log.info('job %s finished: %d file(s) to upload', job_id, len(outcome.files))
                except JobError as e:
                    state = dict(job_id=job_id, kind=kind, status='failed', error=str(e))
                    log.error('job %s failed: %s', job_id, e)
                except (Stop, SystemExit, KeyboardInterrupt):
                    write_json(jdir / 'outcome.json', dict(job_id=job_id, kind=kind, status='abandoned',
                                                           note='worker stopped mid-job; the lease expires and the job is queued again'))
                    raise
                except Exception as e:
                    state = dict(job_id=job_id, kind=kind, status='failed',
                                 error=f'worker error: {type(e).__name__}: {e}\n' + ''.join(traceback.format_exc()[-1500:]))
                    log.exception('job %s crashed', job_id)
                write_json(jdir / 'outcome.json', state)
                return self.report(jdir)
        finally:
            stop.set()
            self.busy = None

    def report(self, jdir: Path) -> str:
        """Send what a job dir still owes the server: the uploads not yet acknowledged, then done (or fail)."""
        path = jdir / 'outcome.json'
        state = read_json(path) or {}
        job_id = state.get('job_id')
        tries = 0
        while state.get('status') in ('finished', 'failed'):
            try:
                if state['status'] == 'finished':
                    for f in state['files']:
                        if f['name'] in state['uploaded']:
                            continue
                        p = Path(f['path'])
                        if not p.is_file():
                            state = dict(state, status='failed', error=f'output {f["name"]} vanished before upload')
                            write_json(path, state)
                            break
                        log.info('upload %s: %s (%s, %.1f MB)', job_id, f['name'], f['kind'], p.stat().st_size / 1e6)
                        self.call('upload', job_id, p, f['name'], f['kind'])
                        state['uploaded'].append(f['name'])
                        write_json(path, state)
                    else:
                        self.call('done', job_id, state['result'])
                        state['status'] = 'reported'
                        log.info('job %s done', job_id)
                    continue
                self.call('fail', job_id, state.get('error', 'failed'))
                state['status'] = 'reported'
                log.info('job %s reported as failed', job_id)
            except Rejected as e:
                log.error('job %s: the server refused the report (%s); outputs kept in %s', job_id, e, jdir)
                state = dict(state, status='rejected', rejected=str(e))
            except (NetworkError, AuthError) as e:
                tries += 1
                if self.max_report_tries is not None and tries >= self.max_report_tries:
                    log.warning('job %s: %s; will retry later (outputs kept in %s)', job_id, e, jdir)
                    write_json(path, state)
                    return 'pending'
                delay = self.backoff.next() if isinstance(e, NetworkError) else 300
                log.warning('job %s: %s; retrying in %.0fs', job_id, e, delay)
                self.sleep(delay)
                continue
            finally:
                write_json(path, state)
        self.backoff.reset()
        return state.get('status', 'unknown')

    def resume_pending(self) -> int:
        """Report every job that finished but was not acknowledged (a crash, a network outage, a restart)."""
        n = 0
        if not self.jobs_dir.is_dir():
            return 0
        for jdir in sorted(self.jobs_dir.iterdir(), key=lambda p: p.stat().st_mtime):
            state = read_json(jdir / 'outcome.json')
            if state is None and (jdir / 'job.json').exists():
                job = read_json(jdir / 'job.json') or {}
                write_json(jdir / 'outcome.json', dict(job_id=job.get('job_id'), kind=job.get('kind'), status='abandoned',
                                                       note='the worker stopped mid-job; the lease expires and the job is queued again'))
                continue
            if state and state.get('status') in ('finished', 'failed'):
                log.info('resuming the report of job %s', state.get('job_id'))
                n += 1
                if self.report(jdir) == 'pending':
                    break
        self.prune()
        return n

    def prune(self):
        """Keep the last KEEP_REPORTED reported job folders (render outputs there are hard links: the originals in
        renders/ stay)."""
        if not self.jobs_dir.is_dir():
            return
        done = [d for d in self.jobs_dir.iterdir() if (read_json(d / 'outcome.json') or {}).get('status') == 'reported']
        done.sort(key=lambda p: p.stat().st_mtime)
        for d in done[:-KEEP_REPORTED]:
            shutil.rmtree(d, ignore_errors=True)

    # ---- the loop ----------------------------------------------------------------------------------
    def maybe_pull(self):
        """`git pull --ff-only` on pipeline-v2 while idle, at most every 10 minutes, only on a clean tree."""
        if not self.cfg.auto_pull or time.time() - self._last_pull < 600:
            return
        self._last_pull = time.time()
        git = lambda *a: subprocess.run(['git', '-C', str(self.cfg.repo), *a], capture_output=True, text=True, timeout=120)
        try:
            if git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip() != 'pipeline-v2':
                return log.info('auto-pull skipped: the checkout is not on pipeline-v2')
            if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
                return log.info('auto-pull skipped: the checkout has local changes')
            r = git('pull', '--ff-only', 'origin', 'pipeline-v2')
            log.info('auto-pull: %s', (r.stdout or r.stderr).strip().splitlines()[-1:] or 'ok')
        except Exception as e:
            log.warning('auto-pull failed: %s', e)

    def run(self, once: bool = False) -> int:
        self.resume_pending()
        net = Backoff(base=5, cap=300)
        while True:
            try:
                self.call('heartbeat', self.cfg.host, VERSION)
                job = self.call('claim', list(CLAIM_KINDS))
                net.reset()
            except NetworkError as e:
                if once:
                    log.error('server unreachable: %s', e)
                    return 1
                delay = net.next()
                log.warning('server unreachable (%s); retrying in %.0fs', e, delay)
                self.sleep(delay)
                continue
            except AuthError as e:
                log.error('%s', e)
                if once:
                    return 2
                self.sleep(300)
                continue
            except Rejected as e:
                log.error('claim refused: %s', e)
                if once:
                    return 1
                self.sleep(self.cfg.poll_s)
                continue
            if job:
                self.process(job)
                if once:
                    return 0
                self.resume_pending()                 # anything a network outage left behind
                continue
            if once:
                log.info('no job queued')
                return 0
            self.maybe_pull()
            self.sleep(self.cfg.poll_s)


# ---- entry point -----------------------------------------------------------------------------------
def setup_logging(cfg: config.Config, verbose: bool = False):
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    fh = logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if verbose or sys.stderr.isatty():
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


def dry_run(cfg: config.Config, client_factory=Client, ollama_check=None) -> int:
    """Check the setup without claiming anything: config, token, repo, Ollama, then /api/health and one heartbeat
    (whose `kinds` say whether this server hands pitch/script to the Mac, i.e. runs SCRIPT_PROVIDER=ollama)."""
    print(f'server   {cfg.server or "(not set)"}')
    print(f'repo     {cfg.repo}')
    print(f'python   {cfg.python} {"ok" if cfg.python.exists() else "MISSING"}')
    print(f'stick    {cfg.stick} {"ok" if cfg.stick.exists() else "MISSING"}')
    print(f'token    {"found" if cfg.token else "MISSING"} ({cfg.token_source or "Keychain"})')
    print(f'log      {cfg.log_file}')
    print(f'state    {cfg.state_dir}')
    try:
        version = (ollama_check or (lambda: ollama_version(cfg.ollama_url)))()
        ollama_problem = None
        print(f'ollama   {cfg.ollama_url} ok (version {version})')
    except JobError as e:
        ollama_problem = str(e)
        print(f'ollama   {cfg.ollama_url} NOT RUNNING')
    problems = cfg.problems()
    if not cfg.server or not cfg.token:
        for p in problems + ([ollama_problem] if ollama_problem else []):
            print('problem:', p)
        return 1
    c = client_factory(cfg.server, cfg.token)
    try:
        h = c.health()
        print(f'health   ok={h.get("ok")} db={h.get("db")} worker={h.get("worker")}')
        hb = c.heartbeat(cfg.host, VERSION)
        print(f'heartbeat ok={hb.get("ok")} queued={hb.get("queued")} (the token works)')
    except (NetworkError, AuthError, Rejected) as e:
        print('problem:', e)
        return 1
    finally:
        c.close()
    kinds = [k for k in (hb.get('kinds') or ('paint', 'render')) if k in CLAIM_KINDS]   # an older server: paint, render
    if ollama_problem:
        if any(k in LLM_KINDS for k in kinds):
            problems.append(ollama_problem)
        else:
            print(f'note: {OLLAMA_DOWN} (not needed now: this server runs research and scripts itself)')
    for p in problems:
        print('problem:', p)
    print(f'dry run: would claim one job at a time of kinds {", ".join(kinds)}; nothing was claimed')
    return 1 if problems else 0


def _on_term(signum, frame):
    """launchd stop / logout: leave the loop; a running job's own children are stopped by proc.run."""
    raise SystemExit(143)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m macworker', description='Stick Figures Mac worker (pitch, script, paint, render jobs)')
    ap.add_argument('--once', action='store_true', help='claim at most one job, run it, then exit')
    ap.add_argument('--dry-run', action='store_true', help='check config, token and server; claim nothing')
    ap.add_argument('--verbose', '-v', action='store_true', help='also log to stderr')
    args = ap.parse_args(argv)
    cfg = config.load()
    if args.dry_run:
        return dry_run(cfg)
    setup_logging(cfg, args.verbose)
    problems = cfg.problems()
    if problems:
        for p in problems:
            log.error('config: %s', p)
        return 2
    signal.signal(signal.SIGTERM, _on_term)
    log.info('stick worker %s on %s: server %s, repo %s', VERSION, cfg.host, cfg.server, cfg.repo)
    client = Client(cfg.server, cfg.token)
    try:
        return Worker(cfg, client).run(once=args.once)
    except (SystemExit, KeyboardInterrupt):
        log.info('stopped')
        return 0
    finally:
        client.close()
