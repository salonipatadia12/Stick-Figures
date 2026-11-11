"""pitch/script on the Mac (SCRIPT_PROVIDER=ollama): the handler, the bridge (story.pitch / script.generate mocked),
the Ollama check and the worker flow. No language model runs, nothing paid is sent, no network but a refused
localhost port."""
import contextlib
import dataclasses
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import httpx

from macworker import bridge, config, handlers, proc
from macworker.handlers import OLLAMA_DOWN, Ctx, JobError
from macworker.util import Backoff
from macworker.worker import Worker, dry_run

from .fakes import FakePopen, FakeServer, TmpDir, make_cfg

ROOT = Path(__file__).resolve().parents[2]
SLUG = 'why-honey-never-spoils'
OTHER = 'why-sunsets-are-red'
RESEARCH = 'research-0123456789abcdef.json'
STORY = 'story-fedcba9876543210.json'
REFUSED = 'http://127.0.0.1:9'                     # the discard port: nothing listens, the connection is refused


def llm_job(job_id=31, kind='pitch', cache=None, others=None):
    j = dict(job_id=job_id, kind=kind, slug=SLUG, payload={}, provider='ollama',
             episode=dict(slug=SLUG, topic='Why honey never spoils', storylines=[]),
             ledger=dict(budget_usd=2.0, entries=[]), cache=cache if cache is not None else {RESEARCH: '{"facts": []}'})
    if kind == 'pitch':
        j['others'] = others if others is not None else {OTHER: dict(slug=OTHER, topic='Sunsets', storyline_pick=1)}
    return j


def fake_bridge(test, notes=('research: 3 cited facts (cache)', 'storylines: 3 (new)'), error=None, rc=0, mark=True):
    """Stands in for `.venv/bin/python macworker/bridge.py <kind> <slug> <episodes dir>`: updates the episode, adds a
    cache file, prints BRIDGE_JSON."""
    def run(cmd, on_line=None):
        test.cmds.append([str(c) for c in cmd])
        kind, slug, edir = cmd[2], cmd[3], Path(cmd[4])
        test.seen = dict(files=sorted(str(p.relative_to(edir)) for p in edir.rglob('*') if p.is_file()))
        d = edir / slug
        if error is None and rc == 0:
            ep = json.loads((d / 'episode.json').read_text())
            ep['storylines' if kind == 'pitch' else 'beats'] = [dict(n=1)]
            (d / 'episode.json').write_text(json.dumps(ep))
            (d / 'ledger.json').write_text(json.dumps(dict(budget_usd=2.0, entries=[dict(id=1, provider='ollama')])))
            (d / 'cache' / STORY).write_text('{"pitch": {}}')
        if not mark:
            return rc, 'Traceback (most recent call last):\n  boom\nKeyError: x'
        ans = dict(error=error, type='ScriptError') if error else dict(notes=list(notes))
        return rc, 'ollama says hi\n' + bridge.MARK + json.dumps(ans)
    return run


class HandlerTest(TmpDir, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / 'repo'
        self.repo.mkdir()
        self.reports, self.cmds, self.checked = [], [], []
        self.workdir = self.tmp / 'state/jobs/31'
        self.workdir.mkdir(parents=True)

    def ctx(self, job, run, ollama=None):
        def ok():
            self.checked.append(1)
            return '0.12.0'
        return Ctx(repo=self.repo, job=job, workdir=self.workdir, report=lambda s, p, m='': self.reports.append((s, p, m)),
                   run=run, ollama=ollama or ok)

    def test_pitch_runs_the_bridge_in_a_scratch_episodes_folder_and_returns_the_episode(self):
        o = handlers.llm(self.ctx(llm_job(), fake_bridge(self)))
        edir = self.workdir / 'episodes'
        cmd = self.cmds[0]
        self.assertTrue(cmd[0].endswith('repo/.venv/bin/python'))
        self.assertTrue(cmd[1].endswith('macworker/bridge.py'))
        self.assertEqual(cmd[2:], ['pitch', SLUG, str(edir)])
        # the bridge saw the episode, its ledger and cache from the claim, and the server's other episodes as stubs
        self.assertEqual(self.seen['files'], sorted([f'{OTHER}/episode.json', f'{SLUG}/cache/{RESEARCH}',
                                                     f'{SLUG}/episode.json', f'{SLUG}/ledger.json']))
        self.assertEqual(o.result['episode']['storylines'], [dict(n=1)])
        self.assertEqual(o.result['ledger']['entries'], [dict(id=1, provider='ollama')])
        self.assertEqual(o.result['notes'], ['research: 3 cited facts (cache)', 'storylines: 3 (new)'])
        self.assertEqual((o.result['provider'], o.result['ollama_version']), ('ollama', '0.12.0'))
        # only the new cache file goes up (the server already has the research)
        self.assertEqual([(f['name'], f['kind']) for f in o.files], [(STORY, 'cache')])
        self.assertTrue(Path(o.files[0]['path']).is_file())
        self.assertEqual(self.checked, [1])
        self.assertEqual(self.reports[0][0], 'ollama')
        self.assertIn('Ollama 0.12.0 on the Mac', self.reports[1][2])
        self.assertFalse((self.repo / 'episodes').exists())          # the checkout's own episodes are not touched

    def test_script_has_no_rotation_stubs_and_a_changed_cache_file_goes_up(self):
        run = fake_bridge(self, notes=['script: 7 beats (new)'])

        def run2(cmd, on_line=None):
            out = run(cmd, on_line)
            (Path(cmd[4]) / SLUG / 'cache' / RESEARCH).write_text('{"facts": [1]}')   # regenerated
            return out
        o = handlers.llm(self.ctx(llm_job(kind='script'), run2))
        self.assertEqual(self.cmds[0][2], 'script')
        self.assertNotIn(f'{OTHER}/episode.json', self.seen['files'])
        self.assertEqual(o.result['episode']['beats'], [dict(n=1)])
        self.assertEqual(sorted(f['name'] for f in o.files), [RESEARCH, STORY])

    def test_ollama_down_fails_before_anything_runs(self):
        def down():
            raise JobError(f'{OLLAMA_DOWN} (no answer)')
        with self.assertRaises(JobError) as cm:
            handlers.llm(self.ctx(llm_job(), fake_bridge(self), ollama=down))
        self.assertTrue(str(cm.exception).startswith(OLLAMA_DOWN))
        self.assertEqual(self.cmds, [])

    def test_a_pipeline_error_is_a_clear_failure(self):
        with self.assertRaises(JobError) as cm:
            handlers.llm(self.ctx(llm_job(), fake_bridge(self, error='No fact came from a reputable source', rc=1)))
        self.assertEqual(str(cm.exception), 'Research/storylines failed: No fact came from a reputable source '
                                            '(press Retry to run it again)')
        with self.assertRaises(JobError) as cm:
            handlers.llm(self.ctx(llm_job(kind='script'), fake_bridge(self, rc=1, mark=False)))
        self.assertIn('Script failed on the Mac (exit 1)', str(cm.exception))
        self.assertIn('KeyError: x', str(cm.exception))

    def test_bad_claims_are_refused(self):
        with self.assertRaises(JobError):
            handlers.llm(self.ctx(dict(llm_job(), episode={}), fake_bridge(self)))
        with self.assertRaises(ValueError):
            handlers.llm(self.ctx(dict(llm_job(), slug='../x'), fake_bridge(self)))
        # unsafe cache names and other slugs are skipped
        job = llm_job(cache={'../evil.json': '{}', '.hidden': '{}', RESEARCH: '{}'}, others={'../up': {}, SLUG: {}})
        handlers.llm(self.ctx(job, fake_bridge(self)))
        self.assertEqual(self.seen['files'], [f'{SLUG}/cache/{RESEARCH}', f'{SLUG}/episode.json', f'{SLUG}/ledger.json'])

    def test_handlers_cover_every_claimed_kind(self):
        self.assertEqual(set(handlers.HANDLERS), set(handlers.CLAIM_KINDS))
        self.assertIs(handlers.HANDLERS['pitch'], handlers.llm)
        self.assertIs(handlers.HANDLERS['script'], handlers.llm)


class OllamaCheckTest(unittest.TestCase):
    def test_version_or_the_brew_hint(self):
        ok = httpx.MockTransport(lambda r: httpx.Response(200, json=dict(version='0.12.0')) if r.url.path == '/api/version'
                                 else httpx.Response(404))
        self.assertEqual(handlers.ollama_version('http://localhost:11434', transport=ok), '0.12.0')
        for bad in (httpx.MockTransport(lambda r: httpx.Response(500)),
                    httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError('refused', request=r)))):
            with self.assertRaises(JobError) as cm:
                handlers.ollama_version('http://localhost:11434', transport=bad)
            self.assertTrue(str(cm.exception).startswith('Ollama is not running on the Mac: brew services start ollama'))
        with self.assertRaises(JobError):
            handlers.ollama_version(REFUSED, timeout=2)                # a real refused connection

    def test_ollama_host_forms(self):
        self.assertEqual(config.ollama_url(''), 'http://localhost:11434')
        self.assertEqual(config.ollama_url('127.0.0.1:11434'), 'http://127.0.0.1:11434')
        self.assertEqual(config.ollama_url('0.0.0.0'), 'http://localhost:11434')
        self.assertEqual(config.ollama_url('http://studio.local:8080/'), 'http://studio.local:8080')
        cfg = config.load(env=dict(OLLAMA_HOST='0.0.0.0:11500', STICK_WORKER_CONFIG='/nonexistent'), token_lookup=lambda: None)
        self.assertEqual(cfg.ollama_url, 'http://localhost:11500')


class BridgeTest(TmpDir, unittest.TestCase):
    """bridge.llm in-process with story.pitch / script.generate mocked: the env it pins, the free-only ledger."""

    def setUp(self):
        super().setUp()
        from pipeline import config as pconfig
        from pipeline.models import Episode
        self.edir = self.tmp / 'episodes'
        (self.edir / SLUG).mkdir(parents=True)
        (self.edir / SLUG / 'episode.json').write_text(Episode(slug=SLUG, topic='Why honey never spoils')
                                                       .model_dump_json())
        self.patches = [mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-ant-should-never-be-used'}),
                        mock.patch.object(pconfig, 'EPISODES_DIR', pconfig.EPISODES_DIR)]
        if hasattr(pconfig, 'SCRIPT_PROVIDER'):
            self.patches.append(mock.patch.object(pconfig, 'SCRIPT_PROVIDER', pconfig.SCRIPT_PROVIDER))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def test_pitch_runs_free_only_and_saves_the_episode(self):
        from pipeline import story
        seen = {}

        def pitch(episode, base, ledger, client=None, regenerate=False):
            from pipeline import config as pconfig
            seen.update(client=client, base=base, env=(os.environ['SCRIPT_PROVIDER'], os.environ['ANTHROPIC_API_KEY'],
                                                       os.environ['STICK_EPISODES_DIR']), dir=pconfig.EPISODES_DIR)
            ledger.reserve('research', 'ollama', 'qwen2.5:14b', 0)     # a free reservation passes
            with self.assertRaises(bridge.PaidCallRefused):
                ledger.reserve('storyline', 'anthropic', 'claude', 0.05)
            return episode.model_copy(update=dict(title='Pitched')), ['storylines: 3 (new)']

        with mock.patch.object(story, 'pitch', pitch):
            ans = bridge.llm('pitch', SLUG, self.edir, ROOT)
        self.assertEqual(ans, dict(notes=['storylines: 3 (new)']))
        self.assertIsNone(seen['client'])
        self.assertEqual(Path(seen['base']), self.edir / SLUG)
        self.assertEqual(seen['env'], ('ollama', '', str(self.edir)))
        self.assertEqual(seen['dir'], self.edir)
        self.assertEqual(json.loads((self.edir / SLUG / 'episode.json').read_text())['title'], 'Pitched')
        entries = json.loads((self.edir / SLUG / 'ledger.json').read_text())['entries']
        self.assertEqual([(e['step'], e['provider']) for e in entries], [('research', 'ollama')])

    def test_script_and_errors(self):
        from pipeline import script
        from pipeline.script import ScriptError
        calls = []

        def generate(episode, style, base, ledger, client=None, use_research=True, regenerate=(), facts_file=None):
            calls.append((style.name if hasattr(style, 'name') else style, client))
            raise ScriptError('No storyline picked for why-honey-never-spoils')

        with mock.patch.object(script, 'generate', generate), contextlib.redirect_stderr(io.StringIO()):
            ans = bridge.llm('script', SLUG, self.edir, ROOT)
        self.assertEqual(ans['type'], 'ScriptError')
        self.assertIn('No storyline picked', ans['error'])
        self.assertIsNone(calls[0][1])

        def boom(*a, **kw):
            raise KeyError('beats')
        with mock.patch.object(script, 'generate', boom), contextlib.redirect_stderr(io.StringIO()):
            ans = bridge.llm('script', SLUG, self.edir, ROOT)
        self.assertEqual(ans['error'], "KeyError: 'beats'")


class WorkerFlowTest(TmpDir, unittest.TestCase):
    def setUp(self):
        super().setUp()
        FakePopen.instances = []
        self.cfg = make_cfg(self.tmp)
        self.cmds = []

    def worker(self, server, runner, handlers_=None):
        return Worker(self.cfg, server.client(), handlers=handlers_, sleep=lambda s: None,
                      awake=lambda: proc.Awake(popen=FakePopen), runner=runner, backoff=Backoff(base=1, rng=lambda: 0.5))

    def test_pitch_job_uploads_cache_then_done_with_the_episode(self):
        server = FakeServer([llm_job(40)])
        bridge_run = fake_bridge(self)
        ok = lambda ctx: handlers.llm(dataclasses.replace(ctx, ollama=lambda: '0.12.0'))
        rc = self.worker(server, lambda cmd, cwd, env, on_line, log_path, timeout: bridge_run(cmd, on_line),
                         dict(pitch=ok)).run(once=True)
        self.assertEqual(rc, 0)
        self.assertEqual([(n, k) for _, n, k, _ in server.files], [(STORY, 'cache')])
        self.assertEqual(server.done['40']['episode']['storylines'], [dict(n=1)])
        paths = [p for p in server.paths() if '/jobs/40/' in p]
        self.assertLess(paths.index('/api/worker/jobs/40/files'), paths.index('/api/worker/jobs/40/done'))
        self.assertTrue(FakePopen.instances)                          # caffeinate while it ran

    def test_ollama_down_reports_the_failure(self):
        self.cfg.ollama_url = REFUSED                                  # the real check, against a refused port
        server = FakeServer([llm_job(41, kind='script')])
        rc = self.worker(server, lambda *a, **kw: self.fail('nothing may run')).run(once=True)
        self.assertEqual(rc, 0)
        self.assertTrue(server.failed['41'].startswith('Ollama is not running on the Mac: brew services start ollama'))
        self.assertNotIn('41', server.done)

    def test_dry_run_checks_ollama_when_the_server_hands_out_pitch_and_script(self):
        def down():
            raise JobError(f'{OLLAMA_DOWN} (no answer)')
        for kinds, problem in ((['pitch', 'script', 'paint', 'render'], True), (['paint', 'render'], False)):
            server = FakeServer([])
            server.kinds = kinds
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                dry_run(self.cfg, client_factory=lambda s, t: server.client(), ollama_check=down)
            out = buf.getvalue()
            self.assertIn('NOT RUNNING', out)
            self.assertEqual(f'problem: {OLLAMA_DOWN}' in out, problem, out)
            self.assertIn(f'kinds {", ".join(kinds)}; nothing was claimed', out)
        buf = io.StringIO()
        server = FakeServer([])
        with contextlib.redirect_stdout(buf):
            dry_run(self.cfg, client_factory=lambda s, t: server.client(), ollama_check=lambda: '0.12.0')
        self.assertIn('ok (version 0.12.0)', buf.getvalue())
        self.assertIn('kinds paint, render; nothing was claimed', buf.getvalue())   # an older server: no kinds


if __name__ == '__main__':
    unittest.main()
