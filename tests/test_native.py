"""Claude-native writing, the word budget, the pronunciation lexicon and layout before render (user 2026-11-19). The
models are stand-ins: no Claude Code, no Ollama, no Kokoro."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('STICK_EMBED', '0')          # no real embedding model in the tests
sys.path.insert(0, str(Path(__file__).parent))
from pipeline import native, pronounce  # noqa: E402
import test_whole_script as tw  # noqa: E402


class NJudge(tw.Judge):
    """test_whole_script's judge, with the hook-gap and the moves-the-story questions."""

    def __init__(self, side=(), no_gap=(), **kw):
        super().__init__(**kw)
        self.side, self.no_gap = set(side), set(no_gap)

    def __call__(self, system, user, schema):
        if system == native.HOOK_SYSTEM:
            hook = user.split('Hook: ', 1)[1]
            return dict(opens_gap=hook not in self.no_gap, restates_topic=False, why='a flat line')
        if system == native.ADVANCES_SYSTEM:
            line = user.split('Line: ', 1)[1]
            return dict(advances=line not in self.side, why='a side fact')
        return super().__call__(system, user, schema)


def plan():
    return dict(twist='s7', hook_fact='s5', answer_words=[], hook_gap='a contrast', beats=[dict(p) for p in tw.PLAN],
                native=True)


def writer(drafts, revised=None, seen=None):
    def call(system, user, schema, what):
        if seen is not None:
            seen.append(what)
        if system == native.WRITE_SYSTEM:
            k = int(what.split()[-1]) - 1
            return dict(lines=drafts[k % len(drafts)])
        if system == native.REVISE_SYSTEM:
            return dict(lines=revised(what) if callable(revised) else (revised or drafts[0]))
        raise AssertionError(f'unexpected writer call: {what}')
    return call


def run(drafts, judge, budget_words=90, rate=2.7, revised=None, seen=None):
    o = tw.outline()
    p = plan()
    entry = native.write(tw.episode(), tw.OIL, o, p, budget_words, judge, rate, writer(drafts, revised, seen))
    return entry, o, p


class Plan(unittest.TestCase):
    def raw(self, **kw):
        beats = [dict(kind='question', fact_ids=[], point=''), dict(kind='myth', fact_ids=[], point=''),
                 dict(kind='reaction', fact_ids=[], point=''),
                 dict(kind='fact', fact_ids=['s5'], point='Oil formed before dinosaurs lived.'),
                 dict(kind='fact', fact_ids=['s3', 'nope'], point='Heat turned the remains of living things into oil.'),
                 dict(kind='fact', fact_ids=['s7'], point='Oil came from tiny sea plants and animals.'),
                 dict(kind='fact', fact_ids=['s8'], point='Heat and pressure turned the remains into oil.'),
                 dict(kind='callback', fact_ids=['s5'], point='')]
        return dict(dict(beats=beats, twist_fact='s7', hook_fact='s5', hook_gap='a contrast', left_out=[]), **kw)

    def test_claude_picks_the_facts_and_the_code_keeps_the_roles(self):
        ep = tw.episode()
        got, problems = native.normalise(self.raw(), ep, tw.OIL)
        from pipeline import grounding
        self.assertEqual([b['role'] for b in got['beats']], grounding.skeleton(ep.story_format, 8))
        self.assertEqual(got['beats'][0], dict(role='hook', kind='question', fact_ids=[], claim=''))
        self.assertEqual(got['beats'][-1]['fact_ids'], [])                  # the loop states no fact
        self.assertEqual(got['twist'], 's7')
        self.assertIn('unknown fact ids nope', ' '.join(problems))
        pay = next(b for b in got['beats'] if b['role'] == 'payoff')
        self.assertTrue({'s7', 's8'} <= set(pay['fact_ids']))               # the core answer's facts back the payoff

    def test_a_beat_count_with_no_roles_is_refused(self):
        raw = self.raw()
        raw['beats'] = raw['beats'][:5]
        got, problems = native.normalise(raw, tw.episode(), tw.OIL)
        self.assertIsNone(got)
        self.assertIn('5 beats', problems[0])

    def test_the_plan_is_checked_and_the_twist_is_claudes(self):
        calls = []

        def write(system, user, schema, what):
            calls.append(what)
            return self.raw()
        ep = tw.episode()
        got = native.plan(ep, tw.OIL, 110, tw.Judge(), write)
        self.assertEqual(calls, ['plan', 'plan (repair 1)'])               # the unknown id went back for a repair
        self.assertTrue(got['native'])
        native.take_twist(ep, got, tw.OIL)
        self.assertEqual(ep.meta['interpretation']['twist']['how'], 'planned by Claude Code')


class Hook(unittest.TestCase):
    def test_a_hook_that_only_restates_the_title_fails(self):
        from pipeline.models import Episode
        ep = Episode(slug='x', topic='The real origin of crude oil', target_length=45)
        self.assertTrue(native.restates_topic('Picture this: the real origin of crude.', ep))
        self.assertTrue(native.restates_topic('The real origin of crude oil.', ep))
        self.assertEqual(native.restates_topic('Did dinosaurs really fill your gas tank?', ep), '')
        self.assertEqual(native.restates_topic('Your fuel is older than the dinosaurs.', ep), '')

    def test_the_judge_fails_a_hook_with_no_gap(self):
        entry, o, _ = run([tw.GOOD], NJudge(no_gap={tw.GOOD[0]}),
                          revised=lambda what: ['Older than any dinosaur: your gas?'] + tw.GOOD[1:])
        self.assertIn('the hook opens no gap', entry['scripts'][0]['problems']['0'])
        self.assertEqual(o['beats'][0]['narration'], 'Older than any dinosaur: your gas?')   # revised, then it passed


class Write(unittest.TestCase):
    def test_a_clean_draft_needs_no_revision(self):
        seen = []
        entry, o, _ = run([tw.GOOD], NJudge(), seen=seen)
        self.assertEqual(seen, [f'draft {k}' for k in range(1, native.N_DRAFTS + 1)])
        self.assertEqual(entry['unresolved'], [])
        self.assertEqual([b['narration'] for b in o['beats']], tw.GOOD)
        self.assertEqual(entry['words'], native.words(tw.GOOD))

    def test_a_side_fact_is_cut(self):
        side = tw.GOOD[4]                                                    # the reveal: true, but a side step here
        entry, o, p = run([tw.GOOD], NJudge(side={side}), rate=2.0,
                          revised=lambda what: tw.GOOD[:4] + [''] + tw.GOOD[5:])
        self.assertEqual([d['line'] for d in entry['dropped']], [side])
        self.assertEqual(len(o['beats']), len(tw.GOOD) - 1)
        self.assertEqual(len(p['beats']), len(tw.GOOD) - 1)
        self.assertNotIn(side, [b['narration'] for b in o['beats']])

    def test_over_budget_claude_trims_before_anything_is_voiced(self):
        short = [tw.GOOD[0], 'Many think oil comes from dinosaurs.', tw.GOOD[2],
                 'Crude oil was forming before any dinosaur walked.', 'Slow heat cooked what was left of living things.',
                 'The real source? Tiny ancient sea plants and animals.', tw.GOOD[6], tw.GOOD[7]]
        seen = []
        entry, o, _ = run([tw.GOOD], NJudge(), budget_words=60, revised=lambda what: short, seen=seen)
        self.assertTrue(any(w.startswith('trim') for w in seen))
        self.assertLess(entry['words'], native.words(tw.GOOD))
        self.assertEqual([b['narration'] for b in o['beats']], short)

    def test_the_budget(self):
        self.assertEqual(native.budget(45, 2.5), 112)                        # about 112 words for 45 s at 2.5 words/s
        self.assertFalse(native.over_budget(123, 112))
        self.assertTrue(native.over_budget(124, 112))


class Gate(unittest.TestCase):
    def test_a_script_twenty_percent_over_its_budget_stops_at_the_gate(self):
        from test_auto_and_qa_fixes import host_episode
        ep = host_episode()
        n = ep.word_count()
        ep.meta['length_plan'] = dict(word_budget=round(n / 1.25))
        self.assertTrue(any('over the word budget' in i.message for i in ep.check(['lead'], 2.7) if i.severity == 'error'))
        ep.meta['length_plan'] = dict(word_budget=round(n / 1.15))
        msgs = [(i.severity, i.message) for i in ep.check(['lead'], 2.7) if 'budget' in i.message]
        self.assertEqual([s for s, _ in msgs], ['warning'])


class Lexicon(unittest.TestCase):
    LEX = {'kerogen': dict(say='KAIR-uh-jen', phonemes='kˈɛɹəʤən'), 'rayleigh': dict(say='RAY-lee')}

    def test_the_voice_says_the_respelling_and_the_text_keeps_the_word(self):
        got = pronounce.marked("Kerogen's heat makes kerogen, then Rayleigh.", lambda e: pronounce.from_say(e['say']),
                               self.LEX)
        self.assertEqual(got, "[Kerogen](/kˈɛɹəʤən/)'s heat makes [kerogen](/kˈɛɹəʤən/), then [Rayleigh](/ɹˈAli/).")
        self.assertEqual(pronounce.stamp('the kerogen', self.LEX), 'kerogen=kˈɛɹəʤən')
        self.assertEqual(pronounce.stamp('no hard words', self.LEX), '')

    def test_respellings_by_their_conventions(self):
        self.assertEqual(pronounce.from_say('KAIR-uh-jen'), 'kˈɛɹəʤɛn')
        self.assertEqual(pronounce.from_say('HY-druh-kar-bunz'), 'hˈIdɹəkɑɹbʌnz')
        self.assertTrue(pronounce.valid_say('KAIR-uh-jen'))
        for bad in ('kair-uh-jen', 'KAIR UH JEN', 'KAIR-UH-JEN', 'kair/uh'):
            self.assertFalse(pronounce.valid_say(bad), bad)

    def test_the_seed_lexicon_has_the_words_past_episodes_missed(self):
        lex = pronounce.load()
        for w in ('kerogen', 'hydrocarbons', 'liptinic', 'lipid-rich'):
            self.assertIn(w, lex)

    def test_the_self_repair_adds_a_respelling_and_voices_the_beat_again(self):
        from pipeline import config, repair, store
        from test_auto_and_qa_fixes import host_episode
        with tempfile.TemporaryDirectory() as d:
            lex = Path(d) / 'pronunciations.json'
            lex.write_text(json.dumps(dict(words={})))
            ep = host_episode()
            b = ep.beats[1]
            b.narration = 'Everyone imagines liptinite comes first.'
            with mock.patch.object(config, 'EPISODES_DIR', Path(d)), mock.patch.object(pronounce, 'PATH', lex):
                store.save(ep, Path(d) / ep.slug)
                repair.save(ep.slug, {'beats': {b.id: {'voice_speed': repair.SLOWER}}})
                tl = dict(beats=[dict(id=x.id, start=k, end=k + 1, words=[[w, k] for w in x.narration.split()])
                                 for k, x in enumerate(ep.beats)])
                report = dict(summary=dict(words=False, all=False), words=dict(missing=['liptinite'], pass_=False))
                write = lambda s, u, sc: dict(say='LIP-tih-nyte') if s == repair.RESPELL_SYSTEM else {}
                applied = repair.run_round(ep.slug, report, tl, 2, write=write)
                self.assertIn('as "LIP-tih-nyte"', applied[0]['fix'])
                self.assertEqual(pronounce.load(lex)['liptinite']['say'], 'LIP-tih-nyte')
                self.assertEqual(repair.load(ep.slug)['beats'][b.id]['respelled'], {'liptinite': 'LIP-tih-nyte'})
                saved, _ = store.load(ep.slug)
                self.assertEqual(saved.beats[1].narration, 'Everyone imagines liptinite comes first.')   # captions keep it


class Layout(unittest.TestCase):
    def test_a_text_on_the_host_or_outside_the_safe_area_is_found(self):
        from pipeline import layout
        drawn = [dict(label='HEAT', em=60, box=[400, 400, 600, 470]), dict(label='MUD', em=60, box=[40, 300, 300, 360]),
                 dict(label='FINE', em=60, box=[400, 250, 600, 300])]
        got = layout.problems('b01', 1.0, drawn, chars=[[380, 380, 700, 900]])
        self.assertEqual([(p['text'], p['why']) for p in got], [('HEAT', 'host'), ('MUD', 'safe')])

    def test_the_ladder_shrinks_wraps_moves_then_drops(self):
        from pipeline import layout, repair
        shot = dict(host=dict(where='scene', side='left', size='medium'),
                    insert=dict(effects=[dict(kind='label', detail='Oil worth drilling', at='top', anchor='')]))
        ep = dict(beats=[dict(id='b07', narration='Oil worth drilling', shot=shot)])
        repairs, steps = {}, []
        for _ in range(5):
            log = layout.fixes([dict(beat='b07', text='Oil worth drilling', why='host', em=60, box=[0, 0, 1, 1], t=1)],
                               ep, repairs)
            steps.append(log[0]['fix'])
        self.assertEqual(steps, ['type 78 %', 'two lines', 'moved to the right', 'dropped (the narration still says it)',
                                 'nothing left to try'])
        h = repairs['beats']['b07']
        self.assertEqual(h['fit']['Oil worth drilling'], dict(scale=0.78, wrap=True))
        self.assertEqual(h['label_at'], {'Oil worth drilling': 'right'})
        self.assertEqual(h['drop'], ['Oil worth drilling'])
        self.assertEqual(repair.hints_for.__name__, 'hints_for')

    def test_a_label_never_shrinks_below_the_readable_size(self):
        from pipeline import layout
        hint, what = layout.ladder('SMALL', 'host', {}, {}, 44)
        self.assertEqual(what, 'two lines' if ' ' in 'SMALL' else what)
        self.assertNotIn('scale', (hint.get('fit') or {}).get('SMALL', {}))

    def test_the_fit_makes_the_tag_smaller_and_two_lines(self):
        from pipeline import overlays as O
        plain = O.tag_label('Oil worth drilling', 60)
        with mock.patch.object(O, 'FIT', {'Oil worth drilling': dict(scale=0.78, wrap=True)}):
            fitted = O.tag_label('Oil worth drilling', 60)
        self.assertLess(fitted.width, plain.width * 0.7)
        self.assertGreater(fitted.height, plain.height)
        self.assertEqual(fitted.info['em'], 47)

    def test_a_drop_takes_out_a_pin_label_and_a_checklist_row(self):
        from pipeline import repair
        from test_auto_and_qa_fixes import host_episode
        ep = host_episode()
        b = ep.beats[1]
        specs = {b.id: dict(id=b.id, narration=b.narration, on_screen_text='', shot=dict(
            insert=dict(key='oil-field', scene='an oil field',
                        effects=[dict(kind='pin', anchor='', at='ground', detail='Oil worth drilling')]),
            motion=[dict(recipe='checklist_tick', anchor='', detail='Heat | Ingredients and steps')]))}
        repair.apply_hints(ep, specs, {'beats': {b.id: {'drop': ['Oil worth drilling', 'Ingredients and steps']}}})
        self.assertEqual(specs[b.id]['shot']['insert']['effects'], [])
        self.assertEqual(specs[b.id]['shot']['motion'][0]['detail'], 'Heat')


if __name__ == '__main__':
    unittest.main()
