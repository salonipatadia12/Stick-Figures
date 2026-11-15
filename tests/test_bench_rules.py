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
        # 2026-11-15 crude oil -5: a lone "millions" in another cited fact does not excuse narrowing the range
        both = ('s5: kerogen becomes fossil fuel over thousands to millions of years.\n'
                's1: plants that lived millions of years ago')
        self.assertIn('narrows the range', grounding.narrowed('It turned to fuel over millions of years.', both))
        self.assertEqual(grounding.narrowed('It turned to fuel over thousands to millions of years.', both), '')

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


class BenchFindings(unittest.TestCase):
    """2026-11-15 first bench run (crude oil, 9B): three no-fact beats all said "Here is the surprise." (every 4-5 word
    transition was refused as too short) and auto took the outline's unchecked hook option 2."""
    ask, episode = BestOfN.ask, BestOfN.episode

    def plan(self):
        return [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='problem', kind='transition', fact_ids=[], claim=''),
                dict(role='twist', kind='fact', fact_ids=['s7'], claim='Oil came from tiny sea plants and animals.'),
                dict(role='payoff', kind='fact', fact_ids=['s8'], claim='Heat and pressure turned the remains into oil.'),
                dict(role='loop', kind='callback', fact_ids=[], claim='')]

    def test_a_short_transition_is_a_whole_line_and_a_fallback_is_never_repeated(self):
        from pipeline import bestof
        e = bestof.write_beat(self.episode(), OIL, self.plan(), 1, ['What is in your tank?'], 'twist', (8, 17),
                              self.ask(['But is that true?']))     # 4 words: refused before (2026-11-15)
        self.assertEqual(e['line'], 'But is that true?')
        e = bestof.write_beat(self.episode(), OIL, self.plan(), 1, ['Hook?', 'Here is the surprise.'], 'twist', (8, 17),
                              self.ask([]))
        self.assertNotEqual(e['line'], 'Here is the surprise.')

    def test_the_payoff_answers_the_hook_and_every_hook_option_is_checked(self):
        from pipeline import bestof
        ep = self.episode()
        o = dict(beats=[dict(narration='', source_ids=[]) for _ in range(5)],
                 hook_options=[dict(line='outline hook one', loop_line='x', answer='a long outline answer'),
                               dict(line='outline hook two', loop_line='y', answer='other'),
                               dict(line='outline hook three', loop_line='z', answer='other')])
        lines = {0: ['Your gas is older than the dinosaurs.', 'What is really in your tank?'],
                 1: ['But is that true?'], 2: ['Tiny sea plants and animals made the oil.'],
                 3: ['Heat and pressure cooked the remains for ages.', 'Heat cooked those tiny sea plants and animals into oil.'],
                 4: ['Older than the dinosaurs, made by tiny life.']}
        k = iter(range(99))
        base = self.ask([])
        calls = []

        def ask(system, user, schema):
            if system == bestof.WRITE_SYSTEM:
                calls.append(user)
                n = sum(1 for c in calls if 'EARLIER LINES FAILED' not in c) - 1
                return dict(lines=lines.get(n, []) if 'EARLIER LINES FAILED' not in user else [])
            return base(system, user, schema)
        log = bestof.write_all(ep, OIL, o, dict(beats=self.plan(), twist='s7'), 40, ask)
        pay = next(x for x in log if x['role'] == 'payoff')
        self.assertIn('does not answer the hook', pay['rejected']['Heat and pressure cooked the remains for ages.'])
        self.assertEqual(o['beats'][3]['narration'], 'Heat cooked those tiny sea plants and animals into oil.')
        self.assertEqual(o['beats'][3]['source_ids'], ['s8', 's7'])
        self.assertEqual([h['line'] for h in o['hook_options']],
                         ['Your gas is older than the dinosaurs.', 'What is really in your tank?',
                          'Your gas is older than the dinosaurs.'])
        self.assertEqual({h['answer'] for h in o['hook_options']}, {'Oil came from tiny sea plants and animals.'})


class BenchRunOne(unittest.TestCase):
    """2026-11-15 bench run 1 (9B writer): off-topic facts told and on-topic ones skipped; the yawn hook and loop said
    the myth as a fact; a reveal reported a new belief; a transition gave the twist away; "*really*" read aloud;
    labels "Telling Real", "NO"."""
    ask, episode, plan = BestOfN.ask, BestOfN.episode, BestOfN.plan

    def test_facts_judged_on_topic_are_told_whatever_words_they_share(self):
        plasma = Source(id='s9', claim='Plasmas allow thermodynamically unfavorable reactions to occur easily.',
                        url='https://doi.org/x')
        senators = Source(id='s10', claim='Senators lacked an incentive to use their authority.', url='https://doi.org/y')
        ep = plan_episode()
        judge = lambda s, u, sc: dict(helps='Plasmas' not in u, why='')
        self.assertEqual(grounding.on_topic([plasma, senators], ep, judge), {'s10'})
        heat = Source(id='s2', claim='Heat and pressure turned ancient remains into crude oil over millions of years.',
                      url='https://www.eia.gov/x')
        asked = []
        self.assertEqual(grounding.on_topic([heat], ep, lambda *a: asked.append(a) or dict(helps=False)), {'s2'})
        self.assertEqual(asked, [])                                  # plainly about the topic: not sent to the judge
        self.assertIsNone(grounding.on_topic([plasma], ep, None))
        roles = ['hook', 'clue', 'clue', 'twist', 'payoff', 'loop']
        out = grounding.assign_facts(roles, [S7, plasma, senators], 's7', ep.meta['interpretation'], None,
                                     relevant={'s7', 's10'})
        self.assertIn('s10', out.values())                           # no word in common with the question, still told
        self.assertNotIn('s9', out.values())

    def test_a_hook_or_loop_that_says_the_myth_as_a_fact_is_refused(self):
        from pipeline import bestof
        interp = self.episode().meta['interpretation']
        says = lambda s, u, sc: dict(says_it_is_true='It was dinosaurs' in u, why='')
        self.assertEqual(bestof.asserts_myth('It was dinosaurs.', interp, says), 'dinosaur')
        self.assertEqual(bestof.asserts_myth('Your gas is older than the dinosaurs.', interp, says), '')   # judge: no
        self.assertEqual(bestof.asserts_myth('Was it dinosaurs?', interp, says), '')                      # a question
        base = self.ask(['It was dinosaurs.', 'Was it really dinosaurs?'])
        ask = lambda s, u, sc: says(s, u, sc) if s == bestof.ASSERTS_SYSTEM else base(s, u, sc)
        e = bestof.write_beat(self.episode(), OIL, self.plan(), 0, [], 'twist', (3, 8), ask)
        self.assertIn('says the myth as a fact', e['rejected']['It was dinosaurs.'])
        self.assertEqual(e['line'], 'Was it really dinosaurs?')

    def test_only_the_myth_beat_reports_a_belief(self):
        from pipeline import bestof
        plan = [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='reveal', kind='transition', fact_ids=[], claim=''), dict(role='loop', kind='callback', fact_ids=[], claim='')]
        base = self.ask(['Others claim oil is just old soup.', 'But is that the whole story?'])

        def ask(system, user, schema):
            if system == entail.SPLIT_SYSTEM and 'Others claim' in user:
                return dict(claims=[dict(text='oil is just old soup', kind='belief')], nonsense=[])
            return base(system, user, schema)
        e = bestof.write_beat(self.episode(), OIL, plan, 1, ['Hook?'], 'loop', (5, 12), ask)
        self.assertIn('reports a belief', e['rejected']['Others claim oil is just old soup.'])
        self.assertEqual(e['line'], 'But is that the whole story?')

    def test_a_no_fact_line_counts_as_a_claim_for_repeats_and_markdown_is_stripped(self):
        from pipeline import bestof
        chosen = ['Hook?', "But it's not just anyone we copy."]
        plan = [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='clue', kind='transition', fact_ids=[], claim=''),
                dict(role='reveal', kind='fact', fact_ids=['s7'], claim='We copy only those we bond with.'),
                dict(role='loop', kind='callback', fact_ids=[], claim='')]
        e = bestof.write_beat(self.episode(), OIL, plan, 2, chosen, 'loop', (4, 14),
                              self.ask(["We don't copy just anyone, only those who bond.", 'Tiny sea plants and animals made the oil.']))
        self.assertIn("same claim as an earlier beat", e['rejected']["We don't copy just anyone, only those who bond."])
        self.assertEqual(bestof._clean('Real? Like *really* real'), 'Real? Like really real.')
        e = bestof.write_beat(self.episode(), OIL, self.plan(), 0, [], 'twist', (3, 8),
                              self.ask(['Believing this costs you billions.', 'but also on the interest.', 'What is in your tank?']))
        self.assertIn('number the research does not have', e['rejected']['Believing this costs you billions.'])
        self.assertEqual(e['rejected']['but also on the interest.'], 'starts mid-sentence')

    def test_a_no_fact_beat_shows_only_its_own_phrases(self):
        ep = host_episode()
        b = ep.beats[1]
        b.narration = "Wait... you're telling me this is real?"
        ep.meta['fact_plan'] = dict(beats=[dict(role=x.role, kind='reaction', fact_ids=[], claim='') for x in ep.beats])
        pb = ep.meta['fact_plan']['beats'][1]
        self.assertFalse(grounding.from_claim('Telling Real', b, pb, 3))
        self.assertTrue(grounding.from_claim('this is real', b, pb, 3))
        self.assertFalse(grounding.from_claim('NO', b, dict(kind='fact'), 3))     # no real word


class BenchRunTwo(unittest.TestCase):
    """2026-11-15 bench run 2 (14B writer): "But there's a twist" opened three lines; an insert with no key stopped Auto."""
    ask, episode, plan = BestOfN.ask, BestOfN.episode, BestOfN.plan

    def test_two_lines_never_open_the_same_way(self):
        from pipeline import bestof
        plan = [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='clue', kind='transition', fact_ids=[], claim=''),
                dict(role='clue', kind='transition', fact_ids=[], claim=''), dict(role='loop', kind='callback', fact_ids=[], claim='')]
        e = bestof.write_beat(self.episode(), OIL, plan, 2, ['Hook?', "But there's a twist?"], 'loop', (2, 12),
                              self.ask(["But there's a twist you won't see coming?", 'Is that the whole story?']))
        self.assertIn('opens like an earlier line', e['rejected']["But there's a twist you won't see coming?"])
        self.assertEqual(e['line'], 'Is that the whole story?')

    def test_an_insert_with_no_key_is_named_from_its_place(self):
        from pipeline import auto
        ep = host_episode()
        b = next(x for x in ep.beats if x.shot.visual == 'insert' and x.shot.insert)
        b.shot.insert.key, b.shot.insert.scene = '', ''
        if b.shot.staging:
            b.shot.staging.setting_detail = 'a white studio wall'
        notes = auto.fix_script(ep)
        self.assertRegex(b.shot.insert.key, r'^[a-z0-9]+(-[a-z0-9]+)*$')
        self.assertTrue(b.shot.insert.scene)
        self.assertTrue(any('no kebab-case key' in n for n in notes))


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
