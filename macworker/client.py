"""The worker side of the HTTP API (docs/WEBAPP.md, "Worker API"). Bearer token; never logs it."""
from __future__ import annotations

from pathlib import Path

import httpx

from .util import safe_name


class NetworkError(Exception):
    """The server could not be reached or answered 5xx/429: retry later, keep results on disk."""


class AuthError(Exception):
    """401/403: the token is wrong or revoked. Retrying soon will not help."""


class Rejected(Exception):
    """Any other 4xx: the server refuses this request (e.g. the job is no longer ours). Not retried."""

    def __init__(self, status: int, body: str):
        super().__init__(f'HTTP {status}: {body[:300]}')
        self.status = status


class Client:
    def __init__(self, server: str, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self.server = server.rstrip('/')
        self.http = httpx.Client(base_url=self.server, transport=transport,
                                 headers={'Authorization': f'Bearer {token}', 'User-Agent': 'stick-macworker'},
                                 timeout=httpx.Timeout(timeout, write=600.0), follow_redirects=False)

    def close(self):
        self.http.close()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.TransportError as e:            # connect, read, write, timeouts, TLS
            raise NetworkError(f'{method} {path}: {type(e).__name__}: {e}') from None
        if r.status_code in (401, 403):
            raise AuthError(f'{method} {path}: HTTP {r.status_code} (check the worker token)')
        if r.status_code >= 500 or r.status_code == 429 or r.status_code in (301, 302, 307, 308):
            raise NetworkError(f'{method} {path}: HTTP {r.status_code}')
        if r.status_code >= 400:
            raise Rejected(r.status_code, r.text)
        return r

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        try:
            return r.json() if r.content else {}
        except ValueError:
            return {}

    # ---- endpoints ---------------------------------------------------------------------------------
    def health(self) -> dict:
        return self._json(self._req('GET', '/api/health'))

    def heartbeat(self, host: str, version: str, busy_job_id=None) -> dict:
        body = dict(host=host, version=version)
        if busy_job_id is not None:
            body['busy_job_id'] = busy_job_id
        return self._json(self._req('POST', '/api/worker/heartbeat', json=body))

    def claim(self, kinds=('paint', 'render')) -> dict | None:
        r = self._req('POST', '/api/worker/claim', json=dict(kinds=list(kinds)))
        if r.status_code == 204 or not r.content:
            return None
        return self._json(r) or None

    def progress(self, job_id, stage: str, pct: float, message: str = '') -> dict:
        body = dict(stage=stage, pct=round(max(0.0, min(100.0, float(pct))), 1), message=message[:500])
        return self._json(self._req('POST', f'/api/worker/jobs/{job_id}/progress', json=body))

    def upload(self, job_id, path: Path, name: str, kind: str) -> dict:
        name = safe_name(name)
        with open(path, 'rb') as f:
            files = {'file': (name, f, _mime(name))}
            return self._json(self._req('POST', f'/api/worker/jobs/{job_id}/files', data=dict(name=name, kind=kind), files=files))

    def done(self, job_id, result: dict) -> dict:
        return self._json(self._req('POST', f'/api/worker/jobs/{job_id}/done', json=dict(result=result)))

    def fail(self, job_id, error: str) -> dict:
        return self._json(self._req('POST', f'/api/worker/jobs/{job_id}/fail', json=dict(error=error[:4000])))


def _mime(name: str) -> str:
    ext = name.rsplit('.', 1)[-1]
    return {'mp4': 'video/mp4', 'srt': 'application/x-subrip', 'json': 'application/json', 'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg', 'png': 'image/png'}.get(ext, 'application/octet-stream')
