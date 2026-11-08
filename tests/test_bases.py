"""The base-pose step: what each beat needs, what the library has, what is missing, and the approval."""
import unittest

from pipeline import bases, store


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
        rows = bases.plan(episode)
        miss = bases.missing(rows)
        self.assertEqual(sorted(miss), ['lead-bed-asleep-top-bedroom', 'lead-bed-awake-bedroom', 'lead-bed-edge-bedroom'])
        by_beat = {r['beat']: r['base'] for r in rows}
        self.assertEqual(by_beat['b04'], 'together')
        self.assertEqual(by_beat['b08'], 'lead-at-table')
        self.assertEqual(by_beat['b09'], 'together')

    def test_candidate_jobs_have_two_seeds_and_the_character_reference(self):
        episode, _ = store.load('why-youre-always-tired-after-8-hours-of-sleep')
        jobs = bases.candidate_jobs(bases.missing(bases.plan(episode)))
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


if __name__ == '__main__':
    unittest.main()
