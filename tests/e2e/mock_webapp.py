"""TEST DOUBLE, not the web app. An in-memory stand-in for lane B's `webapp/` that follows docs/WEBAPP.md as lane D
reads it (episode states, gates, JSON API, worker API, lease expiry), with a runner thread that fakes the VPS steps
(pitch -> 3 storylines, script -> a script) without any pipeline or Anthropic call. It exists so test_e2e's flow is
exercised before the merge (tests/e2e/test_e2e_mock.py); after the merge the same tests run against the real app.

It reads the same environment as the real app: APP_USER, APP_PASSWORD_HASH (scrypt$<salt hex>$<hash hex>),
WORKER_TOKEN_SHA256, STICK_DATA_DIR, WORKER_LEASE_SECONDS (default 900).
"""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

NAME_OK = re.compile(r'^[a-z0-9._-]+$')
DATA = Path(os.getenv('STICK_DATA_DIR', '/tmp/stick-mock-data'))
LEASE = float(os.getenv('WORKER_LEASE_SECONDS', '900'))
lock = threading.RLock()
db = dict(episodes={}, jobs={}, sessions=set(), worker=dict(last_seen=None, host=None, version=None, busy_job_id=None),
          next_job=1)
GATE_OF = {'storyline': 'storylines', 'script': 'script', 'scenes': 'scenes'}


def now():
    return time.time()


# ----------------------------------------------------------------------------------------------- auth

def password_ok(user, password):
    want = os.getenv('APP_PASSWORD_HASH', '')
    try:
        _, salt, digest = want.split('$')
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex()
    except ValueError:
        return False
    return hmac.compare_digest(user, os.getenv('APP_USER', '')) and hmac.compare_digest(got, digest)


def session(request: Request):
    return request.cookies.get('session') in db['sessions']


def need_session(request: Request):
    if not session(request):
        raise HTTPException(401, 'login required')


def need_worker(authorization):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'worker token required')
    got = hashlib.sha256(authorization[7:].encode()).hexdigest()
    if not hmac.compare_digest(got, os.getenv('WORKER_TOKEN_SHA256', '')):
        raise HTTPException(401, 'bad worker token')


# ----------------------------------------------------------------------------------------------- queue

def queue(slug, kind, payload=None):
    jid = db['next_job']
    db['next_job'] += 1
    db['jobs'][jid] = dict(id=jid, slug=slug, kind=kind, status='queued', payload=payload or {}, result=None,
                           progress=None, attempts=0, lease_until=None, error=None)
    return jid


def last_job(slug):
    jobs = [j for j in db['jobs'].values() if j['slug'] == slug]
    return max(jobs, key=lambda j: j['id']) if jobs else None


def requeue_expired():
    for j in db['jobs'].values():
        if j['status'] == 'running' and j['kind'] in ('paint', 'render') and j['lease_until'] < now():
            if j['attempts'] >= 3:
                j.update(status='failed', error='lease expired 3 times')
                db['episodes'][j['slug']].update(state='failed', error=j['error'])
            else:
                j.update(status='queued', lease_until=None)


def online():
    seen = db['worker']['last_seen']
    return bool(seen and now() - seen < 120)


def worker_view():
    w = db['worker']
    return {'online': online(), 'last_seen': w['last_seen'], 'busy': w['busy_job_id']}


STORYLINES = [dict(format=f, title=f'Story {i}', hook=f'Hook {i}', pitch='A pitch', conflict='c', stakes='s', twist='t',
                   payoff='p', facts=[]) for i, f in enumerate(('mini_story', 'myth_vs_fact', 'mystery'), 1)]


def runner():
    """The VPS runner: pitch and script jobs, a little later each, as the fake Anthropic client would."""
    while True:
        time.sleep(0.2)
        with lock:
            requeue_expired()
            for j in list(db['jobs'].values()):
                if j['status'] != 'queued' or j['kind'] not in ('pitch', 'script'):
                    continue
                ep = db['episodes'][j['slug']]
                if j['kind'] == 'pitch':
                    ep.update(state='storylines', storylines=STORYLINES)
                else:
                    ep.update(state='script', script=dict(beats=[{'id': 'b01', 'say': 'Hi'}], hooks=['a', 'b', 'c'],
                                                          errors=[]))
                j.update(status='done', result={})


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=runner, daemon=True).start()
    yield


app = FastAPI(title='stick mock web app (test double)', lifespan=lifespan)


def ep_or_404(slug):
    ep = db['episodes'].get(slug)
    if not ep:
        raise HTTPException(404, 'no such episode')
    return ep


def at_gate(ep, gate):
    if ep['state'] != GATE_OF[gate]:
        raise HTTPException(409, f'not at the {gate} gate (state {ep["state"]})')


def public(ep):
    j = last_job(ep['slug'])
    out = {k: ep[k] for k in ('slug', 'topic', 'state', 'cost_usd', 'error', 'created_at')}
    out['job'] = j and {'kind': j['kind'], 'status': j['status'], 'progress': j['progress']}
    out['worker'] = worker_view()
    for k in ('storylines', 'script', 'scenes', 'files', 'render'):
        if ep.get(k) is not None:
            out[k] = ep[k]
    return out


# ----------------------------------------------------------------------------------------------- JSON API

@app.get('/api/health')
async def health():
    return {'ok': True, 'db': True, 'worker': worker_view()}


@app.post('/api/login')
async def api_login(request: Request):
    body = await request.json()
    if not password_ok(str(body.get('user', '')), str(body.get('password', ''))):
        return JSONResponse({'ok': False}, status_code=401)
    sid = secrets.token_hex(16)
    db['sessions'].add(sid)
    resp = JSONResponse({'ok': True})
    resp.set_cookie('session', sid, httponly=True, samesite='lax')
    return resp


@app.post('/api/logout')
async def api_logout(request: Request):
    db['sessions'].discard(request.cookies.get('session'))
    resp = JSONResponse({'ok': True})
    resp.delete_cookie('session')
    return resp


@app.post('/episodes')
async def create_form(request: Request):
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != os.getenv('PUBLIC_URL', '').rstrip('/'):
        raise HTTPException(403, 'foreign origin')
    raise HTTPException(501, 'the mock has no form create')


@app.post('/logout')
async def logout(request: Request):
    db['sessions'].discard(request.cookies.get('session'))
    resp = RedirectResponse('/login', status_code=303)
    resp.delete_cookie('session')
    return resp


@app.post('/api/episodes')
async def create(request: Request):
    need_session(request)
    body = await request.json()
    if not str(body.get('topic') or '').strip():
        raise HTTPException(400, 'topic required')
    with lock:
        base = re.sub(r'[^a-z0-9]+', '-', body['topic'].lower()).strip('-')[:40] or 'episode'
        slug = base
        while slug in db['episodes']:
            slug = f'{base}-{secrets.token_hex(2)}'
        db['episodes'][slug] = dict(slug=slug, topic=body['topic'], state='researching', cost_usd=0.0, error=None,
                                    created_at=now(), storylines=None, script=None, scenes=None, files=[])
        queue(slug, 'pitch')
    return {'ok': True, 'slug': slug}


@app.get('/api/episodes')
async def listing(request: Request):
    need_session(request)
    return [{k: e[k] for k in ('slug', 'topic', 'state', 'cost_usd', 'created_at')} for e in db['episodes'].values()]


@app.get('/api/episodes/{slug}')
async def detail(slug: str, request: Request):
    need_session(request)
    with lock:
        requeue_expired()
        return public(ep_or_404(slug))


@app.post('/api/episodes/{slug}/storyline')
async def pick_story(slug: str, request: Request):
    need_session(request)
    n = (await request.json()).get('story')
    with lock:
        ep = ep_or_404(slug)
        at_gate(ep, 'storyline')
        if n not in (1, 2, 3):
            raise HTTPException(422, 'story must be 1-3')
        ep['state'] = 'scripting'
        queue(slug, 'script', {'story': n})
    return {'ok': True}


@app.post('/api/episodes/{slug}/script/approve')
async def approve_script(slug: str, request: Request):
    need_session(request)
    n = (await request.json()).get('hook')
    with lock:
        ep = ep_or_404(slug)
        at_gate(ep, 'script')
        if n not in (1, 2, 3):
            raise HTTPException(422, 'hook must be 1-3')
        if ep['script']['errors']:
            return JSONResponse({'errors': ep['script']['errors']}, status_code=409)
        ep['state'] = 'painting'
        queue(slug, 'paint', {})
    return {'ok': True}


@app.post('/api/episodes/{slug}/scenes/approve')
async def approve_scenes(slug: str, request: Request):
    need_session(request)
    with lock:
        ep = ep_or_404(slug)
        at_gate(ep, 'scenes')
        ep['state'] = 'rendering'
        queue(slug, 'render', {})
    return {'ok': True}


@app.post('/api/episodes/{slug}/scenes/redo')
async def redo(slug: str, request: Request):
    need_session(request)
    body = await request.json()
    with lock:
        ep = ep_or_404(slug)
        at_gate(ep, 'scenes')
        if body.get('key') not in [s['key'] for s in ep['scenes'] or []]:
            raise HTTPException(422, 'unknown scene key')
        ep['state'] = 'painting'
        queue(slug, 'paint', {'redo': {body['key']: body.get('note', '')}})
    return {'ok': True}


@app.post('/api/episodes/{slug}/retry')
async def retry(slug: str, request: Request):
    need_session(request)
    with lock:
        ep = ep_or_404(slug)
        j = last_job(slug)
        if ep['state'] != 'failed' or not j or j['status'] != 'failed':
            raise HTTPException(409, 'nothing to retry')
        j.update(status='queued', error=None, lease_until=None)
        ep.update(state={'paint': 'painting', 'render': 'rendering', 'pitch': 'researching',
                         'script': 'scripting'}[j['kind']], error=None)
    return {'ok': True}


# ----------------------------------------------------------------------------------------------- worker API

@app.post('/api/worker/heartbeat')
async def heartbeat(request: Request, authorization: str | None = Header(None)):
    need_worker(authorization)
    body = await request.json()
    with lock:
        db['worker'].update(last_seen=now(), host=body.get('host'), version=body.get('version'),
                            busy_job_id=body.get('busy_job_id'))
        return {'ok': True, 'queued': sum(j['status'] == 'queued' and j['kind'] in ('paint', 'render')
                                          for j in db['jobs'].values())}


@app.post('/api/worker/claim')
async def claim(request: Request, authorization: str | None = Header(None)):
    need_worker(authorization)
    kinds = (await request.json()).get('kinds') or []
    with lock:
        requeue_expired()
        for j in sorted(db['jobs'].values(), key=lambda j: j['id']):
            if j['status'] == 'queued' and j['kind'] in kinds:
                j.update(status='running', attempts=j['attempts'] + 1, lease_until=now() + LEASE)
                return {'job_id': j['id'], 'kind': j['kind'], 'slug': j['slug'], 'payload': j['payload'],
                        'episode': {'slug': j['slug'], 'beats': []}, 'ledger': {'entries': []}}
    return Response(status_code=204)


def running_job(job_id):
    j = db['jobs'].get(job_id)
    if not j:
        raise HTTPException(404, 'no such job')
    if j['status'] != 'running':
        raise HTTPException(409, f'job is {j["status"]}')
    return j


@app.post('/api/worker/jobs/{job_id}/progress')
async def progress(job_id: int, request: Request, authorization: str | None = Header(None)):
    need_worker(authorization)
    body = await request.json()
    with lock:
        j = running_job(job_id)
        j.update(progress={**body, 'at': now()}, lease_until=now() + LEASE)
    return {'ok': True}


@app.post('/api/worker/jobs/{job_id}/files')
async def upload(job_id: int, file: UploadFile = File(...), name: str = Form(...), kind: str = Form(...),
                 authorization: str | None = Header(None)):
    need_worker(authorization)
    if not NAME_OK.match(name):
        raise HTTPException(400, 'bad name')
    data = await file.read()
    with lock:
        j = running_job(job_id)
        out = DATA / 'outputs' / j['slug']
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)
        ep = db['episodes'][j['slug']]
        ep['files'] = [f for f in ep['files'] if f['name'] != name] + [dict(name=name, kind=kind, size=len(data))]
    return {'ok': True}


@app.post('/api/worker/jobs/{job_id}/done')
async def done(job_id: int, request: Request, authorization: str | None = Header(None)):
    need_worker(authorization)
    result = (await request.json()).get('result') or {}
    with lock:
        j = running_job(job_id)
        j.update(status='done', result=result, lease_until=None)
        ep = db['episodes'][j['slug']]
        if j['kind'] == 'paint':
            new = result.get('new') or []
            if new:
                keep = {s['key']: s for s in ep['scenes'] or []}
                keep.update({s['key']: s for s in new})
                ep.update(state='scenes', scenes=list(keep.values()))
            else:
                ep['state'] = 'rendering'
                queue(ep['slug'], 'render', {})
        elif j['kind'] == 'render':
            ep['state'] = 'done' if result.get('qa_pass') else 'done_qa_failed'
            ep['render'] = {'duration': result.get('duration'), 'qa_pass': result.get('qa_pass'),
                            'qa_checks': result.get('qa_summary')}
    return {'ok': True}


@app.post('/api/worker/jobs/{job_id}/fail')
async def fail(job_id: int, request: Request, authorization: str | None = Header(None)):
    need_worker(authorization)
    error = (await request.json()).get('error') or 'failed'
    with lock:
        j = running_job(job_id)
        j.update(status='failed', error=error, lease_until=None)
        db['episodes'][j['slug']].update(state='failed', error=error)
    return {'ok': True}


# ----------------------------------------------------------------------------------------------- HTML

def page(title, body):
    return HTMLResponse(f'<!doctype html><html><head><meta name="viewport" content="width=device-width, '
                        f'initial-scale=1"><link rel="stylesheet" href="/static/app.css"><title>{html.escape(title)}'
                        f'</title></head><body>{body}</body></html>')


def html_session(request):
    return None if session(request) else RedirectResponse('/login', status_code=303)


@app.get('/static/app.css')
async def css():
    return Response('body{font-family:sans-serif;max-width:40rem;margin:auto}', media_type='text/css')


@app.get('/login')
async def login_page():
    return page('Login', '<form method="post" action="/login"><input name="user"><input name="password" '
                         'type="password"><button>Log in</button></form>')


@app.get('/')
async def history(request: Request):
    if r := html_session(request):
        return r
    rows = ''.join(f'<li><a href="/episodes/{e["slug"]}">{html.escape(e["topic"])}</a> {e["state"]} '
                   f'${e["cost_usd"]:.2f}</li>' for e in db['episodes'].values())
    return page('History', f'<ul>{rows}</ul>')


@app.get('/new')
async def new(request: Request):
    if r := html_session(request):
        return r
    return page('New', '<form method="post" action="/episodes"><input name="topic"></form>')


@app.get('/episodes/{slug}')
async def episode_page(slug: str, request: Request):
    if r := html_session(request):
        return r
    ep = ep_or_404(slug)
    return page(ep['topic'], f'<h1>{html.escape(ep["topic"])}</h1><p>{ep["state"]}</p><pre>'
                             f'{html.escape(json.dumps(public(ep))[:2000])}</pre>')


@app.get('/files/{slug}/{name}')
async def files(slug: str, name: str, request: Request, dl: int = 0):
    if r := html_session(request):
        return r
    path = DATA / 'outputs' / slug / name
    if not NAME_OK.match(name) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=name if dl else None,
                        content_disposition_type='attachment' if dl else 'inline')
