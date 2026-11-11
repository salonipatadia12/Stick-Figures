"""SCRIPT_PROVIDER routing: with `ollama` the Mac runs pitch/script (the VPS only queues them) and the server applies
the runner's transitions; with `anthropic` everything is as before. The Mac is simulated through the worker API; its
results are built from the pipeline's models (no language model is called, nothing paid is sent)."""
import json
import unittest

from sqlalchemy import select

from webapp import db, fake
from webapp.testing import Harness
try:
    from webapp.test_queue import expire, job_row       # discover -s tests (this folder is the "webapp" package)
except ImportError:
    from test_queue import expire, job_row               # discover -s tests/webapp

TOPIC = 'Why your salary disappears by the 20th'
ALL = ('pitch', 'script', 'paint', 'render')
OLLAMA_DOWN = 'Ollama is not running on the Mac: brew services start ollama'
RESEARCH = 'research-0123456789abcdef.json'


def mac_pitch(job):
    """What the Mac's story.pitch returns: the claimed episode with three storylines."""
    from pipeline.models import Episode, StoryPitch
    ep = Episode.model_validate(job['episode'])
    sp = StoryPitch.model_validate(fake.fixtures().story_pitch())
    return Episode.model_validate(ep.model_copy(update=dict(storylines=sp.storylines, storyline_pick=0)).model_dump())


def mac_script(job):
    """What the Mac's script.generate returns: the claimed episode with the fake host-format script."""
    from pipeline.models import Beat, Episode, ScriptDraft
    ep = Episode.model_validate(job['episode'])
    draft = ScriptDraft.model_validate(fake.host_draft())
    return Episode.model_validate(ep.model_copy(update=dict(
        title=draft.title, hook_promise=draft.hook_promise, hook_options=draft.hook_options, hook_pick=0,
        subject_settings=draft.subject_settings,
        beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)])).model_dump())


def ledger(entries=()):
    return dict(budget_usd=2.0, entries=[dict(id=k, step=s, provider='ollama', model='qwen2.5:14b', estimate_usd=0.0,
                                              actual_usd=0.0, status='done', detail='', usage=None, at='2026-11-10T10:00:00')
                                         for k, s in enumerate(entries, 1)])


def done(h, job, episode, entries=('research', 'storyline')):
    return h.w('post', f'/api/worker/jobs/{job["job_id"]}/done',
               json=dict(result=dict(notes=['research: 3 cited facts (new)'], episode=episode.model_dump(mode='json'),
                                     ledger=ledger(entries), provider='ollama')))


class RoutingTest(unittest.TestCase):
    def test_ollama_hands_pitch_to_the_mac_and_the_runner_idles(self):
        with Harness(provider='ollama') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            d = h.detail(slug)
            self.assertEqual((d['state'], d['job']['kind'], d['job']['status']), ('researching', 'pitch', 'queued'))
            self.assertEqual(d['job']['progress']['message'], 'Waiting for the Mac to research the topic')
            self.assertFalse(h.runner.run_once())                     # the VPS runner never takes it
            self.assertIsNone(h.claim(('paint', 'render')))            # an old worker that asks for paint/render only
            hb = h.w('post', '/api/worker/heartbeat', json=dict(host='mac', version='1')).json()
            self.assertEqual((hb['queued'], hb['kinds']), (1, list(ALL)))
            job = h.claim(ALL)
            self.assertEqual((job['kind'], job['slug'], job['provider']), ('pitch', slug, 'ollama'))
            self.assertEqual(job['episode']['topic'], TOPIC)
            self.assertEqual((job['cache'], job['others']), ({}, {}))
            self.assertEqual(h.detail(slug)['job']['status'], 'running')
            self.assertTrue(h.service.worker_status()['busy'])
            page = h.client.get(f'/episodes/{slug}').text
            self.assertIn('Mac worker', page)                         # a Mac job shows the worker, not "the server"
            self.assertNotIn('This step runs on the server', page)
            self.assertNotIn('Claude is simulated', page)
            self.assertIn('the Mac researches the topic', h.client.get('/new').text)

    def test_anthropic_keeps_pitch_and_script_on_the_runner(self):
        with Harness(provider='anthropic') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            self.assertEqual(h.detail(slug)['job']['progress']['message'], 'Waiting to research the topic')
            hb = h.w('post', '/api/worker/heartbeat', json=dict(host='mac', version='1')).json()
            self.assertEqual((hb['queued'], hb['kinds']), (0, ['paint', 'render']))
            self.assertIsNone(h.claim(ALL))                           # the Mac never gets the paid steps
            self.assertIn('This step runs on the server', h.client.get(f'/episodes/{slug}').text)
            self.assertIn('Claude researches the topic', h.client.get('/new').text)
            self.assertTrue(h.runner.run_once())
            self.assertEqual(h.detail(slug)['state'], 'storylines')

    def test_settings_from_env(self):
        import os
        from unittest import mock

        from webapp.settings import Settings
        with mock.patch.dict(os.environ, {'STICK_DATA_DIR': '/tmp/stick-x', 'SESSION_SECRET': 's'}):
            os.environ.pop('SCRIPT_PROVIDER', None)
            s = Settings.from_env()
            self.assertEqual((s.script_provider, s.vps_kinds, s.mac_kinds), ('ollama', (), ALL))
            os.environ['SCRIPT_PROVIDER'] = ' Anthropic '
            s = Settings.from_env()
            self.assertEqual((s.script_provider, s.vps_kinds, s.mac_kinds),
                             ('anthropic', ('pitch', 'script'), ('paint', 'render')))
            os.environ['SCRIPT_PROVIDER'] = 'openai'
            with self.assertRaises(ValueError):
                Settings.from_env()


class MacPitchScriptTest(unittest.TestCase):
    def test_pitch_then_script_on_the_mac_reach_their_gates(self):
        with Harness(provider='ollama') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            jid = job['job_id']
            r = h.w('post', f'/api/worker/jobs/{jid}/progress', json=dict(stage='pitch', pct=10, message='Researching'))
            self.assertEqual(r.status_code, 200, r.text)
            # a pitch job uploads only cache files
            self.assertEqual(h.upload(jid, f'{slug}.mp4', 'mp4').status_code, 400)
            self.assertEqual(h.upload(jid, '.hidden.json', 'cache').status_code, 400)
            research = json.dumps(dict(facts=[dict(claim='c', url='https://example.gov/x')])).encode()
            r = h.upload(jid, RESEARCH, 'cache', research)
            self.assertEqual(r.status_code, 200, r.text)
            cache = h.settings.episodes_dir / slug / 'cache' / RESEARCH
            self.assertEqual(cache.read_bytes(), research)
            self.assertFalse((h.settings.outputs_dir / slug / RESEARCH).exists())
            r = done(h, job, mac_pitch(job))
            self.assertEqual(r.status_code, 200, r.text)
            d = h.detail(slug)
            self.assertEqual(d['state'], 'storylines', d.get('error'))
            self.assertEqual(len(d['storylines']['cards']), 3)
            self.assertNotIn('files', d)                              # cache files are not downloads
            self.assertEqual(d['cost_usd'], 0)
            self.assertFalse(h.service.worker_status()['busy'])
            self.assertEqual(job_row(h, jid)['result'], dict(notes=['research: 3 cited facts (new)'], ran_on='mac',
                                                             provider='ollama'))
            self.assertEqual(len(h.episode_file(slug, 'ledger.json')['entries']), 2)
            self.assertEqual(len(h.episode_file(slug)['storylines']), 3)
            self.assertEqual(h.w('post', f'/api/worker/jobs/{jid}/done', json=dict(result={})).status_code, 409)

            # the user's pick (the pipeline's gate) queues the script for the Mac, with the cache to reuse
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=1)).status_code, 200)
            d = h.detail(slug)
            self.assertEqual((d['state'], d['job']['kind']), ('scripting', 'script'))
            self.assertEqual(d['job']['progress']['message'], 'Waiting for the Mac to write the script')
            self.assertFalse(h.runner.run_once())
            job = h.claim(ALL)
            self.assertEqual(job['kind'], 'script')
            self.assertEqual(job['cache'], {RESEARCH: research.decode()})
            self.assertNotIn('others', job)
            self.assertEqual(job['episode']['storyline_pick'], 1)
            self.assertEqual(len(job['ledger']['entries']), 2)
            r = done(h, job, mac_script(job), entries=('research', 'storyline', 'script'))
            self.assertEqual(r.status_code, 200, r.text)
            d = h.detail(slug)
            self.assertEqual(d['state'], 'script', d.get('error'))
            self.assertEqual(len(d['script']['hook_options']), 3)
            self.assertTrue(d['script']['beats'])
            self.assertIn('Review the script', h.client.get(f'/episodes/{slug}').text)

    def test_an_unusable_episode_from_the_mac_fails_the_job_and_retry_requeues_it(self):
        with Harness(provider='ollama') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            ep = mac_pitch(job).model_copy(update=dict(slug='someone-else'))
            self.assertEqual(done(h, job, ep).status_code, 200)
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertIn('Research/storylines failed: the Mac sent back an episode', d['error'])
            self.assertEqual(h.episode_file(slug)['storylines'], [])  # the server's copy is untouched
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/retry', json={}).status_code, 200)
            self.assertEqual(h.detail(slug)['state'], 'researching')
            again = h.claim(ALL)
            self.assertEqual(again['job_id'], job['job_id'])
            self.assertEqual(done(h, again, mac_pitch(again)).status_code, 200)
            self.assertEqual(h.detail(slug)['state'], 'storylines')

    def test_ollama_down_on_the_mac_is_shown_and_retry_is_free(self):
        with Harness(provider='ollama') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            msg = f'{OLLAMA_DOWN} (no answer at http://localhost:11434: ConnectError). Press Retry once it runs.'
            r = h.w('post', f'/api/worker/jobs/{job["job_id"]}/fail', json=dict(error=msg))
            self.assertEqual(r.status_code, 200, r.text)
            d = h.detail(slug)
            self.assertEqual((d['state'], d['error']), ('failed', msg))
            page = h.client.get(f'/episodes/{slug}').text
            self.assertIn('Ollama is not running on the Mac: brew services start ollama', page)
            self.assertNotIn('paid Claude call', page)                # the retry button asks nothing
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/retry', json={}).status_code, 200)
            self.assertEqual(h.claim(ALL)['job_id'], job['job_id'])

    def test_the_pitch_carries_the_other_episodes_for_the_rotation_rule(self):
        from pipeline.models import Episode
        with Harness(provider='ollama') as h:
            h.login()
            first = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            done(h, job, mac_pitch(job))
            h.client.post(f'/api/episodes/{first}/storyline', json=dict(story=2))
            second = h.client.post('/api/episodes', json=dict(topic='Why honey never spoils')).json()['slug']
            job = h.claim(ALL)                                         # the first's script job is older
            self.assertEqual(job['slug'], first)
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/fail', json=dict(error='later'))
            job = h.claim(ALL)
            self.assertEqual((job['slug'], job['kind']), (second, 'pitch'))
            self.assertEqual(set(job['others']), {first})
            stub = Episode.model_validate(job['others'][first])
            self.assertEqual((stub.storyline_pick, stub.story_format), (2, 'myth_vs_fact'))
            self.assertEqual([e['gate'] for e in stub.meta['approval_log']], ['storyline'])
            self.assertEqual(stub.beats, [])


class FreeStepLeaseTest(unittest.TestCase):
    def test_an_expired_mac_pitch_is_queued_again_like_paint(self):
        with Harness(provider='ollama') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            for attempt in (1, 2):
                expire(h, job['job_id'])
                d = h.detail(slug)
                self.assertEqual((d['state'], d['job']['status']), ('researching', 'queued'))
                self.assertIn('queued again', d['job']['progress']['message'])
                self.assertEqual(h.claim(ALL)['job_id'], job['job_id'])
                self.assertEqual(job_row(h, job['job_id'])['attempts'], attempt + 1)
            expire(h, job['job_id'])
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertIn('The Mac stopped answering during pitch 3 times', d['error'])

    def test_the_runner_leaves_a_running_mac_pitch_alone(self):
        with Harness(provider='ollama') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            h.runner.fail_orphans()                                   # a runner restart
            self.assertEqual(h.detail(slug)['state'], 'researching')
            self.assertEqual(job_row(h, job['job_id'])['status'], 'running')

    def test_an_expired_paid_pitch_still_fails(self):
        with Harness(provider='anthropic') as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.service.claim(['pitch'], 20)
            expire(h, job['id'])
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertIn('Paid steps are never retried on their own', d['error'])
            with h.service.engine.connect() as conn:
                self.assertEqual(conn.execute(select(db.jobs.c.status)).scalar_one(), 'failed')


if __name__ == '__main__':
    unittest.main()
