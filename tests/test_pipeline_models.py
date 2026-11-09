import copy
import unittest

from anthropic import transform_schema

from pipeline.models import Beat, Episode, ScriptDraft, Source, beat_range, episode_json_schema
from pipeline_fixtures import salary_draft

CAST = ('lead', 'friend')


def episode(draft=None, **overrides):
    draft = draft or salary_draft()
    ep = Episode(slug='salary', topic='Why your salary disappears by the 20th', target_length=45,
                 title=draft['title'], hook_promise=draft['hook_promise'],
                 sources=[Source(id='s1', claim='Spending rises with income.', url='https://www.example.org/a')],
                 beats=[Beat.from_draft(i, ScriptDraft.model_validate(draft).beats[i]) for i in range(len(draft['beats']))])
    return ep.model_copy(update=overrides)


def errors(ep):
    return [i.message for i in ep.check(CAST) if i.severity == 'error']


def mutate(fn):
    draft = copy.deepcopy(salary_draft())
    fn(draft)
    return episode(draft)


class EpisodeRules(unittest.TestCase):
    def test_reference_draft_passes(self):
        self.assertEqual(errors(episode()), [])

    def test_beat_ids_are_stable_and_first_in_json(self):
        ep = episode()
        self.assertEqual([b.id for b in ep.beats][:3], ['b01', 'b02', 'b03'])
        self.assertEqual(next(iter(ep.model_dump()['beats'][0])), 'id')

    def test_on_screen_text_is_at_most_five_words(self):
        ep = mutate(lambda d: d['beats'][1].update(on_screen_text='this is far too many words'))
        self.assertTrue(any('on-screen text has 6 words' in e for e in errors(ep)))

    def test_hook_rejects_greetings_and_long_openers(self):
        ep = mutate(lambda d: d['beats'][0].update(narration='Hey guys, welcome back. Your salary leaked.'))
        self.assertTrue(any('greeting' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][0].update(
            narration='Have you ever noticed that your monthly salary somehow seems to vanish? It leaked.'))
        self.assertTrue(any('must land in ~2 s' in e for e in errors(ep)))

    def test_numbers_need_a_source_or_an_example_flag(self):
        ep = mutate(lambda d: d['beats'][5].update(illustrative_numbers=False))
        self.assertTrue(any('with no source' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][4].update(narration='Spending rises 38% with income.', source_ids=[]))
        self.assertTrue(any("['38%']" in e for e in errors(ep)))

    def test_ordinal_dates_are_not_statistics(self):
        ep = mutate(lambda d: d['beats'][1].update(narration='By the 20th the 1st feels far away. Permission granted.'))
        self.assertFalse(any('no source' in e for e in errors(ep)))

    def test_unknown_source_and_cast(self):
        ep = mutate(lambda d: d['beats'][4].update(source_ids=['s9']))
        self.assertTrue(any('unknown sources' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][1]['shot'].update(cast=['villain']))
        self.assertTrue(any('unknown cast' in e for e in errors(ep)))

    def test_motion_anchors_must_be_spoken(self):
        ep = mutate(lambda d: d['beats'][0]['shot']['motion'][0].update(anchor='vanished'))
        self.assertTrue(any('"vanished" is not in the narration' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][0]['shot']['motion'][0].update(anchor=''))
        self.assertTrue(any('needs an anchor' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][1]['shot'].update(pose_variants=[]))
        self.assertTrue(any('pose_swap needs' in e for e in errors(ep)))

    def test_one_or_two_motions_and_no_talking_heads(self):
        ep = mutate(lambda d: d['beats'][3]['shot'].update(motion=[]))
        self.assertTrue(any('0 motion recipes' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][2].update(visual_intent='The lead talks to the camera about coffee.'))
        self.assertTrue(any('talking head' in e for e in errors(ep)))

    def test_structure_hook_payoff_ending_and_beat_count(self):
        ep = mutate(lambda d: d['beats'][6].update(role='reveal'))
        self.assertTrue(any('no payoff' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][-1].update(role='reveal'))
        self.assertTrue(any('last beat must be the ending' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][-1].update(role='payoff'))     # a payoff may close the video: a warning only
        self.assertFalse(any('last beat must be the ending' in e for e in errors(ep)))
        ep = mutate(lambda d: d.update(beats=d['beats'][:2] + d['beats'][6:]))
        self.assertTrue(any('needs 7-14' in e for e in errors(ep)))
        self.assertEqual(beat_range(45), (7, 14))
        self.assertEqual(beat_range(20), (3, 6))

    def test_approval_digest_tracks_content_not_timing(self):
        ep = episode()
        before = ep.script_digest()
        ep.beats[0].duration = 2.4
        ep.beats[0].shot.layers = []
        self.assertEqual(before, ep.script_digest())
        ep.beats[0].narration = "Your salary didn't shrink. It escaped."
        self.assertNotEqual(before, ep.script_digest())

    def test_per_beat_digest_isolates_edits(self):
        ep = episode()
        digests = [ep.beat_digest(b) for b in ep.beats]
        ep.beats[3].narration = 'Subscriptions renew every month while you sleep.'
        after = [ep.beat_digest(b) for b in ep.beats]
        self.assertEqual([i for i, (a, b) in enumerate(zip(digests, after)) if a != b], [3])


def warnings(ep):
    return [i.message for i in ep.check(CAST) if i.severity == 'warning']


class StagingRules(unittest.TestCase):
    """Script v3: the staging block is checked against the shot, and what the rig cannot stage is flagged."""
    def test_reference_draft_stages_every_beat(self):
        ep = episode()
        self.assertTrue(all(b.shot.staging is not None for b in ep.beats))
        self.assertEqual(errors(ep), [])

    def test_poses_must_cover_the_cast_exactly(self):
        ep = mutate(lambda d: d['beats'][1]['shot']['staging']['poses'].append(dict(who='friend', pose='standing')))
        self.assertTrue(any('one pose per cast id' in e for e in errors(ep)))

    def test_action_anchor_must_be_spoken_and_actor_in_cast(self):
        ep = mutate(lambda d: d['beats'][1]['shot']['staging']['actions'].append(dict(who='lead', verb='shrug', target='', anchor='banana')))
        self.assertTrue(any('anchor "banana"' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][1]['shot']['staging']['actions'].append(dict(who='boss', verb='shrug', target='', anchor='Payday')))
        self.assertTrue(any('"boss"' in e for e in errors(ep)))

    def test_posture_verbs_and_far_walks_are_flagged_as_cuts(self):
        ep = mutate(lambda d: d['beats'][1]['shot']['staging']['actions'].append(dict(who='lead', verb='stand_up', target='', anchor='Payday')))
        self.assertTrue(any('match cut' in w for w in warnings(ep)))
        ep = mutate(lambda d: d['beats'][1]['shot']['staging']['actions'].append(dict(who='lead', verb='walk', target='across the room', anchor='Payday')))
        self.assertTrue(any('cannot be animated' in w for w in warnings(ep)))

    def test_crowds_animals_and_fast_action_are_flagged_with_simpler_staging(self):
        ep = mutate(lambda d: d['beats'][1].update(visual_intent='A crowd of commuters runs for the train while a dog barks.'))
        ws = ' '.join(warnings(ep))
        self.assertIn('crowd', ws)
        self.assertIn('animal', ws)
        self.assertIn('fast physical action', ws)
        self.assertIn('Suggest', ws)
        self.assertEqual([e for e in errors(ep) if 'crowd' in e], [], 'warnings, not errors: the user decides at the gate')

    def test_other_setting_needs_detail_and_warns_about_a_new_base(self):
        ep = mutate(lambda d: d['beats'][1]['shot']['staging'].update(setting='other', setting_detail=''))
        self.assertTrue(any('no setting_detail' in e for e in errors(ep)))
        ep = mutate(lambda d: d['beats'][1]['shot']['staging'].update(setting='other', setting_detail='a rooftop at night'))
        self.assertTrue(any('bases gate' in w for w in warnings(ep)))

    def test_episodes_written_before_v3_load_without_staging(self):
        d = copy.deepcopy(salary_draft())
        for b in d['beats']:
            del b['shot']['staging']
        ep = Episode(slug='old', topic='An older episode', beats=[Beat.model_validate(dict(id=f'b{i + 1:02d}', **b)) for i, b in enumerate(d['beats'])])
        self.assertTrue(all(b.shot.staging is None for b in ep.beats))
        with self.assertRaises(Exception):
            ScriptDraft.model_validate(d)                    # but Claude's draft must always carry it


class Schemas(unittest.TestCase):
    def test_structured_output_schema_is_api_compatible(self):
        from pipeline.models import wire_schema
        schema = wire_schema(transform_schema(ScriptDraft))     # what is sent: every property required

        def objects(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    yield node
                for v in node.values():
                    yield from objects(v)
            elif isinstance(node, list):
                for v in node:
                    yield from objects(v)
        found = list(objects(schema))
        self.assertGreater(len(found), 3)
        for node in found:
            self.assertIs(node.get('additionalProperties'), False)
            self.assertEqual(sorted(node.get('required', [])), sorted(node.get('properties', {})))

    def test_episode_schema_documents_beats(self):
        schema = episode_json_schema()
        self.assertIn('beats', schema['properties'])
        self.assertIn('Beat', schema['$defs'])


if __name__ == '__main__':
    unittest.main()
