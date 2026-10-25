import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import wave
from unittest.mock import patch
from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from studio.app import create_app
from studio.animation import AnimatedScene, joint
from studio.models import Story

ROOT=Path(__file__).resolve().parents[1]
def example():return json.loads((ROOT/'studio/examples/kindness.json').read_text())

class AnimationTests(unittest.TestCase):
    def test_continuity_and_rig(self):
        s=example();Story.model_validate(s).reviewable()
        s['scenes'][3]['animation']['props'][0]['holder']='none'
        with self.assertRaisesRegex(ValueError,'should still be held'):Story.model_validate(s).reviewable()

    def test_handovers_and_fixed_heads(self):
        s=example();engine=AnimatedScene(s['scenes'][2],s['characters'])
        for q in [0,.25,.5,.649,.651,.8,1]:
            poses,props=engine.poses(q);item=props['gift']
            self.assertEqual(item['position'],poses[item['holder']]['hand'])
            for pose in poses.values():
                shoulder=(pose['x']+pose['direction']*43,pose['shoulder']+5)
                _,wrist=joint(shoulder,pose['hand'])
                self.assertAlmostEqual(wrist[0],pose['hand'][0]);self.assertAlmostEqual(wrist[1],pose['hand'][1])
                self.assertEqual(pose['cy'],530)
        # Faces remain pixel-identical during a handover, while the arms move.
        a,b=engine.frame(0),engine.frame(4)
        self.assertIsNone(ImageChops.difference(a.crop((80,230,320,470)),b.crop((80,230,320,470))).getbbox())
        self.assertIsNotNone(ImageChops.difference(a,b).getbbox())

    def test_wave_does_not_flip_elbow_and_returns_to_rest(self):
        import math
        s=json.loads((ROOT/'studio/examples/first-hello.json').read_text())
        e=AnimatedScene(s['scenes'][0],s['characters']);previous=None
        start=e.poses(0)[0]['hero']['hand']
        self.assertEqual(start,e.poses(1)[0]['hero']['hand'])
        self.assertEqual(start,e.poses(.5)[0]['hero']['hand'])
        for frame in range(145):
            z=e.poses(frame/144)[0]['hero']
            shoulder=(z['x']+z['direction']*43,z['shoulder']+5)
            elbow,wrist=joint(shoulder,z['hand'],side=z['direction'])
            # Even the compressed wave window moves less than 9 scene pixels per
            # 1/144 step; the previous branch flip jumped over 100 pixels.
            if previous:self.assertLess(math.dist(previous,elbow),9)
            self.assertGreater(elbow[0],shoulder[0])
            self.assertAlmostEqual(math.dist(shoulder,elbow),90,places=5)
            self.assertAlmostEqual(math.dist(elbow,wrist),90,places=5)
            previous=elbow
        e=AnimatedScene(s['scenes'][1],s['characters'])
        poses=[e.poses(i/144)[0]['hero'] for i in range(145)]
        self.assertLess(max(p['hand'][1] for p in poses)-min(p['hand'][1] for p in poses),20)
        self.assertLess(max(p['hand'][0] for p in poses)-min(p['hand'][0] for p in poses),40)

    def test_write_pen_and_mouth(self):
        s=example();scene=copy.deepcopy(s['scenes'][0]);scene['animation'].update(setting='office',speaker='hero',actors=[dict(character='hero',x=.32,action='write',prop='page')],props=[dict(id='page',kind='book',x=.45)])
        e=AnimatedScene(scene,s['characters']);p0,items0=e.poses(.1);p1,items1=e.poses(.8)
        self.assertNotEqual(p0['hero']['pen'],p1['hero']['pen']);self.assertGreater(len(items1['page']['ink']),len(items0['page']['ink']))
        self.assertIsNotNone(ImageChops.difference(e.frame(3,energy=0),e.frame(3,energy=.06)).getbbox())
        scene['animation']['speaker']='narrator';e=AnimatedScene(scene,s['characters'])
        self.assertIsNone(ImageChops.difference(e.frame(3,energy=0),e.frame(3,energy=.06)).getbbox())

    def test_all_actions_render_and_second_story(self):
        from studio.animation_models import ACTIONS
        s=example()
        for action in ACTIONS:
            scene=copy.deepcopy(s['scenes'][0]);a=scene['animation'];a['speaker']='hero'
            a['actors'][0].update(action=action,prop='gift' if action in {'write','give','pick_up','put_down'} else '')
            a['props'][0].update(kind='book' if action=='write' else 'flower',holder='hero' if action in {'give','put_down'} else 'none')
            engine=AnimatedScene(scene,s['characters'])
            for t in [0,1.5,3,4.5,6]:
                for energy in [0,.01,.08]:self.assertEqual(engine.frame(t,energy=energy).size,(720,1280))
        second=Story.model_validate_json((ROOT/'studio/examples/first-hello.json').read_text()).reviewable()
        self.assertEqual(second.scenes[1].animation.speaker,'hero')

    def test_action_window_rejects_instant_motion(self):
        s=example();s['scenes'][0]['animation']['actors'][0].update(action_start=.7,action_end=.71)
        with self.assertRaisesRegex(ValueError,'15%'):Story.model_validate(s)

    def test_character_cannot_hold_two_objects(self):
        s=example();a=s['scenes'][2]['animation'];a['props'].append(dict(id='other',kind='cup',x=.6,holder='friend'))
        with self.assertRaisesRegex(ValueError,'free hand'):Story.model_validate(s)

    def test_invalid_reach_rejected(self):
        s=example();s['scenes'][1]['animation']['props'][0]['x']=.12
        with self.assertRaisesRegex(ValueError,'closer'):Story.model_validate(s).reviewable()

    def test_free_workflow_background_and_offline_render(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'OPENAI_API_KEY':'','APP_PASSWORD':'','APP_ENV':''}), TestClient(create_app(tmp)) as c:
            r=c.post('/api/animated-example');self.assertEqual(r.status_code,200,r.text)
            p=r.json();base='/api/projects/'+p['id']
            self.assertEqual(c.post(base+'/illustrate').status_code,409)
            self.assertEqual(c.post(base+'/approve-story').status_code,200)
            r=c.post(base+'/illustrate');self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()['assets']),4)
            self.assertEqual(c.get(base+'/scenes/1/frame?t=2').headers['content-type'],'image/jpeg')
            self.assertEqual(c.get(base+'/scenes/1/frame?t=nan').status_code,422)
            c.post(base+'/approve-storyboard')
            buf=io.BytesIO();Image.new('RGB',(720,1280),'#9286a2').save(buf,format='PNG')
            p=c.post(base+'/scenes/0/upload',json={'kind':'background','data':base64.b64encode(buf.getvalue()).decode()}).json()
            self.assertIsNone(p['storyboard_approval']);self.assertIn('0',p['backgrounds'])
            frame=Image.open(io.BytesIO(c.get(base+'/scenes/0/frame?t=0').content));self.assertLess(abs(frame.getpixel((20,20))[0]-146),3)
            self.assertEqual(c.post(base+'/render').status_code,409)
            self.assertEqual(c.delete(base+'/scenes/0/background').status_code,200)
            exported=c.get(base+'/files/story.json').json();self.assertEqual(exported,example())
            self.assertEqual(c.post('/api/projects/import',json=exported).status_code,200)
            b=io.BytesIO()
            with wave.open(b,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(bytes(24000))
            for i in range(4):self.assertEqual(c.post(base+f'/scenes/{i}/upload',json={'kind':'audio','data':base64.b64encode(b.getvalue()).decode()}).status_code,200)
            c.post(base+'/approve-storyboard');c.post(base+'/render')
            until=time.monotonic()+45
            while time.monotonic()<until:
                p=c.get(base).json()
                if p['status'] not in {'rendering'}:break
                time.sleep(.1)
            self.assertEqual(p['status'],'ready',p.get('error'))
            self.assertEqual(c.get(base+'/files/'+p['video']).status_code,200)
