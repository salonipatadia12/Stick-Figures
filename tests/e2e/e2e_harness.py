"""Shared helpers for the end-to-end tests: subprocesses that always die, free ports, the contract's password hash,
an HTTP client for the JSON API, and media checks (ffprobe, SRT)."""
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PYTHON = sys.executable
FFPROBE = os.getenv('FFPROBE', '/opt/homebrew/bin/ffprobe')
FAKE_WORKER = HERE / 'fake_worker.py'
NAME_OK = re.compile(r'^[a-z0-9._-]+$')
PHONE_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/18.1 Mobile/15E148 Safari/604.1')
REFUSED = (401, 403)
TERMINAL = ('done', 'done_qa_failed', 'failed')
REDIRECT = (301, 302, 303, 307, 308)
# The contract fixes the lease at 15 min and names no override. The tests set these candidates; the lease test runs
# only when webapp/ reads one of them (see LeaseTest). Recommended name: WORKER_LEASE_SECONDS.
LEASE_ENV_NAMES = ('WORKER_LEASE_SECONDS', 'STICK_LEASE_SECONDS', 'LEASE_SECONDS', 'JOB_LEASE_SECONDS')


def free_port() -> int:
    """A free local port, picked from 20000-39999: outside the ephemeral range that port-0 binds (other test runs on
    this Mac) and outgoing connections use, so a race for the same port is unlikely. Callers still check that the
    server answering is theirs (see Stack.start), because the port is free only at the moment it is probed."""
    import random
    for _ in range(50):
        port = random.randint(20000, 39999)
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def scrypt_hash(password: str) -> str:
    """docs/WEBAPP.md: scrypt$<salt hex>$<hash hex>, n=2**14, r=8, p=1, dklen=32 (the salt is the raw bytes)."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f'scrypt${salt.hex()}${digest.hex()}'


def password_hash(password: str, env: dict) -> tuple[str, str]:
    """Prefer the web app's own `python -m webapp.hashpw` (password given both as an argument and on stdin, in case
    it prompts); fall back to the contract's format computed here. Returns (hash, how)."""
    try:
        out = subprocess.run([PYTHON, '-m', 'webapp.hashpw', password], input=f'{password}\n{password}\n',
                             capture_output=True, text=True, timeout=20, env=env, cwd=ROOT, start_new_session=True)
        m = re.search(r'scrypt\$[0-9a-f]+\$[0-9a-f]{64}', out.stdout)
        if out.returncode == 0 and m:
            return m.group(0), 'webapp.hashpw'
    except (OSError, subprocess.SubprocessError):
        pass
    return scrypt_hash(password), 'contract format (tests/e2e)'


def safe_env(**extra) -> dict:
    """The parent environment without any Anthropic key (an empty value also stops pipeline.config's .env loading
    from filling one in, because load_dotenv never overrides a variable that is already set)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ANTHROPIC_', 'E2E_', 'SMOKE_'))}
    env['ANTHROPIC_API_KEY'] = ''
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT / 'tests')])
    env.update({k: str(v) for k, v in extra.items()})
    return env


class Proc:
    """A subprocess in its own session, output to a log file; stop() kills the whole group."""

    def __init__(self, name, cmd, env, log_dir: Path, cwd=ROOT):
        self.name = name
        self.log = Path(log_dir) / f'{name}.log'
        self.fh = open(self.log, 'ab')
        self.p = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=self.fh, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, start_new_session=True)

    def alive(self):
        return self.p.poll() is None

    def wait(self, timeout):
        return self.p.wait(timeout=timeout)

    def stop(self):
        if self.p.poll() is None:
            for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                try:
                    os.killpg(self.p.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    self.p.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
        self.fh.close()

    def tail(self, n=40):
        try:
            return f'--- {self.name} log (last {n} lines) ---\n' + '\n'.join(
                self.log.read_text(errors='replace').splitlines()[-n:])
        except OSError:
            return f'--- {self.name}: no log ---'


def worker_cmd(server, token, *flags, journal=None):
    cmd = [PYTHON, str(FAKE_WORKER), '--server', server, '--token', token, '--poll', '0.3', *flags]
    if journal:
        cmd += ['--journal', str(journal)]
    return cmd


def read_journal(path: Path) -> list:
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    except OSError:
        return []


def wait_for(fn, timeout, every=0.3, what='condition'):
    """Poll fn() until it returns something truthy; TimeoutError after `timeout` s."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise TimeoutError(f'timed out after {timeout:.0f} s waiting for {what} (last: {last!r})')


def probe_duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(f'ffprobe failed on {path}: {out.stderr.strip()}')
    return float(out.stdout.strip())


SRT_TIME = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
SRT_CUE = re.compile(rf'^\s*(\d+)\s*\n{SRT_TIME}\s*-->\s*{SRT_TIME}[^\n]*\n(.+?)(?:\n\s*\n|\Z)', re.S | re.M)


def parse_srt(text: str) -> list:
    """[(index, start_s, end_s, text)]; raises AssertionError when nothing parses or times run backwards."""
    cues = []
    for m in SRT_CUE.finditer(text.replace('\r\n', '\n').lstrip('﻿')):
        g = m.groups()
        a = int(g[1]) * 3600 + int(g[2]) * 60 + int(g[3]) + int(g[4]) / 1000
        b = int(g[5]) * 3600 + int(g[6]) * 60 + int(g[7]) + int(g[8]) / 1000
        if b < a:
            raise AssertionError(f'SRT cue {g[0]} ends before it starts')
        cues.append((int(g[0]), a, b, g[9].strip()))
    if not cues:
        raise AssertionError('SRT has no cues')
    return cues


def qa_all(report: dict):
    """The contract reads qa.json['all']; pipeline.qa writes summary.all. Accept either, top level first."""
    if 'all' in report:
        return report['all']
    return (report.get('summary') or {}).get('all')


class Api:
    """The JSON API of docs/WEBAPP.md, with a session cookie after login(). Never logs passwords."""

    def __init__(self, base, timeout=20.0):
        self.base = base.rstrip('/')
        self.http = httpx.Client(base_url=self.base, timeout=timeout, follow_redirects=False)

    def close(self):
        self.http.close()

    def get(self, path, **kw):
        return self.http.get(path, **kw)

    def post(self, path, json=None, **kw):
        return self.http.post(path, json=json, **kw)

    def health(self):
        r = self.get('/api/health')
        r.raise_for_status()
        return r.json()

    def login(self, user, password):
        return self.post('/api/login', json={'user': user, 'password': password})

    def episode(self, slug):
        r = self.get(f'/api/episodes/{slug}')
        r.raise_for_status()
        return r.json()

    def wait_state(self, slug, want, timeout, *, bad=None, on_state=None, every=0.5):
        """Poll the episode until its state is in `want`. Calls on_state(state, ep) for each new state seen. Raises
        AssertionError at once on a `bad` state that is not wanted (default: any end state), with the error."""
        want = {want} if isinstance(want, str) else set(want)
        bad = set(TERMINAL if bad is None else bad) - want
        seen = [None]

        def check():
            ep = self.episode(slug)
            st = ep.get('state')
            if st != seen[0]:
                seen[0] = st
                if on_state:
                    on_state(st, ep)
            if st in want:
                return ep
            if st in bad:
                raise AssertionError(f'episode {slug} went to {st!r}: {ep.get("error")!r} (job: {ep.get("job")!r})')
            return None
        return wait_for(check, timeout, every=every, what=f'{slug} to reach {sorted(want)}')


def token_pair():
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def scene_keys(ep: dict) -> list:
    """Keys of the scenes awaiting approval. The contract names the field `scenes` but not its shape: accept a list
    of dicts with `key` (the paint result's `new` entries), a list of keys, or a dict keyed by key."""
    sc = ep.get('scenes')
    if isinstance(sc, dict):
        if isinstance(sc.get('new'), list):
            sc = sc['new']
        else:
            return list(sc)
    out = []
    for item in sc or []:
        if isinstance(item, dict) and item.get('key'):
            out.append(item['key'])
        elif isinstance(item, str):
            out.append(item)
    return out


def storyline_cards(ep: dict) -> list:
    """The storyline cards: webapp/ sends {cards: [...], pick, note, text}; a bare list is accepted too."""
    sl = ep.get('storylines')
    return (sl.get('cards') or []) if isinstance(sl, dict) else list(sl or [])


def file_names(ep: dict) -> list:
    """Names in the episode's `files` (list of dicts with `name`, list of names, or dict keyed by name)."""
    fs = ep.get('files')
    if isinstance(fs, dict):
        return list(fs)
    return [f['name'] if isinstance(f, dict) else f for f in fs or [] if (isinstance(f, dict) and f.get('name')) or isinstance(f, str)]
