"""`python -m webapp.runner`: the VPS runner. Runs queued `pitch` and `script` jobs one at a time, in-process,
through the existing pipeline (research + storylines, then the script), with STICK_EPISODES_DIR=$STICK_DATA_DIR/episodes.

Spend rule: before every paid call the pipeline reserves its worst-case estimate in the episode ledger. The runner's
ledger also refuses (nothing is sent) when the ledger total + that estimate would pass the episode's pre-approved
amount (SPEND_PREAPPROVED_USD, raised only by the user's "Allow up to $X more"). The episode then waits in
`spend_approval`. Paid steps never retry on their own; a failure waits for the user's Retry.

STICK_FAKE_ANTHROPIC=1 uses webapp.fake (the fixtures' responses, $0) and nothing paid is ever sent.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
import traceback
from decimal import Decimal

from sqlalchemy import update

from . import core, db
from .db import episodes as E, jobs as J
from .settings import Settings

log = logging.getLogger('stick.runner')
log.addHandler(logging.NullHandler())


class SpendStop(Exception):
    def __init__(self, step, estimate, spent, limit):
        self.step, self.estimate, self.spent, self.limit = step, Decimal(estimate), Decimal(spent), Decimal(limit)
        self.need = core.cents_up(self.spent + self.estimate - self.limit)
        super().__init__(f'The {step} step needs up to ${self.estimate:.4f}; this video has used ${self.spent:.4f} of '
                         f'the ${self.limit:.2f} you allowed. Nothing was sent.')


def guarded_ledger(path, limit, on_reserve=None):
    """The pipeline's Ledger, with the pre-approved spend limit checked before every reservation."""
    from pipeline import config
    from pipeline.cost import Ledger

    class GuardedLedger(Ledger):
        spend_limit = Decimal(str(limit))

        def check(self, estimate_usd):
            super().check(estimate_usd)                    # the per-video cap ($2): a hard stop
            estimate = Decimal(str(estimate_usd))
            if self.spent() + estimate > self.spend_limit:
                raise SpendStop(self._step, estimate, self.spent(), self.spend_limit)

        def reserve(self, step, provider, model, estimate_usd, detail=''):
            self._step = step
            entry = super().reserve(step, provider, model, estimate_usd, detail)
            if on_reserve:
                on_reserve(step, estimate_usd, detail)
            return entry

    return GuardedLedger.open(path, None, default_budget=config.DEFAULT_BUDGET_USD)


def make_client(settings):
    if settings.fake_anthropic:
        from . import fake
        return fake.make_client()
    from pipeline import config, script
    config.load_env()                  # ANTHROPIC_API_KEY normally comes from the service environment; .env if present
    return script.make_client()


def install_fake(settings):
    """In fake mode nothing may reach Anthropic, even code that calls pipeline.script.make_client() itself."""
    if settings.fake_anthropic:
        from pipeline import script
        from . import fake
        script.make_client = fake.make_client


class Runner:
    def __init__(self, settings=None, service=None):
        self.settings = settings or Settings.from_env()
        self.service = service or core.Service(self.settings)
        install_fake(self.settings)
        self.stopping = False

    def set_progress(self, job_id, stage, pct, message):
        try:
            self.service.progress(job_id, stage, pct, message, lease_minutes=self.settings.vps_lease_minutes)
        except (core.GateError, core.NotFound):
            pass

    def run_once(self) -> bool:
        """Claim and run one VPS job. False when the queue is empty."""
        job = self.service.claim(core.VPS_KINDS, self.settings.vps_lease_minutes)
        if not job:
            return False
        self.run_job(job)
        return True

    def run_job(self, job):
        from pipeline import config, store, styles
        from pipeline.cost import BudgetExceeded, PriceUnknown, usd
        slug, kind, job_id = job['slug'], job['kind'], job['id']
        svc = self.service
        row = svc.episode_row(slug)
        limit = svc.spend_limit(row)
        base = config.EPISODES_DIR / slug
        steps = {'research': 'Researching the topic (web search)', 'storyline': 'Pitching three storylines',
                 'script': 'Writing the script'}
        pct = {'research': 20, 'storyline': 60, 'script': 40}

        def on_reserve(step, estimate, detail):
            self.set_progress(job_id, step, pct.get(step, 50),
                              f'{steps.get(step, step)}: Claude, up to ${usd(estimate):.4f}')

        ledger = guarded_ledger(base / 'ledger.json', limit, on_reserve)
        self.set_progress(job_id, kind, 5, 'Starting' + (' (fake Anthropic: nothing paid is sent)'
                                                          if self.settings.fake_anthropic else ''))
        try:
            client = make_client(self.settings)
            episode, base = store.load(slug)
            if kind == 'pitch':
                from pipeline import story
                episode, notes = story.pitch(episode, base, ledger, client=client)
                store.save(episode, base)
                next_state = core.STORYLINES
            else:
                from pipeline import script
                style = styles.load(episode.style)
                episode, notes = script.generate(episode, style, base, ledger, client=client)
                store.save(episode, base)
                next_state = core.SCRIPT
        except SpendStop as stop:
            waiting = dict(kind=kind, job_id=job_id, step=stop.step, estimate_usd=float(usd(stop.estimate)),
                           spent_usd=float(usd(stop.spent)), limit_usd=float(stop.limit), need_usd=float(stop.need),
                           message=str(stop))
            self._finish_failed(job, f'Waiting for your spend approval: {stop}', core.SPEND_APPROVAL, waiting, ledger)
            return
        except BudgetExceeded as exc:
            self._finish_failed(job, f'Budget cap: {exc}', core.FAILED, None, ledger)
            return
        except Exception as exc:                              # ScriptError, ValueError, network, anything: no retry
            from pipeline.script import ScriptError
            known = (ScriptError, ValueError, PriceUnknown, RuntimeError, OSError)
            msg = str(exc) if isinstance(exc, known) else f'{exc.__class__.__name__}: {exc}'
            if not isinstance(exc, known):
                log.error('job %s (%s %s) failed\n%s', job_id, kind, slug, traceback.format_exc())
            what = 'Research/storylines' if kind == 'pitch' else 'Script'
            self._finish_failed(job, f'{what} failed: {msg} (nothing is retried on its own; press Retry to run it '
                                     'again)', core.FAILED, None, ledger)
            return
        t = db.now()
        with svc.engine.begin() as conn:
            conn.execute(update(J).where(J.c.id == job_id).values(
                status='done', finished_at=t, lease_until=None, result=dict(notes=notes),
                progress=dict(stage='done', pct=100, message='Finished', at=db.iso(t))))
            conn.execute(update(E).where(E.c.slug == slug).values(state=next_state, error=None, waiting=None,
                                                                  cost_usd=float(usd(ledger.spent())), updated_at=t))
        log.info('job %s %s %s done -> %s', job_id, kind, slug, next_state)

    def _finish_failed(self, job, error, state, waiting, ledger):
        from pipeline.cost import usd
        with self.service.engine.begin() as conn:
            self.service._fail_job(conn, job, error[:4000], state=state, waiting=waiting)
            conn.execute(update(E).where(E.c.slug == job['slug']).values(cost_usd=float(usd(ledger.spent()))))
        log.warning('job %s %s %s -> %s: %s', job['id'], job['kind'], job['slug'], state, error)

    def fail_orphans(self):
        """At start: a VPS job still `running` belongs to a runner that died mid-step. Paid: fail it, never re-run."""
        from sqlalchemy import and_, select
        with self.service.engine.begin() as conn:
            for j in conn.execute(select(J).where(and_(J.c.status == 'running', J.c.kind.in_(core.VPS_KINDS)))).mappings().all():
                self.service._fail_job(conn, dict(j), f'The runner restarted during the {j["kind"]} step. Paid steps '
                                                      'are never retried on their own: press Retry to run it again.')

    def loop(self, poll_seconds=3.0):
        self.fail_orphans()
        log.info('runner started (fake Anthropic: %s, data: %s)', self.settings.fake_anthropic, self.settings.data_dir)
        while not self.stopping:
            try:
                if not self.run_once():
                    time.sleep(poll_seconds)
            except Exception:                                 # the DB went away etc.: log, wait, carry on
                log.error('runner loop error\n%s', traceback.format_exc())
                time.sleep(max(poll_seconds, 5))


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m webapp.runner', description='Run queued pitch/script jobs on the VPS.')
    p.add_argument('--once', action='store_true', help='run queued jobs until the queue is empty, then exit')
    p.add_argument('--poll', type=float, default=3.0)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    runner = Runner()

    def stop(*_):
        runner.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.once:
        while runner.run_once():
            pass
        return 0
    runner.loop(args.poll)
    return 0


if __name__ == '__main__':
    sys.exit(main())
