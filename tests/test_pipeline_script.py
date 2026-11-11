import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import anthropic
import httpx2

from pipeline import script, styles
from pipeline.cost import BudgetExceeded, Ledger
from pipeline.models import Episode, Storyline
from pipeline_fixtures import FakeClient, anthropic_provider, research_response, script_response, storyline


def api_error(status):
    request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
    cls = {400: anthropic.BadRequestError, 529: anthropic.InternalServerError}[status]
    return cls('error', response=httpx2.Response(status, request=request), body=None)


class ScriptEngine(unittest.TestCase):
    """The paid path (SCRIPT_PROVIDER=anthropic) with a fake client; the free path is OllamaScript below."""
    def setUp(self):
        anthropic_provider(self)
        self.dir = tempfile.TemporaryDirectory()
        self.base = Path(self.dir.name)
        self.style = styles.load('cozy')
        self.episode = Episode(slug='salary', topic='Why your salary disappears by the 20th',
                               niche='personal finance', target_length=45,
                               storylines=[Storyline.model_validate(storyline())], storyline_pick=1)
        self.ledger = Ledger.open(self.base / 'ledger.json', 2.0)

    def tearDown(self):
        self.dir.cleanup()

    def run_generate(self, client, **kwargs):
        return script.generate(self.episode, self.style, self.base, self.ledger, client=client, **kwargs)

    def test_two_budgeted_calls_produce_a_cited_episode(self):
        client = FakeClient([research_response(), script_response()])
        episode, notes = self.run_generate(client)
        self.assertEqual(len(episode.beats), 8)
        self.assertEqual([s.id for s in episode.sources], ['s1', 's2'])
        self.assertTrue(episode.sources[0].url.startswith('https://www.example.edu/'))
        self.assertEqual([e['step'] for e in self.ledger.entries], ['research', 'script'])
        self.assertTrue(all(e['status'] == 'ok' for e in self.ledger.entries))
        research_cost = self.ledger.entries[0]['actual_usd']
        self.assertAlmostEqual(research_cost, (9000 * 2 + 400 * 10) / 1e6 + 0.02, places=6)
        self.assertLessEqual(research_cost, self.ledger.entries[0]['estimate_usd'])
        self.assertEqual(episode.check(self.style.cast_ids), [i for i in episode.check(self.style.cast_ids)
                                                              if i.severity == 'warning'])

    def test_facts_from_unreviewed_sources_are_dropped(self):
        from pipeline_fixtures import SALARY_FACTS_TEXT
        facts = SALARY_FACTS_TEXT + [('Some blog says honey lasts forever if you keep it in a cupboard.',
                                      'https://en.wikipedia.org/wiki/Honey', 'Honey')]
        client = FakeClient([research_response(facts=facts), script_response()])
        episode, notes = self.run_generate(client)
        self.assertEqual([s.id for s in episode.sources], ['s1', 's2'])
        self.assertTrue(any('wikipedia' in n for n in notes))
        self.assertNotIn('wikipedia', client.messages.created[1]['messages'][0]['content'])

    def test_request_shapes(self):
        client = FakeClient([research_response(), script_response()])
        self.run_generate(client)
        research_req, script_req = client.messages.created
        self.assertEqual(research_req['model'], 'claude-sonnet-5-5')
        self.assertEqual(research_req['tools'][0]['type'], 'web_search_20260209')
        self.assertEqual(research_req['tools'][0]['max_uses'], script.RESEARCH_MAX_SEARCHES)
        self.assertIn('researchgate.net', research_req['tools'][0]['blocked_domains'])
        self.assertFalse(script.reputable('https://www.academia.edu/50199544/Stabilising'))
        self.assertNotIn('output_config', {k: v for k, v in research_req.items() if k == 'output_config' and 'format' in v})
        fmt = script_req['output_config']['format']
        self.assertEqual(fmt['type'], 'json_schema')
        self.assertIn('beats', fmt['schema']['properties'])
        self.assertIn('s1: Household spending', script_req['messages'][0]['content'])
        self.assertIn('Cast ids: lead, friend', script_req['messages'][0]['content'])
        self.assertIn('The Empty Chair', script_req['system'])
        for req in (research_req, script_req):
            self.assertFalse({'temperature', 'top_p', 'top_k'} & set(req))
        self.assertEqual(len(client.messages.counted), 2)  # free preflight before each paid call

    def test_cache_means_rerun_costs_nothing(self):
        self.run_generate(FakeClient([research_response(), script_response()]))
        again = FakeClient([])
        episode, notes = self.run_generate(again)
        self.assertEqual(again.messages.created, [])
        self.assertEqual(len(self.ledger.entries), 2)
        self.assertTrue(all('cache' in n for n in notes))

    def test_regenerate_script_reuses_research(self):
        self.run_generate(FakeClient([research_response(), script_response()]))
        client = FakeClient([script_response()])
        self.run_generate(client, regenerate={'script'})
        self.assertEqual(len(client.messages.created), 1)
        self.assertEqual(self.ledger.entries[-1]['step'], 'script')

    def test_budget_stops_before_any_paid_request(self):
        self.ledger.budget = self.ledger.budget.__class__('0.10')
        client = FakeClient([research_response(), script_response()])
        with self.assertRaises(BudgetExceeded):
            self.run_generate(client)
        self.assertEqual(client.messages.created, [])
        self.assertEqual(self.ledger.entries, [])

    def test_no_research_mode_forbids_statistics_in_prompt(self):
        client = FakeClient([script_response()])
        episode, _ = self.run_generate(client, use_research=False)
        self.assertEqual(episode.sources, [])
        self.assertIn('No research facts', client.messages.created[0]['messages'][0]['content'])

    def test_server_errors_are_not_retried_and_stay_counted(self):
        client = FakeClient([api_error(529)])
        with self.assertRaises(script.ScriptError):
            self.run_generate(client)
        self.assertEqual(len(client.messages.created), 1)
        self.assertEqual(self.ledger.entries[0]['status'], 'possibly_billed')
        client = FakeClient([api_error(400)])
        with self.assertRaises(script.ScriptError):
            self.run_generate(client, regenerate={'research'})
        self.assertEqual(self.ledger.entries[1]['status'], 'not_sent')

    def test_invalid_script_json_is_saved_for_repair_without_retry(self):
        client = FakeClient([research_response(), script_response(text='{"title": "half')])
        with self.assertRaises(script.ScriptError) as ctx:
            self.run_generate(client)
        self.assertIn('Saved to', str(ctx.exception))
        failed = list((self.base / 'cache').glob('failed-script-*.json'))
        self.assertEqual(len(failed), 1)
        self.assertEqual(json.loads(failed[0].read_text())['raw'], '{"title": "half')
        self.assertEqual(len(client.messages.created), 2)

    def test_refusal_is_reported_and_billed_once(self):
        refused = script_response(stop='refusal')
        client = FakeClient([research_response(), refused])
        with self.assertRaises(script.ScriptError) as ctx:
            self.run_generate(client)
        self.assertIn('declined', str(ctx.exception))
        self.assertEqual(self.ledger.entries[-1]['status'], 'ok')

    def test_paused_search_loop_resumes_within_budget(self):
        client = FakeClient([research_response(stop='pause_turn', facts=[]), research_response(), script_response()])
        episode, _ = self.run_generate(client)
        self.assertEqual([e['step'] for e in self.ledger.entries], ['research', 'research', 'script'])
        resumed = client.messages.created[1]['messages']
        self.assertEqual(resumed[-1]['role'], 'assistant')
        self.assertEqual(len(episode.sources), 2)

    def test_uncited_text_never_becomes_a_fact(self):
        content = research_response().content + [
            type(research_response().content[0])(type='text', text='\nAn uncited claim that sounds plausible enough.', citations=None)]
        facts = script.facts_from_content(content)
        self.assertEqual(len(facts), 2)
        self.assertNotIn('uncited', ' '.join(f.claim for f in facts))

    def test_research_uses_direct_search_so_answers_carry_url_citations(self):
        client = FakeClient([research_response(), script_response()])
        self.run_generate(client)
        tool = client.messages.created[0]['tools'][0]
        self.assertEqual(tool['allowed_callers'], ['direct'])
        raw = list((self.base / 'cache').glob('research-raw-*.json'))
        self.assertEqual(len(raw), 1)
        self.assertIn('Household spending', raw[0].read_text())

    def test_research_without_cited_facts_stops_before_the_script_call(self):
        client = FakeClient([research_response(facts=[]), script_response()])
        with self.assertRaises(script.ScriptError) as ctx:
            self.run_generate(client)
        self.assertIn('no cited facts', str(ctx.exception))
        self.assertEqual(len(client.messages.created), 1)  # the script call was never sent
        self.assertEqual(list((self.base / 'cache').glob('research-[0-9a-f]*.json')), [])  # empty result not cached
        self.assertEqual(len(list((self.base / 'cache').glob('research-raw-*.json'))), 1)

    def test_search_result_citations_use_their_source_url(self):
        from types import SimpleNamespace as NS
        cite = NS(type='search_result_location', source='https://www.bls.gov/cex/', title='Consumer Expenditures',
                  cited_text='...', search_result_index=0, start_block_index=0, end_block_index=0)
        facts = script.facts_from_content([NS(type='text', text='Housing is the largest share of US household spending.',
                                              citations=[cite])])
        self.assertEqual(facts[0].url, 'https://www.bls.gov/cex/')

    def test_uncited_lead_in_is_dropped_from_a_fact(self):
        from types import SimpleNamespace as NS
        cite = NS(type='web_search_result_location', url='https://www.example.gov/x', title='X', cited_text='...')
        content = [NS(type='text', text='Based on the search results, ', citations=None),
                   NS(type='text', text='median rent rose faster than wages in 2024.', citations=[cite])]
        facts = script.facts_from_content(content)
        self.assertEqual([f.claim for f in facts], ['median rent rose faster than wages in 2024.'])

    def test_sdk_client_never_retries(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):
            client = script.make_client()
        self.assertEqual(client.max_retries, 0)


# ---------------------------------------------------------------- the free path (SCRIPT_PROVIDER=ollama)

OUTLINE_BEATS = [
    ('hook', "Your salary didn't shrink. It leaked.", 'insert', []),
    ('setup', 'Payday feels huge, so the first week feels like permission to spend a little.', 'insert', []),
    ('problem', 'Then the jar is nearly empty by the twentieth, and nothing big happened.', 'insert', []),
    ('stakes', "Small buys don't feel like spending, so they keep adding up all month.", 'insert', []),
    ('stakes', 'Spending tends to rise as income rises, so a raise leaks too.', 'insert', ['s1']),
    ('twist', 'That pattern has a name: lifestyle inflation.', 'graphic', ['s1']),
    ('reveal', 'No big buy did it. The small ones did, together.', 'character', []),
    ('payoff', 'So pay yourself first: move savings out before any spending starts.', 'insert', ['s2']),
    ('loop', "Next payday, your salary won't leak.", 'insert', []),
]


def free_outline(hook_line="Your salary didn't shrink. It leaked."):
    return dict(title='Where Your Salary Goes', hook_promise='Why the money runs out before the month does',
                hook_options=[dict(line=hook_line, frame_one='The host holds a jar with coins trickling out.',
                                   answer='Pay yourself first so small buys cannot drain it.', loop_line="Next payday, your salary won't leak."),
                              dict(line='Where did your paycheck go?', frame_one='The host stares at an empty wallet.',
                                   answer='Small buys add up.', loop_line='Now you know where your paycheck goes.'),
                              dict(line='Payday rich, broke by the twentieth.', frame_one='A calendar flips.',
                                   answer='Lifestyle inflation.', loop_line='Rich on payday, rich by the twentieth.')],
                subject_settings=[],
                beats=[dict(role=r, narration=n, on_screen_text='', visual=v, source_ids=s, illustrative_numbers=False)
                       for r, n, v, s in OUTLINE_BEATS])


def free_staging(i, visual, words, label=''):
    word = words[-1]
    d = dict(visual_intent='The host stands in the scene and shows the idea with a jar of coins.',
             host=dict(where='scene', side=('left', 'right', 'centre')[i % 3], size=('medium', 'large')[i % 2], watching=False,
                       holds='', doing='holds up the jar and looks at the viewer'),
             cast=['lead'], staging=dict(setting='other', setting_detail='a kitchen with a coin jar',
                                         poses=[dict(who='lead', pose='standing')],
                                         actions=[dict(who='lead', verb='point', target='', anchor=word)], props=[]),
             motion=[dict(recipe='push_in', anchor='', detail=''), dict(recipe='punch_in', anchor=word, detail='')], sfx=[])
    if visual in ('insert', 'split'):
        d['insert'] = dict(key='Coin Jar Kitchen', scene='a glass jar of coins on a kitchen counter in warm light',
                           effects=[dict(kind='label', anchor=word, at='centre', detail=label)] if label else [])
    if visual == 'graphic':
        d['graphic'] = dict(kind='cards', items=['Lifestyle inflation'], anchors=['name'], detail='')
    return d


class OllamaScript(unittest.TestCase):
    """script.generate() under SCRIPT_PROVIDER=ollama against a fake Ollama: two stages, the repair loop, no key."""

    def setUp(self):
        from ollama_fixtures import FakeOllama, patched
        from pipeline.models import Source
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.base = Path(self.dir.name)
        self.style = styles.load('cozy')
        self.episode = Episode(slug='salary', topic='Why your salary disappears by the 20th', niche='personal finance',
                               target_length=30, storylines=[Storyline.model_validate(storyline())], storyline_pick=1)
        self.ledger = Ledger.open(self.base / 'ledger.json', 2.0)
        self.facts = [Source(id='s1', claim='Household spending tends to rise as income rises.', url='https://www.example.edu/x',
                             quote='Spending tends to rise as income rises.'),
                      Source(id='s2', claim='Paying yourself first means moving savings out before spending.',
                             url='https://www.example.gov/y', quote='Pay yourself first.')]
        self.outlines = [free_outline()]
        self.bad_label = {2}            # b03's first staging draws "38" on screen, which b03 never says
        self.fake = FakeOllama(self.route)
        for p in (mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'ollama', 'ANTHROPIC_API_KEY': '', 'STICK_CACHE_DIR': str(self.base / 'shared')}),
                  patched(self.fake), mock.patch.object(script, 'make_client', self.no_key),
                  mock.patch('pipeline.search.research_topic', lambda *a, **k: list(self.facts))):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def no_key():
        raise AssertionError('the free path must never build an Anthropic client')

    def route(self, body):
        system, user = body['messages'][0]['content'], body['messages'][1]['content']
        if system == script.SCRIPT_RULES_COMPACT_OUTLINE:
            return self.outlines.pop(0) if len(self.outlines) > 1 else self.outlines[0]
        i = int(user.split('STAGE b')[1][:2]) - 1
        ob = OUTLINE_BEATS[i]
        bad = i in self.bad_label and 'THE CHECKER FOUND' not in user
        return free_staging(i, ob[2], script.anchor_words(ob[1]), label='38 days' if bad else '')

    def run_generate(self, **kw):
        return script.generate(self.episode, self.style, self.base, self.ledger, **kw)

    def test_two_stages_then_a_repair_of_only_the_failing_beat(self):
        episode, notes = self.run_generate(client=FakeClient([]))          # a passed client is ignored under ollama
        self.assertEqual(len(episode.beats), 9)
        self.assertEqual([i for i in episode.check(self.style.cast_ids, self.style.words_per_second) if i.severity == 'error'], [])
        outline_calls = [b for b in self.fake.chats if b['messages'][0]['content'] == script.SCRIPT_RULES_COMPACT_OUTLINE]
        beat_calls = [b for b in self.fake.chats if b['messages'][0]['content'] == script.SCRIPT_RULES_COMPACT_BEAT]
        self.assertEqual(len(outline_calls), 1)
        self.assertEqual(len(beat_calls), 9 + 1)                            # nine beats, then b03 alone again
        repair = beat_calls[-1]['messages'][1]['content']
        self.assertIn('STAGE b03', repair)
        self.assertIn('"38" is drawn on screen', repair)
        self.assertEqual(episode.beats[2].shot.insert.key, 'coin-jar-kitchen')     # kebab-cased in code
        self.assertEqual(episode.meta['script']['provider'], 'ollama')
        self.assertTrue(all(e['provider'] in ('ollama', 'duckduckgo') and e['actual_usd'] == 0 for e in self.ledger.entries))
        self.assertTrue(any('repair round 1' in n for n in notes))
        self.assertEqual(self.fake.unloads[-1]['keep_alive'], 0)            # memory freed for FLUX
        schema = beat_calls[0]['format']
        anchor = schema['properties']['staging']['properties']['actions']['items']['properties']['anchor']
        self.assertEqual(anchor['enum'], script.anchor_words(OUTLINE_BEATS[0][1]))   # anchors can only be spoken words
        self.assertNotIn('off', schema['properties']['host']['properties']['where']['enum'])  # frame 1 shows the host
        self.assertEqual(outline_calls[0]['format']['properties']['beats']['items']['properties']['source_ids']['items']['enum'],
                         ['s1', 's2'])

    def test_an_outline_error_goes_back_to_the_outline_call(self):
        self.outlines = [free_outline(hook_line='So here is the long story of why your salary keeps on leaking away'),
                         free_outline()]
        self.bad_label = set()
        episode, notes = self.run_generate()
        outline_calls = [b for b in self.fake.chats if b['messages'][0]['content'] == script.SCRIPT_RULES_COMPACT_OUTLINE]
        self.assertEqual(len(outline_calls), 2)
        self.assertIn('at most 8', outline_calls[1]['messages'][1]['content'])
        self.assertEqual(episode.beats[0].narration, "Your salary didn't shrink. It leaked.")
        self.assertTrue(any('outline repair 1' in n for n in notes))

    def test_a_rerun_is_answered_from_the_cache(self):
        self.run_generate()
        n = len(self.fake.chats)
        episode, notes = self.run_generate()
        self.assertEqual(len(self.fake.chats), n)
        self.assertTrue(any('script cache' in n for n in notes))
        self.assertEqual(len(episode.beats), 9)

    def test_regenerate_script_reuses_the_model_cache_per_call(self):
        self.run_generate()
        n = len(self.fake.chats)
        self.run_generate(regenerate={'script'})
        self.assertEqual(len(self.fake.chats), n)          # every stage-A/B prompt is the same: layer-3 hits

    def test_errors_that_survive_the_repairs_are_left_for_the_gate(self):
        self.bad_label = {2}
        self.route_orig = self.route
        self.fake.route = lambda body: (free_staging(2, 'insert', script.anchor_words(OUTLINE_BEATS[2][1]), label='38 days')
                                        if 'STAGE b03' in body['messages'][1]['content'] else self.route_orig(body))
        episode, notes = self.run_generate()
        errors = [i for i in episode.check(self.style.cast_ids, self.style.words_per_second) if i.severity == 'error']
        self.assertEqual([e.where for e in errors], ['b03'])                 # no rule was loosened
        self.assertTrue(any('remain after' in n for n in notes))
        b03_calls = [b for b in self.fake.chats if 'STAGE b03' in b['messages'][1]['content']]
        self.assertEqual(len(b03_calls), 1 + script.FREE_REPAIR_ROUNDS)


if __name__ == '__main__':
    unittest.main()
