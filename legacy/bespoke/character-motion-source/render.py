"""Illustrated character acting test; Pillow mesh animation and FFmpeg, no video API.

Artwork layers are created with the built-in image-generation tool from the
user-approved character. This renderer animates their registered foreground.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageChops

HERE=Path(__file__).resolve().parent
W,H=941,1672
FPS=24
NAVY='#15182F'


def ease(x):
    x=max(0,min(1,x));return x*x*(3-2*x)


def pulse(t,start,length):
    q=(t-start)/length
    return math.sin(math.pi*q)**2 if 0<q<1 else 0


def pen_position(t):
    """One pen/hand anchor also drives the ink; no detached writing effect."""
    q=ease((t-4.0)/3.8)
    if 4<t<7.8:
        # Small wrist loops travel along the page's perspective line.
        dx=42*q
        dy=-.12*dx+3.0*math.sin(q*math.pi*14)
    else:
        dx=42*q;dy=-.12*dx
    lift=9*pulse(t,1.0,2.6)+7*ease((t-8.0)/.6)
    return 402+dx,1242+dy-lift


def head_angle(t):
    return math.radians(2.4*pulse(t,.4,3.0)-3.0*ease((t-8.1)/1.4))


def inverse_point(x,y,t):
    """Smooth inverse displacement field, with a fixed resting hand."""
    # The head moves as one shape, blending only across the neck/shoulders.
    weight=ease((850-y)/70)
    a=-head_angle(t);cx,cy=400,805
    rx=cx+(x-cx)*math.cos(a)-(y-cy)*math.sin(a)
    ry=cy+(x-cx)*math.sin(a)+(y-cy)*math.cos(a)
    sx=x+(rx-x)*weight;sy=y+(ry-y)*weight
    breathe=2.4*math.sin(t*1.6)-3.0*pulse(t,2.8,1.3)
    body=ease((1210-y)/380)
    sy-=breathe*body
    return sx,sy


def rigid(sprite,old_anchor,new_anchor,angle):
    c=math.cos(angle);s=math.sin(angle)
    x,y=old_anchor;u,v=new_anchor
    return sprite.transform((W,H),Image.Transform.AFFINE,
        (c,s,x-c*u-s*v,-s,c,y+s*u-c*v),Image.Resampling.BICUBIC)


def elbow_for(a,b,upper,lower):
    dx,dy=b[0]-a[0],b[1]-a[1];distance=math.hypot(dx,dy)
    along=(upper*upper-lower*lower+distance*distance)/(2*distance)
    side=math.sqrt(max(0,upper*upper-along*along))
    return a[0]+along*dx/distance-side*dy/distance,a[1]+along*dy/distance+side*dx/distance


class Actor:
    def __init__(self,body='body-base.png'):
        self.plate=Image.open(HERE/'assets/cafe-plate.png').convert('RGB')
        cutout=Image.open(HERE/'assets'/body).convert('RGBA')
        # Register the extracted layer to the approved notebook contacts.
        self.layer=cutout.transform((W,H),Image.Transform.AFFINE,
                    (1/.86,0,-16/.86,0,1/.86,-52/.86),Image.Resampling.BICUBIC)
        # Ignore near-transparent generation residue outside the actor silhouette.
        alpha=self.layer.getchannel('A').point(lambda a:0 if a<12 else a)
        self.layer.putalpha(alpha)
        original=Image.open(HERE/'assets/character.png').convert('RGBA').transform((W,H),
            Image.Transform.AFFINE,(1/.86,0,-16/.86,0,1/.86,-52/.86),Image.Resampling.BICUBIC)
        # Sprite rig: isolate the two dark arm strokes and the intact hand/pen.
        # Each part moves rigidly; the torso is never stretched by a hand gesture.
        pixels=list(original.get_flattened_data())
        dark=Image.new('L',(W,H));dark.putdata([a if max(r,g,b)<155 else 0 for r,g,b,a in pixels])
        pen_colors=Image.new('L',(W,H));pen_colors.putdata([a if max(r,g,b)<235 else 0 for r,g,b,a in pixels])
        self.shoulder=(250,800);self.elbow=(64,1144);self.wrist=(294,1170)
        self.arm=[]
        for kind in ['upper','forearm','hand']:
            mask=Image.new('L',(W,H));d=ImageDraw.Draw(mask)
            if kind=='upper':d.line([self.shoulder,self.elbow],fill=255,width=42)
            elif kind=='forearm':d.line([self.elbow,self.wrist],fill=255,width=42)
            else:
                d.ellipse((240,1105,418,1255),fill=255)
                d.line([(277,1002),(402,1242)],fill=255,width=34)
            sprite=original.copy()
            sprite.putalpha(ImageChops.multiply(mask,pen_colors if kind=='hand' else dark))
            self.arm.append(sprite)
        self.eyes=[(444,666),(571,696)]
        self.eye_skin=[self.layer.getpixel((x+38,y)) for x,y in self.eyes]

    def foreground(self,t,blink_time=None):
        layer=self.layer.copy()
        bt=t if blink_time is None else blink_time
        blink=max(pulse(bt,1.25,.19),pulse(bt,6.75,.18),pulse(bt,10.0,.19))
        if blink>.28:
            # Eyelid cels are animated with the same head field as the artwork.
            d=ImageDraw.Draw(layer)
            for (x,y),skin in zip(self.eyes,self.eye_skin):
                d.ellipse((x-35,y-48,x+35,y+48),fill=skin)
                d.arc((x-23,y-18,x+24,y+12),5,165,fill=NAVY,width=5)
        mesh=[]
        step=28
        for top in range(0,H,step):
            bottom=min(H,top+step)
            for left in range(0,W,step):
                right=min(W,left+step)
                corners=[inverse_point(x,y,t) for x,y in
                         [(left,top),(left,bottom),(right,bottom),(right,top)]]
                mesh.append(((left,top,right,bottom),tuple(v for p in corners for v in p)))
        return layer.transform((W,H),Image.Transform.MESH,mesh,Image.Resampling.BICUBIC)

    def arms(self,t,tip=None):
        tip=pen_position(t) if tip is None else tip
        delta=(tip[0]-402,tip[1]-1242)
        wrist=(self.wrist[0]+delta[0],self.wrist[1]+delta[1])
        breath=2.4*math.sin(t*1.6)-3.0*pulse(t,2.8,1.3)
        shoulder=(self.shoulder[0],self.shoulder[1]+breath)
        elbow=elbow_for(shoulder,wrist,math.dist(self.shoulder,self.elbow),math.dist(self.elbow,self.wrist))
        def angle(a,b):return math.atan2(b[1]-a[1],b[0]-a[0])
        return [rigid(self.arm[0],self.shoulder,shoulder,angle(shoulder,elbow)-angle(self.shoulder,self.elbow)),
                rigid(self.arm[1],self.elbow,elbow,angle(elbow,wrist)-angle(self.elbow,self.wrist)),
                rigid(self.arm[2],self.wrist,wrist,0)]

    def frame(self,t,size=(720,1280)):
        im=self.plate.copy()
        # A continuous path appears only where the single pen has touched.
        if t>4:
            d=ImageDraw.Draw(im)
            times=[4+i/120 for i in range(int((min(t,7.8)-4)*120)+1)]
            points=[pen_position(q) for q in times]
            if len(points)>1:d.line(points,fill='#555A58',width=2,joint='curve')
        foreground=self.foreground(t)
        im.paste(foreground,(0,0),foreground.getchannel('A'))
        for part in self.arms(t):im.paste(part,(0,0),part.getchannel('A'))
        # Two purposeful framings; both include face, grip and notebook.
        zoom=1.18 if t<4 or t>=8.2 else 1.34
        cx,cy=(470,885) if zoom==1.18 else (455,875)
        cw=W/zoom;ch=H/zoom
        box=tuple(round(v) for v in (cx-cw/2,cy-ch/2,cx+cw/2,cy+ch/2))
        return im.crop(box).resize(size,Image.Resampling.LANCZOS)


def render(output,duration=10.5):
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    voice=HERE/'narration.mp3'
    if not voice.exists() or voice.stat().st_size<100:
        raise ValueError('Generate narration.mp3 first; see README.md')
    temp=output.with_name(output.stem+'.partial.mp4')
    command=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24',
             '-s','720x1280','-r',str(FPS),'-i','-','-i',str(voice),
             '-map','0:v','-map','1:a','-c:v','libx264','-preset','fast','-crf','18',
             '-pix_fmt','yuv420p','-af','adelay=350|350,apad,loudnorm=I=-16:TP=-1.5:LRA=9',
             '-c:a','aac','-b:a','192k','-t',str(duration),'-movflags','+faststart',str(temp)]
    actor=Actor()
    log=output.with_suffix('.render.log')
    with log.open('w') as errors:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=errors)
        try:
            for frame in range(round(duration*FPS)):
                if frame%FPS==0:print(f'Rendering {frame/FPS:.0f}/{duration}s',flush=True)
                process.stdin.write(actor.frame(frame/FPS).tobytes())
            process.stdin.close()
            if process.wait():raise RuntimeError(log.read_text())
            temp.replace(output)
        finally:
            if process.poll() is None:process.kill();process.wait()
            temp.unlink(missing_ok=True)
    report=dict(duration=duration,fps=FPS,width=720,height=1280,
                method='illustrated layers, rigid two-bone arm rig, subtle head mesh',
                voice='en-US-AndrewNeural',rate='-15%',narration=(HERE/'narration.txt').read_text().strip(),
                pen_contact_start=4.0,pen_contact_end=7.8,
                reference='user-approved character-focus-reference.png')
    output.with_suffix('.json').write_text(json.dumps(report,indent=2))
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default=str(HERE.parent/'character-acting-test.mp4'))
    p.add_argument('--preview',action='store_true');args=p.parse_args()
    if args.preview:
        actor=Actor();sheet=Image.new('RGB',(1080,1280),'#EEE')
        for i,t in enumerate([0,1.34,3.4,5.1,7.2,9.5]):
            sheet.paste(actor.frame(t,(360,640)),((i%3)*360,(i//3)*640))
        sheet.save(HERE.parent/'character-acting-storyboard.jpg')
    else:print(render(args.output))
