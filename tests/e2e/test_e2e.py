"""End to end through the JSON API of docs/WEBAPP.md: topic -> storylines -> script -> scenes -> MP4.

Fake mode (default): starts the web app (uvicorn webapp.app:app) and `python -m webapp.runner` on a free local port
with STICK_FAKE_ANTHROPIC=1, SQLite and a data dir in a temp dir, plus tests/e2e/fake_worker.py as the Mac. Nothing
paid is ever sent: the subprocesses get ANTHROPIC_API_KEY='' (which also stops .env loading a real one).

Real mode (E2E_REAL=1 E2E_REAL_CONFIRM=yes E2E_SERVER=... E2E_USER=... E2E_PASSWORD=... E2E_TOPIC=...): the same
flow against a real deployment and the real Mac worker. It costs real money (Anthropic research, storylines and
script). It never approves on its own: each gate waits for the human unless E2E_PICKS names the pick, e.g.
`E2E_PICKS=story=1,hook=1` (and `,scenes=approve` to approve the painted scenes). Run only when the coordinator asks:

    python tests/e2e/test_e2e.py --real
"""
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

if __name__ == '__main__' and '--real' in sys.argv:      # decided before the skip decorators below are evaluated
    sys.argv.remove('--real')
    os.environ['E2E_REAL'] = '1'
    sys.argv[1:1] = ['RealModeTest']

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_harness as h  # noqa: E402

if str(h.ROOT) not in sys.path:
    sys.path.insert(0, str(h.ROOT))


WEBAPP_DIR = h.ROOT / 'webapp'


def _webapp_missing():
    # Looked up on disk, not with find_spec: under `unittest discover -s tests` a tests/webapp/ package would shadow
    # the real one. The subprocesses run from the repository root, where webapp/ wins.
    for mod in ('app', 'runner'):
        if not ((WEBAPP_DIR / f'{mod}.py').exists() or (WEBAPP_DIR / mod / '__init__.py').exists()):
            return f'webapp/{mod} is not in this checkout yet (lane B); the e2e test runs after the merge'
    for mod in ('uvicorn', 'httpx'):
        if importlib.util.find_spec(mod) is None:
            return f'needs {mod}'
    if not Path(h.FFPROBE).exists():
        return f'needs ffprobe at {h.FFPROBE}'
    return None


WEBAPP_MISSING = _webapp_missing()
REAL = os.getenv('E2E_REAL') == '1'
STEP = float(os.getenv('E2E_STEP_TIMEOUT', '120'))       # fake mode: one state change (runner + fake LLM + fake worker)
TEST_LIMIT = int(os.getenv('E2E_TEST_TIMEOUT', '600'))   # fake mode: a whole test, hard


def app_reads(name, src=WEBAPP_DIR):
    """Whether the app's source mentions an environment variable (to find the short-lease override, if any)."""
    files = [src] if src.is_file() else src.rglob('*.py')
    return any(name in p.read_text(errors='replace') for p in files)


def lease_env_name(src=WEBAPP_DIR):
    return next((n for n in h.LEASE_ENV_NAMES if app_reads(n, src)), None)


# ------------------------------------------------------------------------------------------------ the stack

class Stack:
    """The web app + runner (+ fake workers) in a temp dir. stop() always kills everything."""

    def __init__(self, lease_seconds=None, app='webapp.app:app', app_dir=None, runner='webapp.runner'):
        self.app, self.app_dir, self.runner_mod = app, app_dir, runner
        self.tmp = Path(tempfile.mkdtemp(prefix='stick-e2e-'))
        self.logs = self.tmp / 'logs'
        self.logs.mkdir()
        self.procs = []
        self.port = h.free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.user = 'e2e-' + h.secrets.token_hex(3)
        self.password = h.secrets.token_urlsafe(18)
        self.token, token_sha = h.token_pair()
        self.journal = self.tmp / 'worker-journal.jsonl'
        data = self.tmp / 'data'
        (data / 'episodes').mkdir(parents=True)
        (data / 'outputs').mkdir()
        self.env = h.safe_env(
            DATABASE_URL=f'sqlite:///{self.tmp / "stick.db"}', STICK_DATA_DIR=data, STICK_EPISODES_DIR=data / 'episodes',
            APP_USER=self.user, SESSION_SECRET=h.secrets.token_hex(32), WORKER_TOKEN_SHA256=token_sha,
            PUBLIC_URL=self.base, SPEND_PREAPPROVED_USD='0.50', STICK_FAKE_ANTHROPIC='1')
        if lease_seconds:
            self.env.update({n: str(lease_seconds) for n in h.LEASE_ENV_NAMES})
        self.env['APP_PASSWORD_HASH'], self.hash_how = h.password_hash(self.password, self.env)

    def start(self):
        extra = ['--app-dir', str(self.app_dir)] if self.app_dir else []
        self.web = self.spawn('web', [h.PYTHON, '-m', 'uvicorn', self.app, *extra, '--host', '127.0.0.1',
                                      '--port', str(self.port), '--log-level', 'warning'])
        probe = h.Api(self.base, timeout=3)
        try:
            def up():
                if not self.web.alive():
                    raise AssertionError('the web app exited\n' + self.web.tail())
                try:
                    return probe.get('/api/health').status_code == 200
                except Exception:
                    return False
            h.wait_for(up, 45, what='the web app to answer /api/health')
        finally:
            probe.close()
        if self.runner_mod:                              # the mock web app double runs its own runner thread
            self.runner = self.spawn('runner', [h.PYTHON, '-m', self.runner_mod])
        return self

    def spawn(self, name, cmd):
        p = h.Proc(name, cmd, self.env, self.logs)
        self.procs.append(p)
        return p

    def worker(self, *flags, name='worker'):
        return self.spawn(f'{name}{len(self.procs)}', h.worker_cmd(self.base, self.token, *flags, journal=self.journal))

    def tails(self):
        return '\n'.join(p.tail() for p in self.procs)

    def stop(self):
        for p in reversed(self.procs):
            p.stop()
        if os.getenv('E2E_KEEP') != '1':
            shutil.rmtree(self.tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ the flow

class Flow:
    """The phone's journey through the JSON API, shared by fake and real mode. `picks` holds the human's choices
    ({'story': n, 'hook': n, 'scenes': 'approve'}); a gate with no pick waits for the human to act in the UI."""

    def __init__(self, case, api, *, step=STEP, gate_wait=None, render_wait=None, picks=None, log=None):
        self.case, self.api = case, api
        self.step = step
        self.gate_wait = gate_wait or step
        self.render_wait = render_wait or step
        self.picks = picks if picks is not None else {}
        self.html = {}            # state -> HTML status of /episodes/{slug} (phone user agent) when first seen
        self.log = log or (lambda *a: None)
        self.slug = None

    def on_state(self, state, ep):
        self.log(f'state: {state} (job: {json.dumps(ep.get("job"))[:160]})')
        if state not in self.html:
            self.html[state] = self.api.get(f'/episodes/{self.slug}', headers={'User-Agent': h.PHONE_UA}).status_code

    def wait(self, want, timeout=None, bad=('failed',)):
        return self.api.wait_state(self.slug, want, timeout or self.step, bad=bad, on_state=self.on_state)

    def human(self, gate, past, timeout):
        """No pick given for this gate: tell the human and wait for the state to move on."""
        url = f'{self.api.base}/episodes/{self.slug}'
        print(f'\n>>> {gate} gate: make the pick on your phone: {url} (waiting up to {timeout / 60:.0f} min)\n',
              file=sys.stderr, flush=True)
        return self.wait(past, timeout)

    def create(self, topic):
        r = self.api.post('/api/episodes', json={'topic': topic, 'niche': 'explainer', 'audience': 'curious adults'})
        self.case.assertEqual(r.status_code, 200, r.text)
        self.slug = r.json()['slug']
        self.case.assertRegex(self.slug, h.NAME_OK)
        self.log(f'episode {self.slug}')
        return self.slug

    def storylines(self):
        ep = self.wait('storylines', self.step * (10 if REAL else 1))
        self.case.assertTrue(ep.get('storylines'), f'no storylines in {sorted(ep)}')
        if 'story' in self.picks:
            r = self.api.post(f'/api/episodes/{self.slug}/storyline', json={'story': int(self.picks['story'])})
            self.case.assertEqual(r.status_code, 200, r.text)
            return self.wait('script', self.step * (10 if REAL else 1))
        return self.human('storyline', {'script'}, self.gate_wait)

    def script(self):
        ep = self.api.episode(self.slug)
        self.case.assertTrue(ep.get('script'), f'no script in {sorted(ep)}')
        if 'hook' in self.picks:
            r = self.api.post(f'/api/episodes/{self.slug}/script/approve', json={'hook': int(self.picks['hook'])})
            self.case.assertEqual(r.status_code, 200, f'script approve refused: {r.status_code} {r.text}')
            return self.wait({'painting', 'scenes', 'rendering', 'done', 'done_qa_failed'})
        return self.human('script', {'painting', 'scenes', 'rendering', 'done', 'done_qa_failed'}, self.gate_wait)

    def scenes(self, redo_note=None, journal=None):
        ep = self.wait({'scenes', 'rendering', 'done', 'done_qa_failed'}, self.render_wait)
        if ep['state'] != 'scenes':
            return ep                                   # painting found nothing new
        keys = h.scene_keys(ep)
        self.case.assertTrue(keys, f'scenes gate with no scenes: {ep.get("scenes")!r}')
        if redo_note:
            r = self.api.post(f'/api/episodes/{self.slug}/scenes/redo', json={'key': keys[0], 'note': redo_note})
            self.case.assertEqual(r.status_code, 200, r.text)
            h.wait_for(lambda: [j for j in h.read_journal(journal) if j['kind'] == 'paint' and j['outcome'] == 'done'
                                and (j.get('payload') or {}).get('redo') == {keys[0]: redo_note}],
                       self.step, what='the redo paint job (payload.redo = {key: note})')
            ep = self.wait('scenes')
            self.case.assertIn(keys[0], h.scene_keys(ep))
        if self.picks.get('scenes') == 'approve':
            r = self.api.post(f'/api/episodes/{self.slug}/scenes/approve', json={})
            self.case.assertEqual(r.status_code, 200, r.text)
            return ep
        return self.human('scenes', {'rendering', 'done', 'done_qa_failed'}, self.gate_wait)

    def finish(self, want='done'):
        return self.wait({'done', 'done_qa_failed'} if want == 'any' else want, self.render_wait)

    def download(self, tmp: Path, expect_pass=True):
        """MP4 plays (ffprobe > 1 s), SRT parses, qa.json's verdict, attachment download."""
        ep = self.api.episode(self.slug)
        names = h.file_names(ep)
        out = {}
        for name in (f'{self.slug}.mp4', f'{self.slug}.srt', 'qa.json'):
            self.case.assertIn(name, names, f'{name} not in the episode files {names}')
            r = self.api.get(f'/files/{self.slug}/{name}')
            self.case.assertEqual(r.status_code, 200, f'/files/{self.slug}/{name}: {r.status_code}')
            (tmp / name).write_bytes(r.content)
            out[name] = tmp / name
        self.case.assertGreater(h.probe_duration(out[f'{self.slug}.mp4']), 1.0)
        self.case.assertTrue(h.parse_srt(out[f'{self.slug}.srt'].read_text()))
        verdict = h.qa_all(json.loads(out['qa.json'].read_text()))
        self.case.assertIs(verdict, expect_pass, 'qa.json all')
        r = self.api.get(f'/files/{self.slug}/{self.slug}.mp4', params={'dl': 1})
        self.case.assertEqual(r.status_code, 200)
        self.case.assertIn('attachment', r.headers.get('content-disposition', ''))
        return ep

    def cost_and_history(self, topic, state='done'):
        ep = self.api.episode(self.slug)
        self.case.assertIsInstance(ep.get('cost_usd'), (int, float), f'cost_usd: {ep.get("cost_usd")!r}')
        r = self.api.get('/api/episodes')
        self.case.assertEqual(r.status_code, 200)
        row = next((e for e in r.json() if e.get('slug') == self.slug), None)
        self.case.assertIsNotNone(row, 'episode missing from the history')
        self.case.assertEqual((row['topic'], row['state']), (topic, state))
        self.case.assertIsInstance(row.get('cost_usd'), (int, float))
        self.case.assertIn('created_at', row)
        page = self.api.get('/', headers={'User-Agent': h.PHONE_UA})
        self.case.assertEqual(page.status_code, 200)
        self.case.assertIn(topic, page.text, 'history page does not list the topic')
        self.case.assertTrue(re.search(r'\$|USD|cost', page.text, re.I), 'history page shows no cost')
        return ep['cost_usd']


def check_refusals(case, base, slug, token):
    """Unauthenticated requests are refused; the worker token opens only the worker API and a bad one nothing."""
    anon = h.Api(base)
    try:
        health = anon.get('/api/health')
        case.assertEqual(health.status_code, 200)
        case.assertNotIn(token, health.text)
        for path in ('/api/episodes', f'/api/episodes/{slug}'):
            case.assertIn(anon.get(path).status_code, h.REFUSED, path)
        case.assertIn(anon.post('/api/episodes', json={'topic': 'x'}).status_code, h.REFUSED)
        case.assertIn(anon.post(f'/api/episodes/{slug}/scenes/approve', json={}).status_code, h.REFUSED)
        for path in ('/', '/new', f'/episodes/{slug}', f'/files/{slug}/{slug}.mp4'):
            r = anon.get(path, headers={'User-Agent': h.PHONE_UA})
            case.assertIn(r.status_code, h.REFUSED + h.REDIRECT, f'{path} without login: {r.status_code}')
            if r.status_code in h.REDIRECT:
                case.assertIn('/login', r.headers.get('location', ''), path)
        case.assertIn(anon.login('nobody', 'wrong-password').status_code, (400, 401, 403))
        # the worker token is not a login, and no token or a bad one opens nothing
        bearer = {'Authorization': f'Bearer {token}'}
        case.assertIn(anon.get('/api/episodes', headers=bearer).status_code, h.REFUSED)
        for headers in ({}, {'Authorization': 'Bearer not-the-token'}, {'Authorization': token}):
            for path, body in (('/api/worker/heartbeat', {'host': 'x', 'version': 'x'}),
                               ('/api/worker/claim', {'kinds': ['paint', 'render']})):
                r = anon.post(path, json=body, headers=headers)
                case.assertIn(r.status_code, h.REFUSED, f'{path} with {list(headers) or "no"} auth: {r.status_code}')
    finally:
        anon.close()


# ------------------------------------------------------------------------------------------------ fake mode

class _Watchdog:
    """A hard limit for a whole test: SIGALRM raises in the test (main thread only), cleanups still run."""

    def __init__(self, seconds):
        self.on = threading.current_thread() is threading.main_thread() and hasattr(signal, 'SIGALRM')
        if self.on:
            def boom(*_):
                raise TimeoutError(f'e2e test exceeded its hard limit of {seconds} s')
            self.old = signal.signal(signal.SIGALRM, boom)
            signal.alarm(seconds)

    def cancel(self):
        if self.on:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self.old)


@unittest.skipIf(REAL, 'E2E_REAL=1: running real mode only')
@unittest.skipIf(WEBAPP_MISSING, WEBAPP_MISSING or '')
class FakeModeCase(unittest.TestCase):
    lease_seconds = None
    app, app_dir, runner, app_src = 'webapp.app:app', None, 'webapp.runner', WEBAPP_DIR   # test_e2e_mock overrides

    def setUp(self):
        dog = _Watchdog(TEST_LIMIT)
        self.addCleanup(dog.cancel)
        self.stack = Stack(self.lease_seconds, self.app, self.app_dir, self.runner)
        self.addCleanup(self.stack.stop)
        self.stack.start()
        self.api = h.Api(self.stack.base)
        self.addCleanup(self.api.close)
        r = self.api.login(self.stack.user, self.stack.password)
        self.assertEqual(r.status_code, 200, f'login failed (password hash made by {self.stack.hash_how}): {r.text}')
        self.assertTrue(r.json().get('ok'))
        self.flow = Flow(self, self.api, picks={'story': 1, 'hook': 1, 'scenes': 'approve'},
                         log=lambda *a: print('  [e2e]', *a, file=sys.stderr) if os.getenv('E2E_VERBOSE') else None)

    def fail_with_logs(self, msg):
        self.fail(f'{msg}\n{self.stack.tails()}')

    def to_script_gate(self, topic):
        self.flow.create(topic)
        try:
            self.flow.storylines()
        except (AssertionError, TimeoutError) as exc:
            self.fail_with_logs(str(exc))
        return self.api.episode(self.flow.slug)

    def approve_script(self):
        r = self.api.post(f'/api/episodes/{self.flow.slug}/script/approve', json={'hook': 1})
        if r.status_code != 200:
            self.fail_with_logs(f'script approve refused: {r.status_code} {r.text}')


class FakeFlowTest(FakeModeCase):
    def test_topic_to_downloaded_mp4(self):
        api, flow, stack = self.api, self.flow, self.stack
        topic = 'Why your salary disappears by the 20th'
        health = api.health()
        self.assertIs(health['ok'], True)
        self.assertTrue(health['db'])
        self.assertEqual({'online', 'last_seen', 'busy'} - set(health['worker']), set())
        stack.worker()
        h.wait_for(lambda: api.health()['worker']['online'], 15, what='worker online after a heartbeat')

        for path in ('/', '/new'):
            r = api.get(path, headers={'User-Agent': h.PHONE_UA})
            self.assertEqual(r.status_code, 200, path)
            self.assertIn('viewport', r.text, f'{path} has no viewport meta (mobile-first)')
        anon = h.Api(stack.base)
        self.addCleanup(anon.close)
        self.assertEqual(anon.get('/login', headers={'User-Agent': h.PHONE_UA}).status_code, 200)

        flow.create(topic)
        try:
            ep = flow.wait('storylines')
            self.assertEqual(len(ep['storylines']), 3, ep['storylines'])
            # wrong-state actions are refused and change nothing (the contract's 409 is for script errors; a gate
            # acted on in the wrong state should be refused the same way)
            for path, body in (('script/approve', {'hook': 1}), ('scenes/approve', {}),
                               ('scenes/redo', {'key': 'x', 'note': 'y'})):
                r = api.post(f'/api/episodes/{flow.slug}/{path}', json=body)
                self.assertIn(r.status_code, (400, 409, 422), f'{path} at the storylines gate: {r.status_code}')
            self.assertEqual(api.episode(flow.slug)['state'], 'storylines')
            self.assertIn(api.post(f'/api/episodes/{flow.slug}/storyline', json={'story': 9}).status_code,
                          (400, 409, 422))

            ep = flow.storylines()
            self.assertEqual(ep['state'], 'script')
            self.assertIn(api.post(f'/api/episodes/{flow.slug}/script/approve', json={'hook': 9}).status_code,
                          (400, 409, 422))
            self.assertEqual(api.episode(flow.slug)['state'], 'script')
            self.assertIn(api.post(f'/api/episodes/{flow.slug}/storyline', json={'story': 2}).status_code,
                          (400, 409, 422), 'a storyline pick after the gate passed must be refused')

            flow.script()
            ep = flow.wait({'scenes', 'rendering'})
            self.assertEqual(ep['state'], 'scenes', 'fake worker paints two new scenes, so the scenes gate shows')
            paint = [j for j in h.read_journal(stack.journal) if j['kind'] == 'paint']
            self.assertTrue(paint and paint[0]['has_episode'] and paint[0]['has_ledger'],
                            'claim must carry episode.json and ledger.json')
            flow.scenes(redo_note='make the jar twice as big', journal=stack.journal)
            ep = flow.finish('done')
        except (AssertionError, TimeoutError) as exc:
            self.fail_with_logs(str(exc))

        self.assertIsNone(ep.get('error'))
        dl = stack.tmp / 'downloads'
        dl.mkdir()
        flow.download(dl)
        cost = flow.cost_and_history(topic)
        self.assertEqual(cost, 0, 'fake mode spends nothing')
        render = [j for j in h.read_journal(stack.journal) if j['kind'] == 'render']
        self.assertEqual([j['outcome'] for j in render], ['done'])

        for state in ('storylines', 'script', 'scenes', 'done'):
            self.assertIn(state, flow.html, f'never saw state {state}: {flow.html}')
        bad = {s: c for s, c in flow.html.items() if c != 200}
        self.assertFalse(bad, f'episode page not 200 in states {bad}')

        check_refusals(self, stack.base, flow.slug, stack.token)
        self.assertNotIn(stack.password, (stack.logs / 'web.log').read_text(errors='replace'))
        r = api.post('/logout')
        self.assertLess(r.status_code, 400, f'logout: {r.status_code}')
        self.assertIn(api.get('/api/episodes').status_code, h.REFUSED, 'still logged in after logout')


class FakeFailuresTest(FakeModeCase):
    def test_worker_offline_job_stays_queued(self):
        self.to_script_gate('Why the moon looks bigger at the horizon')
        self.approve_script()
        time.sleep(4)
        ep = self.api.episode(self.flow.slug)
        self.assertEqual(ep['state'], 'painting')
        self.assertEqual((ep['job']['kind'], ep['job']['status']), ('paint', 'queued'), ep['job'])
        self.assertIs(ep['worker']['online'], False)
        self.assertIs(self.api.health()['worker']['online'], False)
        self.assertEqual(self.api.get(f'/episodes/{self.flow.slug}', headers={'User-Agent': h.PHONE_UA}).status_code, 200)
        # the job waits, then runs when the Mac comes back
        self.stack.worker()
        self.flow.wait('scenes')

    def test_fail_render_ends_done_qa_failed(self):
        self.stack.worker('--fail-render')
        self.to_script_gate('Why cats knock things off tables')
        try:
            self.flow.script()
            self.flow.scenes()
            ep = self.flow.finish('done_qa_failed')
        except (AssertionError, TimeoutError) as exc:
            self.fail_with_logs(str(exc))
        dl = self.stack.tmp / 'downloads'
        dl.mkdir()
        self.flow.download(dl, expect_pass=False)
        self.assertEqual(self.api.get(f'/episodes/{self.flow.slug}', headers={'User-Agent': h.PHONE_UA}).status_code, 200)
        row = next(e for e in self.api.get('/api/episodes').json() if e['slug'] == self.flow.slug)
        self.assertEqual(row['state'], 'done_qa_failed')
        self.assertEqual(ep['state'], 'done_qa_failed')

    def test_failed_job_then_retry(self):
        self.to_script_gate('Why bread goes stale')
        crash = self.stack.worker('--fail-kind', 'paint', '--once', '--wait', '60')
        self.approve_script()
        try:
            ep = self.flow.wait('failed', bad=())
        except (AssertionError, TimeoutError) as exc:
            self.fail_with_logs(str(exc))
        self.assertIn('on purpose', ep.get('error') or '')
        self.assertEqual(self.api.get(f'/episodes/{self.flow.slug}', headers={'User-Agent': h.PHONE_UA}).status_code, 200)
        crash.wait(30)
        r = self.api.post(f'/api/episodes/{self.flow.slug}/retry', json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.stack.worker()
        try:
            self.flow.wait('scenes')
        except (AssertionError, TimeoutError) as exc:
            self.fail_with_logs(str(exc))


class LeaseTest(FakeModeCase):
    lease_seconds = 4

    def setUp(self):
        self.lease_name = lease_env_name(self.app_src)
        if not self.lease_name:
            self.skipTest('webapp/ has no short-lease override (the contract fixes 15 min); expected an env var '
                          f'such as {h.LEASE_ENV_NAMES[0]} to test lease expiry in seconds')
        super().setUp()

    def test_crashed_claim_is_requeued_after_the_lease(self):
        self.to_script_gate('Why onions make you cry')
        crasher = self.stack.worker('--crash-after-claim', name='crasher')
        self.approve_script()
        try:
            self.assertEqual(crasher.wait(60), 17, 'the crashing worker never claimed the job')
        except Exception:
            self.fail_with_logs('crash-after-claim worker did not claim')
        ep = self.api.episode(self.flow.slug)
        self.assertEqual((ep['state'], ep['job']['status']), ('painting', 'running'), ep['job'])
        time.sleep(self.lease_seconds + 2)
        self.stack.worker()
        try:
            self.flow.wait('scenes', timeout=60)
        except (AssertionError, TimeoutError) as exc:
            self.fail_with_logs(f'paint job not re-queued after the lease ({self.lease_name}): {exc}')
        outcomes = [(j['kind'], j['outcome']) for j in h.read_journal(self.stack.journal)]
        self.assertEqual(outcomes[:2], [('paint', 'crash'), ('paint', 'done')])
        jobs = h.read_journal(self.stack.journal)
        self.assertEqual(jobs[0]['job_id'], jobs[1]['job_id'], 'the same job must be re-claimed')


# ------------------------------------------------------------------------------------------------ real mode

def parse_picks(text):
    """`story=1,hook=1[,scenes=approve]` -> dict; anything else is refused (no invented approvals)."""
    out = {}
    for part in filter(None, (p.strip() for p in (text or '').split(','))):
        k, _, v = part.partition('=')
        k, v = k.strip(), v.strip()
        if k in ('story', 'hook') and v in ('1', '2', '3'):
            out[k] = int(v)
        elif k == 'scenes' and v == 'approve':
            out[k] = v
        else:
            raise ValueError(f'E2E_PICKS: cannot read {part!r} (use story=1-3, hook=1-3, scenes=approve)')
    return out


COST_WARNING = """
!!! E2E REAL MODE: this runs a real topic on {server} with the real Mac worker.
!!! It spends real money on Anthropic (research, storylines, script; the server stops and asks past
!!! SPEND_PREAPPROVED_USD) and keeps the Mac busy painting and rendering (up to ~60 min).
!!! Gates: {gates}
"""


@unittest.skipUnless(REAL, 'real mode: set E2E_REAL=1 (and E2E_REAL_CONFIRM=yes) only when the coordinator asks')
class RealModeTest(unittest.TestCase):
    def test_real_topic_to_mp4(self):
        need = [k for k in ('E2E_SERVER', 'E2E_USER', 'E2E_PASSWORD', 'E2E_TOPIC') if not os.getenv(k)]
        if need:
            self.fail(f'real mode needs {", ".join(need)}')
        server, topic = os.environ['E2E_SERVER'].rstrip('/'), os.environ['E2E_TOPIC']
        picks = parse_picks(os.getenv('E2E_PICKS'))
        gates = ', '.join(f'{g}: {picks[g]}' if g in picks else f'{g}: the human picks in the UI'
                          for g in ('story', 'hook', 'scenes'))
        print(COST_WARNING.format(server=server, gates=gates), file=sys.stderr, flush=True)
        if os.getenv('E2E_REAL_CONFIRM') != 'yes':
            self.skipTest('real mode refused: set E2E_REAL_CONFIRM=yes to accept the cost')
        gate_wait = float(os.getenv('E2E_GATE_TIMEOUT', '3600'))
        render_wait = float(os.getenv('E2E_RENDER_TIMEOUT', '3600'))
        api = h.Api(server, timeout=60)
        self.addCleanup(api.close)
        health = api.health()
        self.assertIs(health['ok'], True, health)
        if not health['worker']['online']:
            print(f'>>> the Mac worker is offline (last seen {health["worker"]["last_seen"]}); jobs will wait',
                  file=sys.stderr)
        r = api.login(os.environ['E2E_USER'], os.environ['E2E_PASSWORD'])
        self.assertEqual(r.status_code, 200, 'login refused')
        flow = Flow(self, api, step=300, gate_wait=gate_wait, render_wait=render_wait, picks=picks,
                    log=lambda *a: print('  [e2e real]', time.strftime('%H:%M:%S'), *a, file=sys.stderr, flush=True))
        flow.create(topic)
        flow.storylines()
        ep = api.episode(flow.slug)
        if ep['state'] == 'script':
            flow.script()
        ep = flow.scenes()
        ep = flow.finish('any')
        self.assertEqual(ep['state'], 'done', f'render ended {ep["state"]}: {ep.get("error")}')
        dl = Path(tempfile.mkdtemp(prefix='stick-e2e-real-'))
        flow.download(dl)
        cost = flow.cost_and_history(topic)
        print(f'\n>>> done: {server}/episodes/{flow.slug}  cost ${cost:.2f}  files in {dl}\n', file=sys.stderr)
        bad = {s: c for s, c in flow.html.items() if c != 200}
        self.assertFalse(bad, f'episode page not 200 in states {bad}')
        token = os.getenv('E2E_WORKER_TOKEN_PROBE', 'not-the-token')     # a bad token; never the real one
        check_refusals(self, server, flow.slug, token)


if __name__ == '__main__':
    unittest.main()
