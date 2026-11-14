"""FastAPI app `webapp.app:app` (docs/WEBAPP.md): browser pages, the JSON API and the Mac worker API.

Browser pages and the JSON API share one signed session cookie (HttpOnly, SameSite=Lax, Secure when PUBLIC_URL is
https). The worker API accepts only `Authorization: Bearer <token>` whose sha256 equals WORKER_TOKEN_SHA256.
Every gate is a user action, except in an episode the user started with Auto on (core.Service.after_step).
"""
from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, core, db
from .settings import Settings
from .uploads import UploadError, receive_upload

HERE = Path(__file__).resolve().parent
VERSION = '1'
LOGIN_MAX_FAILURES, LOGIN_WINDOW_S = 10, 15 * 60


class AuthRequired(Exception):
    pass


def create_app(settings: Settings | None = None, service: core.Service | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    service = service or core.Service(settings)
    app = FastAPI(title='Stick Figures', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.service = settings, service
    templates = Jinja2Templates(directory=str(HERE / 'templates'))
    templates.env.globals.update(state_labels=core.STATE_LABELS)
    app.mount('/static', StaticFiles(directory=str(HERE / 'static')), name='static')

    # ------------------------------------------------------------------ auth helpers
    def user_of(request: Request):
        return auth.read_session(settings, request.cookies.get(auth.COOKIE))

    def same_origin(request: Request) -> bool:
        """Browser POSTs: a cross-site Origin is refused (SameSite=Lax already keeps the cookie off them)."""
        origin = request.headers.get('origin')
        if not origin or origin == 'null':
            return origin != 'null'
        host = request.headers.get('host', '')
        allowed = {host}
        if settings.public_url:
            allowed.add(urlsplit(settings.public_url).netloc)
        return urlsplit(origin).netloc in allowed

    def api_user(request: Request):
        if not user_of(request):
            raise AuthRequired()

    failures = {}                                       # client -> [failure times]; in memory, one process

    def client_key(request: Request) -> str:
        fwd = request.headers.get('x-forwarded-for', '')
        return (fwd.split(',')[0].strip() if fwd else '') or (request.client.host if request.client else '?')

    def throttled(request: Request) -> bool:
        now = time.monotonic()
        recent = [t for t in failures.get(client_key(request), []) if now - t < LOGIN_WINDOW_S]
        failures[client_key(request)] = recent
        return len(recent) >= LOGIN_MAX_FAILURES

    def login_ok(request: Request, user: str, password: str) -> bool:
        if auth.check_login(settings, user, password):
            failures.pop(client_key(request), None)
            return True
        failures.setdefault(client_key(request), []).append(time.monotonic())
        return False

    def worker_ok(request: Request) -> bool:
        return auth.worker_token_ok(settings, request.headers.get('authorization'))

    @app.exception_handler(AuthRequired)
    async def _auth_required(request: Request, exc):
        return JSONResponse({'ok': False, 'error': 'login required'}, status_code=401)

    @app.exception_handler(core.NotFound)
    async def _not_found(request: Request, exc):
        return JSONResponse({'ok': False, 'error': f'not found: {exc}'}, status_code=404)

    @app.exception_handler(core.Moved)
    async def _moved(request: Request, exc: core.Moved):
        # topic understanding renamed the episode after the typed slug was made: old links follow it
        url = str(request.url).replace(f'/{exc.old}', f'/{exc.new}', 1)
        return RedirectResponse(url, status_code=307)

    @app.exception_handler(core.GateError)
    async def _gate_error(request: Request, exc: core.GateError):
        return JSONResponse({'ok': False, 'errors': exc.errors}, status_code=exc.status)

    async def body_json(request: Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            data = None
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise core.GateError('send a JSON object', status=400)
        return data

    def page(request, name, status=200, **ctx):
        ctx.setdefault('user', user_of(request))
        ctx.setdefault('fake', settings.fake_anthropic and not settings.llm_on_mac)
        ctx.setdefault('llm_on_mac', settings.llm_on_mac)     # SCRIPT_PROVIDER=ollama: research/script on the Mac
        ctx.setdefault('mac_kinds', settings.mac_kinds)
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def no_store(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.middleware('http')
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        return response

    # ------------------------------------------------------------------ health (no auth, no secrets)
    @app.get('/api/health')
    def health():
        ok_db = db.ping(service.engine)
        w = service.worker_status() if ok_db else dict(online=False, last_seen=None, busy=False)
        return no_store(JSONResponse({'ok': ok_db, 'db': ok_db,
                                      'worker': dict(online=w['online'], last_seen=w['last_seen'], busy=w['busy'])},
                                     status_code=200 if ok_db else 503))

    # ------------------------------------------------------------------ browser: login
    @app.get('/login', response_class=HTMLResponse)
    def login_page(request: Request):
        if user_of(request):
            return RedirectResponse('/', status_code=303)
        return page(request, 'login.html', error='')

    @app.post('/login')
    async def login_form(request: Request):
        if not same_origin(request):
            return Response('cross-site request refused', status_code=403)
        if throttled(request):
            return page(request, 'login.html', status=429, error='Too many wrong tries. Wait 15 minutes.', user=None)
        form = await request.form()
        user, password = str(form.get('user', '')), str(form.get('password', ''))
        if not login_ok(request, user, password):
            return page(request, 'login.html', status=401, error='Wrong user name or password.', user=None)
        response = RedirectResponse('/', status_code=303)
        auth.set_cookie(response, settings, user)
        return response

    @app.post('/logout')
    def logout(request: Request):
        response = RedirectResponse('/login', status_code=303)
        auth.clear_cookie(response, settings)
        return response

    # ------------------------------------------------------------------ browser: pages
    def html_user(request):
        return user_of(request)

    @app.get('/', response_class=HTMLResponse)
    def history(request: Request):
        if not html_user(request):
            return RedirectResponse('/login', status_code=303)
        return no_store(page(request, 'history.html', episodes=service.list_episodes(), worker=service.worker_status()))

    @app.get('/new', response_class=HTMLResponse)
    def new_page(request: Request):
        if not html_user(request):
            return RedirectResponse('/login', status_code=303)
        return page(request, 'new.html', error='', form={})

    @app.post('/episodes')
    async def create_form(request: Request):
        if not html_user(request):
            return RedirectResponse('/login', status_code=303)
        if not same_origin(request):
            return Response('cross-site request refused', status_code=403)
        form = await request.form()
        data = {k: str(form.get(k, '')) for k in ('topic', 'niche', 'audience')}
        auto = str(form.get('auto', '')).lower() in ('1', 'on', 'true', 'yes')
        try:
            slug = service.create_episode(**data, auto=auto)
        except core.GateError as exc:
            return page(request, 'new.html', status=400, error=' '.join(exc.errors), form=dict(data, auto=auto))
        return RedirectResponse(f'/episodes/{slug}', status_code=303)

    def episode_page(request, slug, errors=(), status=200):
        try:
            d = service.detail(slug)
        except core.NotFound:
            return page(request, 'message.html', status=404, title='Not found', message=f'No episode "{slug}".')
        return no_store(page(request, 'episode.html', status=status, ep=d, errors=list(errors)))

    @app.get('/episodes/{slug}', response_class=HTMLResponse)
    def episode_view(request: Request, slug: str):
        if not html_user(request):
            return RedirectResponse('/login', status_code=303)
        return episode_page(request, slug)

    # browser twins of the JSON gate actions (plain forms; no JavaScript needed)
    async def form_action(request: Request, slug: str, action):
        if not html_user(request):
            return RedirectResponse('/login', status_code=303)
        if not same_origin(request):
            return Response('cross-site request refused', status_code=403)
        form = await request.form()
        try:
            action({k: str(v) for k, v in form.items()})
        except core.NotFound:
            return page(request, 'message.html', status=404, title='Not found', message=f'No episode "{slug}".')
        except core.GateError as exc:
            return episode_page(request, slug, errors=exc.errors, status=exc.status)
        return RedirectResponse(f'/episodes/{slug}', status_code=303)

    @app.post('/episodes/{slug}/storyline')
    async def storyline_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.pick_storyline(slug, f.get('story')))

    @app.post('/episodes/{slug}/script/approve')
    async def script_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.approve_script(slug, f.get('hook')))

    @app.post('/episodes/{slug}/scenes/approve')
    async def scenes_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.approve_scenes(slug))

    @app.post('/episodes/{slug}/scenes/redo')
    async def redo_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.redo_scene(slug, f.get('key'), f.get('note')))

    @app.post('/episodes/{slug}/retry')
    async def retry_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.retry(slug))

    @app.post('/episodes/{slug}/render-again')
    async def rerender_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.rerender(slug, by=f'{user_of(request)} (web)'))

    @app.post('/episodes/{slug}/spend/allow')
    async def spend_form(request: Request, slug: str):
        return await form_action(request, slug, lambda f: service.allow_spend(slug, f.get('more_usd') or None,
                                                                              by=f'{user_of(request)} (web)'))

    # ------------------------------------------------------------------ files
    @app.get('/files/{slug}/{name}')
    def get_file(request: Request, slug: str, name: str, dl: int = 0):
        if not user_of(request):
            return JSONResponse({'ok': False, 'error': 'login required'}, status_code=401)
        if not core.safe_name(name):
            return JSONResponse({'ok': False, 'error': 'bad file name'}, status_code=400)
        try:
            path = service.output_path(slug, name)
        except core.GateError as exc:
            return JSONResponse({'ok': False, 'errors': exc.errors}, status_code=400)
        root = service.settings.outputs_dir.resolve()
        real = path.resolve()
        if root not in real.parents or not real.is_file():
            return JSONResponse({'ok': False, 'error': 'not found'}, status_code=404)
        media = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        if name.endswith('.srt'):
            media = 'text/plain; charset=utf-8' if not dl else 'application/x-subrip'
        return FileResponse(real, media_type=media, filename=name if dl else None,
                            content_disposition_type='attachment' if dl else 'inline',
                            headers={'Cache-Control': 'private, max-age=60'})

    # ------------------------------------------------------------------ JSON API
    @app.post('/api/login')
    async def api_login(request: Request):
        if throttled(request):
            return JSONResponse({'ok': False, 'error': 'too many wrong tries; wait 15 minutes'}, status_code=429)
        data = await body_json(request)
        user = str(data.get('user', ''))
        if not login_ok(request, user, str(data.get('password', ''))):
            return JSONResponse({'ok': False, 'error': 'wrong user name or password'}, status_code=401)
        response = JSONResponse({'ok': True})
        auth.set_cookie(response, settings, user)
        return response

    @app.post('/api/logout')
    def api_logout():
        response = JSONResponse({'ok': True})
        auth.clear_cookie(response, settings)
        return response

    @app.post('/api/episodes')
    async def api_create(request: Request):
        api_user(request)
        data = await body_json(request)
        slug = service.create_episode(str(data.get('topic', '')), str(data.get('niche') or ''),
                                      str(data.get('audience') or ''), auto=data.get('auto') is True)
        return {'ok': True, 'slug': slug}

    @app.get('/api/episodes')
    def api_list(request: Request):
        api_user(request)
        return no_store(JSONResponse([{k: e[k] for k in ('slug', 'topic', 'state', 'cost_usd', 'created_at')}
                                      for e in service.list_episodes()]))

    @app.get('/api/episodes/{slug}')
    def api_detail(request: Request, slug: str, view: str = ''):
        api_user(request)
        return no_store(JSONResponse(service.status(slug) if view == 'status' else service.detail(slug)))

    @app.post('/api/episodes/{slug}/storyline')
    async def api_storyline(request: Request, slug: str):
        api_user(request)
        service.pick_storyline(slug, (await body_json(request)).get('story'))
        return {'ok': True}

    @app.post('/api/episodes/{slug}/script/approve')
    async def api_script(request: Request, slug: str):
        api_user(request)
        service.approve_script(slug, (await body_json(request)).get('hook'))
        return {'ok': True}

    @app.post('/api/episodes/{slug}/scenes/approve')
    async def api_scenes(request: Request, slug: str):
        api_user(request)
        await body_json(request)
        service.approve_scenes(slug)
        return {'ok': True}

    @app.post('/api/episodes/{slug}/scenes/redo')
    async def api_redo(request: Request, slug: str):
        api_user(request)
        data = await body_json(request)
        service.redo_scene(slug, data.get('key'), data.get('note'))
        return {'ok': True}

    @app.post('/api/episodes/{slug}/retry')
    async def api_retry(request: Request, slug: str):
        api_user(request)
        service.retry(slug)
        return {'ok': True}

    @app.post('/api/episodes/{slug}/render-again')
    async def api_rerender(request: Request, slug: str):
        api_user(request)
        service.rerender(slug, by=f'{user_of(request)} (web)')
        return {'ok': True}

    # ------------------------------------------------------------------ notifications (Web Push; webapp/push.py)
    @app.get('/api/push/key')
    def push_key(request: Request):
        api_user(request)
        return no_store(JSONResponse({'enabled': settings.push_enabled, 'public_key': settings.vapid_public_key,
                                      'devices': service.push_count()}))

    @app.post('/api/push/subscribe')
    async def push_subscribe(request: Request):
        api_user(request)
        if not same_origin(request):
            return Response('cross-site request refused', status_code=403)
        service.push_subscribe((await body_json(request)).get('subscription') or {})
        return {'ok': True}

    @app.post('/api/push/unsubscribe')
    async def push_unsubscribe(request: Request):
        api_user(request)
        if not same_origin(request):
            return Response('cross-site request refused', status_code=403)
        service.push_unsubscribe((await body_json(request)).get('endpoint'))
        return {'ok': True}

    @app.post('/api/push/test')
    async def push_test(request: Request):
        api_user(request)
        if not same_origin(request):
            return Response('cross-site request refused', status_code=403)
        service.notify('', 'Notifications are on', 'You will hear from Stick Figures when a video is ready.', url='/')
        return {'ok': True}

    # the service worker must be served from the root to control every page; the manifest makes the site installable
    @app.get('/sw.js')
    def service_worker():
        r = FileResponse(HERE / 'static' / 'sw.js', media_type='application/javascript')
        r.headers['Cache-Control'] = 'no-cache'
        return r

    @app.get('/manifest.webmanifest')
    def manifest():
        return FileResponse(HERE / 'static' / 'manifest.webmanifest', media_type='application/manifest+json')

    @app.post('/api/episodes/{slug}/spend/allow')
    async def api_spend(request: Request, slug: str):
        api_user(request)
        data = await body_json(request)
        service.allow_spend(slug, data.get('more_usd'), by=f'{user_of(request)} (web)')
        return {'ok': True}

    # ------------------------------------------------------------------ worker API (bearer only)
    def worker_denied():
        return JSONResponse({'ok': False, 'error': 'worker token required'}, status_code=401)

    @app.post('/api/worker/heartbeat')
    async def w_heartbeat(request: Request):
        if not worker_ok(request):
            return worker_denied()
        data = await body_json(request)
        queued = service.heartbeat(data.get('host'), data.get('version'), data.get('busy_job_id'))
        return {'ok': True, 'queued': queued, 'kinds': list(settings.mac_kinds)}

    @app.post('/api/worker/claim')
    async def w_claim(request: Request):
        if not worker_ok(request):
            return worker_denied()
        data = await body_json(request)
        allowed = settings.mac_kinds                   # pitch/script only with SCRIPT_PROVIDER=ollama
        kinds = [k for k in (data.get('kinds') or list(allowed)) if k in allowed]
        service.touch_worker()
        job = service.claim(kinds, settings.mac_lease_minutes)
        if not job:
            return Response(status_code=204)
        service.touch_worker(busy_job_id=job['id'])
        return JSONResponse(service.claim_payload(job))

    @app.post('/api/worker/jobs/{job_id}/progress')
    async def w_progress(request: Request, job_id: int):
        if not worker_ok(request):
            return worker_denied()
        data = await body_json(request)
        service.progress(job_id, data.get('stage'), data.get('pct'), data.get('message'))
        service.touch_worker(busy_job_id=job_id)
        return {'ok': True}

    @app.post('/api/worker/jobs/{job_id}/files')
    async def w_files(request: Request, job_id: int):
        if not worker_ok(request):
            return worker_denied()
        with service.engine.connect() as conn:
            job = service._running_mac_job(conn, job_id)
        slug = job['slug']
        llm = job['kind'] in core.LLM_KINDS               # a Mac pitch/script: only its cache/ files come up
        out_dir = service.cache_dir(slug) if llm else service.settings.outputs_dir / slug
        try:
            fields, tmp, size = await receive_upload(request, out_dir, settings.upload_limit_bytes)
        except UploadError as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=exc.status)
        except Exception as exc:                       # a malformed multipart body
            return JSONResponse({'ok': False, 'error': f'bad upload: {exc.__class__.__name__}'}, status_code=400)
        name, kind = fields.get('name', '').strip(), fields.get('kind', '').strip()
        if llm:
            if not core.safe_name(name) or kind != core.CACHE_KIND or size > core.CACHE_FILE_LIMIT:
                tmp.unlink(missing_ok=True)
                return JSONResponse({'ok': False, 'error': f'a {job["kind"]} job uploads only its cache files: kind '
                                                           f'"{core.CACHE_KIND}", name [a-z0-9._-]+, at most '
                                                           f'{core.CACHE_FILE_LIMIT // (1024 * 1024)} MB'},
                                    status_code=400)
            os.replace(tmp, out_dir / name)
            service.extend_lease(job_id)
            service.touch_worker(busy_job_id=job_id)
            return {'ok': True, 'name': name, 'size': size}
        if not core.safe_name(name) or kind not in core.FILE_KINDS:
            tmp.unlink(missing_ok=True)
            return JSONResponse({'ok': False, 'error': f'name must match [a-z0-9._-]+ and kind be one of '
                                                       f'{", ".join(core.FILE_KINDS)}'}, status_code=400)
        dest = service.output_path(slug, name)
        os.replace(tmp, dest)
        service.record_file(slug, name, kind, size)
        service.extend_lease(job_id)
        service.touch_worker(busy_job_id=job_id)
        return {'ok': True, 'name': name, 'size': size}

    @app.post('/api/worker/jobs/{job_id}/done')
    async def w_done(request: Request, job_id: int):
        if not worker_ok(request):
            return worker_denied()
        data = await body_json(request)
        service.worker_done(job_id, data.get('result') or {})
        service.touch_worker(busy_job_id=None, keep_busy=False)
        return {'ok': True}

    @app.post('/api/worker/jobs/{job_id}/fail')
    async def w_fail(request: Request, job_id: int):
        if not worker_ok(request):
            return worker_denied()
        data = await body_json(request)
        service.worker_fail(job_id, data.get('error'))
        service.touch_worker(busy_job_id=None, keep_busy=False)
        return {'ok': True}

    return app


class _LazyApp:
    """`webapp.app:app` for uvicorn: built on first use, so importing this module needs no environment."""

    def __init__(self):
        self._app = None

    def _get(self):
        if self._app is None:
            self._app = create_app()
        return self._app

    async def __call__(self, scope, receive, send):
        await self._get()(scope, receive, send)

    def __getattr__(self, name):
        return getattr(self._get(), name)


app = _LazyApp()
