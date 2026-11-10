"""The whole state machine through the HTTP API: fake Anthropic on the runner and a simulated Mac worker."""
import json
import unittest

from webapp.testing import Harness

TOPIC = 'Why your salary disappears by the 20th'


def scene(key, beats, kind='scene'):
    return dict(key=key, kind=kind, name=f'{key}.jpg', scene=f'painted {key}', beats=beats)


class FullFlowTest(unittest.TestCase):
    def test_topic_to_done_with_every_gate(self):
        with Harness() as h:
            h.login()
            r = h.client.post('/api/episodes', json=dict(topic=TOPIC, niche='money', audience='salaried workers'))
            self.assertEqual(r.status_code, 200, r.text)
            slug = r.json()['slug']
            self.assertEqual(slug, 'why-your-salary-disappears-by-the-20th')
            d = h.detail(slug)
            self.assertEqual(d['state'], 'researching')
            self.assertEqual((d['job']['kind'], d['job']['status']), ('pitch', 'queued'))
            self.assertEqual(h.client.get('/api/episodes').json()[0]['slug'], slug)
            st = h.client.get(f'/api/episodes/{slug}?view=status').json()     # what the pages poll
            self.assertEqual((st['state'], st['job']['kind']), ('researching', 'pitch'))
            self.assertNotIn('storylines', st)
            self.assertIsNone(h.claim())                       # the Mac never gets VPS jobs

            # pitch on the runner (fake Anthropic: research + storylines, $0)
            self.assertTrue(h.runner.run_once())
            self.assertFalse(h.runner.run_once())
            d = h.detail(slug)
            self.assertEqual(d['state'], 'storylines', d.get('error'))
            self.assertEqual(len(d['storylines']['cards']), 3)
            card = d['storylines']['cards'][0]
            self.assertTrue(card['facts'][0]['url'].startswith('https://'))
            self.assertEqual(d['cost_usd'], 0)
            page = h.client.get(f'/episodes/{slug}')
            self.assertEqual(page.status_code, 200)
            self.assertIn('Pick storyline 1', page.text)

            # gate errors: wrong gate, bad pick
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/script/approve', json=dict(hook=1)).status_code, 409)
            r = h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=9))
            self.assertEqual(r.status_code, 409)
            self.assertTrue(r.json()['errors'])

            # storyline gate (the pipeline's own gate, by 'user (web)')
            r = h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=1))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(h.detail(slug)['state'], 'scripting')
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=2)).status_code, 409)
            log = h.episode_file(slug)['meta']['approval_log']
            self.assertEqual((log[-1]['gate'], log[-1]['by'], log[-1]['pick']), ('storyline', 'user (web)', 1))

            self.assertTrue(h.runner.run_once())
            d = h.detail(slug)
            self.assertEqual(d['state'], 'script', d.get('error'))
            sc = d['script']
            self.assertEqual(len(sc['hook_options']), 3)
            self.assertTrue(sc['can_approve'], sc['errors'])
            self.assertTrue(all(c['ok'] for c in sc['story_check']))
            self.assertTrue(any(b['sources'] for b in sc['beats']))
            page = h.client.get(f'/episodes/{slug}')
            self.assertIn('Approve script', page.text)
            self.assertIn('Story check', page.text)
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/script/approve', json=dict(hook=7)).status_code, 409)

            r = h.client.post(f'/api/episodes/{slug}/script/approve', json=dict(hook=2))
            self.assertEqual(r.status_code, 200, r.text)
            ep = h.episode_file(slug)
            self.assertEqual(ep['hook_pick'], 2)
            entry = ep['meta']['approval_log'][-1]
            self.assertEqual((entry['gate'], entry['by']), ('script', 'user (web)'))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'painting')
            self.assertTrue(d['script']['approved'])
            self.assertFalse(h.runner.run_once())               # the runner never takes Mac jobs

            # the Mac: heartbeat, claim, progress, previews, done
            self.assertEqual(h.w('post', '/api/worker/heartbeat', json=dict(host='mac', version='1')).json()['queued'], 1)
            job = h.claim()
            self.assertEqual((job['kind'], job['slug']), ('paint', slug))
            self.assertEqual(job['episode']['slug'], slug)
            self.assertIn('script', job['episode']['approvals'])
            self.assertIn('entries', job['ledger'])
            self.assertIsNone(h.claim())                       # one job at a time: nothing else queued
            r = h.w('post', f'/api/worker/jobs/{job["job_id"]}/progress', json=dict(stage='paint', pct=40, message='b03'))
            self.assertEqual(r.status_code, 200)
            d = h.detail(slug)
            self.assertEqual(d['job']['progress']['pct'], 40)
            self.assertTrue(d['worker']['online'] and d['worker']['busy'])
            for key in ('leaking-coin-jar', 'calendar-wall'):
                self.assertEqual(h.upload(job['job_id'], f'{key}.jpg', 'scene_preview').status_code, 200)
            result = dict(new=[scene('leaking-coin-jar', ['b01', 'b07']), scene('calendar-wall', ['b02'])], failed=[])
            self.assertEqual(h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result=result)).status_code, 200)
            d = h.detail(slug)
            self.assertEqual(d['state'], 'scenes')
            self.assertFalse(d['worker']['busy'])
            self.assertEqual({s['key'] for s in d['scenes']}, {'leaking-coin-jar', 'calendar-wall'})
            first = next(s for s in d['scenes'] if s['key'] == 'leaking-coin-jar')
            self.assertTrue(first['url'].startswith(f'/files/{slug}/leaking-coin-jar.jpg'))
            self.assertEqual([b['id'] for b in first['beat_text']], ['b01', 'b07'])
            self.assertTrue(first['beat_text'][0]['say'])
            self.assertEqual(h.client.get(first['url']).status_code, 200)
            page = h.client.get(f'/episodes/{slug}')
            self.assertIn('Approve all and render', page.text)

            # redo one scene with a note
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/scenes/redo', json=dict(key='nope', note='x')).status_code, 409)
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/scenes/redo', json=dict(key='calendar-wall', note='')).status_code, 409)
            r = h.client.post(f'/api/episodes/{slug}/scenes/redo', json=dict(key='calendar-wall', note='warmer light'))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(h.detail(slug)['state'], 'painting')
            job = h.claim()
            self.assertEqual(job['payload'], {'redo': {'calendar-wall': 'warmer light'}})
            h.upload(job['job_id'], 'calendar-wall.jpg', 'scene_preview', b'y' * 50)
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result=dict(new=[scene('calendar-wall', ['b02'])])))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'scenes')
            self.assertEqual(len(d['scenes']), 2)                # round 1 kept, the redone one replaced
            self.assertEqual(next(s for s in d['scenes'] if s['key'] == 'calendar-wall')['note'], 'warmer light')

            # approve all -> render
            r = h.client.post(f'/api/episodes/{slug}/scenes/approve', json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(h.episode_file(slug)['meta']['approval_log'][-1]['gate'], 'scenes')
            self.assertEqual(h.detail(slug)['state'], 'rendering')
            job = h.claim()
            self.assertEqual(job['kind'], 'render')
            qa = dict(summary=dict(duration=True, words=True, loudness=True, all=True))
            for name, kind, body in ((f'{slug}.mp4', 'mp4', b'\x00' * 4096), (f'{slug}.srt', 'srt', b'1\n00:00:00,000 --> 00:00:01,000\nHi\n'),
                                     ('qa.json', 'qa', json.dumps(qa).encode()), ('contact_sheet.jpg', 'contact_sheet', b'j')):
                self.assertEqual(h.upload(job['job_id'], name, kind, body).status_code, 200)
            r = h.w('post', f'/api/worker/jobs/{job["job_id"]}/done',
                    json=dict(result=dict(duration=44.8, qa_pass=True, qa_summary=qa['summary'])))
            self.assertEqual(r.status_code, 200)
            d = h.detail(slug)
            self.assertEqual(d['state'], 'done')
            self.assertEqual({f['kind'] for f in d['files']} >= {'mp4', 'srt', 'qa', 'contact_sheet'}, True)
            self.assertTrue(all(c['ok'] for c in d['render']['qa_checks']))
            self.assertEqual(d['cost_usd'], 0)
            mp4 = h.client.get(f'/files/{slug}/{slug}.mp4?dl=1')
            self.assertEqual(mp4.status_code, 200)
            self.assertIn('attachment', mp4.headers['content-disposition'])
            self.assertEqual(len(mp4.content), 4096)
            inline = h.client.get(f'/files/{slug}/{slug}.mp4', headers={'Range': 'bytes=0-99'})
            self.assertEqual(inline.status_code, 206)
            self.assertNotIn('attachment', inline.headers.get('content-disposition', ''))
            page = h.client.get(f'/episodes/{slug}')
            self.assertIn('<video', page.text)
            self.assertIn('Download MP4', page.text)
            hist = h.client.get('/')
            self.assertIn(f'/files/{slug}/{slug}.mp4?dl=1', hist.text)
            ledger = h.episode_file(slug, 'ledger.json')
            self.assertTrue(ledger['entries'])
            self.assertTrue(all(e['actual_usd'] == 0 for e in ledger['entries']))

    def test_qa_failure_and_nothing_new_to_paint(self):
        with Harness() as h:
            h.login()
            slug = to_painting(h)
            job = h.claim()
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result=dict(new=[], failed=[])))
            self.assertEqual(h.detail(slug)['state'], 'rendering')       # nothing new: straight to rendering
            job = h.claim()
            self.assertEqual(job['kind'], 'render')
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/done',
                json=dict(result=dict(duration=30, qa_pass=False, qa_summary=dict(duration=False, words=True, all=False))))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'done_qa_failed')
            self.assertIn(dict(name='duration', ok=False, detail=''), d['render']['qa_checks'])
            self.assertIn('QA failed', h.client.get(f'/episodes/{slug}').text)

    def test_failed_scene_blocks_approve_until_redone(self):
        with Harness() as h:
            h.login()
            slug = to_painting(h)
            job = h.claim()
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/done',
                json=dict(result=dict(new=[scene('a-scene', ['b01'])], failed=[dict(key='b-scene', why='QA failed twice')])))
            r = h.client.post(f'/api/episodes/{slug}/scenes/approve', json={})
            self.assertEqual(r.status_code, 409)
            self.assertIn('b-scene', r.json()['errors'][0])
            h.client.post(f'/api/episodes/{slug}/scenes/redo', json=dict(key='b-scene', note='simpler, one object'))
            job = h.claim()
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result=dict(new=[scene('b-scene', ['b02'])])))
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/scenes/approve', json={}).status_code, 200)

    def test_mac_failure_and_retry(self):
        with Harness() as h:
            h.login()
            slug = to_painting(h)
            job = h.claim()
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/retry', json={}).status_code, 409)
            h.w('post', f'/api/worker/jobs/{job["job_id"]}/fail', json=dict(error='FLUX ran out of memory'))
            d = h.detail(slug)
            self.assertEqual(d['state'], 'failed')
            self.assertEqual(d['error'], 'FLUX ran out of memory')
            page = h.client.get(f'/episodes/{slug}')
            self.assertIn('FLUX ran out of memory', page.text)
            self.assertIn('Retry', page.text)
            self.assertEqual(h.w('post', f'/api/worker/jobs/{job["job_id"]}/done', json=dict(result={})).status_code, 409)
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/retry', json={}).status_code, 200)
            d = h.detail(slug)
            self.assertEqual((d['state'], d['job']['status'], d['job']['id']), ('painting', 'queued', job['job_id']))

    def test_browser_forms_drive_the_gates(self):
        with Harness() as h:
            r = h.client.post('/login', data=dict(user='saloni', password='correct horse battery'), follow_redirects=False)
            r = h.client.post('/episodes', data=dict(topic=TOPIC), follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            slug = r.headers['location'].rsplit('/', 1)[1]
            h.runner.run_once()
            r = h.client.post(f'/episodes/{slug}/storyline', data=dict(story='2'), follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            h.runner.run_once()
            r = h.client.post(f'/episodes/{slug}/script/approve', data=dict(hook='1'), follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(h.detail(slug)['state'], 'painting')
            r = h.client.post(f'/episodes/{slug}/script/approve', data=dict(hook='1'))
            self.assertEqual(r.status_code, 409)
            self.assertIn('not at the script gate', r.text)
            self.assertEqual(h.client.get('/new').status_code, 200)

    def test_topic_validation_and_unique_slugs(self):
        with Harness() as h:
            h.login()
            self.assertEqual(h.client.post('/api/episodes', json=dict(topic='x')).status_code, 400)
            a = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            b = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            self.assertNotEqual(a, b)
            self.assertEqual(h.client.get('/api/episodes/nope').status_code, 404)


def to_painting(h, topic=TOPIC):
    slug = h.client.post('/api/episodes', json=dict(topic=topic)).json()['slug']
    h.runner.run_once()
    assert h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=1)).status_code == 200
    h.runner.run_once()
    r = h.client.post(f'/api/episodes/{slug}/script/approve', json=dict(hook=1))
    assert r.status_code == 200, r.text
    return slug


class GateErrorTest(unittest.TestCase):
    def test_script_with_errors_cannot_be_approved(self):
        with Harness() as h:
            h.login()
            slug = h.client.post('/api/episodes', json=dict(topic=TOPIC)).json()['slug']
            h.runner.run_once()
            h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=1))
            h.runner.run_once()
            path = h.settings.episodes_dir / slug / 'episode.json'
            ep = json.loads(path.read_text())
            ep['beats'][2]['shot']['motion'] = []                          # 0 motion recipes: an error
            path.write_text(json.dumps(ep))
            d = h.detail(slug)
            self.assertFalse(d['script']['can_approve'])
            self.assertIn('motion recipes', d['script']['blocked_reason'])
            self.assertTrue(all(d['script']['hook_errors'][k] for k in ('1', '2', '3')))
            page = h.client.get(f'/episodes/{slug}')
            self.assertRegex(page.text, r'data-approve\s+disabled')
            r = h.client.post(f'/api/episodes/{slug}/script/approve', json=dict(hook=1))
            self.assertEqual(r.status_code, 409)
            self.assertTrue(any('motion recipes' in e for e in r.json()['errors']))
            self.assertEqual(h.detail(slug)['state'], 'script')
            self.assertNotIn('script', h.episode_file(slug)['approvals'])

    def test_same_format_as_the_last_video_is_refused(self):
        with Harness() as h:
            h.login()
            first = to_painting(h)
            fmt = h.episode_file(first)['storylines'][0]['format']
            slug = h.client.post('/api/episodes', json=dict(topic='Why the sky is blue at noon')).json()['slug']
            h.runner.run_once()
            formats = [c['format'] for c in h.detail(slug)['storylines']['cards']]
            self.assertNotIn(fmt, formats)                                 # the rotation rule, through the fake
            path = h.settings.episodes_dir / slug / 'episode.json'
            ep = json.loads(path.read_text())
            ep['storylines'][0]['format'] = fmt
            path.write_text(json.dumps(ep))
            card = h.detail(slug)['storylines']['cards'][0]
            self.assertIn('same format', card['refused'])
            r = h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=1))
            self.assertEqual(r.status_code, 409)
            self.assertIn('never the same format twice', ' '.join(r.json()['errors']))
            self.assertEqual(h.detail(slug)['state'], 'storylines')
            self.assertEqual(h.client.post(f'/api/episodes/{slug}/storyline', json=dict(story=2)).status_code, 200)


if __name__ == '__main__':
    unittest.main()
