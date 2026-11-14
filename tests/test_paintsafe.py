"""pipeline/paintsafe.py and the paint fallback chain (2026-11-14: "Explain why sunsets are colorful" died at paint with
`Error: unknown file extension:` after a split beat's empty top key was saved as `.png`). No FLUX runs here: the FLUX
step is replaced by stand-ins that return bad paths, broken files or good images."""
import io
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from pipeline import config, inserts, paintsafe, propgen, qa
from pipeline.models import InsertCue, SplitCue
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_auto_and_qa_fixes import host_episode  # noqa: E402


def good_png(path, size=(64, 96), colour=(120, 80, 60)):
    Image.new('RGB', size, colour).save(path)
    return path


class Names(unittest.TestCase):
    def test_every_flux_path_ends_in_png(self):
        d = Path('/x')
        self.assertEqual(paintsafe.flux_png(d, 'scene-sky-red', 609), d / 'scene-sky-red-s609.png')
        self.assertEqual(paintsafe.png_path(d, ''), d / f"{paintsafe.safe_stem('')}.png")
        self.assertTrue(paintsafe.png_path(d, '').stem.startswith('unnamed-'))      # never ".png"
        self.assertEqual(paintsafe.png_path(d, 'transparent rock layer sheet').name, 'transparent-rock-layer-sheet.png')
        self.assertEqual(paintsafe.png_path(d, '../../etc/passwd').parent, d)        # no way out of the folder
        self.assertEqual(paintsafe.png_path(d, 'photo.JPG').name, 'photo.png')
        self.assertEqual(paintsafe.safe_stem('plate-04-empty'), 'plate-04-empty')    # cached names unchanged
        self.assertEqual(paintsafe.flux_png(d, 'scene-', 100).suffix, '.png')

    def test_is_image(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            self.assertTrue(paintsafe.is_image(good_png(t / 'a.png')))
            (t / 'b.png').write_bytes(b'not an image')
            (t / 'c.png').write_bytes(b'')
            self.assertFalse(paintsafe.is_image(t / 'b.png'))
            self.assertFalse(paintsafe.is_image(t / 'c.png'))
            self.assertFalse(paintsafe.is_image(t / 'missing.png'))
            data = (t / 'a.png').read_bytes()
            (t / 'd.png').write_bytes(data[: len(data) // 2])                       # cut short mid-write
            self.assertFalse(paintsafe.is_image(t / 'd.png'))


class SaveChecked(unittest.TestCase):
    def test_a_broken_file_is_painted_again(self):
        with tempfile.TemporaryDirectory() as t:
            dst, calls = Path(t) / 'x.png', []

            def paint(d):
                calls.append(d)
                if len(calls) == 1:
                    d.write_bytes(b'garbage')
                else:
                    good_png(d)
            self.assertTrue(paintsafe.save_checked(paint, dst, log=lambda m: None))
            self.assertEqual(len(calls), 2)
            self.assertTrue(paintsafe.is_image(dst))

    def test_a_flux_error_never_raises(self):
        with tempfile.TemporaryDirectory() as t:
            logs = []
            ok = paintsafe.save_checked(lambda d: (_ for _ in ()).throw(RuntimeError('metal out of memory')),
                                        Path(t) / 'x.png', log=logs.append)
            self.assertFalse(ok)
            self.assertEqual(len(logs), 2)
            self.assertIn('metal out of memory', logs[0])


class Prompts(unittest.TestCase):
    def test_simplify_keeps_the_core(self):
        self.assertEqual(paintsafe.simplify('a painted sky with a semi-transparent ozone layer graphic and fading blue light'), 'sky')
        self.assertEqual(paintsafe.simplify('a dusty old wooden barn at sunset, swallows overhead'), 'wooden barn at sunset')
        self.assertTrue(paintsafe.simplify(''))

    def test_mood(self):
        self.assertEqual(paintsafe.mood('a starry night over the desert'), 'night')
        self.assertEqual(paintsafe.mood('sunset sky with a red glow'), 'sunset')
        self.assertEqual(paintsafe.mood('the blue sky over an ocean'), 'sky')
        self.assertEqual(paintsafe.mood('a cozy kitchen with a kettle'), 'warm')

    def test_placeholders_are_real_images(self):
        with tempfile.TemporaryDirectory() as t:
            out = paintsafe.placeholder_scene(Path(t) / 'odd name', 'a sunset sky with a red glow')
            self.assertEqual(out.suffix, '.png')
            self.assertTrue(paintsafe.is_image(out))
            self.assertEqual(Image.open(out).size, paintsafe.SCENE_SIZE)
        sprite = paintsafe.placeholder_prop('rock layer sheet')
        self.assertEqual(sprite.mode, 'RGBA')
        self.assertIsNotNone(sprite.getchannel('A').getbbox())


def split_episode(top_key=''):
    ep = host_episode()
    b = ep.beats[1]
    b.shot.visual = 'split'
    b.shot.insert = InsertCue(key=top_key, scene='', effects=[])
    b.shot.split = SplitCue(key='sky-ozone-split', scene='a sky split in two', labels=['Blue Fading', 'Ozone'], anchors=['', ''])
    return ep, b


class SplitWithNoTop(unittest.TestCase):
    def test_an_empty_key_is_never_painted(self):
        ep, b = split_episode('')
        want = inserts.needed(ep, {})
        self.assertNotIn('', want)
        self.assertIn('sky-ozone-split', want)

    def test_the_script_gate_catches_it(self):
        ep, b = split_episode('')
        errs = [i.message for i in ep.check() if i.severity == 'error' and i.where == b.id]
        self.assertTrue(any('split top scene' in e for e in errs), errs)

    def test_the_render_shows_the_bottom_scene_alone(self):
        import sys
        sys.path.insert(0, str(config.ROOT / 'scripts/motion_test'))
        import compose_beats
        ep, b = split_episode('')
        specs = {x.id: dict(id=x.id, shot=x.shot.model_dump()) for x in ep.beats}
        notes = compose_beats.split_fallback(ep, specs)
        self.assertEqual(len(notes), 1)
        self.assertEqual(b.shot.visual, 'insert')
        self.assertEqual(b.shot.insert.key, 'sky-ozone-split')
        self.assertEqual(specs[b.id]['shot']['visual'], 'insert')
        self.assertEqual(specs[b.id]['shot']['insert']['key'], 'sky-ozone-split')


class Fallback(unittest.TestCase):
    """FLUX returns a bad path / an unreadable file: retry on a new seed, then a simplified prompt, then a placeholder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root = t
        for sub in ('styles/cozy/inserts', 'styles/cozy/props', 'flux'):
            (t / sub).mkdir(parents=True)
        self.patches = [mock.patch.object(inserts, 'ROOT', t), mock.patch.object(inserts, 'DIR', t / 'styles/cozy/inserts'),
                        mock.patch.object(inserts, 'LIBRARY', t / 'styles/cozy/inserts/library.json'),
                        mock.patch.object(propgen, 'ROOT', t), mock.patch.object(propgen, 'SPRITES', t / 'styles/cozy/props'),
                        mock.patch.object(propgen, 'LIBRARY', t / 'styles/cozy/props/library.json')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def episode(self):
        ep = host_episode()
        b = ep.beats[1]
        b.shot.visual = 'insert'
        b.shot.insert = InsertCue(key='sunset-sky-red-glow', scene='a vivid sunset sky with a deep red glow over the sea', effects=[])
        ep.beats = [b]
        return ep

    def fake_render(self, good_from_round=None, calls=None):
        """inserts.render stand-in: before `good_from_round` every path it returns is broken or missing."""
        def render(job_map, tag):
            out = {}
            for k, j in job_map.items():
                import re
                m = re.search(r'-r(\d+)$', j['job'])
                rnd = int(m.group(1)) if m else 1
                calls.append((rnd, len(j['seeds']), j['prompt']))
                paths = []
                for s in j['seeds']:
                    p = self.root / 'flux' / f"{j['job']}-s{s}.png"
                    if good_from_round and rnd >= good_from_round:
                        good_png(p)
                    elif s % 2:
                        p.write_bytes(b'')                   # an empty file
                    else:
                        p = self.root / 'flux' / f"{j['job']}-s{s}"   # a path with no extension, never written
                    paths.append(str(p))
                out[k] = paths
            return out
        return render

    def fake_qa(self, paths):
        return {p: dict(path=p, ok=True, reasons=[], score=1, stats={}) for p in paths}

    def test_all_retries_fail_so_a_placeholder_is_used(self):
        calls = []
        out = io.StringIO()
        with mock.patch.object(inserts, 'render', self.fake_render(None, calls)), \
                mock.patch('pipeline.inserts.subprocess.run', side_effect=AssertionError('no checker for broken files')), \
                contextlib.redirect_stdout(out):
            result = inserts.run(self.episode(), generate=True, lib={})
        self.assertEqual([c[:2] for c in calls], [(1, 2), (2, 1), (3, 1)])       # 2 seeds, a new seed, a simple prompt
        self.assertNotIn('vivid', calls[2][2])                                     # adjectives gone on the last retry
        self.assertIn('sunset sky', calls[2][2])
        self.assertEqual(result['failed'], {})                                    # never a dead end
        self.assertIn('sunset-sky-red-glow', result['placeholders'])
        lib = inserts.library()
        self.assertTrue(lib['sunset-sky-red-glow']['placeholder'])
        self.assertTrue(paintsafe.is_image(self.root / lib['sunset-sky-red-glow']['file']))
        self.assertIn('WARNING: scene sunset-sky-red-glow', out.getvalue())
        self.assertIn('PLACEHOLDER', inserts.view(result))
        # the same episode does not paint it again; another episode does
        self.assertEqual(inserts.needed(self.episode()), {})
        other = self.episode()
        other.slug = 'another-video'
        self.assertIn('sunset-sky-red-glow', inserts.needed(other))

    def test_the_first_retry_paints_it(self):
        calls = []
        with mock.patch.object(inserts, 'render', self.fake_render(2, calls)), mock.patch.object(inserts, 'qa', self.fake_qa), \
                contextlib.redirect_stdout(io.StringIO()):
            result = inserts.run(self.episode(), generate=True, lib={})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['retried'], {'sunset-sky-red-glow': 'new seed'})
        self.assertIn('sunset-sky-red-glow', result['made'])
        self.assertNotIn('placeholder', inserts.library()['sunset-sky-red-glow'])

    def test_a_prop_that_will_not_paint_gets_a_placeholder_sprite(self):
        ep = host_episode()
        need = {'rock-layer-sheet': dict(name='transparent rock layer sheet', beats=['b02'])}
        calls = []

        def render(job_map, slug):
            calls.append([(k, j['job'], j['prompt']) for k, j in job_map.items()])
            return {k: [self.root / 'flux' / f"{j['job']}-s{s}" for s in j['seeds']] for k, j in job_map.items()}
        with mock.patch.object(propgen, 'needed', return_value=need), mock.patch.object(propgen, 'render', render), \
                contextlib.redirect_stdout(io.StringIO()):
            result = propgen.run(ep, lib={})
        self.assertEqual(len(calls), 3)
        self.assertIn('rock layer sheet', calls[2][0][2])
        self.assertNotIn('transparent', calls[2][0][2].split('A single ')[1].split(',')[0])
        self.assertEqual(result['failed'], {})
        self.assertIn('rock-layer-sheet', result['placeholders'])
        e = propgen.library()['rock-layer-sheet']
        self.assertTrue(e['placeholder'])
        self.assertEqual(Image.open(self.root / e['sprite']).mode, 'RGBA')


class Report(unittest.TestCase):
    def test_the_qa_report_names_the_placeholder_beat(self):
        ep = {'beats': [dict(id='b01', shot=dict(insert=dict(key='a'))), dict(id='b02', shot=dict(insert=dict(key='b')))]}
        r = qa.placeholder_report(ep, scenes={'a': {}, 'b': {'placeholder': True}}, props={})
        self.assertFalse(r['pass_'])
        self.assertEqual(r['beats'], [dict(beat='b02', placeholder=['scene b'])])
        self.assertTrue(qa.placeholder_report(ep, scenes={'a': {}, 'b': {}}, props={})['pass_'])


if __name__ == '__main__':
    unittest.main()
