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
from pipeline_fixtures import FakeClient, research_response, script_response, storyline


def api_error(status):
    request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
    cls = {400: anthropic.BadRequestError, 529: anthropic.InternalServerError}[status]
    return cls('error', response=httpx2.Response(status, request=request), body=None)


class ScriptEngine(unittest.TestCase):
    def setUp(self):
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
        self.assertTrue(episode.sources[0].url.startswith('https://www.example.org/'))
        self.assertEqual([e['step'] for e in self.ledger.entries], ['research', 'script'])
        self.assertTrue(all(e['status'] == 'ok' for e in self.ledger.entries))
        research_cost = self.ledger.entries[0]['actual_usd']
        self.assertAlmostEqual(research_cost, (9000 * 2 + 400 * 10) / 1e6 + 0.02, places=6)
        self.assertLessEqual(research_cost, self.ledger.entries[0]['estimate_usd'])
        self.assertEqual(episode.check(self.style.cast_ids), [i for i in episode.check(self.style.cast_ids)
                                                              if i.severity == 'warning'])

    def test_request_shapes(self):
        client = FakeClient([research_response(), script_response()])
        self.run_generate(client)
        research_req, script_req = client.messages.created
        self.assertEqual(research_req['model'], 'claude-sonnet-5-5')
        self.assertEqual(research_req['tools'][0]['type'], 'web_search_20260209')
        self.assertEqual(research_req['tools'][0]['max_uses'], script.RESEARCH_MAX_SEARCHES)
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


if __name__ == '__main__':
    unittest.main()
