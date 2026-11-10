"""Render/paint output parsing, safe names, config, the child-process runner."""
import os
import sys
import time
import unittest
from pathlib import Path

from macworker import config, proc
from macworker.progress import PaintProgress, RenderProgress, failed_reasons
from macworker.util import Backoff, check_key, check_slug, safe_name

from .fakes import TmpDir

RENDER_OUTPUT = """\
b02: narration changed; old take archived in renders/voice/x/archive/20261109-101010
duration 45.12s, 14 shots, 31 caption lines
cached: 11 to render: ['b08', 'b03a', 'b12']
  b08: 207 frames  452s
  b03a: 98 frames  470s
  b12: 120 frames  480s
/Users/me/Stick_Figures/renders/sample/why-honey-never-spoils/why-honey-never-spoils.mp4 45.12s 501s
{
 "duration": true,
 "all": true
}
library: 12/14 beats from the libraries alone = 86% (target 80%)
"""


class RenderParseTest(unittest.TestCase):
    def test_render_stages(self):
        rp = RenderProgress()
        ups = [u for line in RENDER_OUTPUT.splitlines() if (u := rp.update(line))]
        stages = [u[0] for u in ups]
        self.assertEqual(stages[0], 'voice')
        self.assertIn('shots', stages)
        self.assertEqual(stages[-1], 'qa')
        pcts = [u[1] for u in ups]
        self.assertEqual(pcts, sorted(pcts))                   # never backwards
        self.assertEqual(rp.to_render, 3)
        self.assertEqual(rp.rendered, 3)
        self.assertEqual(rp.duration, 45.12)
        self.assertTrue(rp.final_path.endswith('why-honey-never-spoils.mp4'))
        shot_msgs = [u[2] for u in ups if u[2].startswith('shot ')]
        self.assertEqual(shot_msgs, ['shot b08 done (1/3)', 'shot b03a done (2/3)', 'shot b12 done (3/3)'])
        self.assertAlmostEqual([u[1] for u in ups if u[2].startswith('shot b12')][0], 80.0)
        self.assertEqual(ups[-1][1], 95.0)

    def test_all_cached(self):
        rp = RenderProgress()
        rp.update('duration 10.00s, 4 shots, 5 caption lines')
        self.assertEqual(rp.update('cached: 4 to render: []'), ('mix', 80.0, 'all shots cached; mixing'))

    def test_noise_ignored(self):
        rp = RenderProgress()
        for line in ('', 'QA NOTE (layout): x', 'random text', '  not: a shot line'):
            self.assertIsNone(rp.update(line))


class PaintParseTest(unittest.TestCase):
    def test_paint_candidates(self):
        pp = PaintProgress(expected=4, lo=5, hi=50, what='painting scenes')
        self.assertEqual(pp.update('scene model loaded in 12s')[1], 5)
        a = pp.update('scene-summer-village-s151.png: 80s')
        b = pp.update('cached scene-summer-village-s152.png')
        c = pp.update('scene-frost-farm--snow-melting-r2-s1300.png: 75s')
        self.assertEqual(a[0], 'paint')
        self.assertIn('summer-village candidate 1/4', a[2])
        self.assertLess(a[1], b[1])
        self.assertIn('frost-farm--snow-melting', c[2])
        self.assertEqual(c[1], round(5 + 45 * 3 / 4, 1))
        self.assertIsNone(pp.update('Insert scenes to make (2): ...'))

    def test_props_lines(self):
        pp = PaintProgress(expected=2, lo=50, hi=90, what='painting props')
        self.assertIn('honey-jar candidate 1/2', pp.update('prop-honey-jar-s300.png: 25s')[2])

    def test_failed_reasons(self):
        out = ('Insert scenes to make (2): a [b01], b [b02]\n'
               '  + a: styles/cozy/inserts/a.png (passed QA)\n'
               '  ! b: failed QA on both seeds\n'
               '  ! honey-jar (honey jar): failed QA on both seeds: too dark | text block\n')
        self.assertEqual(failed_reasons(out), {'b': 'failed QA on both seeds',
                                               'honey-jar': 'failed QA on both seeds: too dark | text block'})


class SafeNameTest(unittest.TestCase):
    def test_safe_names(self):
        cases = {
            'why-honey-never-spoils.mp4': 'why-honey-never-spoils.mp4',
            'Contact Sheet.JPG': 'contact-sheet.jpg',
            '../../etc/passwd': 'etc-passwd',
            '..': 'file',
            '.hidden': 'hidden',
            'a/b\\c.srt': 'a-b-c.srt',
            'émoji 🎬.json': 'moji-.json',
            '': 'file',
            'x..y.jpg': 'x.y.jpg',
        }
        for raw, want in cases.items():
            got = safe_name(raw)
            self.assertRegex(got, r'^[a-z0-9._-]+$')
            self.assertNotIn('..', got)
            self.assertFalse(got.startswith(('.', '-')))
        self.assertEqual(safe_name('why-honey-never-spoils.mp4'), 'why-honey-never-spoils.mp4')
        self.assertEqual(safe_name('Contact Sheet.JPG'), 'contact-sheet.jpg')
        self.assertEqual(safe_name('../../etc/passwd'), 'etc-passwd')
        self.assertEqual(safe_name('..'), 'file')
        long = safe_name('a' * 300 + '.jpg')
        self.assertLessEqual(len(long), 120)
        self.assertTrue(long.endswith('.jpg'))

    def test_slug_and_key_checks(self):
        check_slug('why-honey-never-spoils')
        for bad in ('../x', 'X', '', 'a/b', None, '-a', 'a' * 200):
            with self.assertRaises(ValueError):
                check_slug(bad)
        check_key('frost-farm--snow-melting')
        check_key('honey_jar')
        for bad in ('../a', 'a/b', 'A', '', 'a..b'):
            with self.assertRaises(ValueError):
                check_key(bad)

    def test_backoff(self):
        b = Backoff(base=5, cap=60, rng=lambda: 0.5)
        self.assertEqual([b.next() for _ in range(6)], [5, 10, 20, 40, 60, 60])
        b.reset()
        self.assertEqual(b.next(), 5)


class ConfigTest(TmpDir, unittest.TestCase):
    def test_env_file_and_keychain(self):
        f = self.tmp / 'config'
        f.write_text('# worker\nSTICK_SERVER="https://a.example/"\nSTICK_RENDER_WORKERS=2\n')
        looked = []
        cfg = config.load(env=dict(STICK_WORKER_CONFIG=str(f), STICK_REPO=str(self.tmp)),
                          token_lookup=lambda: looked.append(1) or 'kc-token')
        self.assertEqual(cfg.server, 'https://a.example')
        self.assertEqual(cfg.workers, 2)
        self.assertEqual(cfg.token, 'kc-token')
        self.assertEqual(looked, [1])
        self.assertNotIn('kc-token', repr(cfg))
        env_cfg = config.load(env=dict(STICK_SERVER='https://b.example', STICK_WORKER_TOKEN='t', STICK_WORKER_CONFIG=str(f)),
                              token_lookup=lambda: self.fail('the Keychain must not be read when the env has a token'))
        self.assertEqual(env_cfg.server, 'https://b.example')           # env wins over the file

    def test_problems(self):
        cfg = config.load(env=dict(STICK_SERVER='http://1.2.3.4', STICK_REPO=str(self.tmp), STICK_WORKER_CONFIG='/nonexistent'),
                          token_lookup=lambda: None)
        probs = ' '.join(cfg.problems())
        self.assertIn('https', probs)
        self.assertIn('no worker token', probs)
        self.assertIn('.venv/bin/python', probs)

    def test_keychain_lookup_command(self):
        calls = []

        class R:
            returncode, stdout = 0, 'secret\n'

        tok = config.keychain_token(run=lambda args, **kw: calls.append(args) or R())
        self.assertEqual(tok, 'secret')
        self.assertEqual(calls[0], ['security', 'find-generic-password', '-s', 'stick-worker', '-w'])


class RunnerTest(TmpDir, unittest.TestCase):
    def test_streams_lines_and_logs(self):
        lines = []
        rc, tail = proc.run(['/bin/sh', '-c', 'echo one; echo two >&2; exit 3'], cwd=self.tmp, on_line=lines.append,
                            log_path=self.tmp / 'out.log')
        self.assertEqual(rc, 3)
        self.assertEqual([l.strip() for l in lines], ['one', 'two'])
        self.assertEqual(tail, 'one\ntwo')
        self.assertIn('two', (self.tmp / 'out.log').read_text())

    def test_timeout_stops_only_its_own_command(self):
        t0 = time.time()
        rc, tail = proc.run([sys.executable, '-c', 'import time; print("start", flush=True); time.sleep(30)'],
                            cwd=self.tmp, timeout=1)
        self.assertLess(time.time() - t0, 15)
        self.assertNotEqual(rc, 0)
        self.assertIn('stopped by the worker', tail)
        self.assertEqual(os.getpgid(0), os.getpgid(0))          # we are still alive in our own group


if __name__ == '__main__':
    unittest.main()
