"""Single-owner HTTP application with persistent approval gates and queued jobs."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from .models import Brief, Story, digest
from .providers import GenerationError, OpenAIProvider
from .rendering import render_project, duration
from .store import BUSY, Store
from .animation import AnimatedScene
from .animation_models import ACTIONS, EMOTIONS, SETTINGS, PROPS

ROOT=Path(__file__).resolve().parent.parent
DEMO_STEM='the-empty-chair-story-order'
DEFAULT_CHARACTER='White filled round head, navy outline and slender navy limbs, rounded shaded white torso, expressive oval eyes, no hair. Give a second character one consistent distinguishing accessory.'
security=HTTPBasic(auto_error=False)


def create_app(data_dir=None, provider_factory=OpenAIProvider, renderer=render_project):
    store=Store(data_dir or os.getenv('STUDIO_DATA_DIR',ROOT/'studio-data'))
    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='reel')

    @asynccontextmanager
    async def lifespan(app):
        if os.getenv('APP_ENV')=='production' and not os.getenv('APP_PASSWORD'):
            raise RuntimeError('Set APP_PASSWORD before starting a production server.')
        store.recover()
        yield
        executor.shutdown(wait=True)

    def auth(credentials:HTTPBasicCredentials|None=Depends(security)):
        password=os.getenv('APP_PASSWORD')
        if password and (not credentials or not hmac.compare_digest(credentials.username.encode(),os.getenv('APP_USERNAME','owner').encode())
                         or not hmac.compare_digest(credentials.password.encode(),password.encode())):
            raise HTTPException(401,'Sign in to your studio.',headers={'WWW-Authenticate':'Basic realm="Stick Figures Studio"'})

    app=FastAPI(title='Stick Figures Studio',lifespan=lifespan,dependencies=[Depends(auth)],docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store

    @app.middleware('http')
    async def boundaries(request:Request,call_next):
        if request.method in {'POST','PUT','DELETE'}:
            origin=request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'Cross-origin changes are not allowed.'},status_code=403)
            if int(request.headers.get('content-length','0'))>12*1024*1024:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':'File is too large.'},status_code=413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
        return response

    def load(identifier):
        try:return store.load(identifier)
        except (FileNotFoundError,ValueError):raise HTTPException(404,'Project not found.')

    def editable(p):
        if p['status'] in BUSY:raise HTTPException(409,'Wait for the current job to finish.')

    def approved(p):
        if not p['story'] or p['story_approval']!=digest(p['story']):
            raise HTTPException(409,'Approve the current story before making a storyboard.')

    def board_hash(p):
        return digest({'story':p['story'],'assets':p['assets'],'backgrounds':p.get('backgrounds',{})})

    def update(identifier,**fields):
        with store.lock:
            p=load(identifier);p.update(fields);return store.save(p)

    def work(identifier,kind,task):
        try:
            result=task()
            update(identifier,**result,error=None)
        except Exception as exc:
            # Never expose HTTP request details, raw API exceptions, or credentials.
            if isinstance(exc,(GenerationError,ValueError)) or (kind=='rendering' and isinstance(exc,RuntimeError)):
                message=str(exc)
            else:message='Generation failed. Check the server API key, model access, quota, and connection, then retry. Saved work is retained.'
            update(identifier,status='error',error=message,progress='Job stopped. Review and retry when ready.')

    def enqueue(p,kind,task):
        p.update(status=kind,error=None,progress='Queued…');store.save(p)
        executor.submit(work,p['id'],kind,task)
        return p

    @app.get('/')
    def index():return FileResponse(ROOT/'studio/static/index.html')

    @app.get('/api/config')
    def config():
        return dict(animation=dict(actions=ACTIONS,emotions=EMOTIONS,settings=SETTINGS,props=PROPS),ai_available=bool(os.getenv('OPENAI_API_KEY')),ffmpeg=bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),
            demo_available=(ROOT/'outputs'/f'{DEMO_STEM}.mp4').is_file(),
            story_model=os.getenv('STORY_MODEL','gpt-4.1-mini'),image_model=os.getenv('IMAGE_MODEL','gpt-image-2'))

    @app.get('/api/projects')
    def projects():
        with store.lock:return [dict(id=p['id'],title=(p.get('story') or {}).get('title','Writing a story…'),status=p['status'],updated_at=p['updated_at']) for p in store.all()]

    @app.post('/api/projects')
    def create(brief:Brief):
        with store.lock:
            if brief.mode=='ai' and not os.getenv('OPENAI_API_KEY'):
                raise HTTPException(503,'Set OPENAI_API_KEY on the server, or choose Write it myself.')
            if brief.mode=='manual':
                story=Story(render_style=brief.render_style,title='Untitled story',logline=brief.prompt,character_notes=DEFAULT_CHARACTER,
                    scenes=[dict(narration='',visual='',movement='still',animation=dict(actors=[dict(character='hero')]) if brief.render_style=='rigged' else None) for _ in range(3)])
                return store.create(brief.model_dump(),story.model_dump())
            p=store.create(brief.model_dump())
            def draft():
                story=provider_factory().draft(brief)
                return dict(story=story.model_dump(),status='draft',progress='Your story is ready for review.')
            return enqueue(p,'drafting',draft)

    @app.post('/api/projects/import')
    def import_story(story:Story):
        with store.lock:
            return store.create(dict(prompt=story.logline or story.title,mode='manual',render_style=story.render_style),story.model_dump())

    @app.post('/api/animated-example')
    def animated_example():
        story=Story.model_validate_json((ROOT/'studio/examples/kindness.json').read_text())
        return import_story(story)

    def thumbnail(p,index,folder):
        scene=p['story']['scenes'][index];bg=p.get('backgrounds',{}).get(str(index))
        name=f'animation-{index}-{digest(dict(scene=scene,characters=p["story"]["characters"],bg=bg))[:12]}.png'
        AnimatedScene(scene,p['story']['characters'],folder/bg if bg else None).frame(3,6).save(folder/name)
        return dict(file=name,heads=[])

    @app.get('/api/projects/{identifier}/scenes/{index}/frame')
    def preview(identifier:str,index:int,t:float=Query(default=0,ge=0,le=6,allow_inf_nan=False)):
        with store.lock:
            p=load(identifier);approved(p)
            if p['story'].get('render_style')!='rigged' or not 0<=index<len(p['story']['scenes']):raise HTTPException(404,'Animated scene not found.')
            bg=p.get('backgrounds',{}).get(str(index));folder=store.folder(identifier)
            im=AnimatedScene(p['story']['scenes'][index],p['story']['characters'],folder/bg if bg else None).frame(t,6)
        output=io.BytesIO();im.save(output,format='JPEG',quality=85)
        return Response(output.getvalue(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

    @app.get('/api/projects/{identifier}')
    def project(identifier:str):
        with store.lock:return load(identifier)

    @app.put('/api/projects/{identifier}/story')
    def save_story(identifier:str,story:Story):
        with store.lock:
            p=load(identifier);editable(p)
            if p['story']!=story.model_dump():
                p.update(story=story.model_dump(),story_approval=None,storyboard_approval=None,
                    assets={},backgrounds={},audio={},video=None,captions=None,status='draft',progress='Story saved. Review it before approval.')
            return store.save(p)

    @app.post('/api/projects/{identifier}/approve-story')
    def approve_story(identifier:str):
        with store.lock:
            p=load(identifier);editable(p)
            try:Story.model_validate(p['story']).reviewable()
            except (ValueError,TypeError) as exc:raise HTTPException(422,str(exc))
            p.update(story_approval=digest(p['story']),error=None,progress='Story approved. Build your animated scenes.' if p['story'].get('render_style')=='rigged' else 'Story approved. Add or generate the scene illustrations.')
            return store.save(p)

    @app.post('/api/projects/{identifier}/illustrate')
    def illustrate(identifier:str):
        with store.lock:
            p=load(identifier);editable(p);approved(p)
            if p['story'].get('render_style')=='rigged':
                folder=store.folder(identifier)
                p['assets']={str(i):thumbnail(p,i,folder) for i in range(len(p['story']['scenes']))}
                p.update(storyboard_approval=None,video=None,captions=None,status='storyboard',progress='Scenes built. Preview each action and approve the storyboard.')
                return store.save(p)
            if not os.getenv('OPENAI_API_KEY'):raise HTTPException(503,'AI images need a server API key. You can upload your own illustrations instead.')
            if len(p['assets'])==len(p['story']['scenes']):raise HTTPException(409,'All images are present. Review them or replace one with an upload.')
            folder=store.folder(identifier);story=Story.model_validate(p['story'])
            p.update(storyboard_approval=None,video=None,captions=None)
            def images():
                provider=provider_factory();assets=dict(p['assets'])
                for i in range(len(story.scenes)):
                    if str(i) in assets:continue
                    update(identifier,progress=f'Illustrating scene {i+1}/{len(story.scenes)}…')
                    target=folder/f'image-{i}-{digest(story)[:10]}.png'
                    first=folder/assets['0']['file'] if '0' in assets else None
                    heads=provider.illustrate(story,i,ROOT/'outputs/character-focus-reference.png',first,target)
                    # Verify decoded media before making it visible in the storyboard.
                    with Image.open(target) as im:im.verify()
                    assets[str(i)]=dict(file=target.name,heads=heads)
                    update(identifier,assets=dict(assets))
                return dict(assets=assets,status='storyboard',progress='Storyboard ready. Review every scene before rendering.')
            return enqueue(p,'illustrating',images)

    class Upload(BaseModel):
        data:str=Field(max_length=11_200_000)
        kind:str='image'

    @app.post('/api/projects/{identifier}/scenes/{scene_index}/upload')
    def upload(identifier:str,scene_index:int,body:Upload):
        with store.lock:
            p=load(identifier);editable(p);approved(p)
            if not 0<=scene_index<len(p['story']['scenes']):raise HTTPException(404,'Scene not found.')
            try:data=base64.b64decode(body.data,validate=True)
            except ValueError:raise HTTPException(422,'Invalid file encoding.')
            if len(data)>8*1024*1024:raise HTTPException(413,'Keep files below 8 MB.')
            folder=store.folder(identifier)
            if body.kind in {'image','background'}:
                rigged=p['story'].get('render_style')=='rigged'
                if (body.kind=='background')!=rigged:raise HTTPException(422,'Use a background upload for animation, or an illustration for illustrated mode.')
                try:
                    with Image.open(io.BytesIO(data)) as im:
                        if im.width*im.height>16_000_000:raise ValueError('Image too large.')
                        im.load();name=f'upload-{scene_index}-{hashlib.sha256(data).hexdigest()[:12]}.png'
                        im.convert('RGB').save(folder/name)
                except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):raise HTTPException(422,'Use a PNG, JPEG or WebP image up to 16 megapixels.')
                if body.kind=='background':
                    p.setdefault('backgrounds',{})[str(scene_index)]=name
                    p['assets'][str(scene_index)]=thumbnail(p,scene_index,folder)
                else:p['assets'][str(scene_index)]=dict(file=name,heads=[])
                p['storyboard_approval']=None
            elif body.kind=='audio':
                name=f'audio-{scene_index}-{hashlib.sha256(data).hexdigest()[:12]}.bin';path=folder/name;path.write_bytes(data)
                try:
                    if not .1<duration(path)<=40:raise ValueError()
                except Exception:path.unlink(missing_ok=True);raise HTTPException(422,'Use a valid audio recording between 0.1 and 40 seconds.')
                p['audio'][str(scene_index)]=name
            else:raise HTTPException(422,'Unsupported upload type.')
            p.update(video=None,captions=None,status='storyboard',error=None,progress='File saved. Review your storyboard.')
            return store.save(p)

    @app.delete('/api/projects/{identifier}/scenes/{index}/background')
    def reset_background(identifier:str,index:int):
        with store.lock:
            p=load(identifier);editable(p);approved(p)
            if p['story'].get('render_style')!='rigged' or not 0<=index<len(p['story']['scenes']):raise HTTPException(404,'Animated scene not found.')
            p.setdefault('backgrounds',{}).pop(str(index),None)
            p['assets'][str(index)]=thumbnail(p,index,store.folder(identifier))
            p.update(storyboard_approval=None,video=None,captions=None,status='storyboard')
            return store.save(p)

    @app.post('/api/projects/{identifier}/approve-storyboard')
    def approve_board(identifier:str):
        with store.lock:
            p=load(identifier);editable(p);approved(p)
            if any(str(i) not in p['assets'] for i in range(len(p['story']['scenes']))):
                raise HTTPException(409,'Add an illustration for every scene first.')
            p.update(storyboard_approval=board_hash(p),error=None,progress='Storyboard approved. Ready to render.')
            return store.save(p)

    @app.post('/api/projects/{identifier}/render')
    def render(identifier:str):
        with store.lock:
            p=load(identifier);editable(p);approved(p)
            if p['storyboard_approval']!=board_hash(p):raise HTTPException(409,'Approve the current storyboard before rendering.')
            def movie():
                result=renderer(p,store.folder(identifier),lambda text:update(identifier,progress=text))
                return dict(**result,status='ready',progress='Your reel is ready.')
            return enqueue(p,'rendering',movie)

    @app.get('/api/projects/{identifier}/files/{name}')
    def file(identifier:str,name:str):
        with store.lock:
            p=load(identifier)
            if name=='story.json':
                from fastapi.responses import Response
                return Response(json.dumps(p['story'],indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="story.json"'})
            allowed={a['file'] for a in p['assets'].values()}|set(p['audio'].values())|set(p.get('backgrounds',{}).values())|{p.get('video'),p.get('captions')}
            if name not in allowed or Path(name).name!=name:raise HTTPException(404,'File not found.')
            path=store.folder(identifier)/name
            if not path.is_file():raise HTTPException(404,'File not found.')
            return FileResponse(path)

    @app.get('/api/reference')
    def reference():return FileResponse(ROOT/'outputs/character-focus-reference.png')

    @app.post('/api/demo')
    def demo():
        with store.lock:
            if not (ROOT/'outputs'/f'{DEMO_STEM}.mp4').is_file():raise HTTPException(404,'The example is not installed.')
            data=json.loads((ROOT/'studio/demo.json').read_text());story=Story.model_validate(data['story'])
            p=store.create({'prompt':'The Empty Chair — completed example','mode':'demo'},story.model_dump());folder=store.folder(p['id'])
            for i,name in enumerate(data['images']):
                target=f'image-{i}.png';shutil.copyfile(ROOT/'outputs/the-empty-chair-source/assets'/name,folder/target)
                p['assets'][str(i)]={'file':target,'heads':[]}
            shutil.copyfile(ROOT/'outputs'/f'{DEMO_STEM}.mp4',folder/'reel.mp4')
            shutil.copyfile(ROOT/'outputs'/f'{DEMO_STEM}.srt',folder/'captions.srt')
            p.update(status='ready',video='reel.mp4',captions='captions.srt',duration=data['duration'],
                story_approval=digest(p['story']),storyboard_approval=board_hash(p),progress='Revised example: the hand draws a sketch with visible pen strokes and pen lifts.')
            return store.save(p)

    # Serve static files explicitly so API/media access always passes owner auth.
    @app.get('/static/{name}')
    def static(name:str):
        if name not in {'app.js','style.css'}:raise HTTPException(404)
        return FileResponse(ROOT/'studio/static'/name)

    return app


app=create_app()
