import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.models import Brief, Story
from studio.providers import story_instructions
from studio.directing import prepare_story
from studio.animation import walking_feet
from studio.animation_models import Actor


def sample():return Story.model_validate_json(Path('studio/examples/first-hello.json').read_text())

class AutomaticReels(unittest.TestCase):
    def test_ending_is_validated_and_instructions_include_it(self):
        for ending in ['happy','bittersweet','twist','funny','cliffhanger','tragic']:
            b=Brief(prompt='Students at the auditorium',ending=ending)
            self.assertIn(f'{ending} ending',story_instructions(b))
        with self.assertRaises(ValueError):Brief(prompt='A story idea',ending='unknown')

    def test_staging_separates_heads_over_walk_and_removes_false_stand(self):
        data=sample().model_dump();plan=data['scenes'][0]['animation']
        plan['actors'][0].update(x=.45,end_x=.7,action='walk')
        plan['actors'][1].update(x=.50,end_x=.55,action='stand',pose='standing')
        result=prepare_story(Story.model_validate(data))
        actors=result.scenes[0].animation.actors
        self.assertEqual(actors[1].action,'idle')
        for x in [actors[0].x,actors[0].end_x]:
            for y in [actors[1].x,actors[1].end_x]:self.assertGreater((y-x)*720,204)

    def test_walk_lifts_one_foot_while_other_stays_planted(self):
        actor=Actor(character='hero',x=.2,end_x=.6,action='walk')
        found=False
        for i in range(1,100):
            feet=walking_feet(actor,i/100)
            if min(p[1] for p in feet)<990:
                self.assertAlmostEqual(max(p[1] for p in feet),996)
                found=True
        self.assertTrue(found)
        self.assertEqual(walking_feet(actor,1),[(.6*720-38,996),(.6*720+38,996)])

    def test_automatic_pipeline_has_no_manual_gates_or_extra_provider_calls(self):
        calls=[]
        class Provider:
            def draft(self,brief):calls.append(('draft',brief.ending));return sample()
            def illustrate(self,*args):raise AssertionError('Automatic reels must not purchase illustrations')
        def render(p,folder,progress):
            self.assertEqual(p['story_approval'],__import__('studio.models',fromlist=['digest']).digest(p['story']))
            self.assertTrue(p['storyboard_approval']);self.assertEqual(len(p['assets']),3)
            self.assertEqual(p['approval_source'],'automatic_workflow_requested')
            progress('Rendering test reel')
            (folder/'reel.mp4').write_bytes(b'test-only')
            return dict(video='reel.mp4',captions=None,duration=20)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ANTHROPIC_API_KEY':'test-only','STORY_PROVIDER':'anthropic','APP_PASSWORD':'','APP_ENV':''}),TestClient(create_app(tmp,provider_factory=Provider,renderer=render)) as client:
            r=client.post('/api/projects',json=dict(prompt='An auditorium story',ending='twist',workflow='automatic',render_style='rigged'))
            self.assertEqual(r.status_code,200,r.text);pid=r.json()['id']
            for _ in range(100):
                result=client.get('/api/projects/'+pid).json()
                if result['status'] in {'ready','error'}:break
                time.sleep(.02)
            self.assertEqual(result['status'],'ready',result.get('error'))
            self.assertEqual(result['brief']['ending'],'twist');self.assertEqual(calls,[('draft','twist')])
            self.assertEqual(client.get('/api/projects/'+pid+'/files/reel.mp4').status_code,200)
            r=client.post('/api/projects',json=dict(prompt='An auditorium story',workflow='automatic',render_style='illustrated'))
            self.assertEqual(r.status_code,422)

    def test_staging_keeps_held_prop_and_lighting_in_continuous_scene(self):
        data=sample().model_dump()
        for scene in data['scenes']:scene['animation'].update(setting='stage',time_jump=False)
        data['scenes'][0]['animation']['props']=[dict(id='bag',kind='bag',holder='hero',x=.3)]
        data['scenes'][0]['animation']['light']='night'
        out=prepare_story(Story.model_validate(data))
        self.assertEqual(out.scenes[1].animation.props[0].holder,'hero')
        self.assertEqual(out.scenes[1].animation.light,'night')

if __name__=='__main__':unittest.main()
