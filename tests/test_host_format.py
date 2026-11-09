"""Script v5, the host format (user 2026-11-08): hook -> promise -> story -> payoff -> loop, three hook options."""
import unittest

from pipeline.models import Beat, Episode, ScriptDraft
from pipeline_fixtures import beat, salary_draft, staging


def host(where='scene'):
    return dict(where=where, side='centre', doing='stands in the scene looking around', to_camera=True)


def hosted(role, narration, **kw):
    b = beat(role, narration, 'The host in the scene.', [('push_in', '', '')], sources=['s1'], **kw)
    b['shot']['host'] = host()
    return b


def episode(hook_line='Why is it snowing in June?', loop='So next June, remember why it snowed.'):
    hook = hosted('hook', hook_line)
    beats = [hook, hosted('promise', 'Let me show you what happened that year.'),
             hosted('story', 'A volcano erupted on the other side of the world.'),
             hosted('story', 'Its ash and sulfur spread high into the sky.'),
             hosted('story', 'The haze dimmed the sunlight for a whole year.'),
             hosted('payoff', 'So the summer never warmed up and it snowed.'),
             hosted('loop', loop)]
    d = salary_draft()
    d['beats'] = beats
    d['hook_options'] = [dict(line=hook_line, frame_one='The host in a t-shirt beside snow on summer roses.',
                              host_doing='shivers in a t-shirt', scene_key='snowy-summer-garden', scene='summer roses under snow',
                              answer='A volcano dimmed the sun.', loop_line=loop)] * 3
    d = ScriptDraft.model_validate(d)
    return Episode(slug='t', topic='test', beats=[Beat.from_draft(i, b) for i, b in enumerate(d.beats)], title=d.title,
                   hook_promise=d.hook_promise, hook_options=d.hook_options, subject_settings=d.subject_settings)


def messages(ep):
    return ' | '.join(str(i) for i in ep.check(cast_ids=('lead', 'friend')))


class HostFormatTest(unittest.TestCase):
    def test_a_well_formed_host_script_has_no_format_errors(self):
        out = messages(episode())
        for bad in ('order must be', 'hook option', 'frame 1 must', 'shot.host is missing', 'words; at most'):
            self.assertNotIn(bad, out)

    def test_long_hooks_and_missing_loops_are_caught(self):
        out = messages(episode(hook_line='Why on earth would it ever be snowing here in June?', loop='Thanks for watching.'))
        self.assertIn('words; at most 8', out)
        self.assertIn('the last line should tie back', out)

    def test_picking_a_hook_swaps_beat_one_and_the_loop_line(self):
        ep = episode()
        before = ep.script_digest()
        ep.hook_options[1] = ep.hook_options[1].model_copy(update=dict(line='It snowed in June. Really.', loop_line='Snow in June, remember?'))
        ep.pick_hook(2)
        self.assertEqual(ep.beats[0].shot.insert.key, 'snowy-summer-garden')
        self.assertEqual(ep.beats[0].narration, 'It snowed in June. Really.')
        self.assertEqual(ep.beats[-1].narration, 'Snow in June, remember?')
        self.assertEqual(ep.hook_pick, 2)
        self.assertNotEqual(ep.script_digest(), before)

    def test_old_scripts_keep_their_digest_and_rules(self):
        d = ScriptDraft.model_validate(salary_draft())
        ep = Episode(slug='t', topic='test', beats=[Beat.from_draft(i, b) for i, b in enumerate(d.beats)], title=d.title,
                     hook_promise=d.hook_promise)
        self.assertFalse(ep.host_format)
        self.assertNotIn('hook option', messages(ep))


if __name__ == '__main__':
    unittest.main()
