"""Three-scene, locally animated illustrated short, with continuous narration."""
from array import array
import argparse
import json
import math
from pathlib import Path
import subprocess
import wave
from PIL import Image,ImageDraw,ImageFont

HERE=Path(__file__).resolve().parent
W,H=941,1672
FPS=24
DURATION=10.5
FONT='/System/Library/Fonts/Supplemental/Arial Bold.ttf'
if not Path(FONT).exists():FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
SHOTS=[(0,2.14,0,'A two-minute break.'),(2.14,4.75,1,'Just one more…'),
       (4.75,7.5,2,'Her morning alarm.'),(7.5,10.5,2,'Two minutes. Sure.')]
SETTINGS=[dict(file='01-study.png',head=(435,603,270,265),eyes=[(424,684),(571,717)],phone=(620,963)),
          dict(file='02-sofa.png',head=(455,585,250,250),eyes=[(433,672),(568,708)],phone=(535,940)),
          dict(file='03-alarm.png',head=(556,605,245,240),eyes=[(408,652),(558,698)],phone=(420,1090))]


def ease(x):x=max(0,min(1,x));return x*x*(3-2*x)
def pulse(t,s,n):return math.sin(math.pi*(t-s)/n)**2 if s<t<s+n else 0


class Scene:
    def __init__(self,n):
        self.n=n;self.spec=SETTINGS[n]
        self.im=Image.open(HERE/'assets'/self.spec['file']).convert('RGB')
        self.skin=[self.im.getpixel((x+40,y)) for x,y in self.spec['eyes']]
    def source_point(self,x,y,t):
        cx,cy,rx,ry=self.spec['head']
        radius=math.sqrt(((x-cx)/rx)**2+((y-cy)/ry)**2)
        weight=ease((1.28-radius)/.25)
        angle=math.radians(.65*math.sin(t*1.6))
        lift=2*math.sin(t*2)
        if self.n==2:
            lift-=7*pulse(t,0,.75)
            angle+=math.radians(.9*pulse(t,0,.8))
        a=-angle;c=math.cos(a);s=math.sin(a)
        sx=x+weight*(cx+(x-cx)*c-(y-cy)*s-x)
        sy=y+weight*(cy+(x-cx)*s+(y-cy)*c-y-lift)
        px,py=self.spec['phone']
        grip=ease((1-math.sqrt(((x-px)/145)**2+((y-py)/170)**2))/.4)
        sy-=grip*(1.8*math.sin(t*(5 if self.n==1 else 2)))
        if self.n==2:
            clock=ease((1-math.sqrt(((x-120)/115)**2+((y-850)/155)**2))/.25)
            sx-=clock*1.9*math.sin(t*58)
        return sx,sy
    def frame(self,t):
        im=self.im.copy();d=ImageDraw.Draw(im)
        blink=max(pulse(t,1.0,.18),pulse(t,3.1,.18))
        if blink>.3:
            for (x,y),skin in zip(self.spec['eyes'],self.skin):
                d.ellipse((x-35,y-50,x+35,y+50),fill=skin)
                d.arc((x-23,y-16,x+24,y+14),5,165,fill='#17192F',width=5)
        mesh=[]
        for top in range(0,H,32):
            bottom=min(top+32,H)
            for left in range(0,W,32):
                right=min(left+32,W)
                pts=[self.source_point(x,y,t) for x,y in [(left,top),(left,bottom),(right,bottom),(right,top)]]
                mesh.append(((left,top,right,bottom),tuple(v for p in pts for v in p)))
        return im.transform((W,H),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC)


class Short:
    def __init__(self):
        self.scenes=[Scene(i) for i in range(3)]
        self.font=ImageFont.truetype(FONT,34)
        self.small=ImageFont.truetype(FONT,23)
    def frame(self,t):
        a,b,n,caption=next(s for s in SHOTS if s[0]<=t<s[1])
        local=t-[0,2.14,4.75][n]
        im=self.scenes[n].frame(local)
        if t>=7.5:im=im.crop((70,105,910,1598))
        im=im.resize((720,1280),Image.Resampling.LANCZOS).convert('RGBA')
        overlay=Image.new('RGBA',im.size);d=ImageDraw.Draw(overlay)
        d.rounded_rectangle((38,1122,682,1195),radius=19,fill=(20,24,37,215))
        d.text((360,1158),caption,font=self.font,anchor='mm',fill='#FFF9EB')
        label='TWO MINUTES' if n==0 else ('LATER THAT NIGHT' if n==1 else 'THE NEXT MORNING')
        width=d.textlength(label,font=self.small)+42
        d.rounded_rectangle((360-width/2,60,360+width/2,108),radius=23,fill=(255,249,235,232))
        d.text((360,84),label,font=self.small,anchor='mm',fill='#223C3B')
        return Image.alpha_composite(im,overlay).convert('RGB')


def sound(path):
    sr=24000;data=array('h')
    for n in range(round(DURATION*sr)):
        t=n/sr;v=0
        for start in [2.7,3.5]:
            q=t-start
            if 0<q<.07:v+=.025*math.sin(2*math.pi*1100*q)*math.exp(-q*55)
        if 5.2<t<9.2:
            q=(t-5.2)%.38
            if q<.17:v+=.035*(math.sin(2*math.pi*1568*t)+.32*math.sin(2*math.pi*2475*t))*math.exp(-q*10)
        data.append(round(v*32767))
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes(data.tobytes())


def render(preview=False):
    short=Short();target=HERE.parent/'two-minutes.mp4'
    if preview:
        sheet=Image.new('RGB',(1080,640))
        for i,t in enumerate([.8,3.5,6.0]):sheet.paste(short.frame(t).resize((360,640)),(i*360,0))
        sheet.save(HERE.parent/'two-minutes.storyboard.jpg');return
    sfx=HERE/'sfx.wav';sound(sfx);temp=target.with_name('two-minutes.partial.mp4')
    cmd=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','720x1280','-r',str(FPS),'-i','-',
         '-i',str(HERE/'narration.mp3'),'-i',str(sfx),'-filter_complex',
         '[1:a]adelay=250|250,loudnorm=I=-16:TP=-1.5:LRA=9,apad[v];[v][2:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]',
         '-map','0:v','-map','[a]','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
         '-c:a','aac','-b:a','192k','-t',str(DURATION),'-movflags','+faststart',str(temp)]
    log=target.with_suffix('.render.log')
    with log.open('w') as err:
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=err)
        try:
            for n in range(round(DURATION*FPS)):
                if n%FPS==0:print(f'Rendering {n/FPS:.0f}/{DURATION}s',flush=True)
                p.stdin.write(short.frame(n/FPS).tobytes())
            p.stdin.close()
            if p.wait():raise RuntimeError(log.read_text())
            temp.replace(target)
        finally:
            if p.poll() is None:p.kill();p.wait()
            temp.unlink(missing_ok=True)
    target.with_suffix('.timeline.json').write_text(json.dumps(dict(title='Two Minutes',duration=DURATION,
        width=720,height=1280,fps=FPS,source_images=3,shots=SHOTS,narration_delay=.25,
        animation='subtle local 2D head/hand deformation, blinks and alarm vibration'),indent=2))
    print(target)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--preview',action='store_true');render(p.parse_args().preview)
