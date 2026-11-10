"""The fake Mac worker against the mock server (tests/e2e/mock_server.py, a test double of the worker API): it must
speak the contract exactly before it is trusted to drive the real web app in test_e2e."""
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_harness as h  # noqa: E402

try:
    import fastapi  # noqa: F401
    import multipart  # noqa: F401
    import uvicorn  # noqa: F401
    from PIL import Image
    MISSING = None
except ImportError as exc:  # pragma: no cover
    MISSING = f'needs fastapi, uvicorn, python-multipart and Pillow ({exc})'
if not Path(h.FFPROBE).exists() or not shutil.which(Path(h.FFPROBE).with_name('ffmpeg').name, path=str(Path(h.FFPROBE).parent)):
    MISSING = MISSING or f'needs ffmpeg/ffprobe at {Path(h.FFPROBE).parent}'


def start_mock(case, **env):
    """Start the mock server on a free port; returns (base_url, token). Killed by the case's cleanup."""
    tmp = Path(tempfile.mkdtemp(prefix='mock-srv-'))
    case.addCleanup(shutil.rmtree, tmp, True)
    token, _ = h.token_pair()
    port = h.free_port()
    e = h.safe_env(MOCK_WORKER_TOKEN=token, **env)
    proc = h.Proc('mock_server', [h.PYTHON, '-m', 'uvicorn', 'mock_server:app', '--app-dir', str(h.HERE),
                                  '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'], e, tmp)
    case.addCleanup(proc.stop)
    base = f'http://127.0.0.1:{port}'
    api = h.Api(base)
    case.addCleanup(api.close)

    def up():
        if not proc.alive():
            raise AssertionError('mock server died\n' + proc.tail())
        try:
            return api.get('/api/health').status_code == 200
        except Exception:
            return False
    h.wait_for(up, 20, what='mock server')
    case.tmp, case.api, case.token, case.base = tmp, api, token, base
    return base, token


@unittest.skipIf(MISSING, MISSING or '')
class FakeWorkerTest(unittest.TestCase):
    def setUp(self):
        start_mock(self, MOCK_LEASE_SECONDS=2)
        self.journal = self.tmp / 'journal.jsonl'

    def queue(self, kind, slug='mock-episode', payload=None):
        r = self.api.post('/mock/jobs', json={'kind': kind, 'slug': slug, 'payload': payload or {}})
        r.raise_for_status()
        return r.json()['job_id']

    def job(self, jid):
        return self.api.get(f'/mock/jobs/{jid}').json()

    def file(self, name, slug='mock-episode'):
        r = self.api.get(f'/mock/files/{slug}/{name}')
        self.assertEqual(r.status_code, 200, f'{name} was not uploaded')
        return r.content, r.headers['x-kind']

    def run_worker(self, *flags, token=None, timeout=90):
        p = h.Proc(f'worker{len(flags)}{time.monotonic_ns()}', h.worker_cmd(self.base, token or self.token, *flags,
                                                                              journal=self.journal), h.safe_env(), self.tmp)
        self.addCleanup(p.stop)
        try:
            code = p.wait(timeout)
        except Exception:
            self.fail('fake worker hung\n' + p.tail())
        return code, p

    def test_paint_uploads_two_previews_and_reports_new(self):
        jid = self.queue('paint')
        code, p = self.run_worker('--once', '--wait', '10')
        self.assertEqual(code, 0, p.tail())
        j = self.job(jid)
        self.assertEqual(j['status'], 'done', j)
        new = j['result']['new']
        self.assertEqual(len(new), 2)
        self.assertEqual(j['result']['failed'], [])
        for item in new:
            self.assertEqual(set(item), {'key', 'kind', 'name', 'scene', 'beats'})
            self.assertIn(item['kind'], ('scene', 'prop'))
            self.assertRegex(item['name'], h.NAME_OK)
            data, kind = self.file(item['name'])
            self.assertEqual(kind, 'scene_preview')
            img = Image.open(io.BytesIO(data))
            self.assertEqual((img.format, img.width), ('JPEG', 720))
        self.assertEqual(j['progress']['pct'], 90)
        row = h.read_journal(self.journal)[0]
        self.assertEqual((row['kind'], row['outcome'], row['has_episode'], row['has_ledger']), ('paint', 'done', True, True))

    def test_redo_repaints_only_the_asked_key(self):
        jid = self.queue('paint', payload={'redo': {'fake-scene-1': 'make the jar bigger'}})
        self.assertEqual(self.run_worker('--once', '--wait', '10')[0], 0)
        new = self.job(jid)['result']['new']
        self.assertEqual([n['key'] for n in new], ['fake-scene-1'])
        self.assertEqual(new[0]['scene'], 'make the jar bigger')

    def test_render_uploads_mp4_srt_qa_and_contact_sheet(self):
        jid = self.queue('render', slug='leaky-jar')
        code, p = self.run_worker('--once', '--wait', '10')
        self.assertEqual(code, 0, p.tail())
        j = self.job(jid)
        self.assertEqual(j['status'], 'done', j)
        res = j['result']
        self.assertEqual(set(res), {'duration', 'qa_pass', 'qa_summary'})
        self.assertTrue(res['qa_pass'])
        self.assertGreater(res['duration'], 1.0)
        mp4, kind = self.file('leaky-jar.mp4', 'leaky-jar')
        self.assertEqual(kind, 'mp4')
        path = self.tmp / 'got.mp4'
        path.write_bytes(mp4)
        self.assertGreater(h.probe_duration(path), 1.0)
        srt, kind = self.file('leaky-jar.srt', 'leaky-jar')
        self.assertEqual(kind, 'srt')
        self.assertEqual(len(h.parse_srt(srt.decode())), 2)
        qa, kind = self.file('qa.json', 'leaky-jar')
        self.assertEqual(kind, 'qa')
        report = json.loads(qa)
        self.assertIs(report['all'], True)
        self.assertIs(report['summary']['all'], True)
        self.assertIn('words', report['summary'])
        sheet, kind = self.file('contact_sheet.jpg', 'leaky-jar')
        self.assertEqual((kind, Image.open(io.BytesIO(sheet)).format), ('contact_sheet', 'JPEG'))

    def test_fail_render_reports_qa_fail(self):
        jid = self.queue('render', slug='leaky-jar')
        self.assertEqual(self.run_worker('--once', '--wait', '10', '--fail-render')[0], 0)
        j = self.job(jid)
        self.assertEqual(j['status'], 'done')
        self.assertIs(j['result']['qa_pass'], False)
        self.assertIs(json.loads(self.file('qa.json', 'leaky-jar')[0])['all'], False)

    def test_fail_kind_calls_fail(self):
        jid = self.queue('paint')
        self.assertEqual(self.run_worker('--once', '--wait', '10', '--fail-kind', 'paint')[0], 0)
        j = self.job(jid)
        self.assertEqual(j['status'], 'failed')
        self.assertIn('on purpose', j['error'])

    def test_crash_after_claim_leaves_the_lease_to_expire(self):
        jid = self.queue('paint')
        code, _ = self.run_worker('--crash-after-claim')
        self.assertEqual(code, 17)
        self.assertEqual(self.job(jid)['status'], 'running')
        h.wait_for(lambda: self.job(jid)['status'] == 'queued', 10, what='lease expiry')
        self.assertEqual(self.run_worker('--once', '--wait', '10')[0], 0)
        j = self.job(jid)
        self.assertEqual((j['status'], j['attempts']), ('done', 2))
        self.assertEqual([r['outcome'] for r in h.read_journal(self.journal)], ['crash', 'done'])

    def test_once_with_nothing_queued_exits(self):
        self.assertEqual(self.run_worker('--once', timeout=20)[0], 0)

    def test_a_refused_token_stops_the_worker(self):
        code, p = self.run_worker('--once', '--wait', '5', token='not-the-token', timeout=20)
        self.assertEqual(code, 2, p.tail())
        self.assertIn('token refused', p.log.read_text())
        self.assertNotIn('not-the-token', p.log.read_text())
        for path in ('/api/worker/heartbeat', '/api/worker/claim'):
            self.assertIn(self.api.post(path, json={}).status_code, h.REFUSED)


if __name__ == '__main__':
    unittest.main()
