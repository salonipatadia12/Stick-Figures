"""The writing rules of the benchmark brief (user 2026-11-15): ranges kept whole, hooks never state the twist, no two
beats make the same claim, labels written from the claim, filler words dropped (never swapped), and the bigger writer
model only for the writing calls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import config, entail, grounding, repair, store
from pipeline.models import Source

from test_fact_check import NO_CLAIMS, OIL, S7, BestOfN, host_episode, plan_episode


class Ranges(unittest.TestCase):
    RESEARCH = ('s3: Most organic matter is converted to kerogen over thousands to millions of years.\n'
                'Plain words for it: "millions of years ago" means ancient, long ago, prehistoric.')

    def test_a_range_is_kept_as_wide_as_the_fact(self):
        self.assertIn('narrows the range "thousands to millions" to "thousands"',
                      grounding.narrowed('Most of it turns to kerogen over thousands of years.', self.RESEARCH))
        self.assertIn('to "millions"', grounding.narrowed('It takes millions of years.', self.RESEARCH))  # glossary aside
        self.assertEqual(grounding.narrowed('It takes thousands to millions of years.', self.RESEARCH), '')
        self.assertEqual(grounding.narrowed('A slow, dark change.', self.RESEARCH), '')
        self.assertIn('"Billions" is a number', grounding.narrowed('Billions of years.', self.RESEARCH))
        r = 's1: Oil formed between 10 and 100 million years ago.'
        self.assertIn('narrows', grounding.narrowed('Oil formed 100 million years ago.', r))
        self.assertEqual(grounding.narrowed('Oil formed 10 to 100 million years ago.', r), '')
        self.assertIn('"65"', grounding.narrowed('Dinosaurs died 65 million years ago.', r))
        self.assertEqual(grounding.narrowed('It lived millions of years ago.', 's7: remains that lived millions of years ago'), '')

    def test_the_claims_check_fails_a_narrowed_range_in_code(self):
        asked = []
        ask = lambda s, u, sc: (asked.append(s) or dict(claims=[dict(text=u.removeprefix('Line: '), kind='fact')],
                                                         nonsense=[])) if s == entail.SPLIT_SYSTEM else \
            dict(states_a_fact=True, supported=True, adds=[], why='')
        ok, why = entail.verdict('Most of it turns to kerogen over thousands of years.', self.RESEARCH, set(), ask)
        self.assertFalse(ok)
        self.assertIn('narrows the range', why)
        self.assertNotIn(entail.CHECK_SYSTEM, asked)                  # decided in code, before the judge
        ok, _ = entail.verdict('It takes thousands to millions of years.', self.RESEARCH, set(), ask)
        self.assertTrue(ok)
        self.assertFalse(entail.verdict('Thousands of years', self.RESEARCH, set(), ask, entail.TEXT_SYSTEM)[0])


class SameClaim(unittest.TestCase):
    def test_two_claims_that_say_the_same_thing(self):
        self.assertTrue(grounding.same_claim('That black gold formed before dinosaurs existed.',
                                             'Oil formed before dinosaurs existed.'))
        self.assertFalse(grounding.same_claim('Heat and pressure turned the remains into oil.',
                                              'Tiny sea plants and animals sank to the sea floor.'))
        self.assertFalse(grounding.same_claim('Oil is old.', 'Oil is old.'))           # too short to compare
        self.assertEqual(grounding.repeated_claim(['Crude oil formed before dinosaurs existed.'],
                                                  [('b07', ['Oil formed before dinosaurs existed.'])]),
                         'b07 already says "Oil formed before dinosaurs existed."')


class HookAndRepeats(unittest.TestCase):
    """Best-of-N with the new filters (the stand-in judge of test_fact_check.BestOfN, not its tests)."""
    ask, episode, plan = BestOfN.ask, BestOfN.episode, BestOfN.plan

    def test_a_hook_that_states_the_twist_is_refused(self):
        from pipeline import bestof
        interp = self.episode().meta['interpretation']
        twist = f'{S7.claim} {S7.quote}'
        claim = 'Oil came from tiny sea plants and animals.'
        self.assertIn('names the answer', grounding.gives_away('Tiny sea plants made your oil.', twist, claim, interp, OIL,
                                                               'crude oil'))
        self.assertEqual(grounding.gives_away('Your gas is older than the dinosaurs.', twist, claim, interp, OIL,
                                              'crude oil'), '')
        ask = self.ask(['Tiny sea plants made your oil.', 'Your gas is older than the dinosaurs.'])
        e = bestof.write_beat(self.episode(), OIL, self.plan(), 0, [], 'twist', (5, 12), ask)
        self.assertEqual(e['line'], 'Your gas is older than the dinosaurs.')
        self.assertIn('gives the twist away', e['rejected']['Tiny sea plants made your oil.'])
        self.assertIn('NEVER say it', self.prompts[0])
        self.assertIn(claim, self.prompts[0])

    def test_no_two_beats_make_the_same_claim_but_the_payoff_may_restate_the_twist(self):
        from pipeline import bestof
        plan = [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='twist', kind='fact', fact_ids=['s7'], claim='Oil formed before dinosaurs existed.'),
                dict(role='reveal', kind='fact', fact_ids=['s7'], claim='Oil formed before dinosaurs existed.'),
                dict(role='payoff', kind='fact', fact_ids=['s7'], claim='Oil formed before dinosaurs existed.'),
                dict(role='loop', kind='callback', fact_ids=[], claim='')]
        chosen = ['What is in your tank?', 'Deep in the past, crude oil formed before any dinosaurs existed on Earth.']
        same = 'It formed before dinosaurs existed.'
        other = 'Tiny plants and animals lived in the sea long ago.'
        e = bestof.write_beat(self.episode(), OIL, plan, 2, chosen, 'payoff', (4, 12), self.ask([same, other]))
        self.assertEqual(e['line'], other)
        self.assertIn('same claim as an earlier beat (b02 already says', e['rejected'][same])
        e = bestof.write_beat(self.episode(), OIL, plan, 3, chosen + [other], 'loop', (4, 12), self.ask([same]))
        self.assertEqual(e['line'], same)                             # the payoff restates the twist, once

    def test_the_writer_writes_and_the_judge_judges(self):
        from pipeline import bestof
        judge = self.ask(['never used'])
        wrote = []
        write = lambda s, u, sc: (wrote.append(s) or dict(lines=['Your gas is older than the dinosaurs.']))
        e = bestof.write_beat(self.episode(), OIL, self.plan(), 0, [], 'twist', (5, 12), judge, write=write)
        self.assertEqual(e['line'], 'Your gas is older than the dinosaurs.')
        self.assertEqual(wrote, [bestof.WRITE_SYSTEM])
        self.assertEqual(self.prompts, [])                             # the judge was never asked to write


class WriterModel(unittest.TestCase):
    def test_the_writer_defaults_to_the_judge_model(self):
        with mock.patch.dict(os.environ, {'OLLAMA_MODEL': 'qwen3.5:9b', 'OLLAMA_WRITER_MODEL': ''}):
            self.assertEqual(config.ollama_writer_model(), 'qwen3.5:9b')
        with mock.patch.dict(os.environ, {'OLLAMA_MODEL': 'qwen3.5:9b', 'OLLAMA_WRITER_MODEL': 'qwen3:14b'}):
            self.assertEqual(config.ollama_writer_model(), 'qwen3:14b')
            self.assertEqual(config.ollama_model(), 'qwen3.5:9b')

    def test_the_writer_call_names_its_model(self):
        from pipeline import bestof, llm
        seen = {}
        fake = lambda *a, **kw: seen.update(kw) or dict(lines=[])
        with mock.patch.dict(os.environ, {'OLLAMA_WRITER_MODEL': 'qwen3:14b', 'SCRIPT_PROVIDER': 'ollama'}), \
                mock.patch.object(llm, 'generate_json', fake):
            bestof.write_ask()('s', 'u', {})
        self.assertEqual(seen['model'], 'qwen3:14b')


class LabelsFromTheClaim(unittest.TestCase):
    def episode(self):
        ep = host_episode()
        b = ep.beats[1]
        b.narration = 'Heat and pressure turned the buried remains into crude oil.'
        b.on_screen_text = 'Deep heat source'
        ep.meta['fact_plan'] = dict(beats=[dict(role=x.role, kind='fact', fact_ids=[], claim='') for x in ep.beats])
        ep.meta['fact_plan']['beats'][1]['claim'] = 'Heat and pressure turned the remains into crude oil.'
        return ep, b

    def test_an_invented_label_is_written_again_from_the_claim(self):
        ep, b = self.episode()
        users = []
        ask = lambda s, u, sc: users.append(u) or dict(texts=['Heat and pressure'])
        lab = grounding.ground_labels(ep, ask=ask)
        self.assertEqual(b.on_screen_text, 'Heat and pressure')
        self.assertIn('was "Deep heat source"', users[0])
        self.assertIn(dict(beat=b.id, field='on-screen text', was='Deep heat source', now='Heat and pressure'),
                      lab['changed'])

    def test_a_label_that_is_still_invented_is_dropped_and_good_ones_stay(self):
        ep, b = self.episode()
        lab = grounding.ground_labels(ep, ask=lambda s, u, sc: dict(texts=['Surface soil']))
        self.assertEqual(b.on_screen_text, '')
        self.assertEqual([c['now'] for c in lab['changed'] if c['beat'] == b.id], [''])
        ep, b = self.episode()
        b.on_screen_text = 'Buried remains'
        lab = grounding.ground_labels(ep, ask=lambda *a: {})
        self.assertEqual(b.on_screen_text, 'Buried remains')         # from the narration: kept as it is
        b.on_screen_text = 'MYTH'
        lab2 = grounding.ground_labels(ep, ask=lambda *a: {})
        self.assertEqual(b.on_screen_text, 'MYTH')
        self.assertFalse([c for c in lab['changed'] + lab2['changed'] if c['beat'] == b.id])


class Fillers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(config, 'EPISODES_DIR', Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.ep = host_episode()
        self.b = self.ep.beats[1]
        self.b.narration = 'Hmm. Is that actually right?'
        self.ep.approvals['script'] = self.ep.script_digest()
        store.save(self.ep, Path(self.tmp.name) / self.ep.slug)

    def test_a_filler_is_dropped_never_swapped(self):
        # 2026-11-15 crude oil b03: "Hmm" was not heard and the swap made it "Yes, yes. Is that actually right?"
        self.assertEqual(repair.drop_filler('Hmm. Is that actually right?', 'Hmm'), 'Is that actually right?')
        self.assertEqual(repair.drop_filler('Well, that is wrong.', 'well'), 'That is wrong.')
        self.assertEqual(repair.drop_filler('Wait. Oh, really?', 'oh'), 'Wait. Really?')
        self.assertEqual(repair.drop_filler('Oh!', 'Oh'), '')        # nothing would be left: the line stays
        never = lambda *a: self.fail('a filler is never sent to the swap model')
        self.assertEqual(repair.swap_word(self.ep.slug, self.ep, self.b.id, 'Hmm', write=never), repair.DROPPED)
        self.assertEqual(self.b.narration, 'Is that actually right?')
        self.assertEqual(self.ep.meta['approval_log'][-1]['swapped']['now'], repair.DROPPED)

    def test_an_unheard_filler_is_dropped_at_once_without_a_slower_take(self):
        tl = dict(beats=[dict(id=x.id, start=k, end=k + 1, words=[[w, k] for w in x.narration.split()])
                         for k, x in enumerate(self.ep.beats)])
        report = dict(summary=dict(words=False, all=False), words=dict(missing=['Hmm'], pass_=False))
        ep_data = json.loads((Path(self.tmp.name) / self.ep.slug / 'episode.json').read_text())
        actions = repair.diagnose(report, tl, ep_data, {})
        self.assertEqual(actions[0]['hint'], dict(swap_word='Hmm'))
        applied = repair.run_round(self.ep.slug, report, tl, 1, write=lambda *a: NO_CLAIMS)
        self.assertIn('dropped the filler "Hmm"', applied[0]['fix'])
        saved, _ = store.load(self.ep.slug)
        self.assertEqual(next(x for x in saved.beats if x.id == self.b.id).narration, 'Is that actually right?')
        self.assertTrue(saved.is_approved('script'))

    def test_well_inside_a_sentence_is_a_word_not_a_filler(self):
        self.b.narration = 'It works well.'
        asked = []
        write = lambda s, u, sc: (asked.append(s) or dict(replacement='fine', line='It works fine.')
                                  if s == repair.SWAP_SYSTEM else NO_CLAIMS)
        self.assertEqual(repair.swap_word(self.ep.slug, self.ep, self.b.id, 'well', write=write), 'fine')
        self.assertEqual(asked[0], repair.SWAP_SYSTEM)


class Bench(unittest.TestCase):
    def test_topics_and_the_comparison_table(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bench'))
        import score
        rows = score.topics()
        self.assertGreaterEqual(len(rows), 5)
        self.assertTrue(any('crude oil' in r['topic'].lower() for r in rows))
        t = dict(topic='X', clean=True, seconds=100.0, overall=0.8,
                 scores=dict(hook=4, flow=3, accuracy=1.0, rewrite=0.1))
        a = dict(label='a', topics=[t]); a['summary'] = score.summary(a)
        b = dict(label='b', topics=[dict(t, clean=False, overall=0.6)]); b['summary'] = score.summary(b)
        table = score.compare(a, b)
        self.assertIn('| clean | 1.0 | 0.0 | -1 |', table)
        self.assertIn('| X | 4/3/1.0/0.1/yes/100 | 4/3/1.0/0.1/NO/100 |', table)
        self.assertEqual(score.overall(t['scores'], True), round((0.8 + 0.6 + 1 + 0.9 + 1) / 5, 3))


if __name__ == '__main__':
    unittest.main()
