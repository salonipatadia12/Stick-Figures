import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import cli, config, script
from pipeline_fixtures import FakeClient, anthropic_provider, research_response, script_response, story_response

TOPIC = 'Why your salary disappears by the 20th'
SLUG = 'why-your-salary-disappears-by-the-20th'


class CliFlow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(config, 'EPISODES_DIR', Path(self.dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)
        anthropic_provider(self)                 # these tests drive the paid path with a fake client
        self.client = FakeClient([research_response(), story_response(), script_response()])
        p = mock.patch.object(script, 'make_client', lambda: self.client)
        p.start()
        self.addCleanup(p.stop)
        self.renders = []                        # --auto paints and renders after the script: never for real in a test
        p = mock.patch.object(cli, 'auto_render', lambda slug: self.renders.append(slug) or 0)
        p.start()
        self.addCleanup(p.stop)

    def to_script(self, *extra):
        """Story before script: make pitches three storylines and waits; pick one; make writes the script. With --auto
        one make does it all (user 2026-11-13): the storyline and the script are approved by "auto", then it renders."""
        if '--auto' in extra:
            code, out = self.run_cli('make', TOPIC, *extra)
            self.assertEqual(code, 0, out)
            self.assertIn('--auto: picked storyline 1', out)
            self.assertEqual(self.renders, [SLUG])
            return code, out
        code, out = self.run_cli('make', TOPIC, *extra)
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertIn('WAITING AT GATE: storyline', out)
        self.assertEqual(self.run_cli('approve', SLUG, '--gate', 'storyline', '--story', '1', '--by', 'test')[0], 0)
        return self.run_cli('make', '--episode', SLUG, *[e for e in extra if e == '--auto'])

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_script_gate_waits_then_continues_after_approval(self):
        code, out = self.to_script('--niche', 'personal finance')
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertIn('WAITING AT GATE: script', out)
        self.assertIn('b01  HOOK', out)
        self.assertIn('stick approve why-your-salary-disappears-by-the-20th', out)
        slug = 'why-your-salary-disappears-by-the-20th'
        self.assertEqual(self.run_cli('approve', slug)[0], 0)
        code, out = self.run_cli('make', '--episode', slug)
        self.assertEqual(code, 0)
        self.assertIn('Script approved', out)
        self.assertEqual(len(self.client.messages.created), 3)  # research, storyline, script; resuming sends nothing new

    def test_editing_the_script_invalidates_approval(self):
        self.to_script('--auto')
        path = Path(self.dir.name) / 'why-your-salary-disappears-by-the-20th' / 'episode.json'
        data = json.loads(path.read_text())
        self.assertIn('script', data['approvals'])
        data['beats'][2]['narration'] = "Small buys don't feel like spending. A coffee here, a taxi there."
        data['beats'][2]['sfx'] = [{'name': 'pop', 'anchor': 'coffee'}]
        path.write_text(json.dumps(data))
        code, out = self.run_cli('make', '--episode', 'why-your-salary-disappears-by-the-20th')
        self.assertEqual(code, cli.EXIT_GATE)

    def test_approve_refuses_a_script_that_fails_checks(self):
        self.to_script()
        path = Path(self.dir.name) / 'why-your-salary-disappears-by-the-20th' / 'episode.json'
        data = json.loads(path.read_text())
        data['beats'][0]['on_screen_text'] = 'one two three four five six'
        path.write_text(json.dumps(data))
        code, out = self.run_cli('approve', 'why-your-salary-disappears-by-the-20th')
        self.assertEqual(code, cli.EXIT_CHECKS)
        self.assertIn('6 words', out)
        self.assertEqual(self.run_cli('validate', 'why-your-salary-disappears-by-the-20th')[0], cli.EXIT_CHECKS)

    def test_budget_cap_exit_code(self):
        code, out = self.run_cli('make', TOPIC, '--budget', '0.05')
        self.assertEqual(code, cli.EXIT_BUDGET)
        self.assertIn('Nothing was sent', out)
        self.assertEqual(self.client.messages.created, [])

    def test_dry_run_without_key_sends_nothing(self):
        with mock.patch.object(config, 'has_anthropic_key', lambda: False):
            code, out = self.run_cli('make', TOPIC, '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('estimated offline', out)
        self.assertEqual(self.client.messages.created, [])

    def test_brief_updates_before_a_script_and_is_guarded_after(self):
        slug = 'why-your-salary-disappears-by-the-20th'
        with mock.patch.object(config, 'has_anthropic_key', lambda: False):
            self.run_cli('make', TOPIC, '--dry-run')
            code, out = self.run_cli('make', TOPIC, '--audience', 'US salaried workers', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('brief: audience = "US salaried workers"', out)
        self.to_script()  # writes the script for that audience
        sent = self.client.messages.created[0]['messages'][0]['content']
        self.assertIn('Audience: US salaried workers', sent)
        code, out = self.run_cli('make', TOPIC, '--audience', 'UK students')
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn('--regenerate script', out)
        self.assertEqual(len(self.client.messages.created), 3)  # refused before any new paid call

    def test_cost_and_list(self):
        self.to_script('--auto')
        code, out = self.run_cli('cost', 'why-your-salary-disappears-by-the-20th')
        self.assertIn('research', out)
        self.assertIn('script', out)
        code, out = self.run_cli('list')
        self.assertIn('script approved', out)


if __name__ == '__main__':
    unittest.main()
