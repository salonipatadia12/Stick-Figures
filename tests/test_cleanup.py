"""Cleanup safety (user 2026-11-11). macworker/cleanup.py after each job, and scripts/cleanup.sh run against a
throwaway fake repo: dry run by default, and with --apply it removes only old caches, never the repo, episodes,
rendered videos, .env files, keys or anything outside the tree. Nothing here touches the real repo."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from macworker import cleanup

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'cleanup.sh'
OLD = time.time() - 30 * 86400            # a month ago


def touch(path: Path, text='x', mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class StaleShots(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.repo = Path(self.dir.name)
        self.out = self.repo / 'renders/sample/demo'
        shots = self.out / 'shots'
        self.current = [touch(shots / f'b01-{"a" * 16}.{ext}', mtime=OLD) for ext in ('mp4', 'captions.json', 'text.json')]
        self.stale = [touch(shots / f'b01-{"b" * 16}.{ext}', mtime=OLD) for ext in ('mp4', 'captions.json', 'text.json')]
        self.newer = touch(shots / f'b02-{"c" * 16}.mp4')                 # a render still writing: newer than the list
        self.other = touch(shots / 'notes.txt', mtime=OLD)                 # not a clip name: never touched
        self.final = touch(self.out / 'demo.mp4', mtime=OLD)
        listing = touch(self.out / 'shots.txt',
                        f"file '/Users/someone/Desktop/old/renders/sample/demo/shots/b01-{'a' * 16}.mp4'\n")
        os.utime(listing, (time.time() - 60, time.time() - 60))

    def test_only_unlisted_clips_older_than_the_listing_go(self):
        self.assertEqual(cleanup.current_shots(self.out), {'b01-' + 'a' * 16})
        self.assertEqual(sorted(cleanup.stale_shots(self.out)), sorted(self.stale))
        removed, freed = cleanup.remove_stale_shots(self.repo, 'demo')
        self.assertEqual((removed, freed), (3, 3))
        for p in self.current + [self.newer, self.other, self.final, self.out / 'shots.txt']:
            self.assertTrue(p.exists(), p)
        for p in self.stale:
            self.assertFalse(p.exists(), p)

    def test_no_listing_means_nothing_is_deleted(self):
        (self.out / 'shots.txt').unlink()
        self.assertEqual(cleanup.stale_shots(self.out), [])
        self.assertEqual(cleanup.remove_stale_shots(self.repo, 'demo'), (0, 0))
        self.assertTrue(all(p.exists() for p in self.stale))

    def test_unsafe_slug_is_refused(self):
        with self.assertRaises(ValueError):
            cleanup.remove_stale_shots(self.repo, '../../etc')


class AfterJob(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.repo = Path(self.dir.name)
        self.requests = []

    def transport(self, loaded):
        def handler(request):
            self.requests.append((request.method, request.url.path, request.read()))
            if request.url.path == '/api/ps':
                return httpx.Response(200, json=dict(models=[dict(name=m) for m in loaded]))
            return httpx.Response(200, json={})
        return httpx.MockTransport(handler)

    def test_unloads_the_model_only_when_it_is_loaded(self):
        out = cleanup.after_job(self.repo, dict(job_id=1, kind='script', slug='demo'), 'finished', 'http://ollama.test',
                                'qwen3.5:9b', transport=self.transport(['qwen3.5:9b']))
        self.assertTrue(out['unloaded'])
        post = [r for r in self.requests if r[0] == 'POST']
        self.assertEqual(len(post), 1)
        self.assertEqual(json.loads(post[0][2]), dict(model='qwen3.5:9b', keep_alive=0))
        self.assertIn('qwen3.5:9b unloaded', (self.repo / 'logs/cleanup.log').read_text())
        self.requests.clear()
        out = cleanup.after_job(self.repo, dict(job_id=2, kind='paint', slug='demo'), 'finished', 'http://ollama.test',
                                'qwen3.5:9b', transport=self.transport([]))
        self.assertFalse(out['unloaded'])
        self.assertEqual([r[0] for r in self.requests], ['GET'])          # not loaded: no request that would load it

    def test_never_raises(self):
        out = cleanup.after_job(self.repo, dict(job_id=3, kind='render', slug='../bad'), 'finished', 'http://127.0.0.1:9',
                                'qwen3.5:9b')
        self.assertIn('error', out)                                        # the unsafe slug, caught and logged
        out = cleanup.after_job(self.repo, dict(job_id=4, kind='render', slug='demo'), 'failed', 'http://127.0.0.1:9',
                                'qwen3.5:9b')
        self.assertEqual((out['removed'], out['unloaded']), (0, False))    # a failed render deletes nothing


@unittest.skipUnless(shutil.which('git'), 'git is needed for the fake repo')
class CleanupScript(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        top = Path(self.dir.name)
        self.repo = top / 'Stick_Figures'
        r = self.repo
        (r / 'scripts').mkdir(parents=True)
        shutil.copy2(SCRIPT, r / 'scripts/cleanup.sh')
        touch(r / 'pipeline/cache.py', '# fake')
        touch(r / 'pyproject.toml', '[project]\nname = "stick-figures"\n')
        subprocess.run(['git', 'init', '-q', str(r)], check=True)
        self.outside = touch(top / 'outside' / 'search-old.json', mtime=OLD)
        # deletable: old search/llm caches of a rendered episode, old shared pages, __pycache__
        self.gone = [touch(r / 'episodes/done/cache/search-1.json', mtime=OLD),
                     touch(r / 'episodes/done/cache/llm-1.json', mtime=OLD),
                     touch(r / 'cache/pages/page-1.json', mtime=OLD),
                     touch(r / 'pipeline/__pycache__/cache.cpython-314.pyc', mtime=OLD)]
        touch(r / 'renders/sample/done/done.mp4', mtime=OLD)
        # protected or out of scope, all a month old
        self.kept = [r / 'renders/sample/done/done.mp4',
                     touch(r / 'episodes/done/episode.json', mtime=OLD),
                     touch(r / 'episodes/done/ledger.json', mtime=OLD),
                     touch(r / 'episodes/done/cache/research-1.json', mtime=OLD),     # the paid/provenance records
                     touch(r / 'episodes/done/cache/script-1.json', mtime=OLD),
                     touch(r / 'episodes/done/cache/story-1.json', mtime=OLD),
                     touch(r / 'episodes/open/cache/search-1.json', mtime=OLD),       # no render yet
                     touch(r / 'episodes/_archive/x/cache/search-1.json', mtime=OLD),
                     touch(r / 'episodes/done/cache/search-new.json'),                 # too recent
                     touch(r / 'cache/pages/page-new.json'),
                     touch(r / '.env', mtime=OLD),
                     touch(r / 'deploy/stick-deploy-key', mtime=OLD),
                     touch(r / '.venv/lib/__pycache__/x.pyc', mtime=OLD),
                     self.outside]
        os.symlink(self.outside, r / 'cache/pages/page-link.json')          # a link out of the tree: never followed
        os.utime(r / 'cache/pages/page-link.json', (OLD, OLD), follow_symlinks=False)
        self.kept.append(r / 'cache/pages/page-link.json')

    def run_script(self, *args):
        return subprocess.run(['/bin/bash', str(self.repo / 'scripts/cleanup.sh'), *args], capture_output=True, text=True,
                              env=dict(os.environ, HOME=str(Path(self.dir.name) / 'home')))

    def test_dry_run_deletes_nothing(self):
        p = self.run_script()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Nothing was deleted', p.stdout)
        self.assertIn('would remove episodes/done/cache/search-1.json', p.stdout)
        for f in self.gone + self.kept:
            self.assertTrue(os.path.lexists(f), f)
        self.assertFalse((self.repo / 'logs').exists())

    def test_apply_removes_only_old_caches_and_logs_it(self):
        p = self.run_script('--apply')
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        for f in self.gone:
            self.assertFalse(f.exists(), f)
        for f in self.kept:
            self.assertTrue(os.path.lexists(f), f)
        self.assertFalse((self.repo / 'pipeline/__pycache__').exists())      # emptied, then removed
        self.assertTrue((self.repo / 'pipeline/cache.py').exists())
        log = (self.repo / 'logs/cleanup.log').read_text()
        self.assertIn('removed episodes/done/cache/llm-1.json', log)
        self.assertIn('removed cache/pages/page-1.json', log)
        self.assertIn('done: removed 4 file(s)', log)
        self.assertIn('git gc', log)

    def test_refuses_a_folder_that_is_not_the_repo(self):
        (self.repo / 'pipeline/cache.py').unlink()
        p = self.run_script('--apply')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('does not look like the Stick_Figures repo', p.stderr)
        for f in self.gone:
            self.assertTrue(f.exists(), f)

    def test_rejects_unknown_options(self):
        p = self.run_script('--days', 'x')
        self.assertEqual(p.returncode, 2)
        p = self.run_script('--force')
        self.assertEqual(p.returncode, 2)


class ScriptText(unittest.TestCase):
    def test_no_recursive_delete_and_dry_run_is_the_default(self):
        text = SCRIPT.read_text()
        code = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
        self.assertNotRegex(code, r'rm\s+-[a-zA-Z]*r')
        self.assertIn('APPLY=0', code)


if __name__ == '__main__':
    unittest.main()
