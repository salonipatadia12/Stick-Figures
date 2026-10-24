"""Rhythm revision: original illustrations and stable eyes, new narration and edit.

Offline rebuild: python outputs/the-empty-chair-source/recut.py [--preview]
"""
from array import array
import argparse
import json
import math
from pathlib import Path
import random
import subprocess
import wave
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from render import Scene, SPECS, HERE, OUT, SIZE, FPS, FONT, ease, lines_for

LEAD=.3
PAUSES=[(6.030,.2),(8.801,.3),(16.636,.55),(19.201,.8),(20.335,.45),(22.899,.9),(29.922,.25)]
VOICE_LENGTH=33.456
TAIL=2.8
DURATION=math.ceil((LEAD+VOICE_LENGTH+sum(g for _,g in PAUSES)+TAIL)*FPS)/FPS


def at(t,after=True):
    return LEAD+t+sum(g for p,g in PAUSES if p<t or (after and p==t))


# Uneven durations follow the emotional action rather than a repeating shot grid.
# time, image, camera center/zoom, acting, camera move, brief time-jump label
SHOTS=[
 (0,'alone',(350,720,1.40),'draw','settle',''),
 (2.860,'alone',(470,850,1.06),'draw','hold',''),
 (4.55,'alone',(706,1050,2.0),'still','hold',''),
 (6.030,'question',(470,735,1.15),'ask','hold',''),
 (8.801,'question',(290,665,1.62),'hesitate','hold',''),
 (9.7,'bag',(520,860,1.18),'lift','hold',''),
 (10.979,'together',(470,760,1.15),'talk','hold',''),
 (12.551,'together',(650,600,1.58),'ask','hold',''),
 (14.355,'together',(300,615,1.70),'nod','hold',''),
 (15.45,'together',(470,790,1.14),'talk','hold',''),
 (16.636,'waiting',(470,830,1.06),'wait','hold','THE NEXT MORNING'),
 (18.45,'waiting',(705,980,1.67),'still','hold',''),
 (19.201,'waiting',(320,655,1.57),'wait','hold','THE NEXT DAY'),
 (20.335,'disappointed',(470,830,1.08),'sad','hold','DAY FOUR'),
 (22.899,'return',(470,745,1.12),'return','hold',''),
 (25.979,'return',(625,750,1.46),'show','hold',''),
 (27.422,'books',(705,890,1.7),'still','hold',''),
 (28.556,'books',(738,800,1.88),'still','hold',''),
 (29.922,'return',(285,600,1.65),'laugh','release',''),
 (31.1,'return',(470,800,1.08),'return','hold',''),
 (33.350,'books',(470,835,1.22),'still','reveal',''),
]


def cues():
    def sec(s):
        h,m,rest=s.split(':');v,ms=rest.split(',');return int(h)*3600+int(m)*60+int(v)+int(ms)/1000
    out=[]
    for block in (HERE/'narration-recut.srt').read_text().strip().split('\n\n'):
        rows=block.splitlines();a,b=[sec(v) for v in rows[1].split(' --> ')];text=' '.join(rows[2:]);words=text.split()
        chunks=[text] if len(words)<=9 else [' '.join(words[:len(words)//2]),' '.join(words[len(words)//2:])]
        cursor=a
        for chunk in chunks:
            end=cursor+(b-a)*len(chunk.split())/len(words)
            out.append((at(cursor),at(end,False),chunk));cursor=end
    return out


def voice():
    decoded=HERE/'recut-source.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(HERE/'narration-recut.mp3'),'-ar','24000','-ac','1',str(decoded)],check=True)
    with wave.open(str(decoded),'rb') as f:sr=f.getframerate();pcm=array('h',f.readframes(f.getnframes()))
    out=array('h',[0])*round(LEAD*sr);start=0
    for end,gap in PAUSES+[(VOICE_LENGTH,0)]:
        stop=min(round(end*sr),len(pcm));piece=pcm[start:stop]
        for i in range(min(72,len(piece)//2)):
            piece[i]=round(piece[i]*i/72);piece[-i-1]=round(piece[-i-1]*i/72)
        out.extend(piece);out.extend(array('h',[0])*round(gap*sr));start=stop
    out.extend(pcm[start:]);out.extend(array('h',[0])*max(0,round(DURATION*sr)-len(out)))
    target=HERE/'recut-voice.wav'
    with wave.open(str(target),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr);f.writeframes(out.tobytes())
    return target


class Recut:
    def __init__(self,scene_class=Scene):
        self.scenes={n:scene_class(n,s) for n,s in SPECS.items()};self.starts=[0]+[at(s[0]) for s in SHOTS[1:]]
        self.motion=scene_class is not Scene
        self.captions=cues();self.face=ImageFont.truetype(FONT,34);self.label=ImageFont.truetype(FONT,21)

    def frame(self,t):
        index=max(i for i,start in enumerate(self.starts) if start<=t)
        source,image,crop,acting,move,label=SHOTS[index];local=t-self.starts[index]
        im=self.scenes[image].frame(local,'pull' if source==31.1 and self.motion else acting)
        if image=='disappointed':im=ImageEnhance.Color(im).enhance(.72)
        cx,cy,zoom=crop
        if move=='settle':zoom+=.028*ease(local/2)
        elif move=='release':zoom-=.045*ease(local/1.2)
        elif move=='reveal':zoom-=.14*ease(local/2.7)
        cx*=720/941;cy*=1280/1672;cw=720/zoom;ch=1280/zoom
        cx=max(cw/2,min(720-cw/2,cx));cy=max(ch/2,min(1280-ch/2,cy))
        im=im.crop(tuple(round(v) for v in [cx-cw/2,cy-ch/2,cx+cw/2,cy+ch/2])).resize(SIZE,Image.Resampling.LANCZOS).convert('RGBA')
        overlay=Image.new('RGBA',SIZE);d=ImageDraw.Draw(overlay)
        text=next((text for a,b,text in self.captions if a<=t<b),'')
        if text:
            rows=lines_for(text,self.face,598)
            if len(rows)>2:raise ValueError('Caption exceeds two-line safe area: '+text)
            width=max(d.textlength(row,font=self.face) for row in rows)+42;top=1196-len(rows)*42-22
            d.rounded_rectangle((360-width/2,top,360+width/2,1196),radius=14,fill=(20,25,35,221))
            for i,row in enumerate(rows):d.text((360,top+10+i*42),row,font=self.face,anchor='mt',fill='#FFF9EA')
        if label and local<1.4:
            width=d.textlength(label,font=self.label)+34
            d.rounded_rectangle((360-width/2,60,360+width/2,103),radius=21,fill=(255,249,232,233))
            d.text((360,82),label,font=self.label,anchor='mm',fill='#34483B')
        im=Image.alpha_composite(im,overlay).convert('RGB')
        if t>DURATION-.55:im=Image.blend(im,Image.new('RGB',SIZE,'#1C2D29'),ease((t-DURATION+.55)/.55))
        return im


def sound():
    sr=24000;rng=random.Random(23);out=array('h');low=0
    wait=at(16.636);back=at(22.899);return_music=back+1.2
    plucks=[]
    for start,notes in [(1.0,[261.63,329.63,392]),(5.1,[220,261.63,329.63]),(11.8,[261.63,392,523.25]),
        (return_music,[261.63,329.63,392]),(return_music+3.4,[220,329.63,440]),(at(33.35)-.5,[261.63,392,523.25])]:
        plucks.extend((start+i*.42,f) for i,f in enumerate(notes))
    rustles=[(at(9.7),.25),(at(25.979),.18)];chimes=[at(6.030)-.15,back-.28]
    for n in range(round(DURATION*sr)):
        t=n/sr;value=0;low=.87*low+.13*(rng.random()*2-1)
        # Music withdraws during the wait, rather than repeating underneath it.
        for start,f in plucks:
            q=t-start
            if 0<q<2.8 and not wait<t<back:
                value+=.013*min(1,q/.014)*math.exp(-q*1.9)*(math.sin(2*math.pi*f*q)+.15*math.sin(4*math.pi*f*q))
        value+=low*.0016
        for start,length in rustles:
            q=t-start
            if 0<q<length:value+=low*.012*math.sin(math.pi*q/length)**2
        for start in chimes:
            q=t-start
            if 0<q<1:value+=.016*min(1,q/.009)*math.exp(-q*4)*(math.sin(2*math.pi*1046.5*q)+.28*math.sin(2*math.pi*1568*q))
        value*=ease(t/.7)*(1-ease((t-DURATION+1.1)/1.1));out.append(round(value*32767))
    target=HERE/'recut-sound.wav'
    with wave.open(str(target),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr);f.writeframes(out.tobytes())
    return target


def stamp(t):
    ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def render(preview=False,with_motion=False):
    from motion import MovementScene
    reel=Recut(MovementScene if with_motion else Scene);target=OUT/('the-empty-chair-motion.mp4' if with_motion else 'the-empty-chair-recut.mp4')
    if preview:
        sheet=Image.new('RGB',(1080,1920))
        for n,i in enumerate([0,2,3,6,10,13,14,18,20]):sheet.paste(reel.frame(reel.starts[i]+.4).resize((360,640)),(n%3*360,n//3*640))
        sheet.save(target.with_suffix('.storyboard.jpg'));return
    v=voice();s=sound();temp=target.with_suffix('.partial.mp4')
    cmd=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','720x1280','-r',str(FPS),'-i','-',
         '-i',str(v),'-i',str(s),'-filter_complex','[1:a]loudnorm=I=-17:TP=-2:LRA=9[v];[v][2:a]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]',
         '-map','0:v','-map','[a]','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
         '-t',str(DURATION),'-movflags','+faststart',str(temp)]
    with (HERE/'recut.render.log').open('w') as log:
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=log)
        try:
            for frame in range(round(DURATION*FPS)):
                if frame%(FPS*3)==0:print(f'Rendering {frame/FPS:.0f}/{DURATION:.2f}s',flush=True)
                p.stdin.write(reel.frame(frame/FPS).tobytes())
            p.stdin.close()
            if p.wait():raise RuntimeError('FFmpeg failed; see recut.render.log')
            temp.replace(target)
        finally:
            if p.poll() is None:p.kill();p.wait()
            temp.unlink(missing_ok=True)
    target.with_suffix('.srt').write_text('\n\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{text}' for i,(a,b,text) in enumerate(reel.captions))+'\n')
    target.with_suffix('.timeline.json').write_text(json.dumps(dict(title='The Empty Chair — rhythm revision',duration=DURATION,
        original_artwork=True,eyes='original artwork preserved; no blink overlays',fps=FPS,width=720,height=1280,
        shots=[dict(start=reel.starts[i],end=reel.starts[i+1] if i+1<len(SHOTS) else DURATION,image=s[1],acting=s[3],camera_motion=s[4]) for i,s in enumerate(SHOTS)],speech_cues=reel.captions),indent=2))
    print(target)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true');parser.add_argument('--motion',action='store_true');args=parser.parse_args();render(args.preview,args.motion)
