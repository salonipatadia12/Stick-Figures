"""SCRIPT_PROVIDER=claude-code (user 2026-11-17): the script step through headless Claude Code on the subscription
login, research and everything else local; a missing login or a usage limit falls back to the 9B pipeline and says
so. No Claude Code process runs here: subprocess and the CLI are stood in for."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pipeline import claude_code, config, llm

SCHEMA = dict(type='object', required=['ok'], properties=dict(ok=dict(type='boolean')))


def answer(**kw):
    data = dict(type='result', subtype='success', is_error=False, duration_api_ms=900, result='',
                total_cost_usd=0.01, usage=dict(input_tokens=10, output_tokens=5), modelUsage={'claude-sonnet-5-5': {}})
    data.update(kw)
    return SimpleNamespace(stdout=json.dumps(data), stderr='', returncode=0)


class Cache(dict):
    def get_llm(self, k):
        return self.get(k)

    def put_llm(self, k, v):
        self[k] = v


class Ledger:
    def __init__(self):
        self.free = []

    def record_free(self, step, provider, model, detail='', usage=None):
        self.free.append((step, provider, model, usage['seconds']))


class Settings(unittest.TestCase):
    def test_claude_code_keeps_the_local_path_but_changes_the_writer(self):
        with mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'claude-code'}):
            self.assertEqual(config.script_provider(), 'ollama')      # research, facts, the checks' gates: local
            self.assertEqual(config.script_writer(), 'claude-code')
            self.assertEqual(config.claude_code_model(), 'claude-sonnet-5-5')

    def test_the_default_is_unchanged(self):
        with mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': ''}):
            self.assertEqual(config.script_provider(), 'ollama')
            self.assertEqual(config.script_writer(), 'ollama')


class Cli(unittest.TestCase):
    def test_the_cli_never_sees_an_api_key_or_the_session_it_runs_in(self):
        got = claude_code.env({'ANTHROPIC_API_KEY': 'sk-x', 'ANTHROPIC_AUTH_TOKEN': 't', 'CLAUDECODE': '1',
                               'CLAUDE_CODE_SESSION_ID': 's', 'HOME': '/Users/x', 'PATH': '/usr/bin', 'LANG': 'C'})
        self.assertNotIn('ANTHROPIC_API_KEY', got)
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', got)
        self.assertNotIn('CLAUDECODE', got)
        self.assertNotIn('CLAUDE_CODE_SESSION_ID', got)
        self.assertEqual((got['HOME'], got['LANG']), ('/Users/x', 'C'))

    def test_no_tools_no_mcp_no_settings_no_saved_session(self):
        cmd = claude_code.command('SYSTEM', SCHEMA, 'claude-sonnet-5-5')
        self.assertEqual(cmd[1:3], ['-p', '--output-format'])
        for flag in ('--strict-mcp-config', '--no-session-persistence', '--disable-slash-commands'):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index('--tools') + 1], '')
        self.assertEqual(cmd[cmd.index('--setting-sources') + 1], '')
        self.assertEqual(cmd[cmd.index('--system-prompt') + 1], 'SYSTEM')
        self.assertEqual(json.loads(cmd[cmd.index('--json-schema') + 1]), SCHEMA)
        self.assertNotIn('--json-schema', claude_code.command('SYSTEM', None))

    def test_the_structured_answer_and_its_time(self):
        seen = {}

        def runner(cmd, user, timeout):
            seen.update(user=user)
            return answer(structured_output=dict(ok=True))
        text, usage = claude_code.ask('S', 'the prompt', SCHEMA, runner=runner)
        self.assertEqual(json.loads(text), dict(ok=True))
        self.assertEqual(seen['user'], 'the prompt')                  # the prompt goes on stdin, not argv
        self.assertEqual((usage['input'], usage['output']), (10, 5))
        self.assertIn('seconds', usage)

    def test_a_usage_limit_or_no_login_is_unavailable(self):
        for msg in ('Claude AI usage limit reached|1763380800', 'Not logged in · Please run /login',
                    "You've hit your limit · resets 3pm"):
            with self.assertRaises(claude_code.Unavailable, msg=msg):
                claude_code.ask('S', 'u', None, runner=lambda c, u, t, m=msg: answer(is_error=True, result=m))
        with self.assertRaises(claude_code.Unavailable):
            claude_code.ask('S', 'u', None, runner=lambda c, u, t: answer(is_error=True, result='x', api_error_status=429))

    def test_any_other_failure_is_an_ordinary_failed_call(self):
        with self.assertRaises(llm.LLMError) as e:
            claude_code.ask('S', 'u', None, runner=lambda c, u, t: answer(is_error=True, result='Internal server error'))
        self.assertNotIsInstance(e.exception, claude_code.Unavailable)

        def slow(c, u, t):
            raise subprocess.TimeoutExpired(c, t)
        with self.assertRaises(llm.LLMError):
            claude_code.ask('S', 'u', None, runner=slow)

    def test_no_cli_is_unavailable(self):
        def missing(c, u, t):
            raise FileNotFoundError(c[0])
        with self.assertRaises(claude_code.Unavailable):
            claude_code.ask('S', 'u', None, runner=missing)

    def test_preflight_wants_a_subscription_login(self):
        ok = lambda: SimpleNamespace(stdout=json.dumps(dict(loggedIn=True, authMethod='claude.ai')))
        self.assertEqual(claude_code.preflight(ok), '')
        out = lambda: SimpleNamespace(stdout=json.dumps(dict(loggedIn=False)))
        self.assertIn('not logged in', claude_code.preflight(out))
        key = lambda: SimpleNamespace(stdout=json.dumps(dict(loggedIn=True, authMethod='api_key')))
        self.assertIn('not a claude.ai subscription', claude_code.preflight(key))


class Routing(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def ask(system, user, json_schema=None, **kw):
            self.calls.append(user)
            return json.dumps(dict(ok=True)), dict(seconds=1.5, input=3, output=2, api_equivalent_usd=0.001)
        self.patch = mock.patch.object(claude_code, 'ask', ask)
        self.patch.start()
        self.no_ollama = mock.patch.object(llm, '_requests', side_effect=AssertionError('Ollama was called'))
        self.no_ollama.start()

    def tearDown(self):
        self.no_ollama.stop()
        self.patch.stop()

    def test_inside_the_route_calls_go_to_claude_code_cached_and_timed(self):
        cache, ledger = Cache(), Ledger()
        with llm.claude_route('claude-sonnet-5-5') as r:
            self.assertEqual(llm.generate_json('S', 'one', SCHEMA, 100, cache=cache, ledger=ledger, step='script'), dict(ok=True))
            llm.generate_json('S', 'one', SCHEMA, 100, cache=cache, ledger=ledger, step='script')
        self.assertEqual(self.calls, ['one'])                          # the second came from the cache
        self.assertEqual(ledger.free, [('script', 'claude-code', 'claude-sonnet-5-5', 1.5)])
        s = r.summary()
        self.assertEqual((s['calls'], s['cached'], s['seconds'], s['by_step']), (2, 1, 1.5, {'script': 2}))
        self.assertIsNone(llm.active_route())

    def test_outside_the_route_nothing_goes_to_claude_code(self):
        with mock.patch.object(config, 'ollama_yield', return_value=False):
            with self.assertRaises(llm.LLMError) as e:              # it went to Ollama (stood in by the failing patch)
                llm.generate('S', 'two', None, 10)
        self.assertIn('Ollama is not reachable', str(e.exception))
        self.assertEqual(self.calls, [])

    def test_the_route_reaches_parallel_calls(self):
        with llm.claude_route():
            got = llm.run_parallel(lambda k: llm.generate_json('S', f'p{k}', SCHEMA, 10), [1, 2, 3, 4])
            batch = llm.generate_batch([dict(system='S', user='b1', json_schema=SCHEMA), dict(system='S', user='b2',
                                                                                              json_schema=SCHEMA)],
                                       as_json=True)
        self.assertEqual(got, [dict(ok=True)] * 4)
        self.assertEqual(batch, [dict(ok=True)] * 2)
        self.assertEqual(sorted(self.calls), ['b1', 'b2', 'p1', 'p2', 'p3', 'p4'])

    def test_a_usage_limit_takes_the_route_down_for_the_rest_of_the_step(self):
        def limited(*a, **kw):
            self.calls.append('limited')
            raise claude_code.Unavailable('Claude AI usage limit reached')
        with mock.patch.object(claude_code, 'ask', limited), llm.claude_route() as r:
            with self.assertRaises(llm.ClaudeCodeDown):
                llm.generate('S', 'a', None, 10)
            with self.assertRaises(llm.ClaudeCodeDown):
                llm.generate('S', 'b', None, 10)
        self.assertEqual(self.calls, ['limited'])                      # the second call never reached the CLI
        self.assertIn('usage limit', r.down)


class Fallback(unittest.TestCase):
    """script._script_claude_code: Claude Code writes the script step; when it cannot, the 9B pipeline does."""

    def episode(self):
        from pipeline.models import Episode
        return Episode(slug='why-x', topic='Why x', target_length=45)

    def fake_script(self, seen, use_llm=False):
        def _script(episode, style, base, ledger, client, prov, use_research, regenerate, facts, notes):
            seen.append(dict(route=llm.active_route(), notes=list(notes), target=episode.target_length))
            if use_llm and llm.active_route() is not None:
                llm.generate('S', 'u', None, 10)
            episode.target_length = 30                         # the plan sizes the episode in place
            out = episode.model_copy(update=dict(meta=dict(episode.meta, script=dict(model='qwen3.5:9b'))))
            return out, list(notes) + ['script: 10 beats']
        return _script

    def run_it(self, seen, preflight='', ask=None, use_llm=False):
        from pipeline import script
        ask = ask or (lambda *a, **kw: ('x', dict(seconds=2.0, input=1, output=1)))
        with mock.patch.object(script, '_script', self.fake_script(seen, use_llm)), \
                mock.patch.object(script, 'core_by_claude_code', return_value=['core answer: from the facts']), \
                mock.patch.object(claude_code, 'preflight', return_value=preflight), \
                mock.patch.object(claude_code, 'ask', ask):
            return script._script_claude_code(self.episode(), None, None, None, None, 'ollama', True, (), [], ['research'])

    def test_claude_code_writes_and_the_episode_says_so(self):
        seen = []
        out, notes = self.run_it(seen, use_llm=True)
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0]['route'])
        self.assertIn('core answer: from the facts', seen[0]['notes'])
        cc = out.meta['script']['claude_code']
        self.assertEqual((cc['calls'], cc['down'], out.meta['script']['provider']), (1, '', 'claude-code'))
        self.assertTrue(notes[0].startswith('script written by Claude Code (claude-sonnet-5-5): 1 call(s)'))

    def test_not_logged_in_the_9b_writes_and_says_so(self):
        seen = []
        out, notes = self.run_it(seen, preflight='Claude Code is not logged in')
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0]['route'])                    # the local pipeline, no Claude Code call
        self.assertIn('Claude Code was not available (Claude Code is not logged in)', notes[0])
        self.assertTrue(out.meta['script']['claude_code']['fallback'])

    def test_a_usage_limit_midway_starts_the_9b_from_the_start(self):
        seen = []

        def limited(*a, **kw):
            raise claude_code.Unavailable('Claude AI usage limit reached')
        out, notes = self.run_it(seen, ask=limited, use_llm=True)
        self.assertEqual([s['route'] is None for s in seen], [False, True])
        self.assertEqual([s['target'] for s in seen], [45, 45])   # the second run starts from the episode as it came
        self.assertIn('usage limit', out.meta['script']['claude_code']['down'])
        self.assertIn('the local 9B pipeline wrote this script', notes[0])

    def test_a_failure_that_is_not_the_subscription_is_raised(self):
        from pipeline import script

        def broken(*a, **kw):
            raise ValueError('a bug')
        with mock.patch.object(script, '_script', broken), \
                mock.patch.object(script, 'core_by_claude_code', return_value=[]), \
                mock.patch.object(claude_code, 'preflight', return_value=''):
            with self.assertRaises(ValueError):
                script._script_claude_code(self.episode(), None, None, None, None, 'ollama', True, (), [], [])


class WorkerSwitch(unittest.TestCase):
    def test_the_mac_worker_keeps_ollama_unless_told_claude_code(self):
        from macworker import bridge
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'config'
            self.assertEqual(bridge.script_writer({}, f), 'ollama')
            f.write_text('STICK_SERVER=https://x\nSCRIPT_PROVIDER=claude-code\n')
            self.assertEqual(bridge.script_writer({}, f), 'claude-code')
            self.assertEqual(bridge.script_writer({'SCRIPT_PROVIDER': 'ollama'}, f), 'ollama')   # the environment first
            self.assertEqual(bridge.script_writer({'SCRIPT_PROVIDER': 'anthropic'}, f), 'ollama')   # never paid
            env = {'SCRIPT_PROVIDER': 'claude-code', 'ANTHROPIC_API_KEY': 'sk-x'}
            bridge.free_only_env(Path(d), env)
            self.assertEqual((env['SCRIPT_PROVIDER'], env['ANTHROPIC_API_KEY']), ('claude-code', ''))


if __name__ == '__main__':
    unittest.main()
