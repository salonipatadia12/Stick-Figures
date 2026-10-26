"""Offline provider contracts: no paid API calls."""
import io
import json
import os
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.models import Brief, Story
from studio.providers import (AnthropicProvider, GenerationError, story_available,
    story_provider, story_provider_name, budget_output_tokens, STORY_RATES)


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def valid_story():
    return json.loads(Path('studio/examples/first-hello.json').read_text())


def submitted(value=None):
    return dict(stop_reason='tool_use', content=[dict(type='tool_use',name='submit_story',input=value or valid_story())])


class AnthropicTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ANTHROPIC_API_KEY':'test-only-anthropic','OPENAI_API_KEY':'',
            'STORY_PROVIDER':'auto','ANTHROPIC_STORY_MODEL':'claude-sonnet-5-5',
            'APP_PASSWORD':'','APP_ENV':''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.brief = Brief(prompt='A student finds the courage to say hello.',render_style='rigged')

    def test_provider_selection_does_not_fall_back_to_other_paid_service(self):
        self.assertEqual(story_provider_name(), 'anthropic')
        self.assertIsInstance(story_provider(), AnthropicProvider)
        os.environ['STORY_PROVIDER']='openai'
        self.assertFalse(story_available())
        with self.assertRaises(GenerationError):story_provider()
        os.environ['STORY_PROVIDER']='typo'
        self.assertIsNone(story_provider_name())
        self.assertFalse(story_available())

    def test_preflight_and_single_paid_request_with_supported_schema(self):
        with patch('urllib.request.urlopen',side_effect=[response({'input_tokens':5000}),response(submitted())]) as call:
            draft=AnthropicProvider().draft(self.brief)
        self.assertEqual(draft.render_style,'rigged')
        self.assertEqual(call.call_count,2)
        count_request, generate_request=[c.args[0] for c in call.call_args_list]
        self.assertTrue(count_request.full_url.endswith('/count_tokens'))
        counted=json.loads(count_request.data);generated=json.loads(generate_request.data)
        self.assertNotIn('max_tokens',counted)
        self.assertEqual(generated['max_tokens'],8192)
        self.assertEqual(generated['service_tier'],'standard_only')
        self.assertEqual(generated['tools'][0]['input_schema'],Story.model_json_schema())
        self.assertEqual(generated['messages'],counted['messages'])
        self.assertEqual(generated['system'],counted['system'])
        self.assertEqual(generated['tools'],counted['tools'])
        self.assertEqual(generate_request.get_header('X-api-key'),'test-only-anthropic')

    def test_budget_blocks_before_generation(self):
        with patch('urllib.request.urlopen',return_value=response({'input_tokens':100000})) as call:
            with self.assertRaisesRegex(GenerationError,'budget'):AnthropicProvider().draft(self.brief)
            self.assertEqual(call.call_count,1)
        os.environ['ANTHROPIC_STORY_MODEL']='unpriced-model'
        with patch('urllib.request.urlopen') as call:
            with self.assertRaisesRegex(GenerationError,'verified pricing'):AnthropicProvider().draft(self.brief)
            call.assert_not_called()

    def test_count_failure_is_closed_and_never_retried(self):
        for value in [{}, {'input_tokens':-1}, {'input_tokens':'5000'}]:
            with self.subTest(value=value),patch('urllib.request.urlopen',return_value=response(value)) as call:
                with self.assertRaises(GenerationError):AnthropicProvider().draft(self.brief)
                self.assertEqual(call.call_count,1)
        with patch('urllib.request.urlopen',side_effect=URLError('test-only-anthropic')) as call:
            with self.assertRaises(GenerationError) as caught:AnthropicProvider().draft(self.brief)
            self.assertNotIn('test-only-anthropic',str(caught.exception))
            self.assertEqual(call.call_count,1)

    def test_rejects_truncated_missing_invalid_and_wrong_style_without_retry(self):
        wrong=valid_story();wrong['render_style']='illustrated'
        invalid=valid_story();invalid['scenes'][0]['animation']['actors'][0]['action']='fly'
        for value in [dict(stop_reason='max_tokens'),dict(content=[]),submitted(wrong),submitted(invalid)]:
            with self.subTest(value=str(value)[:60]),patch('urllib.request.urlopen',side_effect=[response({'input_tokens':5000}),response(value)]) as call:
                with self.assertRaises(GenerationError):AnthropicProvider().draft(self.brief)
                self.assertEqual(call.call_count,2)

    def test_auth_error_does_not_leak_response_or_retry(self):
        error=HTTPError('https://api.anthropic.com/v1/messages',401,'test-only-anthropic',{},io.BytesIO(b'secret'))
        with patch('urllib.request.urlopen',side_effect=[response({'input_tokens':5000}),error]) as call:
            with self.assertRaises(GenerationError) as caught:AnthropicProvider().draft(self.brief)
            self.assertIn('rejected the API key',str(caught.exception))
            self.assertNotIn('test-only-anthropic',str(caught.exception))
            self.assertEqual(call.call_count,2)

    def test_anthropic_only_api_flow_preserves_approval_and_local_scenes(self):
        with tempfile.TemporaryDirectory() as folder,TestClient(create_app(folder)) as client:
            config=client.get('/api/config').json()
            self.assertTrue(config['ai_available']);self.assertFalse(config['image_available'])
            self.assertEqual(config['story_provider'],'anthropic')
            self.assertEqual(config['story_budget_usd'],.25)
            self.assertNotIn('test-only-anthropic',json.dumps(config))
            with patch('urllib.request.urlopen',side_effect=[response({'input_tokens':5000}),response(submitted())]):
                project=client.post('/api/projects',json=self.brief.model_dump()).json()
                url='/api/projects/'+project['id']
                for _ in range(100):
                    project=client.get(url).json()
                    if project['status']!='drafting':break
                    time.sleep(.02)
            self.assertEqual(project['status'],'draft',project.get('error'))
            self.assertIsNone(project['story_approval'])
            self.assertEqual(client.post(url+'/illustrate').status_code,409)
            self.assertEqual(client.post(url+'/approve-story').status_code,200)
            scenes=client.post(url+'/illustrate').json()
            self.assertEqual(scenes['status'],'storyboard')
            self.assertEqual(len(scenes['assets']),len(project['story']['scenes']))

    def test_budget_caps_worst_case_for_all_approved_models(self):
        for model,(input_rate,output_rate) in STORY_RATES.items():
            for count in [0,5000,50000]:
                try:maximum=budget_output_tokens(model,count)
                except GenerationError:continue
                cost=((count+1024)*input_rate+maximum*output_rate)/1_000_000
                self.assertLessEqual(cost,.200001)
                self.assertGreaterEqual(maximum,2048)
                self.assertLessEqual(maximum,8192)


if __name__=='__main__':unittest.main()
