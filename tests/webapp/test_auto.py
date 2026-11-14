"""Auto mode in the web app (user 2026-11-13): with Auto on, a topic goes to a finished video with no stops; each gate is
decided by pipeline/auto.py and logged as "auto (web)"; when a rule cannot decide, it stops at that gate and says why;
the phone is notified when the video is done. Also "Render again" and the notification endpoints. The Mac is
simulated through the worker API; no model runs, nothing is painted, rendered or sent anywhere."""
import json
import unittest

from webapp.testing import Harness
try:
    from webapp.test_ollama import ALL, TOPIC, done, mac_pitch, mac_script   # discover -s tests
except ImportError:
    from test_ollama import ALL, TOPIC, done, mac_pitch, mac_script           # discover -s tests/webapp

KEYS = dict(vapid_public_key='BPUB', vapid_private_key='PRIV')


def mac_script_ok(job):
    """The fake host-format script with the research facts it cites (s1, s2): a script that passes every check."""
    from pipeline.models import Source
    ep = mac_script(job)
    ep.sources = [Source(id='s1', claim='Spending tends to rise as income rises.', url='https://www.example.edu/a'),
                  Source(id='s2', claim='Paying yourself first moves savings out before spending.',
                         url='https://www.example.gov/b')]
    return ep
SUB = dict(endpoint='https://push.example.test/device-1', keys=dict(p256dh='BKEY', auth='AUTH'))


def paint_done(h, job, new=('volcano-erupting',), failed=()):
    for key in new:
        assert h.upload(job['job_id'], f'{key}.jpg', 'scene_preview').status_code == 200
    return h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result=dict(
        new=[dict(key=k, kind='scene', name=f'{k}.jpg', scene=f'painted {k}', beats=['b02']) for k in new],
        failed=[dict(key=k, why='failed QA on both seeds') for k in failed])))


def render_done(h, job, qa_pass=True):
    slug = job['slug']
    for name, kind in ((f'{slug}.mp4', 'mp4'), (f'{slug}.srt', 'srt'), ('qa.json', 'qa')):
        assert h.upload(job['job_id'], name, kind, b'x' * 64).status_code == 200
    return h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result=dict(
        duration=44.0, qa_pass=qa_pass, qa_summary=dict(words=True, all=qa_pass))))


class AutoMode(Harness):
    def __init__(self):
        super().__init__(provider='ollama')

    def __enter__(self):
        super().__enter__()
        for k, v in KEYS.items():
            setattr(self.settings, k, v)
        self.sent = []
        self.service.push_sync = True
        self.service.push_sender = lambda sub, payload, key, subject: self.sent.append((sub['endpoint'], payload)) or 201
        self.login()
        return self


class AutoFlowTest(unittest.TestCase):
    def test_topic_to_finished_video_with_no_stops(self):
        with AutoMode() as h:
            self.assertEqual(h.client.post('/api/push/subscribe', json=dict(subscription=SUB)).status_code, 200)
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC, auto=True)).json()['slug']
            d = h.detail(slug)
            self.assertTrue(d['auto'])
            self.assertEqual([s['status'] for s in d['steps']], ['current', 'todo', 'todo', 'todo', 'todo', 'todo'])

            job = h.claim(ALL)                                       # the Mac researches and pitches
            self.assertEqual(done(h, job, mac_pitch(job)).status_code, 200)
            d = h.detail(slug)                                       # storyline picked by auto: script queued
            self.assertEqual((d['state'], d['job']['kind']), ('scripting', 'script'), d.get('auto_note'))
            ep = h.episode_file(slug)
            self.assertEqual(ep['storyline_pick'], 1)
            self.assertEqual([(e['gate'], e['by']) for e in ep['meta']['approval_log']], [('storyline', 'auto (web)')])

            job = h.claim(ALL)                                       # the Mac writes the script
            self.assertEqual(done(h, job, mac_script_ok(job), entries=('research', 'storyline', 'script')).status_code, 200)
            d = h.detail(slug)                                       # approved by auto (it passes): paint queued
            self.assertEqual((d['state'], d['job']['kind']), ('painting', 'paint'), d.get('auto_note'))
            log = h.episode_file(slug)['meta']['approval_log']
            self.assertEqual(log[-1]['gate'], 'script')
            self.assertEqual(log[-1]['by'], 'auto (web)')
            self.assertEqual([s['status'] for s in d['steps']][:4], ['done', 'done', 'done', 'current'])

            job = h.claim(ALL)                                       # the Mac paints; auto accepts the scenes
            self.assertEqual(paint_done(h, job).status_code, 200)
            d = h.detail(slug)
            self.assertEqual((d['state'], d['job']['kind']), ('rendering', 'render'), d.get('auto_note'))
            self.assertEqual(h.episode_file(slug)['meta']['approval_log'][-1]['by'], 'auto (web)')
            self.assertEqual(h.sent, [])                             # nothing to look at yet: no notification

            job = h.claim(ALL)                                       # the Mac renders
            self.assertEqual(render_done(h, job).status_code, 200)
            d = h.detail(slug)
            self.assertEqual(d['state'], 'done')
            self.assertTrue(all(s['status'] == 'done' for s in d['steps']))
            self.assertEqual(len(h.sent), 1)                         # one notification: the video is ready
            endpoint, payload = h.sent[0]
            self.assertEqual(endpoint, SUB['endpoint'])
            self.assertEqual(payload['title'], 'Your video is ready')
            self.assertEqual(payload['url'], f'/episodes/{slug}')
            self.assertIn('every QA check passed', payload['body'])

    def test_without_auto_every_gate_waits_for_the_user(self):
        with AutoMode() as h:
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            job = h.claim(ALL)
            done(h, job, mac_pitch(job))
            d = h.detail(slug)
            self.assertEqual((d['state'], d['auto'], d['steps']), ('storylines', False, []))
            self.assertEqual(h.episode_file(slug)['storyline_pick'], 0)

    def test_a_script_that_fails_its_checks_stops_at_the_gate_and_says_why(self):
        with AutoMode() as h:
            h.client.post('/api/push/subscribe', json=dict(subscription=SUB))
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC, auto=True)).json()['slug']
            job = h.claim(ALL)
            done(h, job, mac_pitch(job))
            job = h.claim(ALL)
            ep = mac_script_ok(job)
            ep.beats[0].narration = 'Hello and welcome, in this video we talk about the long story of salaries'
            for opt in ep.hook_options:                              # every hook fails too: nothing auto may approve
                opt.line = 'Hello and welcome, in this video we talk about the long story of salaries'
            done(h, job, ep, entries=('research', 'storyline', 'script'))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'script')
            self.assertIn('Auto mode stopped at the script', d['auto_note'])
            self.assertFalse(h.episode_file(slug)['approvals'].get('script'))
            self.assertIn('Auto mode needs you', h.client.get(f'/episodes/{slug}').text)
            self.assertEqual(h.sent[-1][1]['title'], 'Auto mode needs you')

    def test_a_failed_scene_stops_at_the_scenes_gate(self):
        with AutoMode() as h:
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC, auto=True)).json()['slug']
            job = h.claim(ALL)
            done(h, job, mac_pitch(job))
            job = h.claim(ALL)
            done(h, job, mac_script_ok(job), entries=('research', 'storyline', 'script'))
            job = h.claim(ALL)
            paint_done(h, job, new=('volcano-erupting',), failed=('frozen-farm',))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'scenes')
            self.assertIn('frozen-farm failed QA', d['auto_note'])

    def test_a_failure_notifies_and_the_steps_show_where(self):
        with AutoMode() as h:
            h.client.post('/api/push/subscribe', json=dict(subscription=SUB))
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC, auto=True)).json()['slug']
            job = h.claim(ALL)
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/fail', json=dict(error='Ollama is not running on the Mac'))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertEqual(d['steps'][0]['status'], 'failed')
            self.assertEqual(h.sent[-1][1]['title'], 'A step failed')

    def test_the_form_and_the_page(self):
        with AutoMode() as h:
            page = h.client.get('/new').text
            self.assertIn('data-auto-toggle', page)
            self.assertIn('data-push', page)
            r = h.client.post('/episodes', data=dict(topic=TOPIC, auto='1'), follow_redirects=False,
                              headers={'origin': 'http://testserver'})
            self.assertEqual(r.status_code, 303)
            slug = r.headers['location'].rsplit('/', 1)[1]
            self.assertTrue(h.detail(slug)['auto'])
            page = h.client.get(f'/episodes/{slug}').text
            self.assertIn('class="steps"', page)
            self.assertIn('>Auto<', page)
            self.assertIn('>Auto<', h.client.get('/').text)                  # the list marks it too


class RenderAgainTest(unittest.TestCase):
    def test_a_finished_video_can_be_rendered_again(self):
        with AutoMode() as h:
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC, auto=True)).json()['slug']
            for step in ('pitch', 'script', 'paint', 'render'):
                job = h.claim(ALL)
                self.assertEqual(job['kind'], step)
                if step == 'pitch':
                    done(h, job, mac_pitch(job))
                elif step == 'script':
                    done(h, job, mac_script_ok(job), entries=('research', 'storyline', 'script'))
                elif step == 'paint':
                    paint_done(h, job)
                else:
                    render_done(h, job, qa_pass=False)
            self.assertEqual(h.detail(slug)['state'], 'done_qa_failed')
            self.assertIn('Render again', h.client.get(f'/episodes/{slug}').text)
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/render-again').status_code, 200)
            d = h.detail(slug)
            self.assertEqual((d['state'], d['job']['kind'], d['job']['status']), ('rendering', 'render', 'queued'))
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/render-again').status_code, 409)   # not finished now
            job = h.claim(ALL)
            render_done(h, job, qa_pass=True)
            self.assertEqual(h.detail(slug)['state'], 'done')


class PushTest(unittest.TestCase):
    def test_subscribe_test_and_dead_devices(self):
        with AutoMode() as h:
            r = h.client.get('/api/push/key').json()
            self.assertEqual((r['enabled'], r['public_key'], r['devices']), (True, 'BPUB', 0))
            self.assertEqual(h.client.post('/api/push/subscribe', json=dict(subscription=SUB)).status_code, 200)
            self.assertEqual(h.client.post('/api/push/subscribe', json=dict(subscription=SUB)).status_code, 200)
            self.assertEqual(h.client.get('/api/push/key').json()['devices'], 1)        # the same device once
            bad = dict(endpoint='http://insecure.example/x', keys=dict(p256dh='k', auth='a'))
            self.assertEqual(h.client.post('/api/push/subscribe', json=dict(subscription=bad)).status_code, 400)
            self.assertEqual(h.client.post('/api/push/test').status_code, 200)
            self.assertEqual(h.sent[-1][1]['title'], 'Notifications are on')
            h.service.push_sender = lambda *a: 410                   # the browser dropped the subscription
            h.service.notify('', 't', 'b')
            self.assertEqual(h.client.get('/api/push/key').json()['devices'], 0)
            self.assertEqual(h.client.post('/api/push/unsubscribe', json=dict(endpoint=SUB['endpoint'])).status_code, 200)

    def test_cross_site_and_logged_out_requests_are_refused(self):
        with AutoMode() as h:
            r = h.client.post('/api/push/subscribe', json=dict(subscription=SUB), headers={'origin': 'https://evil.example'})
            self.assertEqual(r.status_code, 403)
            h.client.post('/api/logout')
            self.assertEqual(h.client.get('/api/push/key').status_code, 401)

    def test_off_without_keys(self):
        with Harness(provider='ollama') as h:
            h.login()
            self.assertFalse(h.client.get('/api/push/key').json()['enabled'])
            self.assertEqual(h.client.post('/api/push/subscribe', json=dict(subscription=SUB)).status_code, 400)

    def test_the_service_worker_and_manifest_are_served(self):
        with Harness(provider='ollama') as h:
            r = h.client.get('/sw.js')
            self.assertEqual(r.status_code, 200)
            self.assertIn('javascript', r.headers['content-type'])
            self.assertIn("addEventListener('push'", r.text)
            m = h.client.get('/manifest.webmanifest')
            self.assertEqual(json.loads(m.text)['display'], 'standalone')
            self.assertEqual(h.client.get('/static/icons/icon-192.png').status_code, 200)


if __name__ == '__main__':
    unittest.main()
