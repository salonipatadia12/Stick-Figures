"""TEST DOUBLE, not the web app. A tiny in-memory server that speaks the worker API of docs/WEBAPP.md (plus the few
routes deploy/smoke_test.sh touches), so tests/e2e/fake_worker.py and the smoke test can be exercised before lane B's
real `webapp/` exists. Nothing here is used in production.

    MOCK_WORKER_TOKEN=t MOCK_USER=u MOCK_PASSWORD=p python -m uvicorn mock_server:app --app-dir tests/e2e --port 8765

Test-only control routes (no auth): POST /mock/jobs {kind, slug, payload} queues a job; GET /mock/jobs/{id} shows it;
GET /mock/files/{slug}/{name} returns an uploaded file. MOCK_LEASE_SECONDS sets the lease (default 900, as the
contract's 15 min).
"""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

NAME_OK = re.compile(r'^[a-z0-9._-]+$')
app = FastAPI(title='stick mock server (test double)')
lock = threading.Lock()
state = dict(jobs={}, files={}, worker=dict(last_seen=None, host=None, version=None, busy_job_id=None), sessions=set(),
             next_id=1)


def lease_s():
    return float(os.getenv('MOCK_LEASE_SECONDS', '900'))


def token_sha():
    return hashlib.sha256(os.getenv('MOCK_WORKER_TOKEN', 'mock-token').encode()).hexdigest()


def worker_auth(authorization):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'worker token required')
    got = hashlib.sha256(authorization[7:].encode()).hexdigest()
    if not hmac.compare_digest(got, token_sha()):
        raise HTTPException(401, 'bad worker token')


def requeue_expired():
    now = time.time()
    for j in state['jobs'].values():
        if j['status'] == 'running' and j['lease_until'] and j['lease_until'] < now:
            j['status'] = 'queued' if j['attempts'] < 3 else 'failed'
            j['lease_until'] = None
            if j['status'] == 'failed':
                j['error'] = 'lease expired 3 times'


def job_or_404(job_id):
    j = state['jobs'].get(int(job_id))
    if not j:
        raise HTTPException(404, 'no such job')
    return j


def online():
    seen = state['worker']['last_seen']
    return bool(seen and time.time() - seen < 120)


# --------------------------------------------------------------------------------------------- the worker API

@app.post('/api/worker/heartbeat')
async def heartbeat(request: Request, authorization: str | None = Header(None)):
    worker_auth(authorization)
    body = await request.json()
    with lock:
        state['worker'].update(last_seen=time.time(), host=body.get('host'), version=body.get('version'),
                               busy_job_id=body.get('busy_job_id'))
        requeue_expired()
        return {'ok': True, 'queued': sum(j['status'] == 'queued' for j in state['jobs'].values())}


@app.post('/api/worker/claim')
async def claim(request: Request, authorization: str | None = Header(None)):
    worker_auth(authorization)
    kinds = (await request.json()).get('kinds') or ['paint', 'render']
    with lock:
        requeue_expired()
        for j in sorted(state['jobs'].values(), key=lambda j: j['id']):
            if j['status'] == 'queued' and j['kind'] in kinds:
                j.update(status='running', attempts=j['attempts'] + 1, lease_until=time.time() + lease_s(),
                         started_at=time.time())
                return {'job_id': j['id'], 'kind': j['kind'], 'slug': j['slug'], 'payload': j['payload'],
                        'episode': {'slug': j['slug'], 'beats': []}, 'ledger': {'entries': []}}
    return Response(status_code=204)


@app.post('/api/worker/jobs/{job_id}/progress')
async def progress(job_id: int, request: Request, authorization: str | None = Header(None)):
    worker_auth(authorization)
    body = await request.json()
    with lock:
        j = job_or_404(job_id)
        j['progress'] = dict(stage=body.get('stage'), pct=body.get('pct'), message=body.get('message'), at=time.time())
        j['lease_until'] = time.time() + lease_s()
    return {'ok': True}


@app.post('/api/worker/jobs/{job_id}/files')
async def files(job_id: int, file: UploadFile = File(...), name: str = Form(...), kind: str = Form(...),
                authorization: str | None = Header(None)):
    worker_auth(authorization)
    if not NAME_OK.match(name):
        raise HTTPException(400, 'bad file name')
    data = await file.read()
    with lock:
        j = job_or_404(job_id)
        state['files'][(j['slug'], name)] = dict(kind=kind, data=data, size=len(data))
    return {'ok': True}


@app.post('/api/worker/jobs/{job_id}/done')
async def done(job_id: int, request: Request, authorization: str | None = Header(None)):
    worker_auth(authorization)
    body = await request.json()
    with lock:
        j = job_or_404(job_id)
        j.update(status='done', result=body.get('result'), finished_at=time.time(), lease_until=None)
    return {'ok': True}


@app.post('/api/worker/jobs/{job_id}/fail')
async def fail(job_id: int, request: Request, authorization: str | None = Header(None)):
    worker_auth(authorization)
    body = await request.json()
    with lock:
        j = job_or_404(job_id)
        j.update(status='failed', error=body.get('error'), finished_at=time.time(), lease_until=None)
    return {'ok': True}


# --------------------------------------------------------------------------------------------- test control

@app.post('/mock/jobs')
async def mock_queue(request: Request):
    body = await request.json()
    with lock:
        jid = state['next_id']
        state['next_id'] += 1
        state['jobs'][jid] = dict(id=jid, kind=body['kind'], slug=body.get('slug', 'mock-episode'),
                                  payload=body.get('payload') or {}, status='queued', attempts=0, lease_until=None,
                                  progress=None, result=None, error=None, started_at=None, finished_at=None)
    return {'job_id': jid}


@app.get('/mock/jobs/{job_id}')
async def mock_job(job_id: int):
    with lock:
        requeue_expired()
        return dict(job_or_404(job_id))


@app.get('/mock/files/{slug}/{name}')
async def mock_file(slug: str, name: str):
    f = state['files'].get((slug, name))
    if not f:
        raise HTTPException(404)
    return Response(f['data'], media_type='application/octet-stream', headers={'X-Kind': f['kind']})


# --------------------------------------------------------------------------------------------- smoke-test routes

@app.get('/api/health')
async def health():
    w = state['worker']
    return {'ok': True, 'db': True, 'worker': {'online': online(), 'last_seen': w['last_seen'], 'busy': w['busy_job_id']}}


@app.post('/api/login')
async def api_login(request: Request):
    body = await request.json()
    ok = (hmac.compare_digest(str(body.get('user', '')), os.getenv('MOCK_USER', 'mock'))
          and hmac.compare_digest(str(body.get('password', '')), os.getenv('MOCK_PASSWORD', 'mock-password')))
    if not ok:
        return JSONResponse({'ok': False}, status_code=401)
    sid = secrets.token_hex(16)
    state['sessions'].add(sid)
    resp = JSONResponse({'ok': True})
    resp.set_cookie('session', sid, httponly=True, samesite='lax')
    return resp


@app.get('/login', response_class=HTMLResponse)
async def login_page():
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<link rel="stylesheet" href="/static/app.css"><title>Login</title></head><body><form method="post">'
            '</form></body></html>')


@app.get('/static/app.css')
async def css():
    return Response('body{font-family:sans-serif}', media_type='text/css')


@app.get('/')
async def history(request: Request):
    if request.cookies.get('session') not in state['sessions']:
        return RedirectResponse('/login', status_code=303)
    return HTMLResponse('<!doctype html><html><head><meta name="viewport" content="width=device-width"></head>'
                        '<body><h1>History</h1></body></html>')
