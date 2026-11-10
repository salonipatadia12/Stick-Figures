"""Configuration from the environment (docs/WEBAPP.md). Read once per app/runner; tests build their own."""
from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


def _flag(name):
    return os.getenv(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass
class Settings:
    database_url: str
    data_dir: Path
    app_user: str
    app_password_hash: str
    session_secret: str
    worker_token_sha256: str
    public_url: str
    spend_preapproved_usd: Decimal
    fake_anthropic: bool
    upload_limit_bytes: int = 300 * 1024 * 1024
    mac_lease_minutes: float = 15
    vps_lease_minutes: int = 20
    max_attempts: int = 3
    worker_online_seconds: int = 120

    @property
    def episodes_dir(self) -> Path:
        return self.data_dir / 'episodes'

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / 'outputs'

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.lower().startswith('https://')

    @classmethod
    def from_env(cls) -> 'Settings':
        data_dir = Path(os.getenv('STICK_DATA_DIR', '/data'))
        secret = os.getenv('SESSION_SECRET', '')
        if not secret:
            print('webapp: SESSION_SECRET is not set; using a random key (sessions end when the app restarts)',
                  file=sys.stderr)
            secret = secrets.token_hex(32)
        return cls(database_url=os.getenv('DATABASE_URL', f'sqlite:///{data_dir / "stick.db"}'),
                   data_dir=data_dir,
                   app_user=os.getenv('APP_USER', ''),
                   app_password_hash=os.getenv('APP_PASSWORD_HASH', ''),
                   session_secret=secret,
                   worker_token_sha256=os.getenv('WORKER_TOKEN_SHA256', '').strip().lower(),
                   public_url=os.getenv('PUBLIC_URL', ''),
                   spend_preapproved_usd=Decimal(os.getenv('SPEND_PREAPPROVED_USD', '0.50') or '0.50'),
                   fake_anthropic=_flag('STICK_FAKE_ANTHROPIC'),
                   # tests only: a short Mac lease so lease expiry can be exercised (default 15 min)
                   mac_lease_minutes=float(os.getenv('WORKER_LEASE_SECONDS', '900') or '900') / 60)

    def apply_pipeline_paths(self):
        """Point the pipeline at $STICK_DATA_DIR/episodes (pipeline.config reads the env at import, so set both)."""
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        os.environ['STICK_EPISODES_DIR'] = str(self.episodes_dir)
        from pipeline import config
        config.EPISODES_DIR = self.episodes_dir
