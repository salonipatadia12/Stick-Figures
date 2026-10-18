import copy
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'outputs/the-message-source'))
from builder.story import validate, load, authoring_prompt
from builder.visuals import preflight, draw
from builder.pipeline import cache_key, render
from builder.cli import draft, main

class StoryTests(unittest.TestCase):
    def setUp(self):self.story=load(ROOT/'examples/the-message.json')
    def test_examples_and_all_layouts(self):
        for path in (ROOT/'examples').glob('*.json'):
            if path.name.endswith('.schema.json'):continue
            story=load(path);preflight(story)
            for scene in story['scenes']:
                self.assertEqual(draw(scene,2).size,(1080,1920))
    def test_unsupported_action_background_pair_is_rejected(self):
        self.story['scenes'][0].update(background='cafe',action='type')
        with self.assertRaisesRegex(ValueError,'supports'):validate(self.story)
    def test_bad_action_is_rejected(self):
        self.story['scenes'][0]['action']='fly'
        with self.assertRaisesRegex(ValueError,'action'):validate(self.story)
    def test_long_text_rejected_before_network(self):
        self.story['scenes'][0]['caption']='W'*100
        with self.assertRaisesRegex(ValueError,'fit'):preflight(self.story)
    def test_unknown_fields_are_not_ignored(self):
        self.story['scenes'][0]['camera_position']='bad'
        with self.assertRaisesRegex(ValueError,'unsupported'):validate(self.story)
    def test_nan_and_bad_rate(self):
        self.story['scenes'][0]['duration']=float('nan')
        with self.assertRaises(ValueError):validate(self.story)
        self.story['scenes'][0]['duration']=3
        self.story['rate']='90%'
        with self.assertRaises(ValueError):validate(self.story)
    def test_voice_and_text_invalidate_cache(self):
        first=cache_key('hello','voice1','-3%')
        self.assertNotEqual(first,cache_key('goodbye','voice1','-3%'))
        self.assertNotEqual(first,cache_key('hello','voice2','-3%'))
    def test_missing_key_fails_without_network(self):
        with patch.dict(os.environ,{},clear=True),patch('urllib.request.urlopen') as network:
            with self.assertRaisesRegex(ValueError,'OPENAI_API_KEY'):draft('a story','model')
            network.assert_not_called()
    def test_structured_api_response_is_validated(self):
        content=json.dumps({'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.story)}]}]})
        response=io.StringIO(content)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-only'}),patch('urllib.request.urlopen',return_value=response):
            self.assertEqual(draft('friendship','test-model')['title'],'The Message')
    def test_bad_api_output_not_written(self):
        response=io.StringIO(json.dumps({'output':[{'type':'message','content':[{'type':'refusal'}]}]}))
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-only'}),patch('urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(ValueError,'no story text'):draft('x','test-model')
    def test_prompt_file_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'author.md'
            main(['prompt','--prompt','A story about a lost key','--output',str(out)])
            self.assertIn('lost key',out.read_text())
            with self.assertRaises(SystemExit):main(['prompt','--prompt','another','--output',str(out)])
    def test_imports_are_side_effect_free(self):
        with patch('subprocess.Popen') as process,patch('urllib.request.urlopen') as network:
            importlib.reload(importlib.import_module('builder.art'))
            process.assert_not_called();network.assert_not_called()
    def test_silent_export_is_a_real_playable_video(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);story=copy.deepcopy(self.story);story['scenes']=story['scenes'][:1]
            story['scenes'][0].update(narration='',audio='',duration=.6,message='')
            (p/'story.json').write_text(json.dumps(story))
            with patch('urllib.request.urlopen') as network:
                report=render(p/'story.json',p/'test.mp4',silent=True)
                network.assert_not_called()
            self.assertEqual(report['duration'],.6)
            subprocess.run(['ffmpeg','-v','error','-i',str(p/'test.mp4'),'-f','null','-'],check=True,stdout=subprocess.DEVNULL)
            with self.assertRaisesRegex(ValueError,'exists'):render(p/'story.json',p/'test.mp4',silent=True)

if __name__=='__main__':unittest.main()
