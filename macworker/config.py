"""Worker configuration: environment first, then an optional KEY=VALUE file, then defaults.

The config file (default `~/.config/stick-worker/config`, or `STICK_WORKER_CONFIG`) holds no secrets: the bearer
token lives in the macOS Keychain (`security find-generic-password -s stick-worker -w`). `STICK_WORKER_TOKEN` in
the environment overrides the Keychain (tests and one-off runs only; launchd never sets it).
"""
from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
DEFAULT_CONFIG = HOME / '.config/stick-worker/config'
KEYCHAIN_SERVICE = 'stick-worker'


def _read_file(path: Path) -> dict:
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def keychain_token(service: str = KEYCHAIN_SERVICE, run=subprocess.run) -> str | None:
    """The worker token from the login Keychain, or None. Never logged."""
    try:
        p = run(['security', 'find-generic-password', '-s', service, '-w'], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    tok = (p.stdout or '').strip()
    return tok if p.returncode == 0 and tok else None


@dataclass
class Config:
    server: str = ''
    repo: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    poll_s: float = 30.0
    workers: int = 4
    log_file: Path = HOME / 'Library/Logs/stick-worker.log'
    state_dir: Path = HOME / 'Library/Application Support/stick-worker'
    host: str = field(default_factory=socket.gethostname)
    auto_pull: bool = False            # `git pull --ff-only` on pipeline-v2 while idle (off by default)
    job_timeout_s: float = 4 * 3600    # a stuck child is stopped after this long
    token: str | None = field(default=None, repr=False)
    token_source: str = ''

    @property
    def python(self) -> Path:
        return self.repo / '.venv/bin/python'

    @property
    def stick(self) -> Path:
        return self.repo / '.venv/bin/stick'

    def problems(self) -> list[str]:
        out = []
        if not self.server.startswith(('https://', 'http://')):
            out.append('STICK_SERVER is not set (e.g. https://187-127-179-129.sslip.io)')
        elif self.server.startswith('http://') and not any(h in self.server for h in ('://localhost', '://127.0.0.1')):
            out.append('STICK_SERVER must use https:// (plain http only for localhost)')
        if not self.token:
            out.append(f'no worker token: store it with `security add-generic-password -U -s {KEYCHAIN_SERVICE} -a stick -w`')
        for p, what in ((self.python, 'the repo venv python'), (self.stick, 'the stick command')):
            if not p.exists():
                out.append(f'{what} is missing: {p}')
        return out


def load(env: dict | None = None, token_lookup=keychain_token) -> Config:
    env = dict(os.environ if env is None else env)
    file_vals = _read_file(Path(env.get('STICK_WORKER_CONFIG', DEFAULT_CONFIG)).expanduser())
    get = lambda k, d=None: env.get(k) or file_vals.get(k) or d
    cfg = Config()
    cfg.server = (get('STICK_SERVER', '') or '').rstrip('/')
    if get('STICK_REPO'):
        cfg.repo = Path(get('STICK_REPO')).expanduser().resolve()
    cfg.poll_s = float(get('STICK_WORKER_POLL', cfg.poll_s))
    cfg.workers = int(get('STICK_RENDER_WORKERS', cfg.workers))
    if get('STICK_WORKER_LOG'):
        cfg.log_file = Path(get('STICK_WORKER_LOG')).expanduser()
    if get('STICK_WORKER_STATE'):
        cfg.state_dir = Path(get('STICK_WORKER_STATE')).expanduser()
    if get('STICK_WORKER_HOST'):
        cfg.host = get('STICK_WORKER_HOST')
    cfg.auto_pull = str(get('STICK_WORKER_PULL', '0')).lower() in ('1', 'true', 'yes')
    cfg.job_timeout_s = float(get('STICK_JOB_TIMEOUT', cfg.job_timeout_s))
    if env.get('STICK_WORKER_TOKEN'):
        cfg.token, cfg.token_source = env['STICK_WORKER_TOKEN'], 'STICK_WORKER_TOKEN'
    else:
        cfg.token, cfg.token_source = token_lookup(), f'Keychain service {KEYCHAIN_SERVICE}'
    return cfg
