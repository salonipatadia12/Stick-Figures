"""pipeline/llm.py against a fake Ollama endpoint: generate(), generate_batch(), is_available(), unload()."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import llm
from pipeline.cache import MultiCache
from pipeline.cost import Ledger
from ollama_fixtures import FakeOllama, Resp, patched


class OllamaGenerate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'ollama', 'OLLAMA_HOST': 'http://fake:11434',
                                           'OLLAMA_MODEL': 'qwen2.5:14b'})
        env.start()
        self.addCleanup(env.stop)
        self.base = Path(self.dir.name)
        self.ledger = Ledger.open(self.base / 'ledger.json', 2.0)
        self.cache = MultiCache(self.base / 'ep', self.base / 'shared')

    def test_generate_sends_a_schema_and_a_small_context_and_logs_zero_cost(self):
        fake = FakeOllama(lambda body: {'queries': ['a b c', 'd e f']})
        schema = {'type': 'object', 'properties': {'queries': {'type': 'array', 'items': {'type': 'string'}}}}
        with patched(fake):
            text = llm.generate('sys', 'user', schema, 200, ledger=self.ledger, step='research', detail='queries')
        self.assertEqual(json.loads(text)['queries'], ['a b c', 'd e f'])
        body = fake.chats[0]
        self.assertEqual(body['model'], 'qwen2.5:14b')
        self.assertEqual(body['format'], schema)
        self.assertFalse(body['stream'])
        self.assertEqual(body['options']['num_ctx'], 4096)
        self.assertEqual(body['options']['num_predict'], 200)
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user'])
        e = self.ledger.entries[0]
        self.assertEqual((e['provider'], e['model'], e['status'], e['actual_usd']), ('ollama', 'qwen2.5:14b', 'ok', 0.0))
        self.assertEqual(e['usage']['output'], 40)
        self.assertEqual(self.ledger.spent(), 0)

    def test_a_long_prompt_gets_a_bigger_context(self):
        self.assertEqual(llm.context_for('x' * 100, 'y' * 100, 500), 4096)
        self.assertEqual(llm.context_for('x' * 9000, 'y' * 6000, 1800), 8192)

    def test_the_same_prompt_is_answered_from_the_cache(self):
        fake = FakeOllama(lambda body: 'hello')
        with patched(fake):
            a = llm.generate('s', 'u', cache=self.cache, ledger=self.ledger)
            b = llm.generate('s', 'u', cache=self.cache, ledger=self.ledger)
            c = llm.generate('s', 'u2', cache=self.cache, ledger=self.ledger)
        self.assertEqual((a, b, c), ('hello', 'hello', 'hello'))
        self.assertEqual(len(fake.chats), 2)                  # the repeat came from llm-<hash>.json
        self.assertEqual(len(list((self.base / 'ep' / 'cache').glob('llm-*.json'))), 2)
        self.assertEqual(len(self.ledger.entries), 2)          # a cached answer is not a call
        with patched(fake):
            llm.generate('s', 'u', cache=self.cache, refresh=True)
        self.assertEqual(len(fake.chats), 3)

    def test_batch_runs_in_parallel_keeps_order_and_isolates_a_failure(self):
        def route(body):
            user = body['messages'][1]['content']
            return Resp(500, text='boom') if user == 'bad' else user.upper()
        fake = FakeOllama(route, delay=0.15)
        prompts = [dict(system='s', user=u, ledger=self.ledger) for u in ('one', 'bad', 'three', 'four')]
        with patched(fake), mock.patch.dict(os.environ, {'OLLAMA_PARALLEL': '2'}):
            out = llm.generate_batch(prompts)
        self.assertEqual(out[0], 'ONE')
        self.assertIsInstance(out[1], llm.LLMError)
        self.assertEqual(out[2:], ['THREE', 'FOUR'])
        self.assertEqual(fake.peak, 2)                         # capped at OLLAMA_PARALLEL

    def test_errors_say_how_to_fix_them(self):
        with patched(FakeOllama(lambda b: Resp(404))):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate('s', 'u')
        self.assertIn('ollama pull qwen2.5:14b', str(ctx.exception))
        cut = FakeOllama(lambda b: 'x')
        cut.route = lambda b: Resp(data={'message': {'content': '{"a": '}, 'done_reason': 'length'})
        with patched(cut):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate('s', 'u', max_tokens=10)
        self.assertIn('limit', str(ctx.exception))

        def down():
            raise ConnectionError('refused')
        from types import SimpleNamespace as NS
        with mock.patch.object(llm, '_requests', lambda: NS(post=lambda *a, **k: down(), get=lambda *a, **k: down())):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate('s', 'u')
            self.assertFalse(llm.is_available())
        self.assertIn('brew services start ollama', str(ctx.exception))

    def test_is_available_needs_the_model_pulled(self):
        with patched(FakeOllama(lambda b: '', models=['qwen2.5:14b'])):
            self.assertTrue(llm.is_available())
        with patched(FakeOllama(lambda b: '', models=['llama3:8b'])):
            self.assertFalse(llm.is_available())

    def test_unload_frees_the_model(self):
        fake = FakeOllama(lambda b: '')
        with patched(fake):
            self.assertTrue(llm.unload())
        self.assertEqual(fake.unloads, [{'model': 'qwen2.5:14b', 'keep_alive': 0}])

    def test_the_anthropic_provider_needs_the_ledger(self):
        with mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'anthropic'}):
            with self.assertRaises(llm.LLMError):
                llm.generate('s', 'u')


class ProviderChoice(unittest.TestCase):
    def test_default_is_ollama_and_a_passed_client_or_fake_mode_means_anthropic(self):
        from pipeline import config
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ('SCRIPT_PROVIDER', 'STICK_FAKE_ANTHROPIC'):
                os.environ.pop(k, None)
            self.assertEqual(config.script_provider(), 'ollama')
            self.assertEqual(config.script_provider(client=object()), 'anthropic')
            os.environ['STICK_FAKE_ANTHROPIC'] = '1'
            self.assertEqual(config.script_provider(), 'anthropic')
            os.environ['SCRIPT_PROVIDER'] = 'ollama'          # set explicitly: a client is ignored
            self.assertEqual(config.script_provider(client=object()), 'ollama')
            os.environ['SCRIPT_PROVIDER'] = 'typo'
            with self.assertRaises(ValueError):
                config.script_provider()


if __name__ == '__main__':
    unittest.main()
