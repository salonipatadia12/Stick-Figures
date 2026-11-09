import unittest

from pipeline import basebuild, bases, models


class PlateJobsTest(unittest.TestCase):
    def test_two_jobs_per_base_from_its_approved_candidate(self):
        entry = dict(status='approved', setting='kitchen', pose='standing', view='eye-level', cast=['friend', 'lead'],
                     source='renders/bases/flux/pack-kitchen-together-s554.png')
        jobs = basebuild.plate_jobs('together-kitchen', entry)
        self.assertEqual(set(jobs), {'plate-together-kitchen-arms', 'plate-together-kitchen-empty'})
        for j in jobs.values():
            self.assertEqual(j['images'], ['pack-kitchen-together-s554.png'])
            self.assertIn('Do not redraw', j['prompt'])
        self.assertEqual(len(jobs['plate-together-kitchen-arms']['seeds']), 2)
        self.assertIn("both standing characters' navy arms", jobs['plate-together-kitchen-arms']['prompt'])
        self.assertIn('the teal scarf', jobs['plate-together-kitchen-empty']['prompt'])

    def test_one_character_without_a_scarf(self):
        entry = dict(status='approved', setting='street', pose='standing', view='eye-level', cast=['lead'], source='renders/bases/flux/x.png')
        j = basebuild.plate_jobs('lead-standing-street', entry)['plate-lead-standing-street-arms']
        self.assertIn("the standing character's navy arms", j['prompt'])
        self.assertNotIn('scarf', j['prompt'])

    def test_only_approved_bases_are_to_build(self):
        lib = {'a': dict(status='built'), 'b': dict(status='approved'), 'c': dict(status='candidate')}
        self.assertEqual(list(basebuild.to_build(lib)), ['b'])


class MatchTest(unittest.TestCase):
    def test_a_two_character_beat_never_takes_another_pose_of_the_room(self):
        lib = {'lead-standing-kitchen': dict(status='approved', setting='kitchen', pose='standing', view='eye-level', cast=['lead']),
               'friend-standing-kitchen': dict(status='approved', setting='kitchen', pose='standing', view='eye-level', cast=['friend'])}
        desc = dict(setting='kitchen', pose='seated-at-table', view='eye-level', cast=['friend', 'lead'])
        self.assertIsNone(bases.match(desc, lib))

    def test_an_explicit_stand_in_pose_is_allowed(self):
        lib = {'lead-standing': dict(status='built', setting='cafe', pose='standing', view='eye-level', cast=['lead']),
               'friend-at-table': dict(status='built', setting='cafe', pose='seated-at-table', view='eye-level', cast=['friend'], serves_poses=['standing'])}
        desc = dict(setting='cafe', pose='standing', view='eye-level', cast=['friend', 'lead'])
        self.assertEqual(bases.match(desc, lib), 'friend-at-table + lead-standing')

    def test_vocab_has_the_doorway_setting_and_passenger_pose(self):
        from typing import get_args
        self.assertIn('doorway', get_args(models.Setting))
        self.assertIn('passenger', get_args(models.PoseName))
        self.assertIn('doorway', bases.SETTINGS)


if __name__ == '__main__':
    unittest.main()


class SfxVocabularyTest(unittest.TestCase):
    def test_every_script_sfx_name_has_a_sound(self):
        from typing import get_args
        from pipeline import sfx
        import numpy as np
        rng = np.random.default_rng(1)
        for name in get_args(models.SfxName):
            self.assertIn(name, sfx.SFX)
            self.assertGreater(len(sfx.SFX[name](rng)), 100)
