"""Assemble the complete narrated First Page reel from the illustrated actor rig."""
import argparse
from array import array
import json
import math
from pathlib import Path
import random
import re
import subprocess
import wave
from PIL import Image,ImageDraw,ImageFont
from render import Actor,HERE,W,H,FPS,ease

DELAY=.25
DURATION=30.5
SIZE=(720,1280)
FONT='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
if not Path(FONT).exists():FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
SHOTS=[
    (0,3.90,'base',1.18,'A notebook full of possibilities.'),
    (3.90,6.35,'doubt',1.55,'The first page had to be perfect.'),
    (6.35,10.13,'doubt',1.25,'So she waited.'),
    (10.13,11.95,'doubt',1.43,'Still blank.'),
    (11.95,16.51,'base',1.34,'One imperfect sentence.'),
    (16.51,19.03,'base',1.23,'Then another.'),
    (19.03,22.1,'relief',1.18,'Crossed-out words.'),
    (22.1,24.97,'relief',1.34,'Actual stories.'),
    (24.97,27.38,'relief',1.55,'It was never perfect.'),
    (27.38,30.5,'relief',1.34,'It just needed to exist.'),
]


def stroke(start,end,t,row=0):
    q=ease((t-start)/(end-start))
    x=402+42*q;y=1242+12*row-.12*(x-402)+3*math.sin(q*math.pi*14)
    return x,y


def tip_at(t):
    if t<11.95:return 402,1242-10*math.sin(t*.7)**2
    if t<=16.25:return stroke(12.6,15.75,t)
    if t<16.51:
        x,y=stroke(12.6,15.75,t);return x,y-6*ease((t-16.25)/.26)
    if t<=19.03:return stroke(16.8,18.8,t,1)
    x,y=stroke(16.8,18.8,t,1);return x,y-9


def ink(im,t):
    d=ImageDraw.Draw(im)
    if t>=19.03:
        # Named month-long time jump: both pages are visibly used, with crossings-out.
        pages=[[(174,1228),(481,1193),(667,1304),(339,1340)],
               [(481,1193),(783,1175),(932,1271),(667,1304)]]
        for page,corners in enumerate(pages):
            a,b,c,e=corners
            def p(u,v):return ((1-v)*((1-u)*a[0]+u*b[0])+v*((1-u)*e[0]+u*c[0]),
                               (1-v)*((1-u)*a[1]+u*b[1])+v*((1-u)*e[1]+u*c[1]))
            for row in range(10):
                v=.18+row*.067;last=.82-(row%3)*.09
                pts=[p(.16+(last-.16)*n/64,v+.006*math.sin(n*1.8+row)) for n in range(65)]
                d.line(pts,fill='#606058',width=1)
                if row in [2,6]:d.line([p(.22,v-.008),p(.72,v+.009)],fill='#886959',width=2)
    for start,end,row in [(12.6,15.75,0),(16.8,18.8,1)]:
        if t>=start:
            points=[stroke(start,end,start+k/100,row)
                    for k in range(int((min(t,end)-start)*100)+1)]
            if len(points)>1:d.line(points,fill='#555A58',width=2,joint='curve')


def subtitle_cues():
    cues=[]
    for block in (HERE/'full-narration.srt').read_text().strip().split('\n\n'):
        rows=block.splitlines();a,b=rows[1].split(' --> ')
        def sec(s):
            h,m,rest=s.split(':');v,ms=rest.split(',')
            return int(h)*3600+int(m)*60+int(v)+int(ms)/1000
        cues.append((sec(a)+DELAY,sec(b)+DELAY,' '.join(rows[2:])))
    return cues


def lines_for(text,font,width):
    d=ImageDraw.Draw(Image.new('RGB',(1,1)));rows=[];row=''
    for word in text.split():
        trial=(row+' '+word).strip()
        if row and d.textlength(trial,font=font)>width:rows.append(row);row=word
        else:row=trial
    return rows+[row] if row else rows


class Reel:
    def __init__(self):
        self.actors={key:Actor(name) for key,name in
                     [('base','body-base.png'),('doubt','body-doubt.png'),('relief','body-relief.png')]}
        self.caption_font=ImageFont.truetype(FONT,31)
        self.label_font=ImageFont.truetype(FONT,22)
    def frame(self,t):
        shot=next(s for s in SHOTS if s[0]<=t<s[1])
        start,end,emotion,zoom,caption=shot
        actor=self.actors[emotion];im=actor.plate.copy();ink(im,t)
        if emotion=='doubt':pose=1.8+.15*math.sin(t)
        elif emotion=='relief':pose=8.2+2*ease((t-19.03)/2)
        elif t>=11.95:pose=3+(t-11.95)*.43
        else:pose=.5+t*.4
        body=actor.foreground(pose,blink_time=t%4.4)
        im.paste(body,(0,0),body.getchannel('A'))
        for part in actor.arms(pose,tip_at(t)):im.paste(part,(0,0),part.getchannel('A'))
        cx,cy=(455,835) if zoom>=1.5 else (470,885)
        cw=W/zoom;ch=H/zoom
        box=tuple(round(v) for v in (cx-cw/2,cy-ch/2,cx+cw/2,cy+ch/2))
        im=im.crop(box).resize(SIZE,Image.Resampling.LANCZOS).convert('RGBA')
        overlay=Image.new('RGBA',SIZE);d=ImageDraw.Draw(overlay)
        rows=lines_for(caption,self.caption_font,610)
        if len(rows)>2:raise ValueError('Caption exceeds safe area')
        top=1170 if len(rows)==1 else 1132
        d.rounded_rectangle((35,top-18,685,top+len(rows)*39+15),radius=17,fill=(19,24,36,205))
        for i,row in enumerate(rows):d.text((360,top+i*39),row,font=self.caption_font,anchor='mt',fill='#FFF8EB')
        label='THE FIRST PAGE' if t<3.4 else ('THE NEXT DAY' if 16.51<=t<19.03 else ('A MONTH LATER' if 19.03<=t<22.1 else ''))
        if label:
            width=d.textlength(label,font=self.label_font)+42
            d.rounded_rectangle((360-width/2,58,360+width/2,105),radius=22,fill=(255,249,237,235))
            d.text((360,81),label,font=self.label_font,anchor='mm',fill='#263B3C')
        im=Image.alpha_composite(im,overlay).convert('RGB')
        if t>DURATION-.4:im=Image.blend(im,Image.new('RGB',SIZE,'#15182F'),ease((t-(DURATION-.4))/.4))
        return im


def sound_bed(path):
    sr=24000;rng=random.Random(18);pcm=array('h');notes=[(130.81,196,261.63),(174.61,220,261.63),
        (110,164.81,220),(146.83,196,246.94)]
    for n in range(round(DURATION*sr)):
        t=n/sr;bar=int(t/4)%4;local=t%4
        env=(1-math.exp(-local*2))*math.exp(-local*.5)
        value=sum(.007*math.sin(2*math.pi*f*t)*env for f in notes[bar])/3
        if 12.6<t<15.75 or 16.8<t<18.8:value+=.0025*(rng.random()*2-1)*(0.4+.6*math.sin(t*29)**2)
        value*=ease(t/1.5)*(1-ease((t-(DURATION-1.2))/1.2))
        pcm.append(round(value*32767))
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(pcm.tobytes())


def render(output,preview=False):
    reel=Reel();output=Path(output).resolve()
    if preview:
        sheet=Image.new('RGB',(1080,1920),'#EEE')
        for i,t in enumerate([1,4.5,8,10.5,13.5,17.8,20.2,23.2,28.5]):
            sheet.paste(reel.frame(t).resize((360,640)),((i%3)*360,(i//3)*640))
        sheet.save(output);return
    bed=HERE/'full-sound-bed.wav';sound_bed(bed)
    temp=output.with_name(output.stem+'.partial.mp4')
    command=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','720x1280',
        '-r',str(FPS),'-i','-','-i',str(HERE/'full-narration.mp3'),'-i',str(bed),
        '-filter_complex',f'[1:a]adelay=250|250,loudnorm=I=-16:TP=-1.5:LRA=9,apad[vo];[vo][2:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[mix]',
        '-map','0:v','-map','[mix]','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
        '-c:a','aac','-b:a','192k','-t',str(DURATION),'-movflags','+faststart',str(temp)]
    log=output.with_suffix('.render.log')
    with log.open('w') as err:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=err)
        try:
            for n in range(round(DURATION*FPS)):
                if n%(FPS*3)==0:print(f'Rendering {n/FPS:.0f}/{DURATION}s',flush=True)
                process.stdin.write(reel.frame(n/FPS).tobytes())
            process.stdin.close()
            if process.wait():raise RuntimeError(log.read_text())
            temp.replace(output)
        finally:
            if process.poll() is None:process.kill();process.wait()
            temp.unlink(missing_ok=True)
    output.with_suffix('.timeline.json').write_text(json.dumps(dict(title='The First Page',duration=DURATION,
        width=720,height=1280,fps=FPS,narration_delay=DELAY,shots=[dict(start=s[0],end=s[1],
        expression=s[2],zoom=s[3],caption=s[4]) for s in SHOTS],speech_cues=subtitle_cues()),indent=2))
    print(output)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--preview',action='store_true');p.add_argument('--output')
    a=p.parse_args();target=a.output or str(HERE.parent/('the-first-page-illustrated.storyboard.jpg' if a.preview else 'the-first-page-illustrated.mp4'))
    render(target,a.preview)
