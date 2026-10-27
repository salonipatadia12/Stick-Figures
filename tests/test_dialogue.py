import copy
import json
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

from PIL import Image
from studio.animation import AnimatedScene
from studio.models import Brief
from studio.providers import story_instructions
from studio.rendering import render_project, scene_voice


def example():
    return json.loads(Path('studio/examples/first-hello.json').read_text())


class DialogueTests(unittest.TestCase):
    def test_page_contact_and_head_size_are_stable_during_reaction(self):
        story=json.loads(Path('studio/examples/listening.json').read_text())
        engine=AnimatedScene(story['scenes'][2],story['characters'])
        head_sizes=[];angles=[];camera=engine.camera_box
        for i in range(25):
            poses,props=engine.poses(i/24)
            self.assertEqual(props['page']['position'],poses['hero']['hand'])
            self.assertTrue(poses['hero']['grip'])
            angles.append(poses['hero']['head_angle'])
        self.assertNotEqual(angles[0],angles[-1])
        from studio.illustrated_assets import place as real_place
        def track(canvas,sprite,anchor,target,angle=0):
            head_sizes.append(sprite.size)
            return real_place(canvas,sprite,anchor,target,angle)
        from studio.animation import Pen
        for q in [0,.5,1]:
            z=engine.poses(q)[0]['hero']
            with patch('studio.animation.place',side_effect=track):
                engine.face(Pen(Image.new('RGB',(1440,2560)),2),engine.plan.actors[0],z,q,0)
        self.assertEqual(len(set(head_sizes[::2])),1)
        self.assertEqual(engine.camera_box,camera)

    def test_cast_is_distinct_for_every_narrator_and_stable_across_turns(self):
        story=example()
        for narrator in ['en-US-AndrewNeural','en-US-AvaNeural','en-GB-SoniaNeural']:
            story['voice']=narrator
            voices=[scene_voice(story,{'animation':{'speaker':s}}) for s in ['narrator','hero','friend','hero']]
            self.assertEqual(voices[0],narrator)
            self.assertEqual(len(set(voices[:3])),3)
            self.assertEqual(voices[1],voices[3])
        story['render_style']='illustrated'
        self.assertEqual(scene_voice(story,{}),story['voice'])

    def test_only_current_speaker_receives_audio_energy(self):
        story=example();scene=story['scenes'][0]
        for speaker in ['narrator','hero','friend']:
            scene['animation']['speaker']=speaker
            engine=AnimatedScene(scene,story['characters']);received={}
            def face(p,a,z,q,energy):received[a.character]=energy
            with patch.object(engine,'face',side_effect=face):engine.frame(1,energy=.08)
            self.assertEqual(received,{a.character:(.08 if a.character==speaker else 0) for a in engine.plan.actors})

    def test_render_routes_lines_and_caches_per_voice_and_preserves_upload(self):
        story=example();base=story['scenes'][0]
        story['scenes']=[]
        for speaker in ['narrator','hero','friend','hero']:
            s=copy.deepcopy(base);s['narration']='Stay here.'
            s['animation']['speaker']=speaker;story['scenes'].append(s)
        calls=[]
        def wav(path):
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
                f.writeframes(b'\0\0'*4800)
        async def speech(text,path,voice):calls.append((text,voice));wav(path)
        with tempfile.TemporaryDirectory() as tmp:
            p=dict(story=story,assets={str(i):{} for i in range(4)},audio={})
            with patch('studio.rendering.speak',side_effect=speech),patch.object(AnimatedScene,'frame',return_value=Image.new('RGB',(720,1280))):
                result=render_project(p,tmp,lambda message:None)
                self.assertTrue((Path(tmp)/result['video']).is_file())
                self.assertEqual(calls,[('Stay here.',scene_voice(story,s)) for s in story['scenes'][:3]])
                # A supplied recording always wins over generated voice casting.
                wav(Path(tmp)/'uploaded.wav');p['audio']['0']='uploaded.wav'
                story['scenes'][0]['narration']='A unique uploaded line.'
                render_project(p,tmp,lambda message:None)
                self.assertEqual(len(calls),3)

    def test_prompt_requests_actual_conversation_and_enough_turns(self):
        instructions=story_instructions(Brief(prompt='Two friends at a cafe',render_style='rigged'))
        self.assertIn('5–8 short speaking turns',instructions)
        self.assertIn('at least one direct line from each',instructions)
        self.assertIn('Only the speaker',instructions)


if __name__=='__main__':unittest.main()
