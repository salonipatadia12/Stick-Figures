"""Explainer inserts, graphics, the subject-on-screen score and the text-placement QA (docs/PLAN.md §7 H)."""
import sys
import unittest
from pathlib import Path

from PIL import Image

from pipeline import overlays as O
from pipeline import qa
from pipeline.models import Episode, ScriptDraft, Beat
from pipeline_fixtures import beat, salary_draft, staging

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/motion_test'))


def episode_with(*beats_):
    draft = salary_draft()
    draft['beats'] = list(beats_)
    d = ScriptDraft.model_validate(draft)
    return Episode(slug='t', topic='test topic', beats=[Beat.from_draft(i, b) for i, b in enumerate(d.beats)],
                   title=d.title, hook_promise=d.hook_promise)


def messages(ep):
    return [str(i) for i in ep.check(cast_ids=('lead', 'friend'))]


class VisualRulesTest(unittest.TestCase):
    def test_an_insert_needs_its_scene_and_spoken_anchors(self):
        b = beat('hook', 'The volcano erupted.', 'A volcano.', [('push_in', '', '')], cast=(), stage=staging(()), kind='insert',
                 insert=dict(key='volcano', scene='a volcano erupting at dusk',
                             effects=[dict(kind='shake', anchor='exploded', at='', detail='')]))
        out = ' | '.join(messages(episode_with(b)))
        self.assertIn('insert effect shake anchor "exploded" is not in the narration', out)

    def test_numbers_drawn_on_screen_must_be_said(self):
        b = beat('reveal', 'Temperatures fell by half a degree in 1816.', 'Two years compared.',
                 [('split_compare', 'fell', '1815 | 1816')], sources=['s1'])
        out = ' | '.join(messages(episode_with(b)))
        self.assertIn('"1815" is drawn on screen', out)
        self.assertNotIn('"1816" is drawn', out)

    def test_insert_scenes_with_people_are_flagged(self):
        b = beat('hook', 'Frost hit the farm.', 'A frosted farm.', [('push_in', '', '')], cast=(), stage=staging(()), kind='insert',
                 insert=dict(key='farm', scene='a frosted farm with farmers looking at the fields', effects=[]))
        self.assertIn('inserts are painted with no characters', ' | '.join(messages(episode_with(b))))

    def test_a_reaction_cut_needs_the_cast(self):
        b = beat('hook', 'Frost hit the farm in May.', 'A frosted farm.', [('push_in', '', '')], cast=(), stage=staging(()),
                 kind='insert', reaction='May', insert=dict(key='farm', scene='a frosted farm', effects=[]))
        self.assertIn('reaction_anchor needs the reacting cast', ' | '.join(messages(episode_with(b))))

    def test_graphic_items_are_short_and_anchored(self):
        b = beat('setup', 'Short waves scatter more than long ones.', 'Two waves.', [('push_in', '', '')], cast=(), stage=staging(()),
                 kind='graphic', graphic=dict(kind='waves', items=['Short waves scatter a lot', 'Long'], anchors=['Short', 'tiny'], detail=''))
        out = ' | '.join(messages(episode_with(b)))
        self.assertIn('over 3 words', out)
        self.assertIn('graphic anchor "tiny" is not in the narration', out)

    def test_v4_fields_at_defaults_do_not_change_old_digests(self):
        ep = episode_with(*salary_draft()['beats'])
        before = ep.script_digest()
        for b in ep.beats:
            b.shot.visual = 'character'
        self.assertEqual(ep.script_digest(), before)
        ep.beats[0].shot.visual = 'insert'
        self.assertNotEqual(ep.script_digest(), before)


class GraphicsTest(unittest.TestCase):
    def test_every_kind_draws_a_frame_with_readable_labels_in_the_safe_area(self):
        from pipeline.rig.graphics import Graphic
        cases = [('flow', ['Eruption', 'Sulfur veil', 'Cold summer'], ''), ('bounce', ['Sunlight', 'Sulfur veil', 'Back to space'], ''),
                 ('scatter', ['Sunlight', 'Air molecules'], ''), ('waves', ['Short: blue', 'Long: red'], ''),
                 ('two_paths', ['Short path', 'Long path'], 'both'), ('bars', ['Same room', 'Through a doorway'], '2 | 4'),
                 ('thermometer', ['1816'], 'falls'), ('cards', ['Old room: purpose', 'New room'], ''), ('layers', ['Haze', 'Air', 'Ground'], '')]
        for kind, items, detail in cases:
            with self.subTest(kind=kind):
                O.take_text_log()
                g = Graphic(kind, items, [0.3 + 0.5 * k for k in range(len(items))], detail, 0.0, 4.0)
                frame = g.frame(3.5)
                self.assertEqual(frame.size, (1080, 1920))
                log = O.take_text_log()
                self.assertTrue(log, 'no label logged')
                for d in log:
                    self.assertGreaterEqual(d['em'], 40)
                    self.assertGreaterEqual(d['box'][0], 40)
                    self.assertLessEqual(d['box'][2], 1040)
                    self.assertLessEqual(d['box'][3], 1300)          # above the captions


class InsertSceneTest(unittest.TestCase):
    def test_every_effect_renders_deterministically(self):
        from pipeline.rig.inserts import Effect, InsertScene
        plate = Image.new('RGB', (944, 1680), (120, 160, 200))
        variant = Image.new('RGB', (944, 1680), (220, 120, 80))
        fx = [Effect('push', None), Effect('ash', None, seed=2), Effect('haze', 1.0, 'sky'), Effect('grade', 1.0, '', 'blue -> red'),
              Effect('sun_path', None, '', 'high -> low'), Effect('crossfade', 2.0, '', 'later'), Effect('label', 1.0, 'sky', 'Sulfur'),
              Effect('embers', 0.5, 'peak'), Effect('shake', 1.0), Effect('arrow', 1.5, '', 'top -> centre')]
        scene = InsertScene(plate, fx, 0.0, 4.0, {'later': variant})
        a = scene.frame(3.0)
        b = scene.frame(3.0)
        self.assertEqual(a.size, (1080, 1920))
        self.assertEqual(a.tobytes(), b.tobytes())
        O.take_text_log()
        scene.frame(3.0)
        self.assertTrue(any(d['label'] == 'Sulfur' for d in O.take_text_log()))
        late = scene.frame(3.9).convert('RGB').getpixel((540, 1500))
        self.assertGreater(late[0], late[2])                           # crossfaded to the warm variant


class ComposerVisualsTest(unittest.TestCase):
    def test_subject_on_screen_counts_inserts_graphics_and_reaction_splits(self):
        import compose_beats as CB
        c = CB.for_episode('why-you-forget-why-you-walked-into-a-room')
        self.assertIn('b01a', c.BEATS)                                  # insert, then the reaction on "gone"
        self.assertIn('b01b', c.BEATS)
        self.assertTrue(c.BEATS['b03'].__name__.startswith('GraphicShot'))
        beats = []
        at = 0.0
        for bid in [f'b{i:02d}' for i in range(1, 11)]:
            words = {'b01': [('Why', at + 0.1), ('gone', at + 3.0)], 'b04': [('A', at + 0.1), ('old', at + 3.2)]}.get(bid, [('x', at + 0.1)])
            beats.append(dict(id=bid, words=words, shot_start=at, shot_end=at + 4.0))
            at += 4.0
        shots = c.shots(dict(beats=beats))
        self.assertIn(('b01a', 0.0, 2.95), [(n, a, round(b, 2)) for n, a, b in shots])
        s = c.library_summary()['subject']
        self.assertEqual(s['subject_beats'], 7)                       # b01 b02 b03 b04 b05 b07 b09
        self.assertEqual(s['subject_pct'], 70)


class TextPlacementQaTest(unittest.TestCase):
    def test_overlapping_text_and_text_on_a_body_fail(self):
        frames = [dict(t=1.0, em=60, label='a', box=[100, 250, 600, 350], box_label='a',
                       items=[[100, 250, 600, 350, 'Longer path, more red'], [300, 280, 900, 360, 'short path | long path']],
                       chars=[]),
                  dict(t=2.0, em=60, label='b', box=[200, 900, 700, 1000], box_label='b', items=[[200, 900, 700, 1000, 'Mostly red']],
                       chars=[[150, 500, 800, 1700]])]
        r = qa.text_check(frames, [])
        self.assertFalse(r['pass_'])
        self.assertEqual(r['overlapping_text'][0]['texts'], 'Longer path, more red / short path | long path')
        self.assertEqual(r['text_on_character'][0]['text'], 'Mostly red')

    def test_uk_spellings_and_close_proper_names_are_heard(self):
        heard = [dict(w=w) for w in 'in april 1815 tambura erupted 53 to 58 million tons'.split()]
        r = qa.words_check('In April 1815, Tambora erupted 53 to 58 million tonnes'.split(), heard)
        self.assertEqual(r['missing'], [])


if __name__ == '__main__':
    unittest.main()
