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



class HostRenderTest(unittest.TestCase):
    def test_lip_sync_opens_only_while_a_word_is_spoken(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/motion_test'))
        import compose_beats as CB
        starts = [1.0, 1.3, 2.5]
        self.assertEqual(CB.talk_curve(starts, 0.5), 0.0)
        self.assertGreater(CB.talk_curve(starts, 1.08), 0.3)
        self.assertEqual(CB.talk_curve(starts, 2.2), 0.0)                 # a pause between words: closed

    def test_new_host_verbs_are_in_the_library(self):
        from pipeline.rig import verbs as V
        for v in ('shiver', 'look_up', 'snap', 'freeze'):
            self.assertIn(v, V.VERBS)
            self.assertIn(v, V.IN_PLACE)



def _cb():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/motion_test'))
    import compose_beats as CB
    return CB


class HostFixesTest(unittest.TestCase):
    """User 2026-11-09: white host, fully in frame, no blank diagram starts, one host look, acting on every line,
    captions never on the host."""

    def test_every_clause_gets_a_gesture_and_no_long_still_stretch(self):
        CB = _cb()
        words = 'Wait... why did I come in here? Let me show you what your brain just did, and why.'.split()
        T = {w.strip('.?,').lower() + ('' if i == 0 else f'#{i}'): 0.4 * i for i, w in enumerate(words)}
        acts = CB.acting(' '.join(words), T, 0.0, 0.4 * len(words) + 0.5, busy=[])
        verbs = [v for v, _, _ in acts]
        self.assertEqual(verbs[0], 'step_back')                     # "Wait..." is a surprise
        self.assertIn('shrug', verbs)                               # the question
        for (_, a, d), (_, b, _) in zip(acts, acts[1:]):
            self.assertLess(b - (a + d), CB.FILL_GAP + 0.4)
        self.assertTrue(all(v1 != v2 for v1, v2 in zip(verbs, verbs[1:])))

    def test_the_light_match_keeps_the_host_white(self):
        CB = _cb()
        from PIL import Image
        sp = Image.new('RGBA', (200, 400), (0, 0, 0, 0))
        sp.paste((245, 245, 245, 255), (40, 40, 160, 360))
        for scene in ((200, 40, 30), (20, 30, 90), (240, 150, 40)):     # red sunset, night, orange glow
            frame = Image.new('RGBA', (1080, 1920), scene + (255,))
            _, ratio = CB.light_match(sp, frame, (0, 0, 200, 400))
            self.assertTrue(all(abs(r - 1) <= 0.15 for r in ratio), (scene, ratio))
            self.assertLess(max(ratio) / min(ratio), 1.10, (scene, ratio))      # a tint, not a colour cast

    def test_captions_slide_off_the_host(self):
        from pipeline import captions as C
        line = C.Line([C.Word(w, 1.0 + i * 0.2, 1.2 + i * 0.2) for i, w in enumerate('So why red sunsets'.split())], 1.0, 2.0)
        host = (40, 1000, 330, 1790)                                  # a host standing at the left

        def avoid(box):
            x0, y0, x1, y1 = max(box[0], host[0]), max(box[1], host[1]), min(box[2], host[2]), min(box[3], host[3])
            return max(0, x1 - x0) * max(0, y1 - y0)
        from PIL import Image
        out = C.draw(Image.new('RGBA', (1080, 1920)), line, 1.1, avoid=avoid)
        self.assertEqual(out['on_host'], 0)
        self.assertGreaterEqual(out['font_size'], C.SIZE - 6)

    def test_qa_fails_a_host_cut_by_the_frame_or_tinted(self):
        from pipeline import qa
        ok = dict(box=[10, 900, 300, 1800], pop_in=False, in_frame=True, body_rgb=[1.0, 0.96, 0.95])
        cut = dict(ok, in_frame=False)
        pink = dict(ok, body_rgb=[1.0, 0.8, 0.82])
        pop = dict(ok, pop_in=True, in_frame=False)
        self.assertTrue(qa.host_check([dict(t=0.0, host=[ok]), dict(t=0.1, host=[pop])])['pass_'])
        self.assertFalse(qa.host_check([dict(t=0.0, host=[cut])])['pass_'])
        self.assertFalse(qa.host_check([dict(t=0.0, host=[pink])])['pass_'])

    def test_blank_paper_is_near_empty_and_a_diagram_is_not(self):
        import numpy as np
        from pipeline import qa
        from pipeline.rig.graphics import Graphic
        blank = Graphic('bars', ['Doorway', 'One room'], [9.0, 9.5], '5 | 3', 0.0, 3.0).frame(0.1)
        drawn = Graphic('bars', ['Doorway', 'One room'], [-2.0, 9.5], '5 | 3', 0.0, 3.0).frame(0.1)
        small = lambda im: np.asarray(im.convert('RGB').resize((270, 480))).astype(np.float32)
        self.assertLess(qa.coverage(small(blank)), qa.FILL_MIN)
        self.assertGreater(qa.coverage(small(drawn)), qa.FILL_MIN)

    def test_a_diagram_shows_its_first_item_from_the_first_frame(self):
        CB = _cb()
        spec = dict(id='b04', narration='Your mind ends one scene there, and files it away.', on_screen_text='', sfx=[],
                    shot=dict(visual='graphic', motion=[], host=dict(where='off'),
                              graphic=dict(kind='flow', items=['Scene one', 'Doorway'], anchors=['scene', 'files'], detail='')))
        cls = CB.make_visual_class(spec)
        T = {'your': 9.4, 'mind': 9.6, 'ends': 9.9, 'one': 10.1, 'scene': 10.3, 'there': 10.6, 'and': 11.0, 'files': 11.2}
        shot = cls(T, 9.3, 12.3)
        self.assertLess(shot.graphic.times[0], 9.3)
        self.assertGreater(shot.graphic.times[1], 9.3)


if __name__ == '__main__':
    unittest.main()
