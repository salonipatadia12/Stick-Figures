"""Reusable illustration renderer: stable original eyes, gentle head motion, captions."""
import asyncio
from array import array
import hashlib
import math
from pathlib import Path
import re
import shutil
import subprocess
import wave
from PIL import Image, ImageDraw, ImageFont, ImageOps

W,H,FPS,SR=720,1280,24,24000


def run(args):
    result=subprocess.run(args, capture_output=True)
    if result.returncode:
        raise RuntimeError('Media processing failed. Check FFmpeg and the supplied media files.')
    return result


def duration(path):
    return float(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
        '-format_whitelist','wav,mp3,ogg,flac,mov,matroska,aac',
        '-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)]).stdout)


def ease(t):
    t=max(0,min(1,t));return t*t*(3-2*t)


def font(size):
    for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/System/Library/Fonts/Supplemental/Arial.ttf']:
        if Path(path).exists():return ImageFont.truetype(path,size)
    return ImageFont.load_default(size=size)


def wrap(text, face, width=600):
    draw=ImageDraw.Draw(Image.new('RGB',(1,1)));rows=[];row=''
    for word in text.split():
        if draw.textlength(word,font=face)>width:
            raise ValueError('One caption word is too long. Shorten it in the story editor.')
        test=(row+' '+word).strip()
        if row and draw.textlength(test,font=face)>width:rows.append(row);row=word
        else:row=test
    return rows+[row] if row else rows


async def speak(text, path, voice="en-US-AndrewNeural"):
    import edge_tts
    temp=path.with_suffix('.partial.mp3')
    try:
        await edge_tts.Communicate(text,voice,rate='-8%').save(str(temp))
        temp.replace(path)
    finally:temp.unlink(missing_ok=True)


def caption_chunks(text):
    """Keep short spoken clauses together instead of a repeating word grid."""
    phrases=re.split(r'(?<=[,;.!?])\s+',text)
    chunks=[];pending=''
    for phrase in phrases:
        words=phrase.split()
        while len(words)>9:
            if pending:chunks.append(pending);pending=''
            count=7 if len(words)>11 else len(words)//2
            chunks.append(' '.join(words[:count]));words=words[count:]
        phrase=' '.join(words)
        if pending and len((pending+' '+phrase).split())>9:
            chunks.append(pending);pending=''
        pending=(pending+' '+phrase).strip()
    if pending:chunks.append(pending)
    return chunks


def beat_hold(text, final=False):
    if final:return 1.5
    if '...' in text or '…' in text:return .65
    if len(text.split())<=7:return .22
    return .4


class IllustratedActor:
    def __init__(self, path, heads):
        with Image.open(path) as original:
            iw,ih=original.size
            self.im=ImageOps.fit(original.convert('RGB'),(W,H),Image.Resampling.LANCZOS)
        scale=max(W/iw,H/ih);dx=(iw*scale-W)/2;dy=(ih*scale-H)/2
        self.heads=[(h['x']*iw*scale-dx,h['y']*ih*scale-dy,h['rx']*iw*scale,h['ry']*ih*scale) for h in heads]

    def frame(self,t,movement):
        if movement=='still' or not self.heads:return self.im.copy()
        def point(x,y):
            sx,sy=x,y
            for i,(cx,cy,rx,ry) in enumerate(self.heads):
                weight=ease((1.22-math.hypot((x-cx)/rx,(y-cy)/ry))/.2)
                angle=math.radians(.18*math.sin(t*1.4+i))
                lift=.65*math.sin(t*1.7+i)
                if movement=='nod' and .3+i*.3<t<1.5+i*.3:
                    beat=math.sin(math.pi*(t-.3-i*.3)/1.2)**2
                    angle+=math.radians(1.0*beat);lift-=beat
                c,s=math.cos(-angle),math.sin(-angle)
                sx+=weight*(cx+(x-cx)*c-(y-cy)*s-x)
                sy+=weight*(cy+(x-cx)*s+(y-cy)*c-y-lift)
            return sx,sy
        mesh=[]
        for y in range(0,H,32):
            b=min(y+32,H)
            for x in range(0,W,32):
                r=min(x+32,W);points=[point(px,py) for px,py in [(x,y),(x,b),(r,b),(r,y)]]
                mesh.append(((x,y,r,b),tuple(v for p in points for v in p)))
        return self.im.transform((W,H),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC)


def stamp(t):
    ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def render_project(project, folder, progress):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('FFmpeg is missing on this server.')
    folder=Path(folder);face=font(32);entries=[];cursor=0;captions=[]
    rigged=project['story'].get('render_style')=='rigged'
    voice=project['story'].get('voice','en-US-AndrewNeural')
    for index,scene in enumerate(project['story']['scenes']):
        progress(f'Preparing narration {index+1}/{len(project["story"]["scenes"])}')
        supplied=project['audio'].get(str(index))
        if supplied:audio=folder/supplied
        else:
            key=hashlib.sha256((scene['narration']+voice+'/-8%').encode()).hexdigest()[:20]
            audio=folder/f'voice-{key}.mp3'
            if not audio.exists():
                try:asyncio.run(speak(scene['narration'],audio,voice))
                except Exception as exc:
                    raise RuntimeError('Narration service unavailable. Retry, or upload a voice recording for each scene.') from exc
        seconds=duration(audio)
        if not .1<seconds<=40:raise ValueError('Each scene recording must be between 0.1 and 40 seconds.')
        wav=folder/f'voice-{index}.wav'
        run(['ffmpeg','-v','error','-y','-protocol_whitelist','file,pipe',
            '-format_whitelist','wav,mp3,ogg,flac,mov,matroska,aac',
            '-i',str(audio),'-ar',str(SR),'-ac','1','-c:a','pcm_s16le',str(wav)])
        with wave.open(str(wav)) as f:pcm=array('h',f.readframes(f.getnframes()))
        hold=beat_hold(scene['narration'],index==len(project['story']['scenes'])-1)
        length=math.ceil((seconds+.25+hold)*FPS)/FPS
        asset=project['assets'][str(index)]
        if rigged:
            from .animation import AnimatedScene
            bg=project.get('backgrounds',{}).get(str(index))
            actor=AnimatedScene(scene,project['story']['characters'],folder/bg if bg else None)
        else:actor=IllustratedActor(folder/asset['file'],asset.get('heads',[]))
        words=scene['narration'].split();chunks=caption_chunks(scene['narration'])
        at=cursor+.25
        for text in chunks:
            span=seconds*len(text.split())/len(words)
            rows=wrap(text,face)
            if len(rows)>3:raise ValueError('Caption is too long. Use shorter words or sentences.')
            captions.append((at,at+span,text));at+=span
        entries.append(dict(start=cursor,end=cursor+length,actor=actor,pcm=pcm,movement=scene['movement']))
        cursor+=length
    if cursor>180:raise ValueError('The narration exceeds three minutes. Shorten the story.')
    mixed=array('h',[0])*round(cursor*SR)
    for e in entries:
        offset=round((e['start']+.25)*SR)
        for i,sample in enumerate(e['pcm']):
            if offset+i<len(mixed):mixed[offset+i]=sample
    audio=folder/'mix.wav'
    with wave.open(str(audio),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(SR);f.writeframes(mixed.tobytes())
    target=folder/'reel.mp4';temp=folder/'reel.partial.mp4'
    command=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','720x1280','-r',str(FPS),'-i','-',
        '-i',str(audio),'-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',
        '-af','loudnorm=I=-17:TP=-1.5:LRA=9','-c:a','aac','-b:a','160k','-t',str(cursor),'-movflags','+faststart',str(temp)]
    with (folder/'render.log').open('w') as log:
        p=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=log)
        try:
            entry_index=0
            for frame in range(round(cursor*FPS)):
                t=frame/FPS
                while entry_index+1<len(entries) and t>=entries[entry_index]['end']:entry_index+=1
                e=entries[entry_index];local=t-e['start']
                if rigged:
                    at=round((local-.25)*SR);chunk=e['pcm'][max(0,at):max(0,at)+960] if at>=0 else []
                    energy=math.sqrt(sum(v*v for v in chunk)/max(1,len(chunk)))/32768
                    im=e['actor'].frame(local,e['end']-e['start'],energy)
                else:im=e['actor'].frame(local,'still')
                text=next((text for a,b,text in captions if a<=t<b),'')
                if text:
                    overlay=Image.new('RGBA',(W,H));d=ImageDraw.Draw(overlay);rows=wrap(text,face)
                    top=1210-(len(rows)*40+26)
                    d.rounded_rectangle((36,top,684,1210),radius=16,fill=(19,24,36,218))
                    for n,row in enumerate(rows):d.text((360,top+12+n*40),row,font=face,anchor='mt',fill='#FFF9ED')
                    im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB')
                p.stdin.write(im.tobytes())
                if frame%(FPS*2)==0:progress(f'Rendering {round(t/cursor*100)}%')
            p.stdin.close()
            if p.wait():raise RuntimeError('Video encoding failed. Check server FFmpeg availability.')
            temp.replace(target)
        finally:
            if p.poll() is None:p.kill();p.wait()
            temp.unlink(missing_ok=True)
    run(['ffmpeg','-v','error','-i',str(target),'-f','null','-'])
    (folder/'captions.srt').write_text('\n\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{text}' for i,(a,b,text) in enumerate(captions))+'\n')
    return dict(video='reel.mp4',captions='captions.srt',duration=round(cursor,3))
