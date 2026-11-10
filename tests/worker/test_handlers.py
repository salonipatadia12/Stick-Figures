"""The paint and render handlers and the redo bridge, against a temporary repo and fake commands (no FLUX,
no rendering)."""
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from PIL import Image

from macworker import bridge, handlers
from macworker.handlers import Ctx, JobError

from .fakes import TmpDir, job


def _px(path):
    with Image.open(path) as im:
        im.load()
        return im.copy()

SLUG = 'why-honey-never-spoils'


class HandlerBase(TmpDir):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / 'repo'
        (self.repo / 'styles/cozy/inserts').mkdir(parents=True)
        (self.repo / 'styles/cozy/props').mkdir(parents=True)
        self.reports, self.cmds = [], []

    def ctx(self, job_dict, run):
        wd = self.tmp / 'state/jobs/1'
        wd.mkdir(parents=True, exist_ok=True)

        def logged_run(cmd, on_line=None):
            self.cmds.append([str(c) for c in cmd])
            return run([str(c) for c in cmd], on_line)

        return Ctx(repo=self.repo, job=job_dict, workdir=wd, report=lambda s, p, m='': self.reports.append((s, p, m)),
                   run=logged_run, render_workers=4)

    def lib(self, kind):
        p = self.repo / f'styles/cozy/{kind}/library.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def put(self, kind, key, entry, size=(300, 533), mode='RGB'):
        lib = self.lib(kind)
        img = self.repo / f'styles/cozy/{kind}/{key}.png'
        Image.new(mode, size, (200, 120, 60) + ((128,) if mode == 'RGBA' else ())).save(img)
        lib[key] = dict(entry, **({'file': str(img.relative_to(self.repo))} if kind == 'inserts' else {'sprite': str(img.relative_to(self.repo))}))
        (self.repo / f'styles/cozy/{kind}/library.json').write_text(json.dumps(lib))


class RenderHandlerTest(HandlerBase, unittest.TestCase):
    def fake_render(self, qa):
        def run(cmd, on_line):
            out = self.repo / 'renders/sample' / SLUG
            out.mkdir(parents=True, exist_ok=True)
            for n in (f'{SLUG}.mp4', f'{SLUG}.srt', 'contact_sheet.jpg'):
                (out / n).write_bytes(b'x')
            (out / 'qa.json').write_text(json.dumps(qa))
            lines = ['duration 44.00s, 3 shots, 9 caption lines', "cached: 1 to render: ['b01', 'b02']",
                     '  b01: 100 frames  10s', '  b02: 90 frames  20s', f'{out}/{SLUG}.mp4 44.00s 30s']
            for l in lines:
                on_line(l + '\n')
            return 0, '\n'.join(lines)
        return run

    def test_render_uploads_four_files_and_qa_pass(self):
        qa = dict(summary=dict(duration=True, captions=True, all=True))
        o = handlers.render(self.ctx(job(1), self.fake_render(qa)))
        self.assertEqual(self.cmds[0][1:], ['scripts/motion_test/episode_compose.py', SLUG, '--workers', '4'])
        self.assertTrue(self.cmds[0][0].endswith('.venv/bin/python'))
        self.assertEqual([(f['name'], f['kind']) for f in o.files],
                         [(f'{SLUG}.mp4', 'mp4'), (f'{SLUG}.srt', 'srt'), ('qa.json', 'qa'), ('contact_sheet.jpg', 'contact_sheet')])
        for f in o.files:                                     # kept inside the job folder
            self.assertTrue(Path(f['path']).is_file())
            self.assertIn('state/jobs/1/files', f['path'])
        self.assertEqual(o.result['duration'], 44.0)
        self.assertIs(o.result['qa_pass'], True)
        self.assertEqual(o.result['qa_summary'], 'QA passed (2 checks)')
        self.assertTrue(json.loads((self.repo / 'episodes' / SLUG / 'episode.json').read_text()))
        stages = [r[0] for r in self.reports]
        self.assertIn('shots', stages)
        self.assertEqual(stages[-1], 'upload')

    def test_qa_fail_and_top_level_all(self):
        o = handlers.render(self.ctx(job(1), self.fake_render(dict(summary=dict(captions=False, hook=True, all=False)))))
        self.assertIs(o.result['qa_pass'], False)
        self.assertEqual(o.result['qa_summary'], 'QA failed: captions (1/2 passed)')
        self.assertIs(handlers.qa_pass(dict(all=True)), True)

    def test_render_failure_raises(self):
        with self.assertRaises(JobError) as e:
            handlers.render(self.ctx(job(1), lambda cmd, on_line: (1, 'Traceback\nValueError: bad beat')))
        self.assertIn('ValueError: bad beat', str(e.exception))

    def test_stale_output_rejected(self):
        out = self.repo / 'renders/sample' / SLUG
        out.mkdir(parents=True)
        for n in (f'{SLUG}.mp4', f'{SLUG}.srt', 'contact_sheet.jpg', 'qa.json'):
            (out / n).write_text('{}')
            old = time.time() - 3600
            import os
            os.utime(out / n, (old, old))
        with self.assertRaises(JobError) as e:
            handlers.render(self.ctx(job(1), lambda cmd, on_line: (0, '')))
        self.assertIn('stale', str(e.exception))

    def test_unsafe_slug_refused(self):
        with self.assertRaises(ValueError):
            handlers.render(self.ctx(job(1, slug='../../etc'), lambda c, o: (0, '')))


class PaintHandlerTest(HandlerBase, unittest.TestCase):
    def needed_json(self):
        return dict(inserts={'honey-cave': dict(scene='a honey cave', beats=['b02'], base=None),
                             'dark-hive': dict(scene='a dark hive', beats=['b05'], base=None)},
                    props={'honey-jar': dict(name='honey jar', beats=['b03'])},
                    uses_inserts={'honey-cave': dict(scene='a honey cave', beats=['b02', 'b04'], base=None)},
                    uses_props={'honey-jar': dict(name='honey jar', beats=['b03'])})

    def fake(self, cmd, on_line):
        if cmd[1].endswith('bridge.py') and cmd[2] == 'needed':
            return 0, 'noise\n' + bridge.MARK + json.dumps(self.needed_json())
        if cmd[0].endswith('stick') and cmd[1] == 'inserts':
            on_line('scene model loaded in 3s\n')
            on_line('scene-honey-cave-s100.png: 80s\n')
            self.put('inserts', 'honey-cave', dict(scene='a honey cave', episode=SLUG), size=(1080, 1920))
            return 3, 'Insert scenes to make (2): ...\n  + honey-cave: x (passed QA)\n  ! dark-hive: failed QA on both seeds'
        if cmd[0].endswith('stick') and cmd[1] == 'props':
            on_line('prop-honey-jar-s300.png: 25s\n')
            self.put('props', 'honey-jar', dict(name='honey jar'), size=(512, 512), mode='RGBA')
            return 0, 'Props to make (1): honey jar [b03]'
        raise AssertionError(cmd)

    def test_paint_new_scenes_and_props(self):
        self.put('inserts', 'old-scene', dict(scene='old'))
        o = handlers.paint(self.ctx(job(1, kind='paint'), self.fake))
        self.assertEqual([c[1] for c in self.cmds], [self.cmds[0][1], 'inserts', 'props'])
        self.assertEqual(self.cmds[1][2:], [SLUG, '--generate'])
        new = {n['key']: n for n in o.result['new']}
        self.assertEqual(set(new), {'honey-cave', 'honey-jar'})          # not old-scene
        self.assertEqual(new['honey-cave'], dict(key='honey-cave', kind='scene', name='honey-cave.jpg', scene='a honey cave',
                                                 beats=['b02', 'b04']))
        self.assertEqual(new['honey-jar']['kind'], 'prop')
        self.assertEqual(new['honey-jar']['scene'], 'honey jar')
        self.assertEqual(o.result['failed'], [dict(key='dark-hive', why='failed QA on both seeds')])
        self.assertEqual([(f['name'], f['kind']) for f in o.files], [('honey-cave.jpg', 'scene_preview'), ('honey-jar.jpg', 'scene_preview')])
        with Image.open(o.files[0]['path']) as im:
            self.assertEqual((im.format, im.size), ('JPEG', (720, 1280)))
        with Image.open(o.files[1]['path']) as im:
            self.assertEqual(im.mode, 'RGB')                              # the sprite is flattened on cream
        self.assertTrue(any(r[0] == 'paint' and 'candidate' in r[2] for r in self.reports))

    def test_nothing_to_paint_skips_commands(self):
        def run(cmd, on_line):
            return 0, bridge.MARK + json.dumps(dict(inserts={}, props={}, uses_inserts={}, uses_props={}))
        o = handlers.paint(self.ctx(job(1, kind='paint'), run))
        self.assertEqual(len(self.cmds), 1)
        self.assertEqual(o.result, dict(new=[], failed=[]))
        self.assertEqual(o.files, [])

    def test_stick_crash_fails_job(self):
        def run(cmd, on_line):
            if 'bridge.py' in cmd[1]:
                return 0, bridge.MARK + json.dumps(self.needed_json())
            return 1, 'Traceback\nRuntimeError: FLUX out of memory'
        with self.assertRaises(JobError) as e:
            handlers.paint(self.ctx(job(1, kind='paint'), run))
        self.assertIn('FLUX out of memory', str(e.exception))

    def test_redo_runs_bridge_redo(self):
        self.put('inserts', 'honey-cave', dict(scene='a honey cave'))
        seen = {}

        def run(cmd, on_line):
            if cmd[2] == 'needed':
                return 0, bridge.MARK + json.dumps(dict(inserts={}, props={}, uses_inserts={'honey-cave': dict(beats=['b02'])}, uses_props={}))
            seen['notes'] = json.loads(Path(cmd[4]).read_text())
            on_line('scene-honey-cave-r2-s1100.png: 80s\n')
            return 0, bridge.MARK + json.dumps(dict(made={'honey-cave': dict(file='x', kind='scene', round=2, stale_variants=['honey-cave--dark'])},
                                                    failed={}))
        o = handlers.paint(self.ctx(job(1, kind='paint', payload=dict(redo={'honey-cave': 'warmer light'})), run))
        self.assertEqual(seen['notes'], {'honey-cave': 'warmer light'})
        self.assertEqual([n['key'] for n in o.result['new']], ['honey-cave'])
        self.assertEqual(o.result['new'][0]['beats'], ['b02'])
        self.assertIn('honey-cave--dark', o.result['note'])
        self.assertFalse(any(c[0].endswith('stick') for c in self.cmds))  # a redo paints only its keys

    def test_redo_bad_key_or_note(self):
        for redo in ({'../x': 'n'}, {'ok-key': '  '}, ['x']):
            with self.assertRaises((JobError, ValueError)):
                handlers.paint(self.ctx(job(1, kind='paint', payload=dict(redo=redo)), lambda c, o: (0, '')))


class WriteEpisodeTest(HandlerBase, unittest.TestCase):
    def test_writes_and_archives_a_different_copy(self):
        d = self.repo / 'episodes' / SLUG
        d.mkdir(parents=True)
        (d / 'episode.json').write_text(json.dumps(dict(slug=SLUG, title='old')))
        handlers.write_episode(self.repo, job(1))
        self.assertEqual(json.loads((d / 'episode.json').read_text())['title'], 'T')
        self.assertEqual(json.loads((d / 'ledger.json').read_text()), dict(entries=[]))
        arch = list((self.repo / 'episodes/_archive/macworker' / SLUG).glob('*/episode.json'))
        self.assertEqual(len(arch), 1)
        self.assertEqual(json.loads(arch[0].read_text())['title'], 'old')

    def test_missing_episode(self):
        with self.assertRaises(JobError):
            handlers.write_episode(self.repo, dict(slug=SLUG, episode=None))


class FakeInserts:
    """The parts of pipeline.inserts the redo bridge uses, on a temp folder."""

    def __init__(self, root: Path, ok=True):
        self.ROOT, self.DIR, self.FLUX_OUT, self.ok = root, root / 'styles/cozy/inserts', root / 'renders/inserts/flux', ok
        self.DIR.mkdir(parents=True, exist_ok=True)
        self.calls = []

    def library(self):
        p = self.DIR / 'library.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def save_library(self, lib):
        (self.DIR / 'library.json').write_text(json.dumps(lib))

    def jobs(self, want, rnd=1, direction=None):
        self.calls.append(('jobs', rnd, direction))
        k = next(iter(want))
        return {k: dict(job=f'scene-{k}' + ('' if rnd == 1 else f'-r{rnd}'), seeds=[1000 * rnd, 1000 * rnd + 1],
                        prompt=f'PROMPT {(direction or {}).get(k)}', images=[])}

    def render(self, jm, tag):
        self.FLUX_OUT.mkdir(parents=True, exist_ok=True)
        out = {}
        for k, j in jm.items():
            out[k] = []
            for s in j['seeds']:
                p = self.FLUX_OUT / f"{j['job']}-s{s}.png"
                Image.new('RGB', (64, 64), (s % 255, 0, 0)).save(p)
                out[k].append(str(p))
        return out

    def qa(self, paths):
        return {p: dict(ok=self.ok, score=i, stats={'i': i}, reasons=[] if self.ok else ['too dark']) for i, p in enumerate(paths)}


class RedoBridgeTest(TmpDir, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.ins = FakeInserts(self.tmp)
        Image.new('RGB', (64, 64), 'blue').save(self.ins.DIR / 'honey-cave.png')
        self.ins.save_library({'honey-cave': dict(scene='A honey cave.', file='styles/cozy/inserts/honey-cave.png', base=None),
                               'honey-cave--dark': dict(scene='dark', base='honey-cave')})
        self.pg = NS(library=lambda: {})

    def test_redo_archives_round1_and_saves_round2(self):
        ans = bridge.redo(SLUG, {'honey-cave': 'warmer light, more bees'}, self.ins, self.pg, today='20261110')
        self.assertEqual(ans['failed'], {})
        made = ans['made']['honey-cave']
        self.assertEqual((made['kind'], made['round']), ('scene', 2))
        self.assertEqual(made['stale_variants'], ['honey-cave--dark'])
        _, rnd, direction = self.ins.calls[-1]
        self.assertEqual(rnd, 2)
        self.assertEqual(direction, {'honey-cave': 'A honey cave. Art direction: warmer light, more bees'})
        arch = self.ins.DIR / 'archive/round1-20261110'
        self.assertEqual(_px(arch / 'honey-cave.png').getpixel((0, 0)), (0, 0, 255))     # round 1 kept
        self.assertIn('honey-cave', json.loads((arch / 'library-entries.json').read_text()))
        self.assertNotEqual(_px(self.ins.DIR / 'honey-cave.png').getpixel((0, 0)), (0, 0, 255))
        entry = self.ins.library()['honey-cave']
        self.assertEqual((entry['round'], entry['direction'], entry['episode']), (2, 'warmer light, more bees', SLUG))
        self.assertEqual(entry['history'][0]['round'], 1)

    def test_second_redo_goes_to_round3_and_keeps_both_archives(self):
        bridge.redo(SLUG, {'honey-cave': 'warmer'}, self.ins, self.pg, today='20261110')
        bridge.redo(SLUG, {'honey-cave': 'even warmer'}, self.ins, self.pg, today='20261110')
        self.assertEqual(self.ins.library()['honey-cave']['round'], 3)
        self.assertTrue((self.ins.DIR / 'archive/round2-20261110/honey-cave.png').exists())
        self.assertTrue((self.ins.DIR / 'archive/round1-20261110/honey-cave.png').exists())

    def test_failed_qa_keeps_round1(self):
        self.ins.ok = False
        ans = bridge.redo(SLUG, {'honey-cave': 'warmer'}, self.ins, self.pg, today='20261110')
        self.assertIn('failed QA', ans['failed']['honey-cave'])
        self.assertEqual(_px(self.ins.DIR / 'honey-cave.png').getpixel((0, 0)), (0, 0, 255))
        self.assertFalse((self.ins.DIR / 'archive').exists())

    def test_unknown_key(self):
        ans = bridge.redo(SLUG, {'nope': 'x'}, self.ins, self.pg)
        self.assertEqual(ans['failed'], {'nope': 'not in the insert or prop library'})

    def test_parse_marker(self):
        self.assertEqual(bridge.parse('a\nBRIDGE_JSON {"x": 1}\n'), {'x': 1})
        self.assertIsNone(bridge.parse('no marker'))


if __name__ == '__main__':
    unittest.main()
