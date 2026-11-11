"""A fake worker API (httpx.MockTransport), a fake caffeinate and helpers for the worker tests. No network."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx

from macworker import config
from macworker.client import Client

TOKEN = 'test-token-123'


class FakeServer:
    """Speaks docs/WEBAPP.md's worker API. `jobs` is the queue; `down` maps a path suffix to how many more
    requests to it should fail with a connection error; `status` maps a path suffix to a forced status code."""

    def __init__(self, jobs=None):
        self.jobs = list(jobs or [])
        self.calls = []                       # (method, path, body)
        self.files = []                       # (job_id, name, kind, bytes)
        self.done, self.failed, self.progress = {}, {}, []
        self.down, self.status = {}, {}
        self.kinds = None                     # the heartbeat's `kinds` (None: an older server that sends none)

    def _match(self, table, path):
        return next((k for k in table if path.endswith(k)), None)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        k = self._match(self.down, path)
        if k and self.down[k] > 0:
            self.down[k] -= 1
            raise httpx.ConnectError('fake network down', request=request)
        if request.headers.get('authorization') != f'Bearer {TOKEN}' and path != '/api/health':
            return httpx.Response(401, json=dict(detail='bad token'))
        k = self._match(self.status, path)
        if k:
            return httpx.Response(self.status[k], json=dict(detail='forced'))
        if path.endswith('/files'):
            ctype = request.headers['content-type']
            body = request.read()
            name, kind, data = _multipart(body, ctype)
            job_id = path.split('/')[-2]
            self.files.append((job_id, name, kind, data))
            self.calls.append((request.method, path, dict(name=name, kind=kind)))
            return httpx.Response(200, json=dict(ok=True))
        body = json.loads(request.read() or b'{}')
        self.calls.append((request.method, path, body))
        if path == '/api/health':
            return httpx.Response(200, json=dict(ok=True, db=True, worker=dict(online=True)))
        if path == '/api/worker/heartbeat':
            body = dict(ok=True, queued=len(self.jobs))
            if self.kinds is not None:
                body['kinds'] = list(self.kinds)
            return httpx.Response(200, json=body)
        if path == '/api/worker/claim':
            if not self.jobs:
                return httpx.Response(204)
            return httpx.Response(200, json=self.jobs.pop(0))
        job_id = path.split('/')[-2]
        if path.endswith('/progress'):
            self.progress.append((job_id, body))
        elif path.endswith('/done'):
            self.done[job_id] = body['result']
        elif path.endswith('/fail'):
            self.failed[job_id] = body['error']
        return httpx.Response(200, json=dict(ok=True))

    def client(self) -> Client:
        return Client('https://stick.test', TOKEN, transport=httpx.MockTransport(self.handler))

    def paths(self):
        return [p for _, p, _ in self.calls]


def _multipart(body: bytes, ctype: str):
    boundary = ctype.split('boundary=')[1].encode()
    fields, data = {}, None
    for part in body.split(b'--' + boundary):
        if b'\r\n\r\n' not in part:
            continue
        head, _, val = part.partition(b'\r\n\r\n')
        val = val[:-2] if val.endswith(b'\r\n') else val
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b'filename=' in head:
            data = val
        else:
            fields[name] = val.decode()
    return fields.get('name'), fields.get('kind'), data


class FakePopen:
    """Records caffeinate starts and stops."""
    instances = []

    def __init__(self, args, **kw):
        self.args, self.pid, self.terminated = args, 4242, False
        FakePopen.instances.append(self)

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def job(job_id=7, kind='render', slug='why-honey-never-spoils', payload=None):
    return dict(job_id=job_id, kind=kind, slug=slug, payload=payload or {}, episode=dict(slug=slug, title='T', beats=[]),
                ledger=dict(entries=[]))


def make_cfg(tmp: Path, repo: Path | None = None) -> config.Config:
    cfg = config.load(env=dict(STICK_SERVER='https://stick.test', STICK_WORKER_TOKEN=TOKEN,
                               STICK_REPO=str(repo or tmp / 'repo'), STICK_WORKER_STATE=str(tmp / 'state'),
                               STICK_WORKER_LOG=str(tmp / 'worker.log'), STICK_WORKER_CONFIG=str(tmp / 'none'),
                               STICK_WORKER_POLL='0.05'),
                      token_lookup=lambda: None)
    return cfg


class TmpDir:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
