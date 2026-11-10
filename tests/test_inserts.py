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

    def test_two_paths_is_only_for_light(self):
        # a debt script drew its "long vs short path" as the sunset diagram (sun, Earth, an eye)
        debt = beat('reveal', 'The minimum takes eight more years.', 'The long path against the short one.', [('push_in', '', '')],
                    cast=(), stage=staging(()), kind='graphic', graphic=dict(kind='two_paths', items=['Eight more years'],
                                                                             anchors=['eight'], detail='both'))
        self.assertIn('two_paths draws sunlight', ' | '.join(messages(episode_with(debt))))
        sky = beat('reveal', 'At sunset the light crosses more air.', 'Two light paths.', [('push_in', '', '')],
                   cast=(), stage=staging(()), kind='graphic', graphic=dict(kind='two_paths', items=['Long path'],
                                                                            anchors=['light'], detail='both'))
        self.assertNotIn('two_paths draws sunlight', ' | '.join(messages(episode_with(sky))))

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
                    self.assertGreaterEqual(d["box"][0], 60)
                    self.assertLessEqual(d["box"][2], 930)          # the safe area's right edge
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
        import json, shutil
        import compose_beats as CB
        slug = 'zz-test-doorway-v4'                                    # the v4 doorway script, frozen as a fixture
        d = ROOT / 'episodes' / slug
        d.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, d, True)
        ep = json.loads((ROOT / 'tests/fixtures/doorway-v4-episode.json').read_text())
        ep['slug'] = slug
        (d / 'episode.json').write_text(json.dumps(ep))
        c = CB.for_episode(slug)
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


class TimingTest(unittest.TestCase):
    """Items anchored late in a shot land early enough to read (user review 2026-11-08)."""

    def test_settle_keeps_order_and_leaves_later_shots_alone(self):
        self.assertEqual(O.settle([41.05, 43.19, 44.73], 40.23, 45.0), [41.05, 43.19, 43.8])
        self.assertEqual(O.settle([None, 50.0, 10.0], 9.0, 12.0), [None, 50.0, 10.0])
        out = O.settle([9.6, 9.7, 9.9], 9.0, 10.0)                  # a very short shot: nothing before t0 + 0.1
        self.assertTrue(all(a >= 9.1 for a in out))

    def test_transitions_finish_before_the_cut(self):
        from pipeline.rig.inserts import RAMP, SETTLE, Effect, settle_effects
        fx = settle_effects([Effect('grade', 38.35, '', 'butterscotch -> mars-dusk'), Effect('label', 39.3, '', 'Dust'),
                             Effect('ash', None)], 34.1, 39.6)
        self.assertLessEqual(fx[0].at + RAMP['grade'], 39.6 - SETTLE + 1e-6)
        self.assertLessEqual(fx[1].at, 39.6 - O.MIN_HOLD + 1e-6)
        self.assertIsNone(fx[2].at)

    def test_on_screen_text_without_a_recipe_is_still_slammed(self):
        import compose_beats as CB
        spec = dict(on_screen_text='Only when overloaded', narration='One 2021 replication saw it only when memory was overloaded.',
                    shot=dict(motion=[dict(recipe='push_in', anchor='', detail='')]))
        T = {'only': 33.0, 'overloaded': 37.4}
        items = CB.finish_text_items(spec, lambda a, d=None: T.get(a, d if d is not None else 31.5), [], 31.5, 37.6)
        self.assertEqual(items, [('slam', 33.0, 'Only when overloaded')])

    def test_subscript_digits_are_drawn_not_boxes(self):
        a, b = O.text_layer('SO\u2082', 78), O.text_layer('SO2', 78)
        self.assertEqual(a.info['label'], 'SO\u2082')
        self.assertLess(a.width, b.width)                             # the 2 is drawn small


class TextTimingQaTest(unittest.TestCase):
    def test_brief_and_missing_texts_fail(self):
        frames = [dict(t=round(10 + k / 30, 3), em=60, label='x', items=[[100, 300, 500, 380, 'Sulfur veil']]) for k in range(40)]
        frames += [dict(t=round(11.5 + k / 30, 3), em=60, label='x', items=[[100, 300, 500, 380, 'Cold summer']]) for k in range(6)]
        frames += [dict(t=round(12 + k / 30, 3), em=60, label='x', items=[[100, 300, 500, 380, str(k * 1000)]]) for k in range(40)]
        promised = [dict(beat='b01', text='Sulfur veil'), dict(beat='b01', text='Cold summer'), dict(beat='b02', text='Frost in May')]
        r = qa.text_check(frames, [], promised=promised)
        self.assertFalse(r['pass_'])
        self.assertEqual([b['text'] for b in r['brief_text']], ['Cold summer'])     # a counting number is one text
        self.assertEqual(r['missing_text'], [dict(beat='b02', text='Frost in May')])

    def test_promised_texts_come_from_the_script(self):
        ep = dict(beats=[dict(id='b01', on_screen_text='Up to 105,000', shot=dict(motion=[dict(recipe='number_counter', detail='0 -> 105000')])),
                         dict(id='b02', on_screen_text='Frost in May', shot=dict(motion=[dict(recipe='push_in')],
                              graphic=dict(kind='cards', items=['Plan: last room']), insert=None))])
        self.assertEqual([p['text'] for p in qa.promised_texts(ep)], ['Frost in May', 'Plan', 'last room'])

    def test_a_dropped_article_does_not_skip_the_words_after_it(self):
        heard = [dict(w=w) for w in 'people forgot more after doorway than after crossing a room even after'.split()]
        r = qa.words_check('People forgot more after a doorway than after crossing a room. Even after'.split(), heard)
        self.assertEqual(r['missing'], [])

    def test_transcription_windows_cut_in_beat_pauses(self):
        tl = {'beats': [dict(words=[['x', t]]) for t in [0.1, 4.4, 10.2, 14.0, 17.7, 23.6, 27.7, 31.5, 37.6, 43.7]]}
        windows = qa.chunks(tl)
        self.assertEqual([round(a, 2) for a, _ in windows], [0.0, 17.55, 37.45])
        self.assertTrue(all(b - a <= qa.CHUNK_S for a, b in windows[:-1]))
        self.assertEqual(qa.chunks(None), [(0.0, 1e9)])

    def test_unshown_on_screen_text_is_a_script_warning(self):
        b = beat('setup', 'One study saw it only when memory was overloaded.', 'A tired lead.', [('push_in', '', '')], sources=['s1'])
        b['on_screen_text'] = 'Only when overloaded'
        self.assertIn('has no text_slam to show it', ' | '.join(messages(episode_with(b))))


if __name__ == '__main__':
    unittest.main()


class InsertRunTest(unittest.TestCase):
    def test_a_scene_that_failed_in_the_first_stage_still_draws_on_the_failed_sheet_after_the_variants(self):
        import tempfile
        from unittest import mock
        from pipeline import inserts
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            files = {}
            for name in ('a-1', 'a-2', 'v-1', 'v-2', 'ok-1', 'ok-2'):
                files[name] = str(tmp / f'{name}.png')
                Image.new('RGB', (90, 160), (200, 180, 150)).save(files[name])
            want = {'a': dict(scene='scene a', beats=['b01']), 'ok': dict(scene='scene ok', beats=['b02']),
                    'v': dict(scene='variant', beats=['b03'], base='ok')}
            found = iter([{'a': [files['a-1'], files['a-2']], 'ok': [files['ok-1'], files['ok-2']]},
                          {'v': [files['v-1'], files['v-2']]}])
            verdict = lambda ps: {p: dict(path=p, ok='ok-' in p, reasons=['too dark'], score=1, stats={}) for p in ps}
            with mock.patch.object(inserts, 'needed', return_value=want), \
                 mock.patch.object(inserts, 'jobs', side_effect=lambda part: {k: dict(prompt='p') for k in part}), \
                 mock.patch.object(inserts, 'render', side_effect=lambda jm, slug: next(found)), \
                 mock.patch.object(inserts, 'qa', side_effect=verdict), \
                 mock.patch.object(inserts, 'save_library'), \
                 mock.patch.object(inserts, 'DIR', tmp / 'lib'), \
                 mock.patch.object(inserts, 'ROOT', tmp), \
                 mock.patch.object(inserts, 'sheet', wraps=inserts.sheet) as sheet:
                (tmp / 'renders/inserts').mkdir(parents=True)
                result = inserts.run(mock.Mock(slug='t'), generate=True, lib={})
        self.assertEqual(set(result['failed']), {'a', 'v'})
        self.assertEqual(set(result['made']), {'ok'})
        self.assertTrue(result['sheet'].endswith('t-inserts-failed.jpg'))
        self.assertEqual(sheet.call_count, 2)

    def test_a_scene_in_space_is_not_asked_for_ground(self):
        from pipeline import inserts
        j = inserts.jobs({'s': dict(scene='A spacecraft drifting in open space, Earth behind it'),
                          'p': dict(scene='A sunlit village street at noon')})
        self.assertNotIn('open ground', j['s']['prompt'])
        self.assertIn('no ground', j['s']['prompt'])
        self.assertIn('open ground across the lower third', j['p']['prompt'])


class QaFalseFailTest(unittest.TestCase):
    def test_a_possessive_heard_without_its_apostrophe_and_a_counter_with_a_word(self):
        heard = [dict(w=w) for w in ('Mission', 'controls', 'fix')]
        self.assertEqual(qa.words_check(['Mission', "control's", 'fix'], heard)['missing'], [])
        frames = [dict(t=round(i / 30, 3), items=[[0, 0, 10, 10, f'{i} seconds']]) for i in range(36)]
        self.assertEqual(qa.brief_texts(frames), [])

    def test_a_pin_on_the_last_word_lands_before_the_cut(self):
        from pipeline.rig.inserts import Effect, settle_effects
        e = settle_effects([Effect('pin', 9.8, 'centre', 'Burn', seed=1)], 7.0, 10.0)[0]
        self.assertLessEqual(e.at, 10.0 - O.MIN_HOLD)


class LabelPlacementTest(unittest.TestCase):
    """Insert labels stay in the captions' safe area and off the host (finance b02/b09: a label on the host's head,
    "14.4% interest" past the phone's buttons)."""

    def test_label_moves_off_the_host_and_stays_in_the_safe_area(self):
        from pipeline.captions import SAFE
        from pipeline.rig.inserts import label_centre, LABEL_Y
        w, h = 420, 110
        host = (620, 560, 1000, 1700)                               # a large host on the right, head near the label row
        cx, cy = label_centre((w, h), 'right', [host])
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        self.assertGreaterEqual(box[0], SAFE['left'])
        self.assertLessEqual(box[2], SAFE['right'])
        self.assertGreaterEqual(box[1], SAFE['top'])
        self.assertFalse(min(box[2], host[2]) > max(box[0], host[0]) and min(box[3], host[3]) > max(box[1], host[1]))
        self.assertEqual(label_centre((w, h), 'right', []), (SAFE['right'] - w / 2, LABEL_Y['right']))

    def test_a_wide_label_is_kept_left_of_the_phone_buttons(self):
        from pipeline.captions import SAFE
        from pipeline.rig.inserts import label_centre
        cx, _ = label_centre((640, 110), 'right', [])
        self.assertLessEqual(cx + 320, SAFE['right'])

    def test_a_second_label_keeps_off_the_first(self):
        from pipeline.rig.inserts import label_centre
        w, h = 300, 100
        first = label_centre((w, h), 'right', [])
        box = (first[0] - w / 2, first[1] - h / 2, first[0] + w / 2, first[1] + h / 2)
        cx, cy = label_centre((w, h), 'right', [], [box])
        self.assertTrue(cy - h / 2 >= box[3] or cy + h / 2 <= box[1] or cx - w / 2 >= box[2] or cx + w / 2 <= box[0])
