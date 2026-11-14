"""Episodes, the job queue, the Mac worker and the gates (docs/WEBAPP.md). Used by the web app and the VPS runner.

Every gate is an explicit user action that goes through the pipeline's own gate code (`stick approve ... --by
'user (web)'`), so the episode's approval log is written exactly as on the command line. The one exception is an
episode the user started with Auto on (user 2026-11-13): then `after_step` makes each gate's decision by the fixed
rules in pipeline/auto.py, through the same gate code, logged with by 'auto (web)'; when a rule cannot decide (a script
that fails its checks, a scene that failed QA) it stops at that gate and says why (episodes.auto_note). Where pitch/script run follows SCRIPT_PROVIDER (settings.vps_kinds / settings.mac_kinds): with `anthropic` they
are paid VPS steps that never retry on their own (a lease that runs out fails them; Retry is a button); with `ollama`
(the default) the Mac runs them like paint/render. A lease that runs out on a Mac job (free work) re-queues it, at
most `max_attempts` times.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import re
import threading
import time
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from sqlalchemy import and_, func, select, update

from . import db
from .db import episodes as E, files as F, jobs as J, push_subscriptions as P, spend_approvals as S, worker as W

RESEARCHING, STORYLINES, SCRIPTING, SCRIPT = 'researching', 'storylines', 'scripting', 'script'
PAINTING, SCENES, RENDERING = 'painting', 'scenes', 'rendering'
DONE, DONE_QA_FAILED, FAILED = 'done', 'done_qa_failed', 'failed'
SPEND_APPROVAL = 'spend_approval'      # addition: a paid step would pass the pre-approved spend; the user decides
STATES = (RESEARCHING, STORYLINES, SCRIPTING, SCRIPT, PAINTING, SCENES, RENDERING, DONE, DONE_QA_FAILED, FAILED,
          SPEND_APPROVAL)
KIND_STATE = {'pitch': RESEARCHING, 'script': SCRIPTING, 'paint': PAINTING, 'render': RENDERING}
LLM_KINDS = ('pitch', 'script')
# SCRIPT_PROVIDER=anthropic routing; the live routing is settings.vps_kinds / settings.mac_kinds
VPS_KINDS, MAC_KINDS = LLM_KINDS, ('paint', 'render')
BY = 'user (web)'
AUTO_BY = 'auto (web)'
# the stepper an Auto episode shows: (label, the states that are this step)
AUTO_STEPS = (('Research', ('researching',)), ('Storyline', ('storylines',)), ('Script', ('scripting', 'script')),
              ('Paint', ('painting', 'scenes')), ('Render', ('rendering',)), ('Done', ('done', 'done_qa_failed')))
NAME_RE = re.compile(r'^[a-z0-9._-]+$')
FILE_KINDS = ('mp4', 'srt', 'qa', 'contact_sheet', 'scene_preview')
CACHE_KIND = 'cache'                   # a pitch/script job's episodes/<slug>/cache/ file (not a user download)
CACHE_FILE_LIMIT, CACHE_TOTAL_LIMIT = 20 * 1024 * 1024, 100 * 1024 * 1024
LLM_WHAT = {'pitch': 'Research/storylines', 'script': 'Script'}
STATE_LABELS = {RESEARCHING: 'Researching', STORYLINES: 'Pick a storyline', SCRIPTING: 'Writing the script',
                SCRIPT: 'Review the script', PAINTING: 'Painting scenes', SCENES: 'Review the scenes',
                RENDERING: 'Rendering', DONE: 'Done', DONE_QA_FAILED: 'Done, QA failed', FAILED: 'Failed',
                SPEND_APPROVAL: 'Waiting: spend'}
GATE_STATES = (STORYLINES, SCRIPT, SCENES, SPEND_APPROVAL, FAILED)


class GateError(Exception):
    """A user action that cannot be done now: HTTP 409 with `errors`."""

    def __init__(self, errors, status=409):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        self.status = status
        super().__init__('; '.join(self.errors))


class NotFound(Exception):
    pass


def safe_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and bool(NAME_RE.match(name)) and name not in ('.', '..') \
        and not name.startswith('.')


def cents_up(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_CEILING)


def _run_cli(argv) -> tuple[int, str]:
    from pipeline import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class Service:
    def __init__(self, settings, engine=None):
        self.settings = settings
        self.engine = engine or db.make_engine(settings.database_url)
        self.gate_lock = threading.Lock()
        settings.apply_pipeline_paths()
        db.init(self.engine)

    # ------------------------------------------------------------------ helpers
    def _episode_row(self, conn, slug):
        row = conn.execute(select(E).where(E.c.slug == slug)).mappings().first()
        if not row:
            raise NotFound(slug)
        return dict(row)

    def episode_row(self, slug):
        with self.engine.connect() as conn:
            return self._episode_row(conn, slug)

    def _set_state(self, conn, slug, state, expect=None, **extra):
        cond = E.c.slug == slug
        if expect is not None:
            cond = and_(cond, E.c.state.in_([expect] if isinstance(expect, str) else list(expect)))
        res = conn.execute(update(E).where(cond).values(state=state, updated_at=db.now(), **extra))
        return res.rowcount == 1

    def _enqueue(self, conn, slug, kind, payload=None):
        res = conn.execute(J.insert().values(slug=slug, kind=kind, status='queued', payload=payload or {}, attempts=0,
                                             created_at=db.now(), progress=dict(stage='queued', pct=0,
                                                                                message=self._queued_message(kind),
                                                                                at=db.iso(db.now()))))
        return res.inserted_primary_key[0]

    @property
    def mac_kinds(self):
        return self.settings.mac_kinds

    @property
    def vps_kinds(self):
        return self.settings.vps_kinds

    def _queued_message(self, kind):
        if kind in LLM_KINDS and kind in self.mac_kinds:
            return {'pitch': 'Waiting for the Mac to research the topic',
                    'script': 'Waiting for the Mac to write the script'}[kind]
        return {'pitch': 'Waiting to research the topic', 'script': 'Waiting to write the script',
                'paint': 'Waiting for the Mac to paint the scenes', 'render': 'Waiting for the Mac to render'}[kind]

    def _load(self, slug):
        from pipeline import store
        return store.load(slug)

    def _ledger_spent(self, slug) -> float:
        from pipeline import config, store
        path = config.EPISODES_DIR / slug / 'ledger.json'
        if not path.exists():
            return 0.0
        from pipeline.cost import Ledger, usd
        return usd(Ledger.open(path, None, default_budget=config.DEFAULT_BUDGET_USD).spent())

    def spend_limit(self, row) -> Decimal:
        lim = row.get('spend_limit_usd')
        return Decimal(str(lim)) if lim is not None else self.settings.spend_preapproved_usd

    # ------------------------------------------------------------------ episodes
    def create_episode(self, topic, niche='', audience='', auto=False):
        from pipeline import config, store
        from pipeline.models import Episode
        topic = ' '.join((topic or '').split())
        niche, audience = ' '.join((niche or '').split()), ' '.join((audience or '').split())
        if not 3 <= len(topic) <= 300:
            raise GateError('Give a topic of 3-300 characters.', status=400)
        if len(niche) > 200 or len(audience) > 200:
            raise GateError('Niche and audience are at most 200 characters each.', status=400)
        with self.gate_lock, self.engine.begin() as conn:
            base_slug = store.slugify(topic)
            slug, n = base_slug, 1
            while conn.execute(select(E.c.slug).where(E.c.slug == slug)).first() or store.exists(slug) \
                    or (config.EPISODES_DIR / slug).exists():
                n += 1
                slug = f'{base_slug[:44]}-{n}'
            episode = Episode(slug=slug, topic=topic, niche=niche, audience=audience)
            store.save(episode, store.folder(slug))
            t = db.now()
            conn.execute(E.insert().values(slug=slug, topic=topic, niche=niche, audience=audience, state=RESEARCHING,
                                           created_at=t, updated_at=t, cost_usd=0.0, error=None, auto=bool(auto)))
            self._enqueue(conn, slug, 'pitch')
        return slug

    def list_episodes(self):
        with self.engine.connect() as conn:
            rows = conn.execute(select(E).order_by(E.c.created_at.desc(), E.c.slug)).mappings().all()
            names = {}
            for f in conn.execute(select(F.c.slug, F.c.name, F.c.kind)).mappings():
                names.setdefault(f['slug'], {})[f['kind']] = f['name']
        return [dict(slug=r['slug'], topic=r['topic'], state=r['state'], cost_usd=round(r['cost_usd'] or 0, 4),
                     created_at=db.iso(r['created_at']), files=names.get(r['slug'], {}), auto=bool(r.get('auto')))
                for r in rows]

    # ------------------------------------------------------------------ queue
    def sweep(self):
        """Expired leases: Mac jobs (free work, incl. pitch/script under ollama) go back to the queue (at most
        max_attempts); VPS (paid) jobs fail."""
        t = db.now()
        mac = self.mac_kinds
        failed = []
        with self.engine.begin() as conn:
            stale = conn.execute(select(J).where(and_(J.c.status == 'running', J.c.lease_until < t))).mappings().all()
            for j in stale:
                if j['kind'] in mac and j['attempts'] < self.settings.max_attempts:
                    conn.execute(update(J).where(and_(J.c.id == j['id'], J.c.status == 'running')).values(
                        status='queued', lease_until=None,
                        progress=dict(stage='queued', pct=0, at=db.iso(t),
                                      message=f'The Mac stopped answering; queued again (attempt {j["attempts"]} of '
                                              f'{self.settings.max_attempts} used)')))
                    conn.execute(update(W).where(W.c.busy_job_id == j['id']).values(busy_job_id=None))
                    continue
                if j['kind'] in mac:
                    why = (f'The Mac stopped answering during {j["kind"]} {self.settings.max_attempts} times; '
                           'press Retry when it is back.')
                else:
                    why = (f'The {j["kind"]} step stopped before it finished. Paid steps are never retried on their '
                           'own: press Retry to run it again.')
                self._fail_job(conn, j, why)
                failed.append(j['slug'])
        for slug in failed:
            self.after_step(slug)

    def _fail_job(self, conn, job, error, state=FAILED, waiting=None):
        t = db.now()
        conn.execute(update(J).where(J.c.id == job['id']).values(status='failed', finished_at=t, lease_until=None,
                                                                 error=error))
        conn.execute(update(E).where(E.c.slug == job['slug']).values(state=state, error=error if state == FAILED else None,
                                                                    waiting=waiting, updated_at=t))
        conn.execute(update(W).where(W.c.busy_job_id == job['id']).values(busy_job_id=None))

    def claim(self, kinds, lease_minutes):
        """Claim the oldest queued job of these kinds; None when there is none. Safe with several claimers."""
        kinds = [k for k in kinds if k in KIND_STATE]
        if not kinds:
            return None
        self.sweep()
        for _ in range(5):
            with self.engine.begin() as conn:
                q = (select(J).where(and_(J.c.status == 'queued', J.c.kind.in_(kinds))).order_by(J.c.id).limit(1)
                     .with_for_update(skip_locked=True))
                j = conn.execute(q).mappings().first()
                if not j:
                    return None
                t = db.now()
                res = conn.execute(update(J).where(and_(J.c.id == j['id'], J.c.status == 'queued')).values(
                    status='running', attempts=J.c.attempts + 1, started_at=t,
                    lease_until=t + dt.timedelta(minutes=lease_minutes),
                    progress=dict(stage='starting', pct=0, message='Started', at=db.iso(t))))
                if res.rowcount != 1:
                    continue
                conn.execute(update(E).where(E.c.slug == j['slug']).values(state=KIND_STATE[j['kind']], error=None,
                                                                          updated_at=t))
                return dict(conn.execute(select(J).where(J.c.id == j['id'])).mappings().first())
        return None

    def job(self, job_id):
        with self.engine.connect() as conn:
            j = conn.execute(select(J).where(J.c.id == job_id)).mappings().first()
        if not j:
            raise NotFound(job_id)
        return dict(j)

    def _running_mac_job(self, conn, job_id):
        j = conn.execute(select(J).where(J.c.id == job_id)).mappings().first()
        if not j:
            raise NotFound(job_id)
        if j['kind'] not in self.mac_kinds:
            raise GateError(f'job {job_id} is not a Mac job')
        if j['status'] != 'running':
            raise GateError(f'job {job_id} is {j["status"]}, not running (its lease may have run out)')
        return dict(j)

    def progress(self, job_id, stage, pct, message, lease_minutes=None):
        lease = lease_minutes or self.settings.mac_lease_minutes
        t = db.now()
        try:
            pct = max(0, min(100, int(float(pct or 0))))
        except (TypeError, ValueError):
            pct = 0
        prog = dict(stage=str(stage or '')[:64], pct=pct, message=str(message or '')[:500], at=db.iso(t))
        with self.engine.begin() as conn:
            j = conn.execute(select(J).where(J.c.id == job_id)).mappings().first()
            if not j:
                raise NotFound(job_id)
            if j['status'] != 'running':
                raise GateError(f'job {job_id} is {j["status"]}, not running')
            conn.execute(update(J).where(J.c.id == job_id).values(progress=prog,
                                                                  lease_until=t + dt.timedelta(minutes=lease)))

    def extend_lease(self, job_id):
        t = db.now()
        with self.engine.begin() as conn:
            conn.execute(update(J).where(and_(J.c.id == job_id, J.c.status == 'running')).values(
                lease_until=t + dt.timedelta(minutes=self.settings.mac_lease_minutes)))

    def worker_done(self, job_id, result):
        result = result if isinstance(result, dict) else {}
        t = db.now()
        with self.engine.begin() as conn:
            j = self._running_mac_job(conn, job_id)
            slug = j['slug']
            if j['kind'] in LLM_KINDS:
                self._llm_done(conn, j, result)
            else:
                self._mac_done(conn, j, result, t)
        self.after_step(slug)

    def _mac_done(self, conn, j, result, t):
        """A paint or render job the Mac finished: the scenes gate (or straight to rendering), or done."""
        job_id, slug = j['id'], j['slug']
        conn.execute(update(J).where(J.c.id == job_id).values(
            status='done', result=result, finished_at=t, lease_until=None,
            progress=dict(stage='done', pct=100, message='Finished', at=db.iso(t))))
        conn.execute(update(W).where(W.c.busy_job_id == job_id).values(busy_job_id=None))
        if j['kind'] == 'paint':
            if result.get('new') or result.get('failed'):
                self._set_state(conn, slug, SCENES, error=None)
            else:                                      # nothing new to look at: straight to rendering
                self._set_state(conn, slug, RENDERING, error=None)
                self._enqueue(conn, slug, 'render', {})
        else:
            self._set_state(conn, slug, DONE if result.get('qa_pass') is True else DONE_QA_FAILED, error=None)

    def _llm_done(self, conn, j, result):
        """A pitch/script job the Mac ran (SCRIPT_PROVIDER=ollama): the result carries the updated episode.json and
        ledger.json (its cache/ files were uploaded first). The same transitions as the VPS runner: storylines gate
        after pitch, script gate after script. An episode the server cannot read fails the job (Retry re-runs it)."""
        from pipeline import store
        from pipeline.models import Episode
        slug, kind = j['slug'], j['kind']
        what = LLM_WHAT[kind]
        try:
            episode = Episode.model_validate(result.get('episode'))
            if episode.slug != slug:
                raise ValueError(f'it is for "{episode.slug}", not "{slug}"')
            if kind == 'pitch' and not episode.storylines:
                raise ValueError('it has no storylines')
            if kind == 'script' and not episode.beats:
                raise ValueError('it has no beats')
            ledger = result.get('ledger')
            if ledger is not None and not (isinstance(ledger, dict) and isinstance(ledger.get('entries'), list)
                                           and isinstance(ledger.get('budget_usd'), (int, float, str))):
                raise ValueError('its ledger.json has no budget_usd and entries list')
        except ValueError as exc:
            msg = ' '.join(str(exc).split())[:1500]
            self._fail_job(conn, j, f'{what} failed: the Mac sent back an episode the server cannot use ({msg}). '
                                    'Press Retry to run it again.')
            return
        base = store.folder(slug)
        store.save(episode, base)
        if ledger is not None:
            tmp = base / 'ledger.json.tmp'
            tmp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
            tmp.replace(base / 'ledger.json')
        notes = [str(n)[:500] for n in (result.get('notes') or []) if isinstance(n, (str, int, float))][:200]
        try:
            cost = float(self._ledger_spent(slug))
        except Exception:                                  # an odd ledger entry must not lose the finished step
            cost = float(self._episode_row(conn, slug).get('cost_usd') or 0)
        t = db.now()
        conn.execute(update(J).where(J.c.id == j['id']).values(
            status='done', finished_at=t, lease_until=None,
            result=dict(notes=notes, ran_on='mac', provider=str(result.get('provider') or 'ollama')[:40]),
            progress=dict(stage='done', pct=100, message='Finished', at=db.iso(t))))
        conn.execute(update(W).where(W.c.busy_job_id == j['id']).values(busy_job_id=None))
        conn.execute(update(E).where(E.c.slug == slug).values(
            state=STORYLINES if kind == 'pitch' else SCRIPT, error=None, waiting=None, cost_usd=cost, updated_at=t))

    def cache_dir(self, slug) -> Path:
        from pipeline import config
        return config.EPISODES_DIR / slug / 'cache'

    def cache_files(self, slug) -> dict:
        """{name: text} of the episode's cache/ JSON (research, storylines, script responses) for a Mac pitch/script
        job, so a retry or the next step reuses them. Failed-response dumps and oversized files stay behind."""
        out, total = {}, 0
        d = self.cache_dir(slug)
        if not d.is_dir():
            return out
        for p in sorted(d.iterdir()):
            if not p.is_file() or not safe_name(p.name) or p.name.startswith('failed-'):
                continue
            size = p.stat().st_size
            if size > CACHE_FILE_LIMIT or total + size > CACHE_TOTAL_LIMIT:
                continue
            try:
                out[p.name] = p.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            total += size
        return out

    def rotation_episodes(self, slug) -> dict:
        """{slug: episode.json} stubs of every other episode with storylines (slug, topic, storylines, the pick and
        its approval log): what the storyline rotation rule reads, so the Mac pitches with the server's history."""
        from pipeline import store
        from pipeline.models import Episode
        out = {}
        for other in store.list_episodes():
            if other == slug:
                continue
            try:
                ep, _ = store.load(other)
            except ValueError:
                continue
            if not ep.storylines:
                continue
            log = [e for e in ep.meta.get('approval_log', []) if isinstance(e, dict) and e.get('gate') == 'storyline']
            stub = Episode(slug=ep.slug, topic=ep.topic, storylines=ep.storylines, storyline_pick=ep.storyline_pick,
                           meta=dict(approval_log=log))
            out[other] = stub.model_dump(mode='json')
        return out

    def worker_fail(self, job_id, error):
        error = (str(error or '') or 'The Mac reported a failure without a message.')[:4000]
        with self.engine.begin() as conn:
            j = self._running_mac_job(conn, job_id)
            self._fail_job(conn, j, error)
        self.after_step(j['slug'])

    def heartbeat(self, host, version, busy_job_id=None):
        t = db.now()
        with self.engine.begin() as conn:
            vals = dict(last_seen=t, host=str(host or '')[:200], version=str(version or '')[:100],
                        busy_job_id=int(busy_job_id) if busy_job_id else None)
            if conn.execute(select(W.c.id).where(W.c.id == 1)).first():
                conn.execute(update(W).where(W.c.id == 1).values(**vals))
            else:
                conn.execute(W.insert().values(id=1, **vals))
            queued = conn.execute(select(func.count()).select_from(J).where(
                and_(J.c.status == 'queued', J.c.kind.in_(self.mac_kinds)))).scalar_one()
        self.sweep()
        return queued

    def touch_worker(self, busy_job_id=None, keep_busy=True):
        """Any authenticated worker call counts as a sign of life."""
        with self.engine.begin() as conn:
            vals = dict(last_seen=db.now())
            if not keep_busy:
                vals['busy_job_id'] = busy_job_id
            elif busy_job_id:
                vals['busy_job_id'] = busy_job_id
            if conn.execute(select(W.c.id).where(W.c.id == 1)).first():
                conn.execute(update(W).where(W.c.id == 1).values(**vals))
            else:
                conn.execute(W.insert().values(id=1, **vals))

    def worker_status(self):
        with self.engine.connect() as conn:
            w = conn.execute(select(W).where(W.c.id == 1)).mappings().first()
            running = conn.execute(select(J.c.id, J.c.kind, J.c.slug).where(
                and_(J.c.status == 'running', J.c.kind.in_(self.mac_kinds)))).mappings().first()
        if not w or not w['last_seen']:
            return dict(online=False, last_seen=None, busy=bool(running), busy_job_id=running['id'] if running else None,
                        host=None, version=None)
        age = (db.now() - w['last_seen']).total_seconds()
        busy_id = w['busy_job_id'] or (running['id'] if running else None)
        return dict(online=age < self.settings.worker_online_seconds, last_seen=db.iso(w['last_seen']),
                    seconds_ago=int(max(age, 0)), busy=bool(busy_id), busy_job_id=busy_id, host=w['host'],
                    version=w['version'])

    def claim_payload(self, j):
        from pipeline import config
        base = config.EPISODES_DIR / j['slug']
        read = lambda p: json.loads(p.read_text()) if p.exists() else {}
        out = dict(job_id=j['id'], kind=j['kind'], slug=j['slug'], payload=j['payload'] or {},
                   episode=read(base / 'episode.json'), ledger=read(base / 'ledger.json'))
        if j['kind'] in LLM_KINDS:                        # a Mac pitch/script (SCRIPT_PROVIDER=ollama)
            out.update(provider=self.settings.script_provider, cache=self.cache_files(j['slug']))
            if j['kind'] == 'pitch':
                out['others'] = self.rotation_episodes(j['slug'])
        return out

    # ------------------------------------------------------------------ files
    def output_path(self, slug, name) -> Path:
        if not safe_name(name) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug or ''):
            raise GateError(f'unsafe file name "{name}" (allowed: a-z 0-9 . _ -)', status=400)
        return self.settings.outputs_dir / slug / name

    def record_file(self, slug, name, kind, size):
        with self.engine.begin() as conn:
            conn.execute(F.delete().where(and_(F.c.slug == slug, F.c.name == name)))
            conn.execute(F.insert().values(slug=slug, name=name, kind=kind, size=size, created_at=db.now()))

    def files_for(self, slug):
        with self.engine.connect() as conn:
            rows = conn.execute(select(F).where(F.c.slug == slug).order_by(F.c.id)).mappings().all()
        return [dict(name=r['name'], kind=r['kind'], size=r['size'], created_at=db.iso(r['created_at']),
                     url=f'/files/{slug}/{r["name"]}?v={int(r["created_at"].timestamp())}',
                     download_url=f'/files/{slug}/{r["name"]}?dl=1') for r in rows]

    # ------------------------------------------------------------------ gates (explicit user actions only)
    def pick_storyline(self, slug, story, by=BY):
        with self.gate_lock:
            row = self.episode_row(slug)
            if row['state'] != STORYLINES:
                raise GateError(f'This episode is at "{row["state"]}", not at the storyline gate.')
            episode, _ = self._load(slug)
            n = _int(story)
            if not episode.storylines or not 1 <= n <= len(episode.storylines):
                raise GateError(f'Pick a storyline 1-{len(episode.storylines)}.')
            code, out = _run_cli(['approve', slug, '--gate', 'storyline', '--story', str(n), '--by', by])
            episode, _ = self._load(slug)
            if code != 0 or episode.storyline_pick != n:
                raise GateError(_clean(out) or f'The storyline gate refused the pick (exit {code}).')
            with self.engine.begin() as conn:
                if not self._set_state(conn, slug, SCRIPTING, expect=STORYLINES, error=None, auto_note=None):
                    raise GateError('The episode changed while picking; reload the page.')
                self._enqueue(conn, slug, 'script')

    def script_errors(self, episode, hook=None):
        """Blocking problems at the script gate: Episode.check errors (after the hook pick) and missing bases."""
        from pipeline import bases, styles
        ep = episode.model_copy(deep=True)
        errors = []
        if ep.hook_options and hook is not None:
            try:
                ep.pick_hook(hook)
            except ValueError as exc:
                return [dict(severity='error', where='hook', message=str(exc))]
        style = styles.load(ep.style)
        errors += [dict(severity=i.severity, where=i.where, message=i.message)
                   for i in ep.check(style.cast_ids, style.words_per_second) if i.severity == 'error']
        try:
            miss = bases.missing(bases.plan(ep))
        except Exception as exc:                          # the base library is part of the repo; report, don't crash
            miss = {}
            errors.append(dict(severity='error', where='bases', message=f'could not read the base library: {exc}'))
        for name, d in miss.items():
            errors.append(dict(severity='error', where='bases',
                               message=f'beats {", ".join(d.get("beats", []))} need a base the library does not have '
                                       f'({name}). New bases are out of scope for the web app in v1: make it on the Mac '
                                       f'(stick bases {episode.slug} --candidates), or edit the script.'))
        return errors

    def approve_script(self, slug, hook, by=BY):
        with self.gate_lock:
            row = self.episode_row(slug)
            if row['state'] != SCRIPT:
                raise GateError(f'This episode is at "{row["state"]}", not at the script gate.')
            episode, _ = self._load(slug)
            argv = ['approve', slug, '--gate', 'script', '--by', by]
            if episode.hook_options:
                n = _int(hook)
                if not 1 <= n <= len(episode.hook_options):
                    raise GateError(f'Pick one of the {len(episode.hook_options)} hooks.')
                argv += ['--hook', str(n)]
            else:
                n = None
            errors = self.script_errors(episode, n)
            if errors:
                raise GateError([f'{e["where"]}: {e["message"]}' for e in errors])
            code, out = _run_cli(argv)
            episode, _ = self._load(slug)
            if code != 0 or not episode.is_approved('script'):
                raise GateError(_clean(out) or f'The script gate refused (exit {code}).')
            with self.engine.begin() as conn:
                if not self._set_state(conn, slug, PAINTING, expect=SCRIPT, error=None, auto_note=None):
                    raise GateError('The episode changed while approving; reload the page.')
                self._enqueue(conn, slug, 'paint', {})

    def scenes(self, slug):
        """Every scene painted since the script was approved: the last full paint, then any redo rounds on top."""
        with self.engine.connect() as conn:
            done = conn.execute(select(J).where(and_(J.c.slug == slug, J.c.kind == 'paint', J.c.status == 'done'))
                                .order_by(J.c.id)).mappings().all()
        out = {}
        for j in done:
            payload, result = j['payload'] or {}, j['result'] or {}
            if not payload.get('redo'):
                out = {}
            for s in result.get('new') or []:
                if isinstance(s, dict) and s.get('key'):
                    out[s['key']] = dict(s, status='new', why='', round=j['id'],
                                         note=(payload.get('redo') or {}).get(s['key'], ''))
            for s in result.get('failed') or []:
                if isinstance(s, dict) and s.get('key'):
                    out[s['key']] = dict(out.get(s['key'], {}), key=s['key'], status='failed', why=s.get('why', ''),
                                         round=j['id'])
        return list(out.values())

    def approve_scenes(self, slug, by=BY):
        from pipeline import store
        from pipeline.models import digest
        with self.gate_lock:
            row = self.episode_row(slug)
            if row['state'] != SCENES:
                raise GateError(f'This episode is at "{row["state"]}", not at the scenes gate.')
            scenes = self.scenes(slug)
            failed = [s['key'] for s in scenes if s['status'] == 'failed']
            if failed:
                raise GateError([f'scene {k} failed on the Mac; redo it with a note first' for k in failed])
            episode, base = self._load(slug)
            keys = sorted(s['key'] for s in scenes)
            episode.meta.setdefault('approval_log', []).append(dict(
                gate='scenes', by=by, at=time.strftime('%Y-%m-%dT%H:%M:%S'), keys=keys,
                digest=digest([dict(key=s['key'], name=s.get('name'), round=s.get('round')) for s in scenes])[:12]))
            store.save(episode, base)
            with self.engine.begin() as conn:
                if not self._set_state(conn, slug, RENDERING, expect=SCENES, error=None, auto_note=None):
                    raise GateError('The episode changed while approving; reload the page.')
                self._enqueue(conn, slug, 'render', {})

    def redo_scene(self, slug, key, note):
        from pipeline import store
        key, note = str(key or '').strip(), ' '.join(str(note or '').split())
        with self.gate_lock:
            row = self.episode_row(slug)
            if row['state'] != SCENES:
                raise GateError(f'This episode is at "{row["state"]}", not at the scenes gate.')
            keys = {s['key'] for s in self.scenes(slug)}
            if key not in keys:
                raise GateError(f'No painted scene "{key}" in this episode.')
            if not note or len(note) > 500:
                raise GateError('Say in a few words (up to 500 characters) how the scene should change.')
            episode, base = self._load(slug)
            episode.meta.setdefault('scene_redo', []).append(dict(key=key, note=note, by=BY,
                                                                  at=time.strftime('%Y-%m-%dT%H:%M:%S')))
            store.save(episode, base)
            with self.engine.begin() as conn:
                if not self._set_state(conn, slug, PAINTING, expect=SCENES, error=None, auto_note=None):
                    raise GateError('The episode changed; reload the page.')
                self._enqueue(conn, slug, 'paint', {'redo': {key: note}})

    def retry(self, slug):
        with self.gate_lock, self.engine.begin() as conn:
            row = self._episode_row(conn, slug)
            if row['state'] != FAILED:
                raise GateError(f'Only a failed episode can be retried; this one is at "{row["state"]}".')
            j = conn.execute(select(J).where(and_(J.c.slug == slug, J.c.status == 'failed')).order_by(J.c.id.desc())
                             .limit(1)).mappings().first()
            if not j:
                raise GateError('There is no failed job to run again.')
            t = db.now()
            conn.execute(update(J).where(J.c.id == j['id']).values(
                status='queued', attempts=0, error=None, lease_until=None, started_at=None, finished_at=None,
                progress=dict(stage='queued', pct=0, message='Queued again by you', at=db.iso(t))))
            self._set_state(conn, slug, KIND_STATE[j['kind']], expect=FAILED, error=None, waiting=None, auto_note=None)

    def allow_spend(self, slug, more_usd=None, by=BY):
        """The user allows the episode to spend more than the pre-approved amount; the stopped step is queued again."""
        with self.gate_lock, self.engine.begin() as conn:
            row = self._episode_row(conn, slug)
            waiting = row.get('waiting') or {}
            if row['state'] != SPEND_APPROVAL or not waiting:
                raise GateError('This episode is not waiting for a spend approval.')
            need = Decimal(str(waiting['need_usd']))
            more = cents_up(more_usd) if more_usd not in (None, '') else need
            if more < need:
                raise GateError(f'The next step needs ${need:.2f} more; allow at least that.')
            if more > Decimal('5'):
                raise GateError('Allow at most $5.00 more at a time.')
            limit = self.spend_limit(row) + more
            t = db.now()
            conn.execute(S.insert().values(slug=slug, more_usd=float(more), limit_usd=float(limit), approved_by=by,
                                           approved_at=t))
            j = conn.execute(select(J).where(J.c.id == waiting['job_id'])).mappings().first()
            if not j or j['status'] != 'failed':
                raise GateError('The stopped step is gone; reload the page.')
            conn.execute(update(J).where(J.c.id == j['id']).values(
                status='queued', attempts=0, error=None, lease_until=None, started_at=None, finished_at=None,
                progress=dict(stage='queued', pct=0, message=f'Queued again: you allowed ${more:.2f} more', at=db.iso(t))))
            self._set_state(conn, slug, KIND_STATE[j['kind']], expect=SPEND_APPROVAL, error=None, waiting=None,
                            spend_limit_usd=float(limit))

    def rerender(self, slug, by=BY):
        """Render a finished video again (e.g. after renderer fixes on the Mac): the same approved script and scenes."""
        with self.gate_lock, self.engine.begin() as conn:
            row = self._episode_row(conn, slug)
            if row['state'] not in (DONE, DONE_QA_FAILED):
                raise GateError(f'Only a finished video can be rendered again; this one is at "{row["state"]}".')
            if not self._set_state(conn, slug, RENDERING, expect=(DONE, DONE_QA_FAILED), error=None, auto_note=None):
                raise GateError('The episode changed; reload the page.')
            self._enqueue(conn, slug, 'render', {'again': True, 'by': by})

    # ------------------------------------------------------------------ auto mode
    def after_step(self, slug):
        """Called after every step finishes (or fails). For an Auto episode, make the decision of the gate it reached
        (pipeline/auto.py rules) or note why it cannot; then notify the phone when there is something to look at.
        Never raises: the step itself is already recorded."""
        try:
            row = self.episode_row(slug)
        except NotFound:
            return
        if row.get('auto') and row['state'] in (STORYLINES, SCRIPT, SCENES):
            try:
                self._auto_advance(slug, row['state'])
            except GateError as exc:
                self._auto_stop(slug, f'Auto mode stopped at "{STATE_LABELS.get(row["state"], row["state"])}": '
                                      + '; '.join(exc.errors)[:1500])
            except Exception as exc:                  # noqa: BLE001
                self._auto_stop(slug, f'Auto mode stopped: {type(exc).__name__}: {str(exc)[:500]}')
            row = self.episode_row(slug)
        state = row['state']
        if state in (DONE, DONE_QA_FAILED):
            self.notify(slug, 'Your video is ready', f'{row["topic"]}: ' + (
                'every QA check passed' if state == DONE else 'done, but some QA checks failed'))
        elif state == FAILED:
            self.notify(slug, 'A step failed', f'{row["topic"]}: {(row.get("error") or "")[:140]}')
        elif row.get('auto') and row.get('auto_note'):
            self.notify(slug, 'Auto mode needs you', f'{row["topic"]}: {row["auto_note"][:160]}')

    def _auto_advance(self, slug, state):
        from pipeline import auto, store, styles
        episode, base = self._load(slug)
        if state == STORYLINES:
            n, why = auto.first_storyline(episode)
            if n is None:
                return self._auto_stop(slug, f'Auto mode stopped at the storylines: {why}. Pick one yourself.')
            self.pick_storyline(slug, n, by=AUTO_BY)
        elif state == SCRIPT and not episode.is_approved('script'):
            notes = auto.resolve_bases(episode)
            for n in notes:
                auto.note(episode, 'bases', n, by=AUTO_BY)
            if notes:
                store.save(episode, base)
            hook, problems = auto.pick_hook(episode, styles.load(episode.style))
            if hook is None:
                return self._auto_stop(slug, 'Auto mode stopped at the script: it fails these checks, so it was not '
                                             'approved: ' + '; '.join(problems[:6]))
            self.approve_script(slug, hook or None, by=AUTO_BY)
        elif state == SCENES:
            failed = [s['key'] for s in self.scenes(slug) if s['status'] == 'failed']
            if failed:
                return self._auto_stop(slug, f'Auto mode stopped at the scenes: {", ".join(failed)} failed QA on the '
                                             'Mac, and the video cannot be rendered without them. Redo them with a note.')
            self.approve_scenes(slug, by=AUTO_BY)

    def _auto_stop(self, slug, note):
        with self.engine.begin() as conn:
            conn.execute(update(E).where(E.c.slug == slug).values(auto_note=note[:2000], updated_at=db.now()))

    # ------------------------------------------------------------------ push notifications (webapp/push.py)
    push_sender = None                                # tests: a stand-in for push.send
    push_sync = False                                 # tests: send in this thread

    def push_subscribe(self, sub):
        if not self.settings.push_enabled:
            raise GateError('Notifications are not set up on the server (VAPID keys).', status=400)
        keys = (sub or {}).get('keys') or {}
        endpoint, p256dh, auth_ = str(sub.get('endpoint') or ''), str(keys.get('p256dh') or ''), str(keys.get('auth') or '')
        if not endpoint.startswith('https://') or len(endpoint) > 2000 or not p256dh or not auth_ \
                or len(p256dh) > 200 or len(auth_) > 100:
            raise GateError('That is not a push subscription.', status=400)
        with self.engine.begin() as conn:
            conn.execute(P.delete().where(P.c.endpoint == endpoint))
            conn.execute(P.insert().values(endpoint=endpoint, p256dh=p256dh, auth=auth_, created_at=db.now()))
            n = conn.execute(select(func.count()).select_from(P)).scalar_one()
            if n > 20:                                # one user: keep the newest twenty devices
                old = [r[0] for r in conn.execute(select(P.c.id).order_by(P.c.id).limit(n - 20))]
                conn.execute(P.delete().where(P.c.id.in_(old)))

    def push_unsubscribe(self, endpoint):
        with self.engine.begin() as conn:
            conn.execute(P.delete().where(P.c.endpoint == str(endpoint or '')))

    def push_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(P)).scalar_one()

    def notify(self, slug, title, body, url=None):
        """A notification to every subscribed device; dead subscriptions (404/410) are removed. Never raises."""
        if not self.settings.push_enabled:
            return
        with self.engine.connect() as conn:
            subs = [dict(r) for r in conn.execute(select(P)).mappings()]
        if not subs:
            return
        payload = dict(title=title[:80], body=body[:240], url=url or (f'/episodes/{slug}' if slug else '/'), tag=slug or 'stick')
        from . import push
        send = self.push_sender or push.send

        def run():
            for s in subs:
                code = send(dict(endpoint=s['endpoint'], keys=dict(p256dh=s['p256dh'], auth=s['auth'])), payload,
                            self.settings.vapid_private_key, self.settings.push_subject)
                try:
                    with self.engine.begin() as conn:
                        if code in (404, 410):
                            conn.execute(P.delete().where(P.c.id == s['id']))
                        elif 200 <= code < 300:
                            conn.execute(update(P).where(P.c.id == s['id']).values(last_ok_at=db.now()))
                except Exception:                     # noqa: BLE001
                    pass
        if self.push_sync:
            run()
        else:
            threading.Thread(target=run, daemon=True, name='push').start()

    # ------------------------------------------------------------------ views
    def current_job(self, slug):
        with self.engine.connect() as conn:
            j = conn.execute(select(J).where(J.c.slug == slug).order_by(J.c.id.desc()).limit(1)).mappings().first()
        if not j:
            return None
        return dict(id=j['id'], kind=j['kind'], status=j['status'], progress=j['progress'] or {}, attempts=j['attempts'],
                    error=j['error'], created_at=db.iso(j['created_at']), started_at=db.iso(j['started_at']))

    def status(self, slug):
        """The cheap part of `detail` (state, job progress, worker, cost): what the pages poll every 3 s."""
        self.sweep()
        row = self.episode_row(slug)
        state = row['state']
        job = self.current_job(slug)
        return dict(slug=slug, topic=row['topic'], niche=row['niche'], audience=row['audience'], state=state,
                    state_label=STATE_LABELS.get(state, state), cost_usd=round(row['cost_usd'] or 0, 4),
                    error=row['error'], created_at=db.iso(row['created_at']), updated_at=db.iso(row['updated_at']),
                    job=job, worker=self.worker_status(),
                    spend=dict(limit_usd=float(self.spend_limit(row)), waiting=row.get('waiting')),
                    auto=bool(row.get('auto')), auto_note=row.get('auto_note'),
                    steps=self.steps(state, job) if row.get('auto') else [])

    @staticmethod
    def steps(state, job=None):
        """The Auto stepper: each step done, current, failed or to do."""
        here = next((i for i, (_, states) in enumerate(AUTO_STEPS) if state in states), None)
        failed = state in (FAILED, SPEND_APPROVAL)
        if here is None and job:                      # failed or waiting: the step of the job that stopped
            here = next((i for i, (_, states) in enumerate(AUTO_STEPS) if KIND_STATE.get(job['kind']) in states), 0)
        here = 0 if here is None else here
        done_all = state in (DONE, DONE_QA_FAILED)
        return [dict(label=label, status=('done' if i < here or done_all else
                                          ('failed' if failed else 'current') if i == here else 'todo'))
                for i, (label, _) in enumerate(AUTO_STEPS)]

    def detail(self, slug):
        out = self.status(slug)
        try:
            episode, _ = self._load(slug)
        except ValueError as exc:
            out['episode_error'] = str(exc)
            return out
        if episode.storylines:
            out['storylines'] = self.storylines_view(episode)
        if episode.beats:
            out['script'] = self.script_view(episode)
        scenes = self.scenes(slug)
        files = self.files_for(slug)
        if scenes:
            by_name = {f['name']: f for f in files}
            say = {b.id: b.narration for b in episode.beats}
            for s in scenes:
                f = by_name.get(s.get('name') or '')
                s['url'] = f['url'] if f else None
                s['beat_text'] = [dict(id=b, say=say.get(b, '')) for b in (s.get('beats') or []) if isinstance(b, str)]
            out['scenes'] = scenes
        if files:
            out['files'] = files
        render = self._last_result(slug, 'render')
        if render is not None:
            out['render'] = dict(duration=render.get('duration'), qa_pass=render.get('qa_pass'),
                                 qa_checks=self.qa_checks(slug, render))
        return out

    def _last_result(self, slug, kind):
        with self.engine.connect() as conn:
            j = conn.execute(select(J.c.result).where(and_(J.c.slug == slug, J.c.kind == kind, J.c.status == 'done'))
                             .order_by(J.c.id.desc()).limit(1)).first()
        return (j[0] or {}) if j else None

    def qa_checks(self, slug, render):
        summary = render.get('qa_summary')
        if not isinstance(summary, (dict, list)):
            path = self.settings.outputs_dir / slug / 'qa.json'
            try:
                summary = json.loads(path.read_text()).get('summary')
            except (OSError, ValueError, AttributeError):
                summary = None
        checks = []
        if isinstance(summary, dict):
            for k, v in summary.items():
                ok = v if isinstance(v, bool) else (v.get('pass', v.get('pass_', v.get('ok'))) if isinstance(v, dict) else None)
                checks.append(dict(name=str(k), ok=ok if isinstance(ok, bool) else None,
                                   detail='' if isinstance(v, bool) else str(v)[:300]))
        elif isinstance(summary, list):
            for item in summary:
                if isinstance(item, dict):
                    checks.append(dict(name=str(item.get('name', item.get('check', '?'))),
                                       ok=item.get('pass', item.get('ok')), detail=str(item.get('detail', ''))[:300]))
        elif isinstance(render.get('qa_summary'), str):
            checks.append(dict(name='summary', ok=render.get('qa_pass'), detail=render['qa_summary'][:500]))
        return checks

    def storylines_view(self, episode):
        from pipeline import story
        last, last_slug = story.last_picked_format(episode.slug)
        src = {s.id: s for s in episode.sources}
        cards = []
        for k, s in enumerate(episode.storylines, 1):
            refused = ''
            if s.format == last and not episode.storyline_pick:
                refused = (f'A {s.format}, the same format as the last video ({last_slug}); never the same format '
                           'twice in a row.')
            cards.append(dict(n=k, format=s.format, format_label=s.format.replace('_', ' '), title=s.title, hook=s.hook,
                              pitch=s.pitch, conflict=s.conflict, stakes=s.stakes, twist=s.twist, payoff=s.payoff,
                              visuals=list(s.visuals), refused=refused, picked=episode.storyline_pick == k,
                              facts=[dict(id=i, claim=src[i].claim if i in src else '', url=src[i].url if i in src else '',
                                          title=src[i].title if i in src else '', known=i in src) for i in s.fact_ids]))
        return dict(cards=cards, pick=episode.storyline_pick, note=episode.story_note, text=story.view(episode))

    def script_view(self, episode):
        from pipeline import styles
        from pipeline.review import script_view as text_view
        style = styles.load(episode.style)
        issues = episode.check(style.cast_ids, style.words_per_second)
        src = {s.id: s for s in episode.sources}
        beats = []
        for b in episode.beats:
            host = ''
            h = b.shot.host
            if h is not None:
                host = f'{h.where} · {h.side}' + (f' · {h.size}' if episode.storyline_pick else '')
                host += f' · holds {h.holds}' if getattr(h, 'holds', '') else ''
                host += f' · {h.doing}' + (' · to camera' if h.to_camera else '')
            scene = ''
            if b.shot.visual in ('insert', 'split') and b.shot.insert:
                scene = f'{b.shot.insert.key}: {b.shot.insert.scene}'
            elif b.shot.visual == 'graphic' and b.shot.graphic:
                scene = f'{b.shot.graphic.kind}: ' + ' | '.join(b.shot.graphic.items)
            beats.append(dict(id=b.id, role=b.role, say=b.narration, show=b.visual_intent, host=host, scene=scene,
                              visual=b.shot.visual, on_screen_text=b.on_screen_text,
                              example=b.illustrative_numbers,
                              sources=[dict(id=i, url=src[i].url if i in src else '', title=src[i].title if i in src else '',
                                            claim=src[i].claim if i in src else '') for i in b.source_ids]))
        blocking = self.script_errors(episode, episode.hook_pick or (1 if episode.hook_options else None))
        hook_errors = {str(k): [f'{e["where"]}: {e["message"]}' for e in self.script_errors(episode, k)]
                       for k in range(1, len(episode.hook_options) + 1)}
        story_check = [dict(item=i, ok=ok, detail=d) for i, ok, d in episode.story_report()] \
            if episode.host_format and episode.storyline_pick else []
        sl = episode.storyline
        return dict(title=episode.title, hook_promise=episode.hook_promise, words=episode.word_count(),
                    seconds=round(episode.word_count() / style.words_per_second),
                    target_length=episode.target_length, approved=episode.is_approved('script'),
                    storyline=dict(n=episode.storyline_pick, format=sl.format, title=sl.title, pitch=sl.pitch) if sl else None,
                    hook_options=[dict(n=k, line=h.line, frame_one=h.frame_one, answer=h.answer, loop_line=h.loop_line)
                                  for k, h in enumerate(episode.hook_options, 1)],
                    hook_pick=episode.hook_pick, beats=beats, story_check=story_check,
                    issues=[dict(severity=i.severity, where=i.where, message=i.message) for i in issues],
                    errors=blocking, can_approve=not blocking, hook_errors=hook_errors,
                    blocked_reason=('Approve is off until these are fixed: ' + '; '.join(
                        f'{e["where"]}: {e["message"]}' for e in blocking[:5])) if blocking else '',
                    sources=[dict(id=s.id, claim=s.claim, url=s.url, title=s.title) for s in episode.sources],
                    text=text_view(episode, style, issues))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clean(out: str) -> str:
    return '\n'.join(line.rstrip() for line in out.strip().splitlines() if line.strip())[:2000]
