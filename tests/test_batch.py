"""Fewer Claude Code calls (user 2026-11-18): under claude-code every check of a script goes out in one call (each check
keeps its own prompt and code), the staging of every beat in one call; staging directions never reach the screen; the
fact check fails a line that turns "some X" into "X". No Claude Code process runs here: the judge is a stand-in."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('STICK_EMBED', '0')          # no real embedding model in the tests
sys.path.insert(0, str(Path(__file__).parent))
from pipeline import batch, claude_code, entail, llm, whole  # noqa: E402
from pipeline.models import staging_leak, unstaged  # noqa: E402
import test_fact_check as tf  # noqa: E402
import test_whole_script as tw  # noqa: E402

SCHEMA = dict(type='object', required=['ok'], properties=dict(ok=dict(type='boolean')))


def no_cli(*a, **kw):
    raise AssertionError('the Claude Code CLI was called')


class Batched:
    """A judge that can batch: `.many` answers each question with the stand-in, and counts the calls."""

    def __init__(self, judge):
        self.judge, self.single, self.batches = judge, 0, []

    def __call__(self, system, user, schema):
        self.single += 1
        return self.judge(system, user, schema)

    def many(self, questions, what=''):
        self.batches.append((what, len(questions)))
        return [self.judge(s, u, sc) for s, u, sc in questions]


class LineWhole(tw.Judge):
    """test_whole_script's judge, with the split and the check as one question (Claude Code)."""

    def __call__(self, system, user, schema):
        if system == entail.LINE_SYSTEM:
            line = user.split('\n', 1)[0].removeprefix('Line: ')
            self.calls.append(system)
            if '?' in line or line.startswith('Hold on'):
                return dict(claims=[], nonsense=[], why='x')
            kind = 'belief' if line.startswith('Many think') else 'fact'
            bad = line in self.bad
            return dict(claims=[dict(text=line, kind=kind, adds=[line] if bad else [], contradicts=[])], nonsense=[],
                        why='x')
        return super().__call__(system, user, schema)


class LineFacts(tf.Judge):
    """test_fact_check's judge, with the split and the check as one question (Claude Code)."""

    def __call__(self, system, user, schema):
        if system == entail.LINE_SYSTEM:
            self.calls.append((system, user))
            line = user.split('\n', 1)[0].removeprefix('Line: ')
            cites = 'Cited research:' in user
            facty = line in self.uncited_facts or (line not in tf.NO_CLAIM_LINES and not line.endswith('?'))
            if not facty:
                return dict(claims=[], nonsense=[], why='')
            ok = line in self.ok or not cites
            return dict(claims=[dict(text=line, kind='fact', adds=[] if ok else [line], contradicts=[])], nonsense=[],
                        why='the fact says organic matter')
        return super().__call__(system, user, schema)


class Many(unittest.TestCase):
    def test_one_call_the_rules_written_once_the_answers_in_order(self):
        seen = []

        def gen(system, user, schema, max_tokens, **kw):
            seen.append((system, user, schema, kw['detail']))
            return {'q1': dict(ok=True), 'q2': dict(ok=False), 'q3': 'junk'}
        qs = [('RULES A', 'first', SCHEMA), ('RULES B', 'second', SCHEMA), ('RULES A', 'third', SCHEMA)]
        with mock.patch.object(llm, 'generate_json', gen):
            got = batch.many(qs, step='fact-check', detail='checks of script 1')
        self.assertEqual(got, [dict(ok=True), dict(ok=False), None])   # no usable answer: None, asked alone later
        self.assertEqual(len(seen), 1)
        system, user, schema, detail = seen[0]
        self.assertEqual(system.count('RULES A'), 1)                  # each rule text once
        self.assertIn('=== q3 (rules R1)\nthird', user)
        self.assertEqual(schema['required'], ['q1', 'q2', 'q3'])
        self.assertEqual(detail, 'checks of script 1: 3 checks in one call')

    def test_many_questions_go_out_in_a_few_calls(self):
        calls = []

        def gen(system, user, schema, max_tokens, **kw):
            calls.append(len(schema['required']))
            return {k: dict(ok=True) for k in schema['required']}
        with mock.patch.object(llm, 'generate_json', gen), mock.patch.object(batch, 'MAX_PER_CALL', 2):
            got = batch.many([('S', f'u{k}', SCHEMA) for k in range(5)])
        self.assertEqual((sorted(calls), len(got)), ([1, 2, 2], 5))


class MemoAndRun(unittest.TestCase):
    def check(self, ask):
        def q(u):                                    # like every check: no answer is a pass, and it goes on
            try:
                return ask('S', u, SCHEMA)
            except Exception:
                return {}
        a, b = q('one'), q('two')
        return [a, b] + ([q('three: only when two passed')] if b.get('ok') else [])

    def judge(self):
        return Batched(lambda s, u, sc: dict(ok=True))

    def test_off_the_claude_code_route_each_question_is_asked_alone(self):
        j = self.judge()
        self.assertEqual(batch.run(self.check, j), [dict(ok=True)] * 3)
        self.assertEqual((j.single, j.batches), (3, []))             # the 9B: exactly as before

    def test_on_the_route_one_call_then_only_what_depends_on_an_answer(self):
        j = self.judge()
        with mock.patch.object(claude_code, 'ask', no_cli), llm.claude_route():
            got = batch.run(self.check, j, what='a check')
        self.assertEqual(got, [dict(ok=True)] * 3)
        self.assertEqual(j.batches, [('a check', 2)])                 # one and two together
        self.assertEqual(j.single, 1)                                 # three: it depends on two's answer

    def test_an_ask_that_cannot_batch_is_used_as_it_is(self):
        calls = []
        ask = lambda s, u, sc: calls.append(u) or dict(ok=True)
        with mock.patch.object(claude_code, 'ask', no_cli), llm.claude_route():
            batch.run(self.check, ask)
        self.assertEqual(calls, ['one', 'two', 'three: only when two passed'])


class OneCallPerScript(unittest.TestCase):
    def test_every_check_of_each_script_in_one_call(self):
        j = Batched(LineWhole())
        scripts = [tw.GOOD, tw.GOOD[:3] + ['Here is the thing: crude oil was forming before any dinosaur walked.']
                   + tw.GOOD[4:]]
        with mock.patch.object(claude_code, 'ask', no_cli), llm.claude_route():
            entry, o, _ = tw.run(scripts, j)
        self.assertIn(len(j.batches), (2, 3, 4))                    # one per script (two are alike): lines, scores
        self.assertTrue(all(what.startswith('checks of script') for what, _ in j.batches))
        self.assertEqual(j.single, 0)                                 # nothing asked on its own
        self.assertTrue(entry['survived'])
        self.assertEqual(o['beats'][0]['narration'], tw.GOOD[0])

    def test_the_9b_still_asks_one_question_at_a_time(self):
        j = Batched(tw.Judge())
        entry, _, _ = tw.run([tw.GOOD], j)
        self.assertEqual(j.batches, [])
        self.assertGreater(j.single, 20)
        self.assertTrue(entry['survived'])

    def test_a_failing_line_is_repaired_from_the_answers_already_fetched(self):
        bad = 'Buried deep, slow heat cooked what was left of living things into crude oil, said every geologist.'
        first = tw.GOOD[:4] + [bad] + tw.GOOD[5:]
        j = Batched(LineWhole(bad={bad}))
        with mock.patch.object(claude_code, 'ask', no_cli), llm.claude_route():
            entry, o, _ = tw.run([first, tw.GOOD], j)
        self.assertEqual(o['beats'][4]['narration'], tw.GOOD[4])     # the other script's line, already checked
        self.assertEqual(j.single, 0)


class OneCallFactCheck(unittest.TestCase):
    def test_every_line_and_text_in_one_call_with_the_same_result(self):
        plain = tf.episode_with({1: tf.PLAIN})
        j = Batched(LineFacts(ok={tf.PLAIN}))
        with mock.patch.object(claude_code, 'ask', no_cli), llm.claude_route():
            out = entail.check(plain, ask=j)
        self.assertEqual((len(j.batches), j.single), (1, 0))
        self.assertEqual(out['changed'], [])
        self.assertEqual(out['batched']['asked_alone'], 0)

    def test_a_failing_line_is_fixed_as_before(self):
        def run(route):
            ep = tf.episode_with({1: tf.BACTERIA})
            j = Batched((LineFacts if route else tf.Judge)(ok={tf.PLAIN}, rewrites=[tf.PLAIN]))
            if route:
                with mock.patch.object(claude_code, 'ask', no_cli), llm.claude_route():
                    return entail.check(ep, ask=j), ep, j
            return entail.check(ep, ask=j), ep, j
        before, ep0, _ = run(False)
        after, ep1, j = run(True)
        self.assertEqual([b.narration for b in ep1.beats], [b.narration for b in ep0.beats])
        self.assertEqual([(c['beat'], c['action'], c['now']) for c in after['changed']],
                         [(c['beat'], c['action'], c['now']) for c in before['changed']])
        self.assertEqual(len(j.batches), 1)
        self.assertLessEqual(j.single, 2)                            # the rewrite and its check, as before


class StagingDirections(unittest.TestCase):
    def test_directions_are_found(self):
        for text in ("Slam 'BLUE SCATTERS, RED GOES STRAIGHT' at the bottom of the frame.",
                     '10 METERS DEEP lands at the top of the stack', '"HEAT CHANGES IT"', 'Zoom on the rock',
                     'Camera pans left'):
            self.assertTrue(staging_leak(text), text)
        for text in ('BLUE SCATTERS MORE', 'MYTH: Dinosaurs?', "Earth's crust", 'Heat + pressure', '10 METERS DEEP',
                     'Ocean bottom', 'Top predator', 'Time frame', 'Top layer', 'Cut costs', 'Pop culture'):   # real noun phrases
            self.assertEqual(staging_leak(text), '', text)

    def test_the_quoted_words_are_kept(self):
        self.assertEqual(unstaged("Slam 'BLUE SCATTERS, RED GOES STRAIGHT' at the bottom of the frame."),
                         'BLUE SCATTERS, RED GOES STRAIGHT')
        self.assertEqual(unstaged('Slam it at the top'), '')

    def test_the_gate_refuses_a_direction_on_screen(self):
        from test_auto_and_qa_fixes import host_episode
        ep = host_episode()
        b = ep.beats[1]
        b.on_screen_text = ''
        b.shot.motion[0].recipe, b.shot.motion[0].detail = 'text_slam', 'Slam it at the bottom of the frame'
        msgs = [i.message for i in ep.check(['lead'], 2.7) if i.severity == 'error' and i.where == b.id]
        self.assertTrue(any('staging direction' in m for m in msgs), msgs)

    def test_the_fact_check_repairs_it(self):
        ep = tf.episode_with({1: tf.PLAIN})
        b = ep.beats[1]
        b.on_screen_text = ''
        b.shot.motion[0].recipe, b.shot.motion[0].anchor = 'text_slam', b.narration.split()[0]
        b.shot.motion[0].detail = "Slam 'ONCE ALIVE' at the bottom of the frame"
        out = entail.check(ep, ask=tf.Judge(ok={tf.PLAIN}))
        self.assertEqual(b.shot.motion[0].detail, 'ONCE ALIVE')
        self.assertIn('staging direction', [c for c in out['changed'] if c['field'] == 'slammed text'][0]['why'])

    def test_the_staging_answer_keeps_only_the_quoted_words(self):
        from pipeline import script
        self.assertEqual(script._shown(dict(recipe='text_slam', detail="Slam 'HEAT WINS' at the top")), 'HEAT WINS')
        self.assertEqual(script._shown(dict(recipe='push_in', detail='slow push to the top')), 'slow push to the top')


class OverGeneralising(unittest.TestCase):
    def test_the_check_prompt_names_the_case(self):
        for system in (entail.CHECK_SYSTEM, entail.LINE_SYSTEM):
            self.assertIn('OVER-GENERALISES', system)
            self.assertIn('only some', system)
        self.assertNotIn('qualifiers out', entail.CHECK_SYSTEM)     # no longer listed as fine

    def test_some_x_said_as_x_fails(self):
        research = 's1: Oil forms in organic-rich mudrocks; not all mudrocks are organic-rich.'
        line = 'The birthplace is mudrock, rich in organic matter.'
        ask = lambda s, u, sc: dict(claims=[dict(text=line, kind='fact', adds=[],
                                                 contradicts=['all mudrock is rich in organic matter'])],
                                    nonsense=[], why='only some mudrocks are organic-rich')
        ok, why = entail.line_verdict(line, research, set(), ask, entail.CHECK_SYSTEM)
        self.assertFalse(ok)
        self.assertIn('over-generalises', why)


class StagingInOneCall(unittest.TestCase):
    def test_every_beat_staged_in_one_call(self):
        from pipeline import script
        calls = []

        def ask(system, user, json_schema=None, **kw):
            calls.append(json_schema)
            return json.dumps({k: dict(ok=True) for k in json_schema['required']}), dict(seconds=1.0, input=1, output=1)
        o = dict(beats=[dict(narration=f'line {k}') for k in range(3)])
        with mock.patch.object(script, 'beat_schema', lambda ob, i, cast: SCHEMA), \
                mock.patch.object(script, '_beat_prompt', lambda ctx, o, i, schema, prev=None, issues=(): f'beat {i}'), \
                mock.patch.object(claude_code, 'ask', ask), llm.claude_route():
            got = script._stage_beats(dict(cast=['lead']), o, [0, 1, 2], None, None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['required'], ['q1', 'q2', 'q3'])
        self.assertEqual(got, {0: dict(ok=True), 1: dict(ok=True), 2: dict(ok=True)})


if __name__ == '__main__':
    unittest.main()
