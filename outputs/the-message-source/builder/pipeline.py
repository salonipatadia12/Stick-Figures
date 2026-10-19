"""Narration caching, timing, review frames and deterministic video export."""
import asyncio
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave
import math
from PIL import Image, ImageDraw
from .story import load
from .visuals import draw, preflight

FPS=30
SR=44100


def require_ffmpeg():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('Install FFmpeg and ensure ffmpeg and ffprobe are on PATH')


def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def cache_key(text, voice, rate):
    return hashlib.sha256(json.dumps([text,voice,rate], ensure_ascii=False).encode()).hexdigest()[:24]


async def synthesize(text,voice,rate,target):
    import edge_tts
    temp=target.with_suffix('.tmp.mp3')
    try:
        await edge_tts.Communicate(text,voice,rate=rate).save(str(temp))
        if temp.stat().st_size<100:
            raise ValueError('Speech service returned an empty audio file')
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def audio_for(scene,story,base,cache,offline):
    if scene['audio']:
        path=(base/scene['audio']).resolve()
        if not path.is_file():
            raise ValueError(f'Audio file does not exist: {path}')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()[:24]
    else:
        digest=cache_key(scene['narration'],story['voice'],story['rate'])
        path=cache/f'{digest}.mp3'
        if not path.exists():
            if offline:
                raise ValueError('Narration is not cached. Run without --offline to generate speech, or supply scene.audio')
            try:
                asyncio.run(synthesize(scene['narration'],story['voice'],story['rate'],path))
            except Exception as exc:
                raise ValueError('Speech generation failed. Check your connection/service availability, or supply a local audio file.') from exc
    wav=cache/f'{digest}.wav'
    if not wav.exists():
        temp=wav.with_suffix('.tmp.wav')
        try:
            run(['ffmpeg','-v','error','-y','-i',str(path),'-ar',str(SR),'-ac','1','-c:a','pcm_s16le',str(temp)])
            with wave.open(str(temp)) as q:
                if q.getnframes()==0:raise ValueError('Narration audio is empty')
            temp.replace(wav)
        finally:
            temp.unlink(missing_ok=True)
    return wav


def storyboard(story,output):
    preflight(story)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    cols=min(4,len(story['scenes']));rows=math.ceil(len(story['scenes'])/cols)
    sheet=Image.new('RGB',(cols*270,rows*520),'#EEEEEE')
    for i,scene in enumerate(story['scenes']):
        t=max(1.8,scene['event_delay']+.3)
        img=draw(scene,t,story['character_style']).resize((270,480))
        x=(i%cols)*270;y=(i//cols)*520
        sheet.paste(img,(x,y));ImageDraw.Draw(sheet).text((x+10,y+490),f"Scene {i+1}: {scene['background']}",fill='black')
    sheet.save(output)
    return output


def render(story_path,output,offline=False,silent=False,force=False):
    require_ffmpeg()
    story_path=Path(story_path).resolve();story=load(story_path);preflight(story)
    output=Path(output).resolve()
    if output.suffix.lower()!='.mp4':raise ValueError('Output filename must end in .mp4')
    if output.exists() and not force:raise ValueError(f'Output exists: {output}. Use --force to replace it')
    output.parent.mkdir(parents=True,exist_ok=True)
    cache=output.parent/'.reel-cache';cache.mkdir(exist_ok=True)
    timeline=[];cursor=0
    for i,scene in enumerate(story['scenes']):
        wav=None
        if not silent and (scene['audio'] or scene['narration']):
            print(f'Preparing narration {i+1}/{len(story["scenes"])}',flush=True)
            wav=audio_for(scene,story,story_path.parent,cache,offline)
            with wave.open(str(wav)) as q:seconds=q.getnframes()/q.getframerate()
            if not .1<=seconds<=45:raise ValueError(f'Scene {i+1}: narration must be 0.1–45 seconds')
            duration=seconds+.55
        else:
            duration=max(scene['duration'],len(scene['narration'].split())/2.7+.55) if silent else scene['duration']
        if scene['message']:duration=max(duration,scene['event_delay']+1.0)
        n=round(duration*FPS)
        timeline.append(dict(scene=scene,start=cursor,end=cursor+n,wav=wav));cursor+=n
    if cursor/FPS>180:raise ValueError('Keep a reel under 180 seconds; shorten the story')
    mix=array('f',[0])*round(cursor/FPS*SR)
    for entry in timeline:
        at=round((entry['start']/FPS+.15)*SR)
        if entry['wav']:
            with wave.open(str(entry['wav'])) as q:
                samples=array('h');samples.frombytes(q.readframes(q.getnframes()))
            if sys.byteorder!='little':samples.byteswap()
            for k,v in enumerate(samples):
                if at+k<len(mix):mix[at+k]+=v/32768*.92
        if not silent and entry['scene']['action']=='send':
            at=round((entry['start']/FPS+entry['scene']['event_delay'])*SR)
            for k in range(round(.18*SR)):
                if at+k<len(mix):
                    t=k/SR;mix[at+k]+=.05*math.sin(2*math.pi*1046*t)*math.exp(-t*18)
    audio_filter='loudnorm=I=-16:TP=-1.5:LRA=9' if any(abs(v)>1e-8 for v in mix) else 'anull'
    audio=cache/'mix.wav';pcm=array('h',(round(max(-.95,min(.95,v))*32767) for v in mix))
    if sys.byteorder!='little':pcm.byteswap()
    with wave.open(str(audio),'wb') as q:
        q.setnchannels(1);q.setsampwidth(2);q.setframerate(SR);q.writeframes(pcm.tobytes())
    storyboard_path=output.with_suffix('.storyboard.jpg');storyboard(story,storyboard_path)
    temp=output.with_name(output.stem+'.partial.mp4');log=output.with_suffix('.render.log')
    cmd=['ffmpeg','-v','warning','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1080x1920','-r',str(FPS),'-i','-',
         '-i',str(audio),'-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',
         '-af',audio_filter,'-c:a','aac','-b:a','192k','-movflags','+faststart','-shortest',str(temp)]
    try:
        with log.open('w') as errors:
            p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=errors)
            try:
                for entry in timeline:
                    print(f'Rendering {entry["scene"]["background"]}: {entry["start"]/FPS:.1f}s',flush=True)
                    for frame in range(entry['end']-entry['start']):
                        p.stdin.write(draw(entry['scene'],frame/FPS,story['character_style']).tobytes())
                p.stdin.close()
                if p.wait()!=0:raise ValueError(f'FFmpeg failed. See {log}')
            except BaseException as exc:
                if p.poll() is None:p.kill()
                p.wait()
                p.stdin.close()
                if isinstance(exc, BrokenPipeError):
                    errors.flush()
                    raise ValueError(f'FFmpeg stopped: {log.read_text()[-1500:]}') from exc
                raise
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)
    report={'title':story['title'],'duration':cursor/FPS,'video':str(output),
            'width':1080,'height':1920,'fps':FPS,'silent':silent,
            'character_style':story['character_style'],
            'scenes':[{'background':e['scene']['background'],'action':e['scene']['action'],
                       'start':e['start']/FPS,'end':e['end']/FPS} for e in timeline]}
    output.with_suffix('.timing.json').write_text(json.dumps(report,indent=2))
    print(f'Finished {cursor/FPS:.2f}s: {output}',flush=True)
    return report
