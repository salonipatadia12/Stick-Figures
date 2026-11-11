"""Claim -> progress -> upload -> done, idle, fail, retries and resume, caffeinate only during a job."""
import json
import unittest
from pathlib import Path

from macworker import proc
from macworker.handlers import JobError, Outcome
from macworker.util import Backoff
from macworker.worker import Worker, dry_run

from .fakes import FakePopen, FakeServer, TmpDir, job, make_cfg


class Base(TmpDir, unittest.TestCase):
    def setUp(self):
        super().setUp()
        FakePopen.instances = []
        self.cfg = make_cfg(self.tmp)
        self.sleeps = []

    def worker(self, server, handlers, **kw):
        return Worker(self.cfg, server.client(), handlers=handlers, sleep=self.sleeps.append,
                      awake=lambda: proc.Awake(popen=FakePopen), backoff=Backoff(base=1, rng=lambda: 0.5), **kw)

    def outputs(self, *names):
        out = []
        for n in names:
            p = self.tmp / 'out' / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f'data of {n}'.encode())
            out.append(dict(path=str(p), name=n, kind=n.rsplit('.', 1)[-1]))
        return out


class FlowTest(Base):
    def test_claim_progress_upload_done(self):
        server = FakeServer([job(7)])
        files = self.outputs('why-honey-never-spoils.mp4', 'qa.json')

        def render(ctx):
            ctx.report('shots', 40, 'shot b03 done')
            ctx.report('qa', 85, 'running QA')
            return Outcome(dict(duration=44.2, qa_pass=True, qa_summary='QA passed'), files)

        rc = self.worker(server, dict(render=render)).run(once=True)
        self.assertEqual(rc, 0)
        paths = [p for p in server.paths() if not p.endswith('/heartbeat')]
        self.assertEqual(paths[0], '/api/worker/claim')
        self.assertEqual(paths[-1], '/api/worker/jobs/7/done')
        self.assertLess(paths.index('/api/worker/jobs/7/progress'), paths.index('/api/worker/jobs/7/files'))
        self.assertEqual([(n, k) for _, n, k, _ in server.files], [('why-honey-never-spoils.mp4', 'mp4'), ('qa.json', 'json')])
        self.assertEqual(server.files[0][3], b'data of why-honey-never-spoils.mp4')
        self.assertEqual(server.done['7'], dict(duration=44.2, qa_pass=True, qa_summary='QA passed'))
        self.assertEqual([b['stage'] for _, b in server.progress][:2], ['shots', 'qa'])
        claim = next(b for _, p, b in server.calls if p == '/api/worker/claim')
        self.assertEqual(claim, dict(kinds=['pitch', 'script', 'paint', 'render']))   # the server filters by provider
        hb = next(b for _, p, b in server.calls if p == '/api/worker/heartbeat')
        self.assertEqual(set(hb), {'host', 'version'})
        state = json.loads((self.cfg.state_dir / 'jobs/7/outcome.json').read_text())
        self.assertEqual(state['status'], 'reported')

    def test_idle_204(self):
        server = FakeServer([])
        called = []
        rc = self.worker(server, dict(render=lambda ctx: called.append(1))).run(once=True)
        self.assertEqual(rc, 0)
        self.assertEqual(server.paths(), ['/api/worker/heartbeat', '/api/worker/claim'])
        self.assertEqual(called, [])
        self.assertEqual(FakePopen.instances, [])           # no caffeinate while idle

    def test_job_error_reports_fail(self):
        server = FakeServer([job(8)])

        def render(ctx):
            raise JobError('render failed (exit 1):\nboom')

        self.worker(server, dict(render=render)).run(once=True)
        self.assertEqual(server.failed['8'], 'render failed (exit 1):\nboom')
        self.assertNotIn('8', server.done)
        self.assertEqual(server.files, [])

    def test_crash_reports_fail_with_type(self):
        server = FakeServer([job(9)])

        def render(ctx):
            raise RuntimeError('unexpected')

        self.worker(server, dict(render=render)).run(once=True)
        self.assertIn('worker error: RuntimeError: unexpected', server.failed['9'])

    def test_unknown_kind_fails(self):
        server = FakeServer([job(10, kind='paint')])
        self.worker(server, dict(render=lambda ctx: None)).run(once=True)
        self.assertIn("does not run 'paint'", server.failed['10'])

    def test_auth_error_once(self):
        server = FakeServer([job(11)])
        w = Worker(self.cfg, __import__('macworker.client', fromlist=['Client']).Client(
            'https://stick.test', 'wrong', transport=server.client().http._transport), handlers={}, sleep=self.sleeps.append)
        self.assertEqual(w.run(once=True), 2)
        self.assertEqual(len(server.jobs), 1)                  # nothing claimed

    def test_network_down_on_claim_backs_off_then_claims(self):
        server = FakeServer([job(12)])
        server.down['/heartbeat'] = 2
        files = self.outputs('a.srt')
        w = self.worker(server, dict(render=lambda ctx: Outcome(dict(ok=1), files)))
        orig = w.process
        w.process = lambda j: (orig(j), (_ for _ in ()).throw(SystemExit(0)))   # stop the endless loop after one job
        with self.assertRaises(SystemExit):
            w.run()
        self.assertEqual(len(self.sleeps), 2)                  # two backoffs, then the claim worked
        self.assertIn('12', server.done)


class RetryTest(Base):
    def test_upload_retry_after_network_error(self):
        server = FakeServer([job(20)])
        server.down['/jobs/20/files'] = 1                      # the first upload attempt fails
        files = self.outputs('x.mp4', 'qa.json')
        self.worker(server, dict(render=lambda ctx: Outcome(dict(qa_pass=True), files))).run(once=True)
        self.assertEqual([n for _, n, _, _ in server.files], ['x.mp4', 'qa.json'])   # each uploaded once
        self.assertEqual(server.done['20'], dict(qa_pass=True))
        self.assertEqual(len(self.sleeps), 1)

    def test_second_file_failure_does_not_reupload_first(self):
        server = FakeServer([job(21)])
        files = self.outputs('x.mp4', 'qa.json')
        orig = server.handler
        state = dict(n=0)

        def flaky(request):
            if request.url.path.endswith('/files'):
                state['n'] += 1
                if state['n'] == 2:
                    import httpx
                    raise httpx.ReadTimeout('slow', request=request)
            return orig(request)

        server.handler = flaky
        self.worker(server, dict(render=lambda ctx: Outcome({}, files))).run(once=True)
        self.assertEqual([n for _, n, _, _ in server.files], ['x.mp4', 'qa.json'])
        self.assertIn('21', server.done)

    def test_result_kept_on_disk_and_resumed_later(self):
        server = FakeServer([job(22)])
        server.down['/jobs/22/done'] = 99                      # the server goes away after the uploads
        files = self.outputs('x.mp4')
        status = self.worker(server, dict(render=lambda ctx: Outcome(dict(duration=3.0), files)), max_report_tries=2)
        status.run(once=True)
        state = json.loads((self.cfg.state_dir / 'jobs/22/outcome.json').read_text())
        self.assertEqual(state['status'], 'finished')
        self.assertEqual(state['uploaded'], ['x.mp4'])
        self.assertNotIn('22', server.done)
        server.down.clear()                                    # back online: a new worker process resumes the report
        w2 = self.worker(server, {})
        self.assertEqual(w2.resume_pending(), 1)
        self.assertEqual(server.done['22'], dict(duration=3.0))
        self.assertEqual([n for _, n, _, _ in server.files], ['x.mp4'])     # not uploaded twice

    def test_pending_fail_is_resumed(self):
        server = FakeServer([job(23)])
        server.down['/jobs/23/fail'] = 99

        def render(ctx):
            raise JobError('nope')

        self.worker(server, dict(render=render), max_report_tries=1).run(once=True)
        server.down.clear()
        self.worker(server, {}).resume_pending()
        self.assertEqual(server.failed['23'], 'nope')

    def test_rejected_done_keeps_outputs(self):
        server = FakeServer([job(24)])
        server.status['/jobs/24/done'] = 409
        files = self.outputs('x.mp4')
        self.worker(server, dict(render=lambda ctx: Outcome({}, files))).run(once=True)
        state = json.loads((self.cfg.state_dir / 'jobs/24/outcome.json').read_text())
        self.assertEqual(state['status'], 'rejected')
        self.assertTrue(Path(files[0]['path']).exists())

    def test_abandoned_job_is_not_reported(self):
        jdir = self.cfg.state_dir / 'jobs/30'
        jdir.mkdir(parents=True)
        (jdir / 'job.json').write_text(json.dumps(dict(job_id=30, kind='render', status='running')))
        server = FakeServer([])
        self.worker(server, {}).resume_pending()
        self.assertEqual(json.loads((jdir / 'outcome.json').read_text())['status'], 'abandoned')
        self.assertEqual(server.calls, [])


class CaffeinateTest(Base):
    def test_caffeinate_only_while_job_runs(self):
        server = FakeServer([job(40)])
        seen = {}

        def render(ctx):
            seen['during'] = [(p.args, p.terminated) for p in FakePopen.instances]
            return Outcome({}, [])

        self.worker(server, dict(render=render)).run(once=True)
        self.assertEqual(len(seen['during']), 1)
        args, terminated = seen['during'][0]
        self.assertEqual(args[:3], ['caffeinate', '-i', '-w'])
        self.assertFalse(terminated)
        self.assertTrue(FakePopen.instances[0].terminated)    # released after the job
        self.assertEqual(len(FakePopen.instances), 1)

    def test_caffeinate_released_when_job_crashes(self):
        server = FakeServer([job(41)])

        def render(ctx):
            raise RuntimeError('x')

        self.worker(server, dict(render=render)).run(once=True)
        self.assertTrue(FakePopen.instances[0].terminated)

    def test_awake_waits_on_worker_pid(self):
        a = proc.Awake(popen=FakePopen, pid=999)
        with a:
            self.assertEqual(FakePopen.instances[-1].args, ['caffeinate', '-i', '-w', '999'])
        self.assertTrue(FakePopen.instances[-1].terminated)


class DryRunTest(Base):
    def test_dry_run_never_claims(self):
        server = FakeServer([job(50)])
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dry_run(self.cfg, client_factory=lambda s, t: server.client(), ollama_check=lambda: '0.12.0')
        self.assertIn('nothing was claimed', buf.getvalue())
        self.assertNotIn('test-token-123', buf.getvalue())     # the token is never printed
        self.assertEqual(server.paths(), ['/api/health', '/api/worker/heartbeat'])
        self.assertEqual(len(server.jobs), 1)
        self.assertEqual(rc, 1)                                 # the temp repo has no .venv: reported as a problem


if __name__ == '__main__':
    unittest.main()
