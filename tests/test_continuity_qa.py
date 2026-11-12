"""QA continuity (kaomei, user 2026-11-11): a warning, never a failure, when adjacent beats change place on a hard cut
with no named connection. Also: the field never shifts an existing episode's digests."""
import json
import unittest

from pipeline import qa
from pipeline.models import Beat, Episode, ScriptDraft
from pipeline_fixtures import salary_draft


def beat(i, place, continuity='', transition='cut', insert=None):
    shot = dict(visual='insert' if insert else 'character', transition=transition, continuity=continuity,
                staging=dict(setting=place, setting_detail=''), insert=dict(key=insert) if insert else None)
    return dict(id=f'b{i:02d}', shot=shot)


def episode(beats, version='script-v7'):
    return dict(beats=beats, meta=dict(script=dict(prompt_version=version)))


class ContinuityReport(unittest.TestCase):
    def test_a_place_change_on_a_hard_cut_with_no_connection_is_flagged(self):
        r = qa.continuity_report(episode([beat(1, 'cafe'), beat(2, 'street')]))
        self.assertEqual(len(r['flagged']), 1)
        self.assertEqual(r['flagged'][0]['beats'], 'b01 -> b02')
        self.assertIn('setting:cafe -> setting:street', r['warnings'][0])
        self.assertNotIn('pass_', r)                       # a warning: never part of the pass/fail summary

    def test_named_connection_whip_or_same_place_pass(self):
        cases = [
            [beat(1, 'cafe', continuity='the host points out of the window; b02 opens on the street'), beat(2, 'street')],
            [beat(1, 'cafe'), beat(2, 'street', transition='whip')],
            [beat(1, 'cafe'), beat(2, 'street', transition='match_cut')],
            [beat(1, 'cafe'), beat(2, 'cafe')],
            [beat(1, 'other', insert='volcano-erupting'), beat(2, 'other', insert='volcano-erupting')],
        ]
        for beats in cases:
            with self.subTest(beats=beats):
                self.assertEqual(qa.continuity_report(episode(beats))['flagged'], [])

    def test_insert_scenes_are_places_by_key(self):
        r = qa.continuity_report(episode([beat(1, 'other', insert='volcano-erupting'), beat(2, 'other', insert='frozen-farm')]))
        self.assertEqual(len(r['flagged']), 1)
        self.assertIn('scene:volcano-erupting -> scene:frozen-farm', r['warnings'][0])

    def test_counts_and_note(self):
        beats = [beat(1, 'cafe', continuity='held cup'), beat(2, 'street'), beat(3, 'park'), beat(4, 'park')]
        r = qa.continuity_report(episode(beats))
        self.assertEqual((r['pairs'], r['named'], len(r['flagged'])), (3, 1, 1))
        self.assertEqual(r['note'], '1 of 3 cuts change place with no named connection')

    def test_scripts_written_before_the_rule_are_not_checked(self):
        r = qa.continuity_report(episode([beat(1, 'cafe'), beat(2, 'street')], version='script-v6'))
        self.assertEqual(r['flagged'], [])
        self.assertIn('before the continuity rule', r['note'])
        r = qa.continuity_report(episode([beat(1, 'cafe', continuity='x'), beat(2, 'street'), beat(3, 'park')], 'script-v6'))
        self.assertEqual(len(r['flagged']), 1)             # a v6 episode edited to name links is checked

    def test_empty_and_staging_less_episodes(self):
        self.assertEqual(qa.continuity_report({})['flagged'], [])
        self.assertEqual(qa.continuity_report(episode([dict(id='b01', shot={}), dict(id='b02', shot={})]))['flagged'], [])


class DigestStability(unittest.TestCase):
    def test_an_empty_continuity_leaves_digests_as_they_were(self):
        draft = ScriptDraft.model_validate(salary_draft())
        ep = Episode(slug='salary', topic='Why your salary disappears', beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)])
        before_script, before_beat = ep.script_digest(), ep.beat_digest(ep.beats[0])
        dumped = json.loads(ep.model_dump_json())
        for b in dumped['beats']:
            self.assertEqual(b['shot']['continuity'], '')
            b['shot'].pop('continuity')                     # an episode.json written before the field existed
        old = Episode.model_validate(dumped)
        self.assertEqual(old.script_digest(), before_script)
        self.assertEqual(old.beat_digest(old.beats[0]), before_beat)
        ep.beats[0].shot.continuity = 'the jar slides to the right; b02 opens on it'
        self.assertNotEqual(ep.script_digest(), before_script)    # a named link is content: approval binds to it
        self.assertNotEqual(ep.beat_digest(ep.beats[0]), before_beat)


if __name__ == '__main__':
    unittest.main()
