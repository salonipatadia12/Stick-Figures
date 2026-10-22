import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import wave

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest('Install requirements-studio.txt and httpx for studio tests.')
from PIL import Image, ImageDraw
from studio.app import create_app
from studio.models import Story, Brief, Landmarks
from studio.providers import GenerationError, OpenAIProvider
from studio.store import Store


def story():
    return Story(title='The little repair',logline='A broken cup is repaired and used again.',character_notes='Round white figure with navy limbs.',
        scenes=[dict(narration=text,visual=visual,movement='still') for text,visual in [
            ('Her favorite cup broke.','She holds a broken cup at the table.'),
            ('She repaired it.','She joins the cup pieces with careful hands.'),
            ('It held her tea again.','The repaired cup sits on her table, full of tea.')]])


def image_bytes():
    im=Image.new('RGB',(360,640),'#e9e1cd');d=ImageDraw.Draw(im)
    d.ellipse((110,140,250,280),fill='white',outline='#151a31',width=5)
    d.ellipse((145,205,157,227),fill='#151a31');d.ellipse((203,205,215,227),fill='#151a31')
    b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()


def audio_bytes():
    b=io.BytesIO()
    with wave.open(b,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(bytes(24000))
    return b.getvalue()


class FakeProvider:
    calls=[]
    fail_second=False
    def draft(self,brief):return story()
    def illustrate(self,s,index,reference,first,target):
        self.calls.append(index)
        if self.fail_second and index==1:raise GenerationError('Image generation interrupted.')
        target.write_bytes(image_bytes());return []


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'OPENAI_API_KEY':'','APP_PASSWORD':'','APP_ENV':''})
        self.env.start();FakeProvider.calls=[];FakeProvider.fail_second=False
        self.app=create_app(self.temp.name,provider_factory=FakeProvider)
        self.client=TestClient(self.app);self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None);self.env.stop();self.temp.cleanup()

    def create(self):
        p=self.client.post('/api/projects',json={'prompt':'Repair a broken cup','mode':'manual'}).json()
        self.assertIn('id',p)
        return self.client.put(f'/api/projects/{p["id"]}/story',json=story().model_dump()).json()

    def wait(self,identifier):
        until=time.monotonic()+20
        while time.monotonic()<until:
            p=self.client.get('/api/projects/'+identifier).json()
            if p['status'] not in {'drafting','illustrating','rendering'}:return p
            time.sleep(.025)
        self.fail('Background job did not complete')

    def upload_images(self,identifier):
        for i in range(3):
            r=self.client.post(f'/api/projects/{identifier}/scenes/{i}/upload',json={'data':base64.b64encode(image_bytes()).decode()})
            self.assertEqual(r.status_code,200,r.text)

    def test_missing_key_and_manual_workflow(self):
        self.assertFalse(self.client.get('/api/config').json()['ai_available'])
        r=self.client.post('/api/projects',json={'prompt':'A very original story','mode':'ai'})
        self.assertEqual(r.status_code,503)
        p=self.create();self.assertEqual(p['status'],'draft');self.assertIsNone(p['story_approval'])

    def test_approval_gates_and_edits_invalidate_output(self):
        p=self.create();url='/api/projects/'+p['id']
        self.assertEqual(self.client.post(url+'/render').status_code,409)
        self.assertEqual(self.client.post(url+'/illustrate').status_code,409)
        self.assertEqual(self.client.post(url+'/approve-story').status_code,200)
        self.assertEqual(self.client.post(url+'/approve-storyboard').status_code,409)
        self.upload_images(p['id']);self.assertEqual(self.client.post(url+'/approve-storyboard').status_code,200)
        revised=story().model_dump();revised['scenes'][0]['narration']='A different beginning.'
        changed=self.client.put(url+'/story',json=revised).json()
        self.assertIsNone(changed['story_approval']);self.assertIsNone(changed['storyboard_approval']);self.assertEqual(changed['assets'],{})
        self.assertEqual(self.client.post(url+'/render').status_code,409)

    def test_upload_requires_approval_and_media_is_allowlisted(self):
        p=self.create();url='/api/projects/'+p['id']
        body={'data':base64.b64encode(image_bytes()).decode()}
        self.assertEqual(self.client.post(url+'/scenes/0/upload',json=body).status_code,409)
        self.client.post(url+'/approve-story');self.upload_images(p['id'])
        p=self.client.get(url).json();name=p['assets']['0']['file']
        self.assertEqual(self.client.get(url+'/files/'+name).status_code,200)
        self.assertEqual(self.client.get(url+'/files/project.json').status_code,404)
        self.assertEqual(self.client.get('/api/projects/not-a-valid-id').status_code,404)
        self.assertEqual(self.client.post(url+'/scenes/0/upload',json={'data':'not-base64'}).status_code,422)
        self.assertEqual(self.client.post(url+'/scenes/0/upload',json={'data':base64.b64encode(b'not an image').decode()}).status_code,422)

    def test_image_replacement_invalidates_board_approval(self):
        p=self.create();url='/api/projects/'+p['id'];self.client.post(url+'/approve-story');self.upload_images(p['id'])
        self.client.post(url+'/approve-storyboard')
        p=self.client.post(url+'/scenes/0/upload',json={'data':base64.b64encode(image_bytes()).decode()}).json()
        self.assertIsNone(p['storyboard_approval']);self.assertEqual(self.client.post(url+'/render').status_code,409)

    def test_paid_image_retry_reuses_partial_work(self):
        os.environ['OPENAI_API_KEY']='test-only';FakeProvider.fail_second=True
        p=self.create();url='/api/projects/'+p['id'];self.client.post(url+'/approve-story')
        self.client.post(url+'/illustrate');p=self.wait(p['id'])
        self.assertEqual(p['status'],'error');self.assertIn('0',p['assets']);self.assertEqual(FakeProvider.calls,[0,1])
        FakeProvider.fail_second=False;self.client.post(url+'/illustrate');p=self.wait(p['id'])
        self.assertEqual(p['status'],'storyboard');self.assertEqual(FakeProvider.calls,[0,1,1,2])

    def test_background_job_blocks_edits_and_duplicate_jobs(self):
        started=threading.Event();release=threading.Event()
        class Slow(FakeProvider):
            def illustrate(self,*args):started.set();release.wait(5);return super().illustrate(*args)
        with tempfile.TemporaryDirectory() as folder,TestClient(create_app(folder,provider_factory=Slow)) as client:
            os.environ['OPENAI_API_KEY']='test-only'
            p=client.post('/api/projects',json={'prompt':'A broken cup story','mode':'manual'}).json();url='/api/projects/'+p['id']
            client.put(url+'/story',json=story().model_dump());client.post(url+'/approve-story')
            try:
                client.post(url+'/illustrate');self.assertTrue(started.wait(2))
                self.assertEqual(client.post(url+'/illustrate').status_code,409)
                self.assertEqual(client.put(url+'/story',json=story().model_dump()).status_code,409)
            finally:release.set()

    def test_restart_marks_jobs_interrupted_without_retry(self):
        s=Store(Path(self.temp.name)/'recovery');p=s.create({},story().model_dump());p['status']='illustrating';s.save(p)
        s.recover();self.assertEqual(s.load(p['id'])['status'],'error');self.assertIn('server restarted',s.load(p['id'])['error'])

    def test_owner_auth_and_cross_origin_changes(self):
        os.environ['APP_PASSWORD']='test-password'
        self.assertEqual(self.client.get('/api/config').status_code,401)
        self.assertEqual(self.client.get('/static/app.js').status_code,401)
        self.assertEqual(self.client.get('/api/config',auth=('owner','test-password')).status_code,200)
        self.assertEqual(self.client.post('/api/demo',headers={'Origin':'https://unrelated.example'},auth=('owner','test-password')).status_code,403)

    def test_production_requires_password(self):
        os.environ['APP_ENV']='production'
        with self.assertRaisesRegex(RuntimeError,'APP_PASSWORD'):
            with TestClient(create_app(Path(self.temp.name)/'production')):pass

    def test_story_validation_rejects_empty_approval_and_excess_scenes(self):
        p=self.client.post('/api/projects',json={'prompt':'Start a manual story','mode':'manual'}).json()
        self.assertEqual(self.client.post('/api/projects/'+p['id']+'/approve-story').status_code,422)
        invalid=story().model_dump();invalid['scenes']*=3
        self.assertEqual(self.client.put('/api/projects/'+p['id']+'/story',json=invalid).status_code,422)

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_real_offline_render_with_uploaded_images_and_audio(self):
        p=self.create();url='/api/projects/'+p['id'];self.client.post(url+'/approve-story');self.upload_images(p['id'])
        for i in range(3):
            r=self.client.post(url+f'/scenes/{i}/upload',json={'kind':'audio','data':base64.b64encode(audio_bytes()).decode()})
            self.assertEqual(r.status_code,200,r.text)
        self.client.post(url+'/approve-storyboard');r=self.client.post(url+'/render');self.assertEqual(r.status_code,200)
        p=self.wait(p['id']);self.assertEqual(p['status'],'ready',p.get('error'))
        path=Path(self.temp.name)/p['id']/p['video']
        subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],check=True)
        self.assertGreater(self.client.get(url+'/files/reel.mp4').headers.get('content-length','0'),'0')
        self.assertIn('00:00',self.client.get(url+'/files/captions.srt').text)


class ProviderContracts(unittest.TestCase):
    def test_structured_story_and_reference_image_contract(self):
        fake=MagicMock();fake.responses.parse.side_effect=[SimpleNamespace(output_parsed=story()),SimpleNamespace(output_parsed=Landmarks(heads=[]))]
        fake.images.edit.return_value=SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(image_bytes()).decode())])
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'OPENAI_API_KEY':'test-only'}),patch('openai.OpenAI',return_value=fake):
            provider=OpenAIProvider();draft=provider.draft(Brief(prompt='A cup is repaired'))
            reference=Path(folder)/'reference.png';reference.write_bytes(image_bytes());target=Path(folder)/'scene.png'
            heads=provider.illustrate(draft,0,reference,None,target)
            self.assertTrue(target.is_file());self.assertEqual(heads,[])
            self.assertEqual(fake.responses.parse.call_args_list[0].kwargs['text_format'],Story)
            self.assertEqual(fake.images.edit.call_args.kwargs['n'],1)
            self.assertEqual(len(fake.images.edit.call_args.kwargs['image']),1)


if __name__=='__main__':unittest.main()
