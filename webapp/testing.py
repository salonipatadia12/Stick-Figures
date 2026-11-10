"""Test harness for the web app: a throwaway data dir + SQLite DB, the app, the runner (fake Anthropic) and a
simulated Mac worker that speaks the worker API. Used by tests/webapp; nothing here sends a paid request."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

from . import auth
from .settings import Settings

USER, PASSWORD = 'saloni', 'correct horse battery'
WORKER_TOKEN = 'worker-token-for-tests-0123456789'
_HASH = None


def password_hash():
    global _HASH
    if _HASH is None:
        _HASH = auth.hash_password(PASSWORD)
    return _HASH


class Harness:
    """with Harness() as h: h.client (TestClient), h.service, h.runner, h.worker_headers ..."""

    def __init__(self, spend='0.50', public_url='http://testserver', upload_limit=None):
        self.spend, self.public_url, self.upload_limit = spend, public_url, upload_limit

    def __enter__(self):
        from fastapi.testclient import TestClient

        from pipeline import config, script

        from . import app as webapp_app
        from . import core
        from .runner import Runner
        self.tmp = tempfile.TemporaryDirectory(prefix='stick-webapp-test-')
        data = Path(self.tmp.name)
        self.patches = [mock.patch.object(config, 'EPISODES_DIR', config.EPISODES_DIR),
                        mock.patch.object(script, 'make_client', script.make_client),
                        mock.patch.dict(os.environ, {'STICK_EPISODES_DIR': str(data / 'episodes')})]
        for p in self.patches:
            p.start()
        self.settings = Settings(database_url=f'sqlite:///{data / "test.db"}', data_dir=data, app_user=USER,
                                 app_password_hash=password_hash(), session_secret='test-session-secret',
                                 worker_token_sha256=hashlib.sha256(WORKER_TOKEN.encode()).hexdigest(),
                                 public_url=self.public_url, spend_preapproved_usd=Decimal(self.spend),
                                 fake_anthropic=True)
        if self.upload_limit:
            self.settings.upload_limit_bytes = self.upload_limit
        self.service = core.Service(self.settings)
        self.app = webapp_app.create_app(self.settings, self.service)
        self.client = TestClient(self.app, base_url=self.public_url or 'http://testserver')
        self.runner = Runner(self.settings, self.service)
        self.worker_headers = {'Authorization': f'Bearer {WORKER_TOKEN}'}
        return self

    def __exit__(self, *exc):
        self.client.close()
        self.service.engine.dispose()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    # ---- user
    def login(self):
        r = self.client.post('/api/login', json=dict(user=USER, password=PASSWORD))
        assert r.status_code == 200, r.text
        return r

    def detail(self, slug):
        r = self.client.get(f'/api/episodes/{slug}')
        assert r.status_code == 200, r.text
        return r.json()

    # ---- worker
    def w(self, method, path, **kw):
        return getattr(self.client, method)(path, headers=self.worker_headers, **kw)

    def claim(self, kinds=('paint', 'render')):
        r = self.w('post', '/api/worker/claim', json=dict(kinds=list(kinds)))
        return None if r.status_code == 204 else r.json()

    def upload(self, job_id, name, kind, content=b'x' * 100):
        return self.w('post', f'/api/worker/jobs/{job_id}/files', data=dict(name=name, kind=kind),
                      files=dict(file=(name, content, 'application/octet-stream')))

    def episode_file(self, slug, name='episode.json'):
        return json.loads((self.settings.episodes_dir / slug / name).read_text())
