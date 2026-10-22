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
from builder.character import joint
from builder.story import ACTIONS_BY_BACKGROUND

class StoryTests(unittest.TestCase):
    def setUp(self):self.story=load(ROOT/'examples/the-message.json')
    def test_character_style_and_classic_compatibility(self):
        self.assertEqual(validate({'scenes':[{}]})['character_style'],'expressive')
        scene=self.story['scenes'][0]
        self.assertEqual(draw(scene,1,'classic').size,(1080,1920))
        self.story['character_style']='unknown'
        with self.assertRaisesRegex(ValueError,'character_style'):validate(self.story)
    def test_reaching_keeps_both_arm_lengths(self):
        import math
        start=(450,965)
        for end in [(675,1110),(550,1113),(530,900),(640,1030)]:
            for bend in [-1,1]:
                elbow=joint(start,end,146,151,bend)
                self.assertAlmostEqual(math.dist(start,elbow),146,places=7)
                self.assertAlmostEqual(math.dist(end,elbow),151,places=7)
        with self.assertRaisesRegex(ValueError,'Unreachable'):joint((0,0),(1000,0),146,151)
    def test_all_actions_have_reachable_contacts_during_motion(self):
        for bg,actions in ACTIONS_BY_BACKGROUND.items():
            for action in actions:
                scene=validate({'scenes':[{'background':bg,'action':action}]})['scenes'][0]
                for t in [0,.4,1.0,2.4]:
                    with self.subTest(background=bg,action=action,t=t):
                        self.assertEqual(draw(scene,t).size,(1080,1920))
    def test_examples_and_all_layouts(self):
        for path in (ROOT/'examples').glob('*.json'):
            if path.name.endswith('.schema.json'):continue
            story=load(path);preflight(story)
            for scene in story['scenes']:
                self.assertEqual(draw(scene,2,story['character_style']).size,(1080,1920))
    def test_cozy_contacts_and_cuts(self):
        from builder.cozy import actor_pose, camera_for
        import math
        story=load(ROOT/'examples/the-first-page-cozy.json')
        for scene in story['scenes']:
            for t in [0,.3,.7,1.34,1.36,2.34,2.36,3.15,4.1]:
                shoulder,hip,head,left,right,tip=actor_pose(scene,t)
                elbow=joint((shoulder[0]+20,shoulder[1]+8),right,160,180)
                self.assertAlmostEqual(math.dist(elbow,right),180)
                if scene['action']=='type':
                    self.assertEqual(right,(tip[0]+13,tip[1]-35))
            for camera in ['wide','close','detail']:
                self.assertEqual(draw({**scene,'camera':camera},2.6,'cozy').size,(1080,1920))
        writing=story['scenes'][2]
        self.assertEqual(camera_for(writing,1.0),'wide')
        self.assertEqual(camera_for(writing,1.5),'detail')
        self.assertEqual(camera_for(writing,4.1),'close')
    def test_cozy_rejects_unsupported_props_and_controls(self):
        story=load(ROOT/'examples/the-first-page-cozy.json')
        story['scenes'][0]['background']='home'
        with self.assertRaisesRegex(ValueError,'desk'):validate(story)
        story['scenes'][0]['background']='desk'
        story['scenes'][0]['page_lines']=8
        with self.assertRaisesRegex(ValueError,'page_lines'):validate(story)
        story['scenes'][0]['page_lines']=0
        story['scenes'][0]['camera']='orbit'
        with self.assertRaisesRegex(ValueError,'camera'):validate(story)
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
