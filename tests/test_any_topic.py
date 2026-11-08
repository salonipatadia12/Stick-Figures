"""The any-topic phase (docs/PLAN.md §7): verbs, the composer, the props step and the starter base pack."""
import json
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/motion_test'))

from pipeline import basepack, propgen  # noqa: E402
from pipeline.rig import verbs as V  # noqa: E402
from pipeline.rig.actions import Idle, Performance  # noqa: E402
from pipeline.rig.skeleton import GRIPPING  # noqa: E402


class VerbsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from beat_base import Beat

        class Bt(Beat):
            base_name = 'together'
            cast = ('lead', 'friend')

            def build(self):
                return {}
        cls.beat = Bt({}, 0.0, 1.0, load_art=False)
        cls.p = cls.beat.p['lead']
        cls.base = cls.beat.rest_pose('lead')

    def moves(self):
        p, tgt = self.p, self.p.body((600, 780))
        return [V.reach(p, 1.0, tgt), V.point(p, 1.0, tgt), V.shrug(p, 1.0), V.facepalm(p, 1.0), V.pick_up(p, 1.0, 'cup', tgt),
                V.put_down(p, 1.0, 'cup', tgt), V.throw(p, 1.0, 'cup'), V.drink(p, 1.0, 'cup', tgt), V.type_(p, 1.0, 3.0),
                V.write(p, 1.0, 3.0), V.wave(p, 1.0), V.nod(p, 1.0), V.shake_head(p, 1.0), V.slump(p, 1.0), V.jolt(p, 1.0),
                V.gaze(p, 1.0, direction='camera')]

    def test_every_in_place_verb_exists_and_builds_a_pose(self):
        self.assertEqual(sorted(V.VERBS), sorted(V.IN_PLACE))
        for m in self.moves():
            perf = Performance(self.base, Idle(), [m.action])
            for k in range(40):
                perf.pose(0.9 + 0.05 * k)

    def test_a_held_prop_is_always_in_a_gripping_hand(self):
        for m in self.moves():
            perf = Performance(self.base, Idle(), [m.action])
            for k in range(60):
                t = 0.9 + 0.05 * k
                pose = perf.pose(t)
                for (a, b, side, prop) in m.holds:
                    if a <= t <= b:
                        self.assertIn(getattr(pose, side + '_shape'), GRIPPING, f'{m.label} {side} at {t:.2f}')

    def test_hand_shapes_are_ones_the_rig_can_draw(self):
        from pipeline.rig.hands import POSES
        for m in self.moves():
            perf = Performance(self.base, Idle(), [m.action])
            for k in range(30):
                pose = perf.pose(0.9 + 0.1 * k)
                self.assertIn(pose.l_shape, POSES)
                self.assertIn(pose.r_shape, POSES)


class ComposerTest(unittest.TestCase):
    def test_the_salary_episode_composes_with_no_hand_written_beats(self):
        import compose_beats as CB
        c = CB.for_episode('why-your-salary-disappears-by-the-20th')
        self.assertEqual(sorted(c.BEATS), [f'b{i:02d}' for i in range(1, 10)])
        s = c.library_summary()
        self.assertEqual((s['beats'], s['library_beats'], s['library_pct']), (9, 9, 100))

    def test_a_composed_beat_builds_from_word_times_alone(self):
        import compose_beats as CB
        from beat_base import word_times
        c = CB.for_episode('why-your-salary-disappears-by-the-20th')
        ep = json.loads((ROOT / 'episodes/why-your-salary-disappears-by-the-20th/episode.json').read_text())
        b = ep['beats'][8]
        T = word_times([(w, 1.0 + 0.3 * i) for i, w in enumerate(b['narration'].split())])
        beat = c.BEATS['b09'](T, 1.0, 6.0, load_art=False)
        self.assertEqual(set(beat.perfs), {'lead', 'friend'})
        self.assertTrue(beat.camera(2.0)[2] > 0)

    def test_an_override_counts_as_hand_written(self):
        import compose_beats as CB
        import sleep_beats
        c = CB.for_episode('why-youre-always-tired-after-8-hours-of-sleep', overrides={'b05': sleep_beats.B05})
        s = c.library_summary()
        self.assertEqual(s['per_beat']['b05']['hand_written'], ['B05'])
        self.assertEqual(s['library_pct'], round(100 * 8 / 9))

    def test_staged_verbs_split_the_beat_when_the_library_has_the_second_base(self):
        import compose_beats as CB
        from pipeline import store
        from pipeline.models import ActionCue, CastPose, Staging
        episode, _ = store.load('why-youre-always-tired-after-8-hours-of-sleep')
        b = episode.beats[0]                                   # bed-edge; sit_up has no new base, lie_down -> bed-awake
        b.shot.staging = Staging(setting='bedroom', setting_detail='', poses=[CastPose(who='lead', pose='bed-edge')],
                                 actions=[ActionCue(who='lead', verb='lie_down', target='', anchor='exhausted')], props=[])
        raw = json.loads((ROOT / 'episodes' / episode.slug / 'episode.json').read_text())
        raw['beats'][0]['shot']['staging'] = b.shot.staging.model_dump()
        import pipeline.bases as B
        rows = {r['beat']: r for r in B.plan(episode)}
        self.assertEqual(rows['b01']['base'], 'lead-bed-edge-bedroom')
        d = dict(setting='bedroom', pose=CB.POSE_AFTER['lie_down'], view='eye-level', cast=['lead'], poses={'lead': 'bed-awake'})
        self.assertEqual(B.match(d, B.library()), 'lead-bed-awake-bedroom')


class PropsStepTest(unittest.TestCase):
    def test_check_accepts_one_solid_object_and_rejects_two(self):
        im = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((150, 150, 360, 380), fill=(190, 90, 60, 255))
        self.assertTrue(propgen.check(im)['pass_'])
        im2 = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
        d = ImageDraw.Draw(im2)
        d.ellipse((50, 50, 200, 200), fill=(190, 90, 60, 255))
        d.ellipse((300, 300, 450, 450), fill=(90, 160, 150, 255))
        r = propgen.check(im2)
        self.assertFalse(r['pass_'])
        self.assertIn('2 separate pieces', r['reasons'][0])

    def test_check_rejects_grey_and_white_blobs(self):
        im = Image.new('RGBA', (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((150, 150, 360, 380), fill=(240, 240, 240, 255))
        r = propgen.check(im)
        self.assertFalse(r['pass_'])

    def test_needed_skips_props_the_libraries_already_have(self):
        from pipeline.models import Beat, Episode
        from pipeline_fixtures import beat, staging
        st = staging(('lead',), 'cafe', 'seated-at-table', props=(('coffee cup', 'lead', 'table'), ('desk lamp', '', 'table'),
                                                                   ('brass trumpet', '', 'table')))
        b = Beat.model_validate(dict(id='b01', **beat('hook', 'A trumpet on the table.', 'The lead stares at a trumpet.',
                                                      [('punch_in', 'trumpet', '')], stage=st)))
        ep = Episode(slug='t', topic='A trumpet test', beats=[b])
        need = propgen.needed(ep, lib={})
        self.assertEqual(list(need), ['brass trumpet'])
        self.assertEqual(need['brass trumpet']['beats'], ['b01'])
        self.assertEqual(propgen.needed(ep, lib={'brass trumpet': dict(sprite='x.png')}), {})

    def test_jobs_are_text_to_image_at_512_with_two_seeds(self):
        j = propgen.jobs({'brass trumpet': dict(name='brass trumpet', beats=['b01'])})
        self.assertEqual(j['brass trumpet']['size'], 512)
        self.assertEqual(len(j['brass trumpet']['seeds']), 2)
        self.assertIn('brass trumpet', j['brass trumpet']['prompt'])
        self.assertIn('No text', j['brass trumpet']['prompt'])


class BasePackTest(unittest.TestCase):
    def test_eight_settings_three_bases_each(self):
        w = basepack.wanted()
        self.assertEqual(len(w), 24)
        self.assertEqual(len({d['setting'] for d in w.values()}), 8)
        self.assertIn('lead-seated-desk-office', w)
        self.assertIn('together-car', w)
        self.assertEqual(w['together-classroom']['poses'], {'friend': 'standing', 'lead': 'seated-desk'})

    def test_jobs_have_two_seeds_character_references_and_the_setting(self):
        w = basepack.wanted(['kitchen'])
        j = basepack.jobs(w, dict(pack='t', candidates={}, rejected={}))
        self.assertEqual(set(j), {'lead-standing-kitchen', 'friend-standing-kitchen', 'together-kitchen'})
        for n, job in j.items():
            self.assertEqual(len(job['seeds']), 2)
            self.assertIn('kitchen', job['prompt'])
            self.assertIn('No text', job['prompt'])
        self.assertIn('04-waiting.png', j['lead-standing-kitchen']['images'])
        self.assertIn('03-together.png', j['together-kitchen']['images'])

    def test_a_rejection_makes_a_redo_round_with_three_seeds(self):
        w = basepack.wanted(['park'])
        st = dict(pack='t', candidates={}, rejected={'lead-seated-bench-park': ['renders/bases/flux/pack-park-lead-s401.png']})
        j = basepack.jobs(w, st, {'lead-seated-bench-park': 'sitting on the bench feeding pigeons'})
        self.assertEqual(j['lead-seated-bench-park']['round'], 2)
        self.assertEqual(len(j['lead-seated-bench-park']['seeds']), 3)
        self.assertIn('feeding pigeons', j['lead-seated-bench-park']['prompt'])
        self.assertTrue(j['lead-seated-bench-park']['job'].endswith('-r2'))


if __name__ == '__main__':
    unittest.main()
