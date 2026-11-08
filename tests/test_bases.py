"""The base-pose step: what each beat needs, what the library has, what is missing, and the approval."""
import unittest

from pipeline import bases, store


CAFE_ONLY = {n: e for n, e in bases.library().items() if e.get('setting') == 'cafe'}


class BasePlanTest(unittest.TestCase):
    def test_salary_episode_is_fully_served_by_the_library(self):
        episode, _ = store.load('why-your-salary-disappears-by-the-20th')
        rows = bases.plan(episode)
        self.assertEqual(bases.missing(rows), {})
        by_beat = {r['beat']: r['base'] for r in rows}
        self.assertEqual(by_beat['b05'], 'lead-at-table')        # the jar insert uses the empty café plate
        self.assertEqual(by_beat['b09'], 'together')

    def test_sleep_episode_needs_three_bedroom_bases(self):
        episode, _ = store.load('why-youre-always-tired-after-8-hours-of-sleep')
        rows = bases.plan(episode, CAFE_ONLY)                       # the library before the bedroom picks
        miss = bases.missing(rows)
        self.assertEqual(sorted(miss), ['lead-bed-asleep-top-bedroom', 'lead-bed-awake-bedroom', 'lead-bed-edge-bedroom'])
        by_beat = {r['beat']: r['base'] for r in rows}
        self.assertEqual(by_beat['b04'], 'together')
        self.assertEqual(by_beat['b08'], 'lead-at-table')
        self.assertEqual(by_beat['b09'], 'together')

    def test_candidate_jobs_have_two_seeds_and_the_character_reference(self):
        episode, _ = store.load('why-youre-always-tired-after-8-hours-of-sleep')
        jobs = bases.candidate_jobs(bases.missing(bases.plan(episode, CAFE_ONLY)), lib=CAFE_ONLY)
        self.assertEqual(set(jobs), {'lead-bed-asleep-top-bedroom', 'lead-bed-awake-bedroom', 'lead-bed-edge-bedroom'})
        for j in jobs.values():
            self.assertEqual(len(j['seeds']), 2)
            self.assertIn('04-waiting.png', j['images'])
            self.assertIn('No text', j['prompt'])

    def test_a_forced_base_in_shot_layers_wins(self):
        episode, _ = store.load('why-youre-always-tired-after-8-hours-of-sleep')
        episode.beats[0].shot.layers.append('base:lead-standing')
        self.assertEqual(bases.plan(episode)[0]['base'], 'lead-standing')

    def test_approval_enters_the_library_and_then_matches(self):
        lib = {}
        desc = dict(setting='bedroom', pose='bed-edge', view='eye-level', cast=['lead'])
        bases.approve(lib, 'lead-bed-edge-bedroom', 'renders/bases/flux/cand-bed-edge-s101.png', desc, 'test')
        self.assertEqual(lib['lead-bed-edge-bedroom']['status'], 'approved')
        self.assertEqual(bases.match(desc, lib), 'lead-bed-edge-bedroom')
        self.assertEqual(bases.match(desc | dict(view='top-down'), lib), 'lead-bed-edge-bedroom')   # view falls back
        self.assertIsNone(bases.match(desc | dict(pose='bed-awake'), lib))


class StagingPlanTest(unittest.TestCase):
    """Script v3 beats are planned from their staging fields, not the prose."""
    def _beat(self, **kw):
        from pipeline.models import Beat
        from pipeline_fixtures import beat, staging
        b = beat('setup', 'Payday feels huge.', 'Something that mentions a bed and a quilt and a pillow by mistake.',
                 [('push_in', '', '')], **kw)
        return Beat.model_validate(dict(id='b02', **b))

    def test_staging_wins_over_keywords(self):
        from pipeline_fixtures import staging
        b = self._beat(stage=staging(('lead',), 'cafe', 'seated-at-table'))
        d = bases.describe(b)
        self.assertEqual((d['setting'], d['pose'], d['source']), ('cafe', 'seated-at-table', 'staging'))
        self.assertEqual(bases.match(d, CAFE_ONLY), 'lead-at-table')

    def test_two_characters_in_different_poses_take_one_base_each(self):
        from pipeline_fixtures import staging
        st = staging(('lead', 'friend'), 'cafe', 'seated-at-table')
        st['poses'][0]['pose'] = 'standing'
        d = bases.describe(self._beat(cast=('lead', 'friend'), stage=st))
        self.assertEqual(d['pose'], 'mixed')
        self.assertEqual(bases.match(d, CAFE_ONLY), 'friend-at-table + lead-standing')
        self.assertEqual(bases.wanted_name(d), 'friend-seated-at-table + lead-standing')

    def test_an_other_setting_becomes_a_new_wanted_base(self):
        from pipeline_fixtures import staging
        d = bases.describe(self._beat(stage=staging(('lead',), 'other', 'standing', detail='A rooftop at night')))
        self.assertEqual(d['setting'], 'a-rooftop-at-night')
        self.assertIsNone(bases.match(d, CAFE_ONLY))
        rows = [dict(beat='b02', **d, base=None, wanted=bases.wanted_name(d))]
        self.assertEqual(list(bases.missing(rows)), ['lead-standing-a-rooftop-at-night'])
        self.assertIn('staging', bases.plan_view(rows))


if __name__ == '__main__':
    unittest.main()


class RedoRoundTest(unittest.TestCase):
    def test_new_candidates_use_the_approved_bases_of_the_same_setting_as_references(self):
        lib = {}
        edge = dict(setting='bedroom', pose='bed-edge', view='eye-level', cast=['lead'])
        bases.approve(lib, 'lead-bed-edge-bedroom', 'renders/bases/flux/cand-bed-edge-s101.png', edge, 'test')
        awake = dict(setting='bedroom', pose='bed-awake', view='eye-level', cast=['lead'], beats=['b03'])
        miss = {'lead-bed-awake-bedroom': awake}
        first = bases.candidate_jobs(miss, lib={})
        self.assertEqual(first['lead-bed-awake-bedroom']['images'], ['04-waiting.png', '01-alone.png'])
        redo = bases.candidate_jobs(miss, lib=lib,
                                    rejected={'lead-bed-awake-bedroom': ['renders/bases/flux/cand-bed-awake-s121.png',
                                                                         'renders/bases/flux/cand-bed-awake-s122.png']},
                                    direction={'lead-bed-awake-bedroom': 'now LYING ON THEIR BACK looking up at the ceiling'})
        j = redo['lead-bed-awake-bedroom']
        self.assertEqual(j['round'], 2)
        self.assertEqual(j['job'], 'cand-bed-awake-r2')
        self.assertEqual(len(j['seeds']), 3)
        self.assertNotIn(121, j['seeds'])
        self.assertEqual(j['images'], ['cand-bed-edge-s101.png'])
        self.assertIn('LYING ON THEIR BACK looking up at the ceiling.', j['prompt'])
        self.assertIn('same bed, headboard, quilt pattern', j['prompt'])
        self.assertIn('No text', j['prompt'])


class PackPromptTest(unittest.TestCase):
    """Per-episode candidates reuse the starter pack's places and two-character prompts (docs/PLAN.md §7 D)."""

    def test_two_characters_are_prompted_together_in_the_pack_setting(self):
        from pipeline import basepack
        miss = {'friend-lead-seated-bench-street': dict(setting='street', pose='seated-bench', view='eye-level', cast=['friend', 'lead'], beats=['b07'])}
        j = bases.candidate_jobs(miss, lib={})['friend-lead-seated-bench-street']
        self.assertEqual(j['images'], ['03-together.png'])
        self.assertIn('two characters', j['prompt'])
        self.assertIn('both now SITTING ON THE BENCH', j['prompt'])
        self.assertIn(basepack.PLACES['street'][:40], j['prompt'])
        self.assertTrue(j['job'].startswith('cand-together-seated-bench-street-p'))

    def test_one_character_in_a_pack_setting_gets_the_pack_pose_and_place(self):
        from pipeline import basepack
        miss = {'lead-seated-sofa-living-room': dict(setting='living-room', pose='seated-sofa', view='eye-level', cast=['lead'], beats=['b01'])}
        j = bases.candidate_jobs(miss, lib={})['lead-seated-sofa-living-room']
        self.assertEqual(j['images'], ['04-waiting.png', '01-alone.png'])
        self.assertIn('SITTING ON THE SOFA', j['prompt'])
        self.assertIn(basepack.PLACES['living-room'][:30], j['prompt'])

    def test_a_changed_prompt_changes_the_job_name(self):
        miss = {'lead-standing-kitchen': dict(setting='kitchen', pose='standing', view='eye-level', cast=['lead'], beats=['b04'])}
        a = bases.candidate_jobs(miss, lib={})['lead-standing-kitchen']['job']
        b = bases.candidate_jobs(miss, lib={}, direction={'lead-standing-kitchen': 'now STANDING at the sink'})['lead-standing-kitchen']['job']
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith('cand-standing-kitchen-p') and b.startswith('cand-standing-kitchen-p'))

    def test_the_pack_covers_matching_descriptions_and_inserts(self):
        from pipeline import basepack
        self.assertEqual(basepack.covers(dict(setting='kitchen', pose='standing', cast=['friend', 'lead'])), 'together-kitchen')
        self.assertEqual(basepack.covers(dict(setting='street', pose='standing', cast=['friend'])), 'friend-standing-street')
        self.assertEqual(basepack.covers(dict(setting='living-room', pose='insert', cast=[])), 'lead-seated-sofa-living-room')
        self.assertIsNone(basepack.covers(dict(setting='living-room', pose='standing', cast=['lead'])))
        self.assertIsNone(basepack.covers(dict(setting='bedroom', pose='bed-edge', cast=['lead'])))
