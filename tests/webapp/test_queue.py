"""Leases, the spend rule, paid-step failures, file names and upload limits."""
import datetime as dt
import json
import unittest
from unittest import mock

from sqlalchemy import select, update

from webapp import db, fake
from webapp.testing import Harness
try:
    from webapp.test_flow import TOPIC, to_painting       # discover -s tests (this folder is the "webapp" package)
except ImportError:
    from test_flow import TOPIC, to_painting               # discover -s tests/webapp


def expire(h, job_id):
    with h.service.engine.begin() as conn:
        conn.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(lease_until=db.now() - dt.timedelta(seconds=5)))


def job_row(h, job_id):
    with h.service.engine.connect() as conn:
        return dict(conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().first())


class LeaseTest(unittest.TestCase):
    def test_an_expired_mac_job_is_queued_again_at_most_three_times(self):
        with Harness() as h:
            h.login()
            slug = to_painting(h)
            job = h.claim()
            self.assertEqual(job_row(h, job['job_id'])['attempts'], 1)
            lease = job_row(h, job['job_id'])['lease_until']
            self.assertGreater(lease, db.now() + dt.timedelta(minutes=14))
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/progress', json=dict(stage='x', pct=1, message='m'))
            for attempt in (1, 2):
                expire(h, job['job_id'])
                d = h.detail(slug)                                       # any request sweeps expired leases
                self.assertEqual(d['job']['status'], 'queued')
                self.assertEqual(d['state'], 'painting')
                self.assertIn('queued again', d['job']['progress']['message'])
                # a late report from the old claim is refused
                r = h.w('post', f'/api/worker/jobs/{job["job_id"]}/progress', json=dict(stage='x', pct=1, message='m'))
                self.assertEqual(r.status_code, 409)
                again = h.claim()
                self.assertEqual(again['job_id'], job['job_id'])
                self.assertEqual(job_row(h, job['job_id'])['attempts'], attempt + 1)
            expire(h, job['job_id'])
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertEqual(d['job']['status'], 'failed')
            self.assertIn('3 times', d['error'])
            self.assertIsNone(h.claim())
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/retry', json={}).status_code, 200)
            self.assertEqual(h.claim()['job_id'], job['job_id'])

    def test_an_expired_paid_step_fails_and_is_never_rerun_on_its_own(self):
        with Harness() as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.service.claim(['pitch'], 20)                      # as if the runner claimed it, then died
            expire(h, job['id'])
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertIn('never retried on their own', d['error'])
            self.assertFalse(h.runner.run_once())
            h.client.post(f'/api/episodes/{slug}/retry', json={})
            self.assertTrue(h.runner.run_once())
            self.assertEqual(h.detail(slug)['state'], 'storylines')

    def test_runner_restart_fails_orphaned_paid_jobs(self):
        with Harness() as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            h.service.claim(['pitch'], 20)
            h.runner.fail_orphans()
            self.assertEqual(h.detail(slug)['state'], 'failed')

    def test_worker_status_goes_offline_after_two_minutes(self):
        with Harness() as h:
            h.w('post', '/api/worker/heartbeat', json=dict(host='mac', version='1'))
            self.assertTrue(h.service.worker_status()['online'])
            with h.service.engine.begin() as conn:
                conn.execute(update(db.worker).values(last_seen=db.now() - dt.timedelta(seconds=121)))
            w = h.service.worker_status()
            self.assertFalse(w['online'])
            self.assertEqual(w['host'], 'mac')

    def test_claim_is_one_job_once(self):
        with Harness() as h:
            h.login()
            to_painting(h)
            first = h.claim()
            self.assertIsNotNone(first)
            self.assertIsNone(h.claim())
            self.assertIsNone(h.claim(kinds=['pitch', 'script']))      # VPS kinds are never handed to the Mac


class SpendRuleTest(unittest.TestCase):
    def test_a_step_past_the_preapproved_spend_waits_for_the_user(self):
        client = fake.FakeClient()
        with Harness() as h, mock.patch.object(fake, 'make_client', lambda: client):
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            # this video has already used $0.45 (e.g. an earlier paid research); research reserves ~$0.28
            ledger = h.settings.episodes_dir / slug / 'ledger.json'
            ledger.write_text(json.dumps(dict(budget_usd=2.0, spent_usd=0.45, entries=[dict(
                id=1, step='research', provider='anthropic', model='claude-sonnet-5-5', estimate_usd=0.5,
                actual_usd=0.45, status='ok', detail='earlier', usage=None, at='2026-11-09T10:00:00')])))
            self.assertTrue(h.runner.run_once())
            d = h.detail(slug)
            self.assertEqual(d['state'], 'spend_approval')
            w = d['spend']['waiting']
            self.assertEqual(w['step'], 'research')
            self.assertAlmostEqual(w['spent_usd'], 0.45)
            self.assertGreater(w['need_usd'], 0)
            self.assertIn('Nothing was sent', w['message'])
            self.assertEqual(client.messages.created, [])               # nothing was sent
            self.assertEqual(len(json.loads(ledger.read_text())['entries']), 1)
            self.assertAlmostEqual(d['cost_usd'], 0.45)
            page = h.client.get(f'/episodes/{slug}')
            self.assertIn(f'Allow up to ${w["need_usd"]:.2f} more', page.text)
            self.assertFalse(h.runner.run_once())                         # never retried on its own
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/retry', json={}).status_code, 409)
            r = h.client.post(f'/api/episodes/{slug}/spend/allow', json=dict(more_usd=0.01))
            self.assertEqual(r.status_code, 409)                          # less than the step needs
            r = h.client.post(f'/api/episodes/{slug}/spend/allow', json={})
            self.assertEqual(r.status_code, 200, r.text)
            with h.service.engine.connect() as conn:
                rows = conn.execute(select(db.spend_approvals)).mappings().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['by'], 'saloni (web)')
            self.assertAlmostEqual(rows[0]['more_usd'], w['need_usd'])
            self.assertEqual(h.detail(slug)['state'], 'researching')
            # the allowance covers research's worst case; research really cost $0 here, so the storyline call fits
            self.assertTrue(h.runner.run_once())
            self.assertEqual(h.detail(slug)['state'], 'storylines')
            self.assertEqual(len(client.messages.created), 2)             # research was cached, not re-sent

    def test_low_preapproval_stops_before_the_first_call(self):
        with Harness(spend='0.10') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            h.runner.run_once()
            d = h.detail(slug)
            self.assertEqual(d['state'], 'spend_approval')
            self.assertEqual(d['spend']['limit_usd'], 0.1)


class PaidFailureTest(unittest.TestCase):
    def test_a_research_failure_is_readable_and_waits_for_retry(self):
        from webapp.fake import fixtures
        empty = fixtures().research_response(facts=[])
        empty.usage = fake.zero_usage()
        client = fixtures().FakeClient([empty])
        with Harness() as h, mock.patch.object(fake, 'make_client', lambda: client):
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            h.runner.run_once()
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertIn('Research returned no cited facts', d['error'])
            self.assertIn('press Retry', d['error'])
            self.assertFalse(h.runner.run_once())


class FileSafetyTest(unittest.TestCase):
    def test_upload_names_and_kinds(self):
        with Harness() as h:
            h.login()
            slug = to_painting(h)
            job = h.claim()
            for name in ('../evil.jpg', 'Bad Name.jpg', 'UPPER.jpg', '.hidden', 'a/b.jpg', '', 'x' * 200):
                r = h.upload(job['job_id'], name, 'scene_preview')
                self.assertEqual(r.status_code, 400, name)
            self.assertEqual(h.upload(job['job_id'], 'ok.jpg', 'executable').status_code, 400)
            self.assertEqual(h.upload(job['job_id'], 'ok_1-2.jpg', 'scene_preview').status_code, 200)
            out = h.settings.outputs_dir / slug
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['ok_1-2.jpg'])   # no temp files left
            self.assertFalse((h.settings.data_dir / 'evil.jpg').exists())
            self.assertEqual(h.upload(999, 'a.jpg', 'scene_preview').status_code, 404)

    def test_upload_size_limit(self):
        with Harness(upload_limit=1000) as h:
            h.login()
            slug = to_painting(h)
            job = h.claim()
            r = h.upload(job['job_id'], 'big.mp4', 'mp4', b'z' * 5000)
            self.assertEqual(r.status_code, 413)
            self.assertEqual(list((h.settings.outputs_dir / slug).glob('*')), [])
            self.assertEqual(h.upload(job['job_id'], 'small.mp4', 'mp4', b'z' * 900).status_code, 200)

    def test_file_download_names_are_safe(self):
        with Harness() as h:
            h.login()
            (h.settings.data_dir / 'secret.txt').write_text('no')
            (h.settings.outputs_dir / 'slug').mkdir(parents=True)
            (h.settings.outputs_dir / 'slug' / 'a.srt').write_text('1\n')
            self.assertEqual(h.client.get('/files/slug/a.srt').status_code, 200)
            for path in ('/files/slug/..%2Fsecret.txt', '/files/slug/A.srt', '/files/slug/.a', '/files/..%2F..%2Fx/a.srt',
                         '/files/slug/missing.srt', '/files/Slug/a.srt'):
                r = h.client.get(path)
                self.assertIn(r.status_code, (400, 404), path)
                self.assertNotEqual(r.text, 'no')


if __name__ == '__main__':
    unittest.main()
