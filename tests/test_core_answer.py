"""Built around the answer (user 2026-11-15): the core answer from topic understanding, backed by research (targeted
searches when no fact backs it), the twist picked on relevance to it and surprise (never a side fact), fact beats in
causal order, each beat linked to the one before it (a flow judge per adjacent pair, a weak pair's second line written
again), the hook judged by meaning, and the gate failing a script that never says the core answer. Every model is a
stand-in; nothing is searched."""
import sys
import os as _os
_os.environ.setdefault('STICK_EMBED', '0')     # no real embedding model in the tests
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import bestof, grounding, story, understand  # noqa: E402
from pipeline.models import Episode, Source  # noqa: E402

CORE = 'Compound interest means your interest starts earning interest too.'
F = [Source(id='s1', claim='Compound interest is interest on the principal and on earlier interest.', url='https://x.gov/a'),
     Source(id='s2', claim='The Medici bank used interest in the 1400s.', url='https://x.edu/b'),
     Source(id='s3', claim='Interest added to the balance then earns interest itself.', url='https://x.gov/c'),
     Source(id='s4', claim='Money doubles in about 72 divided by the rate years.', url='https://x.gov/d'),
     Source(id='s5', claim='Frequent compounding grows a balance faster.', url='https://x.gov/e')]


def episode(**interp):
    base = dict(source='model', question='How does compound interest work?', core=dict(text=CORE, fact_ids=['s3']))
    return Episode(slug='ci', topic='How compound interest works', meta=dict(interpretation={**base, **interp}))


class Understanding(unittest.TestCase):
    def test_the_core_answer_is_one_sentence_never_a_question(self):
        got = understand.clean(dict(question='How does compound interest work', title='Compound interest', claims=[],
                                    core_answer='  your interest earns interest  ', about_history=False), 'compund intrest')
        self.assertEqual(got['core_answer'], 'your interest earns interest.')
        self.assertFalse(got['about_history'])
        self.assertEqual(understand.core_text('Why does it grow?'), '')
        self.assertEqual(understand.fallback('x')['core_answer'], '')

    def test_written_from_the_facts_only_when_it_cites_one(self):
        ask = lambda s, u, sc: dict(answer='Interest is added and then earns interest.', fact_ids=['s3', 'nope'])
        self.assertEqual(understand.core_from_facts('q', F, ask),
                         dict(text='Interest is added and then earns interest.', fact_ids=['s3']))
        self.assertEqual(understand.core_from_facts('q', F, lambda *a: dict(answer='A guess.', fact_ids=[])), {})


class Support(unittest.TestCase):
    def run_support(self, verdicts, found=(), from_facts=None):
        verdicts = list(verdicts)
        searched = []
        interp = dict(question='How does compound interest work?', core_answer=CORE)
        ep = Episode(slug='ci', topic='How compound interest works')

        def verify(i, f):
            v, ids = verdicts.pop(0)
            return [dict(claim=i['claims'][0], verdict=v, fact_ids=ids)]
        ask = lambda s, u, sc: from_facts or {}
        with tempfile.TemporaryDirectory() as d:
            facts, notes = story.core_support(ep, interp, list(F), Path(d), None,
                                              search_claim=lambda c: searched.append(c) or (['q1', 'q2'], list(found)),
                                              verify=verify, ask=ask)
        return interp['core'], facts, searched, ep

    def test_a_backed_answer_needs_no_search(self):
        core, facts, searched, _ = self.run_support([('supported', ['s3'])])
        self.assertEqual((core['text'], core['fact_ids'], core['how']), (CORE, ['s3'], 'supported'))
        self.assertEqual(searched, [])

    def test_no_fact_backs_it_so_it_is_searched_for(self):
        new = dict(claim='Interest earns interest on itself over time.', url='https://y.gov/z', quote='interest on interest')
        core, facts, searched, ep = self.run_support([('unverified', []), ('supported', ['s6'])], found=[new])
        self.assertEqual(searched, [CORE])
        self.assertEqual((core['fact_ids'], core['how'], core['queries']), (['s6'], 'searched', ['q1', 'q2']))
        self.assertEqual(facts[-1].id, 's6')
        self.assertEqual(ep.meta['claim_facts'][0]['url'], 'https://y.gov/z')     # the script step gets it too

    def test_still_unbacked_it_is_written_from_the_facts(self):
        core, *_ = self.run_support([('unverified', [])],
                                    from_facts=dict(answer='Interest is added, then earns interest.', fact_ids=['s1']))
        self.assertEqual((core['text'], core['fact_ids'], core['how']),
                         ('Interest is added, then earns interest.', ['s1'], 'from facts'))


class SupportOnTopic(unittest.TestCase):
    def test_written_from_the_facts_only_from_facts_on_topic(self):
        # 2026-11-15 bench: the Roman Republic's answer stood on a fact about the later Empire's migrations
        offered = []

        def ask(system, user, schema):
            if system == grounding.ON_TOPIC_SYSTEM:
                return dict(helps='Medici' not in user, why='')
            offered.append(user)
            return dict(answer='Interest is added, then earns interest.', fact_ids=['s1'])
        interp = dict(question='How does compound interest work?', core_answer=CORE)
        ep = Episode(slug='ci', topic='How compound interest works', meta=dict(interpretation=interp))
        with tempfile.TemporaryDirectory() as d:
            story.core_support(ep, interp, list(F), Path(d), None, search_claim=lambda c: ([], []),
                               verify=lambda i, f: [dict(claim=CORE, verdict='unverified', fact_ids=[])], ask=ask)
        self.assertNotIn('Medici', offered[0])
        self.assertIn('s3: Interest added', offered[0])
        self.assertEqual(interp['core']['how'], 'from facts')


class Twist(unittest.TestCase):
    SCORES = dict(facts=[dict(id='s1', relevance=5, surprise=2, kind='what'),
                         dict(id='s2', relevance=3, surprise=5, kind='history'),
                         dict(id='s3', relevance=5, surprise=3, kind='how'),
                         dict(id='s4', relevance=4, surprise=4, kind='number'),
                         dict(id='s5', relevance=2, surprise=5, kind='effect')])

    def scores(self):
        return grounding.score_facts(F, episode().meta['interpretation'], episode(), lambda s, u, sc: self.SCORES)

    def test_the_most_surprising_of_the_most_relevant_never_a_side_fact(self):
        sc = self.scores()
        self.assertEqual({k: sc['s2'][k] for k in ('relevance', 'surprise', 'kind')}, dict(relevance=3, surprise=5, kind='history'))
        interp = episode().meta['interpretation']
        # s2 (history, most surprising) and s5 (barely relevant) are out; s3 backs the core answer (the payoff's)
        self.assertEqual(grounding.pick_twist(interp, F, sc), 's4')
        self.assertEqual(grounding.pick_twist(dict(interp, about_history=True), F,
                                              {**sc, 's2': dict(relevance=5, surprise=5, kind='history')}), 's2')
        self.assertEqual(grounding.pick_twist(interp, F, {}), grounding.twist_fact(interp, F))   # no judge: as before

    def test_the_prompt_names_the_core_answer(self):
        asked = []
        grounding.score_facts(F, episode().meta['interpretation'], episode(), lambda s, u, sc: asked.append(u) or {})
        self.assertIn(f'Core answer: {CORE}', asked[0])

    def test_a_side_fact_is_never_told_unless_the_question_is_about_history(self):
        # 2026-11-15 bench: compound interest told Fibonacci, the Low Countries and printed books in three beats
        from test_fact_check import GOOD, OIL, Judge, answers, plan_episode
        ep = plan_episode()
        scores = {'s3': dict(relevance=4, surprise=5, kind='history'), 's5': dict(relevance=5, surprise=2, kind='what'),
                  's7': dict(relevance=5, surprise=4, kind='what'), 's8': dict(relevance=5, surprise=3, kind='how')}
        ep.meta['interpretation']['fact_scores'] = scores
        gen = lambda *a: answers()
        plan = grounding.make_plan(ep, OIL, (9, 11), ask=Judge(ok=set(GOOD.values())), generate=gen)
        told = {i for b in plan['beats'] for i in b['fact_ids']}
        self.assertNotIn('s3', told)
        self.assertNotEqual(plan['hook_fact'], 's3')             # nor is the hook built from it
        ep.meta['interpretation']['about_history'] = True
        plan = grounding.make_plan(ep, OIL, (9, 11), ask=Judge(ok=set(GOOD.values())), generate=gen)
        self.assertIn('s3', {i for b in plan['beats'] for i in b['fact_ids']})

    def test_middle_fact_beats_run_what_how_why(self):
        roles = ['hook', 'problem', 'stakes', 'clue', 'clue', 'twist', 'payoff', 'loop']
        sc = dict(a=dict(kind='why', relevance=4), b=dict(kind='what', relevance=4), c=dict(kind='how', relevance=5),
                  t=dict(kind='number', relevance=5), p=dict(kind='what', relevance=5))
        got = grounding.chain_order(roles, {1: 'p', 2: 'a', 3: 'c', 4: 'b', 5: 't', 6: 'p'}, sc)
        self.assertEqual([got[k] for k in (2, 3, 4)], ['b', 'c', 'a'])          # what, how, why
        self.assertEqual((got[1], got[5], got[6]), ('p', 't', 'p'))             # problem, twist, payoff stay


class SaysCore(unittest.TestCase):
    def test_the_code_check_needs_more_than_the_topics_name(self):
        ep = episode()
        self.assertTrue(grounding.says_core('So your interest earns interest of its own.', CORE, ep))
        self.assertFalse(grounding.says_core('Compound interest is a big deal.', CORE, ep))
        self.assertTrue(grounding.states_core('So your interest earns interest of its own.', CORE, ep,
                                              lambda *a: dict(states=True, why='')))
        self.assertFalse(grounding.states_core('So your interest earns interest of its own.', CORE, ep,
                                               lambda *a: dict(states=False, why='')))

    def test_the_gate_fails_a_script_that_never_says_it(self):
        from test_auto_and_qa_fixes import host_episode
        ep = host_episode()
        ep.meta['interpretation'] = dict(core=dict(text=CORE, fact_ids=[]))
        report = {name: ok for name, ok, _ in ep.story_report()}
        self.assertFalse(report['core answer stated'])
        pay = next(b for b in ep.beats if b.role == 'payoff')
        pay.narration = 'And that is how your interest earns interest too.'
        got = {name: ok for name, ok, _ in ep.story_report()}
        self.assertTrue(got['core answer stated'])
        self.assertTrue(got['payoff answers the hook'])               # the same check as the writer's


class Flow(unittest.TestCase):
    def test_weak_pairs_are_read_weakest_first(self):
        ask = lambda s, u, sc: dict(pairs=[dict(second=2, score=4, why=''), dict(second=3, score=1, why='a jump'),
                                           dict(second=4, score=2, why='new point'), dict(second=9, score=1, why='')])
        self.assertEqual(grounding.weak_pairs(['a', 'b', 'c', 'd'], ask), [(2, 1, 'a jump'), (3, 2, 'new point')])

    def test_a_weak_pair_gets_its_second_line_written_again_and_kept_only_when_better(self):
        ep = episode()
        pbs = [dict(role='hook', kind='question', fact_ids=[], claim=''),
               dict(role='clue', kind='fact', fact_ids=['s1'], claim=F[0].claim),
               dict(role='twist', kind='fact', fact_ids=['s4'], claim=F[3].claim),
               dict(role='loop', kind='callback', fact_ids=[], claim='')]
        chosen = ['Why does money grow by itself?', 'Interest is paid on interest already earned.',
                  'Rates are set by banks.', 'Now money grows by itself.']
        beats = [dict(narration=x, source_ids=[]) for x in chosen]
        calls = []

        def ask(system, user, schema):
            if system == grounding.FLOW_SYSTEM and user.count('\n') > 3:     # the whole script
                return dict(pairs=[dict(second=3, score=1, why='a jump to banks')])
            if system == grounding.FLOW_SYSTEM:                               # one pair, after the rewrite
                return dict(pairs=[dict(second=2, score=4, why='')])
            calls.append(system)
            return {}
        new = 'So that money doubles in about 72 divided by the rate years.'
        orig = bestof.write_beat
        try:
            bestof.write_beat = lambda *a, **k: dict(line=new, kept=[new], ids=['s4'], tried=[new], rejected={},
                                                     beat='b03', role='twist', kind='fact', retried=k.get('retried'))
            done = bestof.flow_pass(ep, F, beats, pbs, chosen, 40, (6, 12), ask)
        finally:
            bestof.write_beat = orig
        self.assertEqual(done[0]['beat'], 'b03')
        self.assertEqual((done[0]['score'], done[0]['new_score']), (1, 4))
        self.assertEqual(chosen[2], new)
        self.assertEqual(beats[2]['narration'], new)


class Links(unittest.TestCase):
    def test_a_link_word_opens_at_most_two_lines_never_two_in_a_row(self):
        ep = episode()
        plan = [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='clue', kind='transition', fact_ids=[], claim=''),
                dict(role='clue', kind='transition', fact_ids=[], claim=''),
                dict(role='loop', kind='callback', fact_ids=[], claim='')]
        from test_fact_check import BestOfN
        t = BestOfN('setUp')
        t.prompts, t.ranked = [], []
        e = bestof.write_beat(ep, F, plan, 2, ['Why does money grow?', 'But nobody adds a cent.'], 'loop', (2, 9),
                              t.ask(['But who pays for it?', 'Then what happens next?']))
        self.assertIn('opens with "but"', e['rejected']['But who pays for it?'])
        self.assertEqual(e['line'], 'Then what happens next?')
        self.assertEqual(bestof.link_word('So, what now?'), 'so')


class Gate(unittest.TestCase):
    def test_no_line_saying_it_makes_the_payoff_the_core_answer(self):
        ep = episode()
        pbs = [dict(role='hook', kind='question', fact_ids=[], claim=''),
               dict(role='twist', kind='fact', fact_ids=['s4'], claim=F[3].claim),
               dict(role='payoff', kind='fact', fact_ids=['s1'], claim=F[0].claim, core=CORE),
               dict(role='loop', kind='callback', fact_ids=[], claim='')]
        chosen = ['Why does money grow?', 'It doubles in about 72 over the rate years.', 'Banks love this.', 'Now you know.']
        beats = [dict(narration=x, source_ids=[]) for x in chosen]
        orig = bestof.write_beat
        try:
            bestof.write_beat = lambda *a, **k: dict(line='Banks pay you.', kept=[], ids=[], tried=[], rejected={},
                                                     beat='b03', role='payoff', kind='fact')
            got = bestof.core_gate(ep, F, beats, pbs, chosen, 40, (6, 12), lambda *a: dict(states=True))
        finally:
            bestof.write_beat = orig
        self.assertEqual(got['how'], 'payoff is the core answer')
        self.assertEqual(chosen[2], CORE)
        self.assertIn('s3', beats[2]['source_ids'])                  # it stands on the fact that backs it

    def test_a_payoff_line_must_say_the_core_answer(self):
        ep = episode()
        plan = [dict(role='hook', kind='question', fact_ids=[], claim=''),
                dict(role='payoff', kind='fact', fact_ids=['s3'], claim=F[2].claim, core=CORE),
                dict(role='loop', kind='callback', fact_ids=[], claim='')]
        lines = ['Banks quietly love this.', 'So your interest earns interest of its own.']
        from test_fact_check import BestOfN
        t = BestOfN('setUp')
        t.prompts, t.ranked = [], []
        base = t.ask(lines)
        ask = lambda s, u, sc: dict(states=True, why='') if s == grounding.STATES_SYSTEM else base(s, u, sc)
        e = bestof.write_beat(ep, F, plan, 1, ['Why does money grow?'], 'loop', (3, 12), ask)
        self.assertIn('does not say the core answer', e['rejected']['Banks quietly love this.'])
        self.assertEqual(e['line'], lines[1])
        self.assertIn(f'must say the core answer (plain words of your own are fine): {CORE}', t.prompts[0])


if __name__ == '__main__':
    unittest.main()
