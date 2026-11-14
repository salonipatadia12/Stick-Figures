"""The self-repair loop (pipeline/repair.py, 2026-11-14): each check's diagnosis and fix, the hints the composer applies,
the duration fix through a stand-in for the local model, the runner's rounds, and the measured speaking rate.
Nothing is voiced or rendered here."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import config, pacing, qa, repair, store  # noqa: E402
from pipeline.models import Source  # noqa: E402
from test_auto_and_qa_fixes import host_episode  # noqa: E402

TL = dict(beats=[dict(id='b01', start=0.35, end=2.3, words=[['Oil', 0.4], ["doesn't", 0.7]]),
                 dict(id='b02', start=2.5, end=6.8, words=[['Everyone', 2.7], ['imagines', 3.1], ['oil', 3.6]]),
                 dict(id='b03', start=7.0, end=9.9, words=[['But', 7.0], ['the', 7.1], ['timeline', 7.3]])])
EP = dict(beats=[dict(id='b01', narration="Oil doesn't", shot=dict(host=dict(side='right', size='medium'))),
                 dict(id='b02', narration='Everyone imagines oil', shot=dict(
                     host=dict(side='right', size='medium'), visual='insert',
                     insert=dict(key='museum', scene='a museum', effects=[dict(kind='label', anchor='', at='right', detail='The Myth')]))),
                 dict(id='b03', narration='But the timeline', shot=dict(
                     host=dict(side='right', size='medium'), visual='graphic',
                     graphic=dict(kind='layers', items=['ancient_sludge_layer', 'dinosaur_fossils_65million_years_old'])))])


def failing(**checks):
    rep = dict(summary=dict({k: True for k in ('duration', 'words', 'text', 'freeze')}, **{k: False for k in checks}),
               duration=dict(seconds=45.0, target=45.0, pass_=True), words=dict(missing=[]), text=dict())
    for k, v in checks.items():
        rep[k] = v
    rep['summary']['all'] = not checks
    return rep


class Labels(unittest.TestCase):
    def test_a_key_on_screen_reads_as_words(self):
        self.assertEqual(repair.tidy_label('dinosaur_fossils_65million_years_old'), 'Dinosaur fossils 65 million years old')
        self.assertEqual(repair.tidy_label('The Common Myth'), 'The Common Myth')


class Words(unittest.TestCase):
    def test_new_normalisation_rules(self):
        H = lambda t: [dict(w=x) for x in t.split()]
        for script, heard in (('more CO2 now', 'more C-O-2 now'), ('more CO2 now', 'more C O two now'),
                              ('sixty-five million years', '65 million years'), ('it ran 5km', 'it ran 5 kilometers'),
                              ('twenty percent', '20%'), ('three hundred and twelve cats', '312 cats'),
                              ('it costs $5 today', 'it costs five dollars today'), ('one of them', 'one of them')):
            self.assertEqual(qa.words_check(script.split(), H(heard))['missing'], [], (script, heard))
        self.assertEqual(qa.words_check('Everyone imagines oil'.split(), H('Everyone in Magenese oil'))['missing'], ['imagines'])

    def test_an_unheard_word_is_voiced_again_once(self):
        rep = failing(words=dict(missing=['imagines'], pass_=False))
        a = repair.diagnose(rep, TL, EP, {})
        self.assertEqual([(x['check'], x['beat'], x['hint']) for x in a], [('words', 'b02', {'voice_speed': repair.SLOWER})])
        again = repair.diagnose(rep, TL, EP, {'beats': {'b02': {'voice_speed': repair.SLOWER}}})
        self.assertIsNone(again[0]['hint'])                           # once only
        self.assertIn('still not heard', repair.still_wrong(rep, again)[0])


class Text(unittest.TestCase):
    def test_an_insert_label_on_the_host_moves_then_drops(self):
        rep = failing(text=dict(text_on_character=[dict(text='The Myth', t=3.0)], pass_=False))
        a = repair.diagnose(rep, TL, EP, {})[0]
        self.assertEqual((a['beat'], a['hint']), ('b02', {'label_at': {'The Myth': 'left'}}))
        r = {'beats': {'b02': {'label_at': {'The Myth': 'left'}}}}
        self.assertEqual(repair.diagnose(rep, TL, EP, r)[0]['hint'], {'drop': ['The Myth']})
        r['beats']['b02']['drop'] = ['The Myth']
        self.assertIsNone(repair.diagnose(rep, TL, EP, r)[0]['hint'])

    def test_a_diagram_label_on_the_host_moves_the_host(self):
        rep = failing(text=dict(text_on_character=[dict(text='dinosaur_fossils_65million_years_old', t=8.3)], pass_=False))
        a = repair.diagnose(rep, TL, EP, {})[0]
        self.assertEqual(a['hint'], {'host_side': 'left', 'host_size': 'small'})
        self.assertIn('Dinosaur fossils 65 million years old', a['cause'])

    def test_the_hints_reach_the_composer_and_qa(self):
        ep = host_episode()
        ep.beats = ep.beats[:3]
        specs = {b.id: dict(id=b.id, on_screen_text=b.on_screen_text, shot=b.shot.model_dump()) for b in ep.beats}
        b = ep.beats[1]
        specs[b.id]['shot']['visual'] = 'graphic'
        specs[b.id]['shot']['graphic'] = dict(kind='layers', items=['a_b_c', 'keep me'], anchors=[], detail='')
        from pipeline.models import GraphicCue
        b.shot.visual, b.shot.graphic = 'graphic', GraphicCue.model_validate(specs[b.id]['shot']['graphic'])
        hints = {'beats': {b.id: {'drop': ['keep me'], 'host_side': 'left', 'host_size': 'small'}}}
        repair.apply_hints(ep, specs, hints)
        self.assertEqual(specs[b.id]['shot']['graphic']['items'], ['A b c', ''])
        self.assertEqual(b.shot.graphic.items, ['A b c', ''])
        if b.shot.host:
            self.assertEqual((b.shot.host.side, b.shot.host.size), ('left', 'small'))
        shown = repair.repaired_episode_dict(EP, {'beats': {'b02': {'drop': ['The Myth']}}})
        texts = [p['text'] for p in qa.promised_texts(shown)]
        self.assertNotIn('The Myth', texts)                           # a dropped label is not "missing"
        self.assertIn('Dinosaur fossils 65 million years old', texts)


class Duration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(config, 'EPISODES_DIR', Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.ep = host_episode()
        self.ep.sources = [Source(id='s1', claim='Oil formed from plankton buried 300 million years ago.', url='https://example.org/a')]
        for b in self.ep.beats:
            b.source_ids = ['s1']
        self.ep.approvals['script'] = self.ep.script_digest()
        store.save(self.ep, Path(self.tmp.name) / self.ep.slug)

    def test_a_short_video_gets_a_sentence_from_its_facts(self):
        rep = failing(duration=dict(seconds=37.0, target=45.0, pass_=False))
        a = repair.diagnose(rep, TL, EP, {})
        self.assertEqual(a[0]['hint'], 'lengthen')
        thin = min(self.ep.beats[1:-1], key=lambda b: (len(b.narration.split()), b.id)).id
        asked = []

        def write(system, user, schema):
            asked.append(json.loads(user))
            return dict(beats=[dict(id=thin, sentence='It was buried 300 million years ago'),
                               dict(id='b99', sentence='Not a beat of this video at all.')])
        added = repair.lengthen(self.ep.slug, self.ep, 2.5, 1.25, write=write)
        self.assertEqual(added, [(thin, 'It was buried 300 million years ago.')])
        self.assertIn('Oil formed from plankton', json.dumps(asked[0]))
        saved, _ = store.load(self.ep.slug)
        beat = next(b for b in saved.beats if b.id == thin)
        self.assertTrue(beat.narration.endswith('It was buried 300 million years ago.'))
        self.assertTrue(saved.is_approved('script'))                  # re-stamped as the self-repair, logged
        self.assertEqual(saved.meta['approval_log'][-1]['by'], 'self-repair (QA duration)')

    def test_a_sentence_with_a_new_number_is_refused(self):
        thin = min(self.ep.beats[1:-1], key=lambda b: (len(b.narration.split()), b.id)).id
        write = lambda s, u, sc: dict(beats=[dict(id=thin, sentence='About 7000 tonnes of it every day.')])
        self.assertEqual(repair.lengthen(self.ep.slug, self.ep, 2.5, 1.25, write=write), [])

    def test_the_model_being_down_is_reported_not_raised(self):
        rep = failing(duration=dict(seconds=37.0, target=45.0, pass_=False))
        tl = dict(beats=[dict(id=b.id, start=0, end=1, words=[]) for b in self.ep.beats])

        def down(*a):
            raise RuntimeError('Ollama is not reachable')
        applied = repair.run_round(self.ep.slug, rep, tl, 1, write=down)
        self.assertEqual(applied, [])
        log = repair.load(self.ep.slug)['log']
        self.assertIn('could not lengthen', log[0]['fix'])


class Rounds(unittest.TestCase):
    """The runner: at most two repair rounds, then qa.json lists the repairs and what is still wrong."""

    def test_two_rounds_then_still_wrong(self):
        sys.path.insert(0, str(config.ROOT / 'scripts/motion_test'))
        import episode_runner
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / 'episodes/x').mkdir(parents=True)
            (t / 'episodes/x/episode.json').write_text(json.dumps(EP))
            r = episode_runner.Runner('x', 'compose_beats')
            r.out, r.episode_path = t / 'out', t / 'episodes/x/episode.json'
            r.out.mkdir()
            rep = failing(words=dict(missing=['imagines'], pass_=False))
            calls = []

            def main(argv):
                calls.append(argv)
                r.after_qa(json.loads(json.dumps(rep)), TL, mock.Mock(no_repair=False), argv)
            entry = lambda n: [dict(round=n, check='words', beat='b02', cause='c', fix='f')]
            with mock.patch.object(config, 'EPISODES_DIR', t / 'episodes'), mock.patch.object(r, 'main', side_effect=main), \
                    mock.patch.object(repair, 'run_round', side_effect=lambda slug, report, tl, n, **k: entry(n)), \
                    contextlib.redirect_stdout(io.StringIO()):
                r._repairs_done = []
                r.after_qa(json.loads(json.dumps(rep)), TL, mock.Mock(no_repair=False), [])
            self.assertEqual(len(calls), 2)                            # rendered again twice, not more
            out = json.loads((r.out / 'qa.json').read_text())
            self.assertEqual([x['round'] for x in out['repairs']], [1, 2])
            self.assertEqual(out['repair_rounds'], 2)
            self.assertTrue(out['still_wrong'])

    def test_a_passing_video_is_left_alone(self):
        sys.path.insert(0, str(config.ROOT / 'scripts/motion_test'))
        import episode_runner
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / 'ep.json').write_text(json.dumps(EP))
            r = episode_runner.Runner('x', 'compose_beats')
            r.out, r.episode_path = t, t / 'ep.json'
            r._repairs_done = []
            with mock.patch.object(repair, 'run_round', side_effect=AssertionError('nothing to repair')):
                r.after_qa(failing(), TL, mock.Mock(no_repair=False), [])
            self.assertEqual(json.loads((t / 'qa.json').read_text())['still_wrong'], [])


class Pacing(unittest.TestCase):
    def test_the_rate_comes_from_the_finished_videos(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            self.assertIsNone(pacing.measured_rate(t))
            for n, (words, sec) in enumerate(((90, 37.0), (108, 42.0), (116, 47.9), (10, 4.0))):
                (t / f'v{n}').mkdir()
                (t / f'v{n}/qa.json').write_text(json.dumps(dict(words=dict(script_words=words), duration=dict(seconds=sec))))
            self.assertEqual(pacing.measured_rate(t), 2.43)           # the median; a 10-word clip is ignored
            style = mock.Mock(words_per_second=2.7)
            self.assertEqual(pacing.writer_rate(style, t), 2.43)
            self.assertEqual(pacing.writer_rate(style, t / 'none'), 2.7)


if __name__ == '__main__':
    unittest.main()
