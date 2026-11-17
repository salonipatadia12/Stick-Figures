"""Whole-script best-of-N (pipeline/whole.py, user 2026-11-16): N complete scripts from the fact-first outline, the
checks as filters on whole scripts, the best survivor kept, only failing lines repaired, no raw fallback; repeats by
meaning (pipeline/embed.py); the gentler fact check; the twist that changes the viewer's mind. The models are stand-ins."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('STICK_EMBED', '0')          # no real embedding model in the tests
sys.path.insert(0, str(Path(__file__).parent))
from pipeline import bestof, embed, entail, grounding, whole  # noqa: E402
from test_fact_check import OIL, S3, S5, S7, S8, plan_episode  # noqa: E402

CORE = 'Oil formed from tiny sea plants and animals heated under pressure.'
PLAN = [dict(role='hook', kind='question', fact_ids=[], claim=''),
        dict(role='setup', kind='myth', fact_ids=[], claim=''),
        dict(role='problem', kind='reaction', fact_ids=[], claim=''),
        dict(role='clue', kind='fact', fact_ids=['s5'], claim='Oil formed before dinosaurs lived.'),
        dict(role='reveal', kind='fact', fact_ids=['s3'], claim='Heat turned the remains of living things into oil.'),
        dict(role='twist', kind='fact', fact_ids=['s7'], claim='Oil came from tiny sea plants and animals.'),
        dict(role='payoff', kind='fact', fact_ids=['s8'], claim='Heat and pressure turned the remains into oil.'),
        dict(role='loop', kind='callback', fact_ids=[], claim='')]
GOOD = ['Did dinosaurs really fill your gas tank?',
        'Many think oil comes from dinosaurs that died long ago.',
        'Hold on, is that even true?',
        'Here is the thing: crude oil was already forming before any dinosaur walked.',
        'Buried deep, slow heat cooked what was left of living things into crude oil.',
        'The real source? Tiny plants and animals from ancient seas, long before the dinosaurs.',
        'So your fuel is ancient sea life, plants and animals pressed and heated into oil.',
        'Dinosaurs in your tank? Not even close.']


def episode():
    ep = plan_episode()
    ep.meta['interpretation'].update(twist=dict(id='s7', claim=S7.claim), core=dict(text=CORE, fact_ids=['s7', 's8']))
    return ep


class Judge:
    """The stand-in judge: a line fails the claims check when it is in `bad`; the hook gap comes from `gaps`."""

    def __init__(self, bad=(), gaps=None, rewrites=(), weak=None):
        self.bad, self.gaps, self.rewrites, self.weak = set(bad), dict(gaps or {}), list(rewrites), dict(weak or {})
        self.calls = []

    def __call__(self, system, user, schema):
        self.calls.append(system)
        if system == entail.SPLIT_SYSTEM:
            line = user.removeprefix('Line: ')
            if '?' in line or line.startswith('Hold on'):
                return dict(claims=[], nonsense=[])
            kind = 'belief' if line.startswith('Many think') else 'fact'
            return dict(claims=[dict(text=line, kind=kind)], nonsense=[])
        if system in (entail.CHECK_SYSTEM, entail.NONFACT_SYSTEM):
            sentence = user.split('\n', 1)[0].split(': ', 1)[1]
            bad = sentence in self.bad
            return dict(states_a_fact=True, adds=[sentence] if bad else [], contradicts=[], supported=not bad, why='x')
        if system == whole.SCORE_SYSTEM:
            hook = user.split('Hook: ', 1)[1]
            return dict(gap=self.gaps.get(hook, 3), why='x')
        if system == grounding.FLOW_SYSTEM:
            lines = [x.split('. ', 1)[1] for x in user.split('\n')[1:]]
            return dict(pairs=[dict(second=k + 1, score=self.weak.get(lines[k], 4), why='x') for k in range(1, len(lines))])
        if system == bestof.WRITE_SYSTEM:
            return dict(lines=self.rewrites)
        if system == bestof.RANK_SYSTEM:
            return dict(best=1, why='x')
        if system == grounding.POINTS_SYSTEM:
            return dict(points=True, why='x')
        if system == grounding.REVEALS_SYSTEM:
            return dict(reveals=False, why='x')
        if system == grounding.STATES_SYSTEM:
            return dict(states=True, why='x')
        if system == bestof.ASSERTS_SYSTEM:
            return dict(says_it_is_true=False, why='x')
        if system == bestof.BELIEF_SYSTEM:
            return dict(real_belief=True, why='x')
        raise AssertionError(f'unexpected call: {system[:60]}')


def outline():
    return dict(beats=[dict(role=p['role'], narration='', source_ids=[]) for p in PLAN],
                hook_options=[dict(line='', answer='', loop_line='') for _ in range(3)])


def run(scripts, judge, words=90, rate=2.7):
    o, plan = outline(), dict(twist='s7', hook_fact='s5', answer_words=[], beats=[dict(p) for p in PLAN])
    entry = whole.write(episode(), OIL, o, plan, words, judge, rate,
                        write_script=lambda s, u, sc, k: dict(lines=scripts[k % len(scripts)]))
    return entry, o, plan


class WholeScripts(unittest.TestCase):
    def test_the_good_script_passes_every_check(self):
        ctx = whole.context(episode(), OIL, dict(twist='s7', hook_fact='s5', beats=PLAN), 90)
        self.assertEqual(whole.check(GOOD, ctx, Judge())['problems'], {})

    def test_the_best_survivor_is_kept(self):
        weaker = list(GOOD)
        weaker[0] = 'Did dinosaurs make the gas in your car?'
        broken = list(GOOD)
        broken[5] = S7.claim                                              # its fact's own sentence
        entry, o, _ = run([broken, weaker, GOOD, broken], Judge(gaps={GOOD[0]: 5, weaker[0]: 2}))
        self.assertTrue(entry['survived'])
        self.assertEqual(entry['winner'], 3)
        self.assertEqual([b['narration'] for b in o['beats']], GOOD)
        self.assertIn('copies its fact sentence', entry['scripts'][0]['problems'][5])
        self.assertEqual(o['hook_options'][0]['line'], GOOD[0])
        self.assertEqual(o['hook_options'][1]['line'], weaker[0])        # every option is a hook that passed
        self.assertEqual(o['beats'][3]['source_ids'], ['s5'])
        self.assertEqual(o['beats'][6]['source_ids'], ['s8', 's7'])       # the payoff stands on the core's facts too

    def test_no_survivor_only_the_failing_line_is_written_again(self):
        bad = list(GOOD)
        bad[4] = 'Buried deep, tiny bacteria cooked what was left into crude oil.'
        fixed = 'Down there, slow heat turned the remains of living things into crude oil.'
        entry, o, _ = run([bad], Judge(bad={bad[4]}, rewrites=[fixed]))
        self.assertFalse(entry['survived'])
        self.assertEqual(entry['repairs'][0]['beat'], 'b05')
        self.assertTrue(entry['repairs'][0]['fixed'])
        self.assertEqual(o['beats'][4]['narration'], fixed)
        self.assertEqual([b['narration'] for k, b in enumerate(o['beats']) if k != 4], [x for k, x in enumerate(GOOD) if k != 4])

    def test_no_raw_fallback_a_line_nothing_fixes_is_dropped_when_the_video_stays_long_enough(self):
        bad = list(GOOD)
        bad[4] = 'Buried deep, tiny bacteria cooked what was left into crude oil.'
        entry, o, plan = run([bad], Judge(bad={bad[4]}, rewrites=['Now you know.', S3.claim]), words=60, rate=1.5)
        self.assertEqual([d['beat'] for d in entry['dropped']], ['b05'])
        self.assertEqual(len(o['beats']), 7)
        self.assertEqual(len(plan['beats']), 7)
        self.assertNotIn('Now you know.', [b['narration'] for b in o['beats']])
        self.assertNotIn(S3.claim, [b['narration'] for b in o['beats']])
        # too short to lose a beat: the line stays, reported (never a stock line or the research sentence)
        entry, o, _ = run([bad], Judge(bad={bad[4]}, rewrites=['Now you know.']), words=60, rate=2.7)
        self.assertEqual(entry['dropped'], [])
        self.assertEqual(entry['unresolved'][0]['beat'], 'b05')
        self.assertEqual(o['beats'][4]['narration'], bad[4])

    def test_a_failing_line_first_comes_from_the_next_best_script(self):
        one, two = list(GOOD), list(GOOD)
        one[7] = 'Now you know.'                                  # script 1: a stock loop
        two[0] = 'Why does it matter where things come from, really, after all?'   # script 2: a hook too long
        judge = Judge(rewrites=['never asked for'])
        entry, o, _ = run([one, two], judge)
        self.assertEqual(entry['winner'], 1)
        self.assertEqual(o['beats'][7]['narration'], GOOD[7])    # script 2's loop passes here: no rewrite needed
        self.assertTrue(entry['repairs'][0]['fixed'])
        self.assertNotIn(bestof.WRITE_SYSTEM, judge.calls)

    def test_stock_lines_and_beliefs_outside_the_myth_beat_fail(self):
        ctx = whole.context(episode(), OIL, dict(twist='s7', hook_fact='s5', beats=PLAN), 90)
        lines = list(GOOD)
        lines[7] = 'Now you know.'
        lines[2] = 'But there is a catch.'
        lines[4] = 'Most people believe heat cooked what was left of living things into oil.'
        got = whole.check(lines, ctx, Judge())['problems']
        self.assertEqual(got[7], 'a stock line')
        self.assertEqual(got[2], 'a stock line')
        self.assertIn('reports a belief', got[4])

    def test_a_weak_pair_is_written_again_only_if_it_scores_higher(self):
        better = 'So the first clue: crude oil was already forming before any dinosaur walked.'
        judge = Judge(weak={GOOD[3]: 1}, rewrites=[better])
        entry, o, _ = run([GOOD], judge)
        self.assertEqual(entry['flow'][0]['beat'], 'b04')
        self.assertEqual(o['beats'][3]['narration'], better)
        judge = Judge(weak={GOOD[3]: 1, better: 1}, rewrites=[better])    # the rewrite is no better: kept as it was
        entry, o, _ = run([GOOD], judge)
        self.assertEqual(o['beats'][3]['narration'], GOOD[3])

    def test_the_outline_names_one_point_per_beat(self):
        ctx = whole.context(episode(), OIL, dict(twist='s7', hook_fact='s5', beats=PLAN), 90)
        text = whole.outline_text(ctx)
        self.assertIn('Its one point: Oil came from tiny sea plants and animals.', text)
        self.assertIn(f'Its one point: {CORE}', text)                    # the payoff says the core answer
        self.assertIn('ONLY its own beat', whole.WRITE_SYSTEM)

    def test_the_writer_prompt_names_no_benchmark_topic(self):
        for word in ('oil', 'gold', 'dinosaur', 'yawn', 'sky', 'rome', 'roman', 'interest'):
            self.assertNotRegex(whole.WRITE_SYSTEM.lower(), rf'\b{word}')
            self.assertNotRegex(entail.CHECK_SYSTEM.lower(), rf'\b{word}')


class Repeats(unittest.TestCase):
    """Repeats by meaning: vectors close in raw cosine, or fairly close and still close once the shared subject is
    taken out; the payoff may restate one line; myth, hook-payoff and loop pairs are not repeats."""

    def ctx(self):
        return whole.context(episode(), OIL, dict(twist='s7', hook_fact='s5', beats=PLAN), 90)

    def test_similar_pairs(self):
        vecs = [[1, 0, 0], [0.99, 0.1, 0], [0, 1, 0], [0, 0, 1]]
        self.assertEqual([p[:2] for p in embed.similar_pairs(['a', 'b', 'c', 'd'], vecs)], [(0, 1)])

    def test_the_payoff_may_restate_once(self):
        lines = list(GOOD)
        same = {GOOD[4], GOOD[5], GOOD[6]}                  # reveal, twist and payoff say one thing
        vecs = [[1.0 if d == (8 if x in same else k) else 0.0 for d in range(9)] for k, x in enumerate(lines)]
        with mock.patch.object(embed, 'vectors', lambda ls: [vecs[lines.index(x)] for x in ls]):
            got = whole.repeats(lines, self.ctx())
        self.assertEqual(sorted(got), [5])                  # 4 = 5 = 6: the twist goes; the payoff may restate the reveal
        self.assertIn('says the same as', got[5])

    def test_without_the_model_shared_words_stand_in(self):
        lines = list(GOOD)
        lines[5] = lines[4]
        with mock.patch.object(embed, 'vectors', lambda ls: None):
            self.assertIn(5, whole.repeats(lines, self.ctx()))


class GentlerFactCheck(unittest.TestCase):
    """User 2026-11-16: reject only a line that ADDS a claim or CONTRADICTS its fact; simplifying is fine."""

    def ask(self, adds=(), contradicts=(), supported=False):
        return lambda s, u, sc: dict(states_a_fact=True, adds=list(adds), contradicts=list(contradicts),
                                     supported=supported, why='x')

    def test_leaving_details_out_passes_and_contradicting_fails(self):
        research = 's7: The longer the time period, the more you earn from compound interest in a savings account.'
        ok, _ = entail.supported('Longer time periods mean greater earnings.', research,
                                 self.ask(adds=['savings account']))       # the judge's "omits 'savings account'"
        self.assertTrue(ok)
        ok, why = entail.supported('Longer time periods mean smaller earnings.', research,
                                   self.ask(contradicts=['smaller earnings']))
        self.assertFalse(ok)
        self.assertIn('contradicts', why)
        ok, _ = entail.supported('Longer time periods pay dividends.', research, self.ask(adds=['pay dividends']))
        self.assertFalse(ok)

    def test_a_narrowed_range_still_fails(self):
        research = 's1: Oil formed over thousands to millions of years.'
        ok, why = entail.verdict('Oil formed over thousands of years.', research, set(),
                                 lambda s, u, sc: dict(claims=[dict(text='Oil formed over thousands of years.', kind='fact')],
                                                       nonsense=[]) if s == entail.SPLIT_SYSTEM else
                                 dict(states_a_fact=True, adds=[], contradicts=[], supported=True, why=''))
        self.assertFalse(ok)


class TwistQuality(unittest.TestCase):
    """The twist changes the viewer's mind: never a list, a definition or a name; a myth's twist corrects it."""

    def test_lists_and_definitions_are_never_the_twist(self):
        from pipeline.models import Source
        types = Source(id='s9', claim='Kerogen is classified into three types based on its primary source.',
                       url='https://a.edu/x', quote='three types')
        interp = dict(question='Where does oil come from?')
        sc = {'s9': dict(relevance=5, surprise=5, kind='how', changes_mind=True, corrects_myth=False),
              's8': dict(relevance=5, surprise=2, kind='how', changes_mind=True, corrects_myth=False)}
        self.assertFalse(grounding.can_twist(types, sc['s9'], interp))      # "classified" in code
        self.assertEqual(grounding.pick_twist(interp, [types, S8], sc), 's8')
        self.assertFalse(grounding.can_twist(S8, dict(sc['s8'], kind='definition'), interp))
        self.assertFalse(grounding.can_twist(S8, dict(sc['s8'], changes_mind=False), interp))

    def test_a_myth_topic_twists_on_the_fact_that_corrects_it(self):
        interp = plan_episode().meta['interpretation']
        sc = {'s5': dict(relevance=5, surprise=5, kind='history', changes_mind=True, corrects_myth=True),
              's3': dict(relevance=5, surprise=4, kind='how', changes_mind=True, corrects_myth=False),
              's8': dict(relevance=5, surprise=3, kind='how', changes_mind=True, corrects_myth=False)}
        # "before dinosaurs" corrects the myth: the twist even though it is history and backs the core answer
        self.assertEqual(grounding.pick_twist(dict(interp, core=dict(fact_ids=['s5'])), [S3, S5, S8], sc), 's5')
        # a history fact that does not correct the myth stays out
        self.assertFalse(grounding.can_twist(S5, dict(sc['s5'], corrects_myth=False), interp))


if __name__ == '__main__':
    unittest.main()
