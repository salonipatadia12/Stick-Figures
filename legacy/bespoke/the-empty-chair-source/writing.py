"""Articulated drawing: one coordinate path drives the pen tip and fresh ink.

The original hand grips its original pen throughout. Pen-up transfers leave no
ink. A bending elbow connects to the moving wrist, and the cup occludes the
forearm as it did in the original illustration.
"""
from dataclasses import dataclass
import math
from PIL import Image, ImageDraw, ImageChops, ImageFilter
from render import HERE, SIZE, ease

SOURCE=(941,1672)
PIVOT=(273,902)
WRIST=(202,880)
SHOULDER=(177,714)
UPPER_LENGTH=172
FOREARM_LENGTH=111
INK=(153,101,52)
NAVY=(23,26,50)

@dataclass
class Stroke:
    points: list
    duration: float

# A small café table and adjacent chair, in the perspective of the left page.
STROKES=[
    Stroke([(319+34*math.cos(a),907+8*math.sin(a)) for a in [math.pi+i*2*math.pi/80 for i in range(81)]],1.5),
    Stroke([(299,914),(289,933)],.42),
    Stroke([(337,914),(342,929)],.42),
    Stroke([(270,910),(279,890),(293,894),(284,914)],.78),
    Stroke([(268,912),(285,908),(298,914),(279,919),(268,912)],.7),
    Stroke([(275,918),(268,931)],.38),
    Stroke([(293,916),(295,928)],.38),
]
HOLD=.25
TRANSFER=.16

def along(points,q):
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    distance=sum(lengths)*max(0,min(1,q));partial=[points[0]]
    for a,b,length in zip(points,points[1:],lengths):
        if distance<=length:
            f=distance/length if length else 0
            point=(a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f)
            return point,partial+[point]
        partial.append(b);distance-=length
    return points[-1],points

def pose(t):
    """Return page contact, pen lift, rotation, and all currently visible strokes."""
    ink=[];last=STROKES[0].points[0];clock=HOLD
    if t<HOLD:return last,7*(1-ease(t/HOLD)),0,ink
    for i,stroke in enumerate(STROKES):
        if t<clock+stroke.duration:
            q=(t-clock)/stroke.duration
            point,line=along(stroke.points,ease(q))
            return point,0,2*math.sin(q*math.pi),ink+[line]
        ink.append(stroke.points);clock+=stroke.duration;last=stroke.points[-1]
        if i+1<len(STROKES):
            nextpoint=STROKES[i+1].points[0]
            if t<clock+TRANSFER:
                q=(t-clock)/TRANSFER;f=ease(q)
                point=(last[0]+(nextpoint[0]-last[0])*f,last[1]+(nextpoint[1]-last[1])*f)
                return point,9*math.sin(math.pi*q),0,ink
            clock+=TRANSFER
    # Finish by lifting the pen and holding, without repeating the drawing.
    return last,8*ease((t-clock)/.3),0,ink

def attached(point,tip,lift,angle):
    a=math.radians(angle);c,s=math.cos(a),math.sin(a)
    dx,dy=point[0]-PIVOT[0],point[1]-PIVOT[1]
    return tip[0]+c*dx-s*dy,tip[1]-lift+s*dx+c*dy

def arm_elbow(wrist):
    dx,dy=wrist[0]-SHOULDER[0],wrist[1]-SHOULDER[1]
    distance=math.hypot(dx,dy)
    along=(UPPER_LENGTH**2-FOREARM_LENGTH**2+distance**2)/(2*distance)
    side=math.sqrt(max(0,UPPER_LENGTH**2-along**2))
    return (SHOULDER[0]+along*dx/distance-side*dy/distance,
            SHOULDER[1]+along*dy/distance+side*dx/distance)

class WritingRig:
    def __init__(self):
        original=Image.open(HERE/'assets/01-alone.png').convert('RGB')
        clean=Image.open(HERE/'assets/motion/writing-clean.png').convert('RGB').resize(SOURCE)
        self.base=original.copy()
        mask=Image.new('L',SOURCE);d=ImageDraw.Draw(mask)
        d.rectangle((78,798,279,916),fill=255)
        d.polygon([(192,900),(323,877),(437,931),(278,960),(190,926)],fill=255)
        self.base.paste(clean,(0,0),mask.filter(ImageFilter.GaussianBlur(.6)))
        armplate=Image.open(HERE/'assets/motion/writing-arm-clean.png').convert('RGB').resize(SOURCE)
        mask=Image.new('L',SOURCE);d=ImageDraw.Draw(mask)
        d.line([SHOULDER,(96,866)],fill=255,width=40)
        d.ellipse((157,698,197,736),fill=255)
        self.base.paste(armplate,(0,0),mask.filter(ImageFilter.GaussianBlur(.6)))
        # Extract the original navy fist; the rest of the arm is articulated.
        hand=Image.new('L',SOURCE);d=ImageDraw.Draw(hand)
        outline=[(192,868),(203,857),(212,850),(220,848),(233,850),(247,850),
                 (256,854),(263,860),(264,872),(260,879),(255,881),(252,886),
                 (248,887),(246,893),(239,897),(233,899),(230,903),(219,904),
                 (214,902),(210,904),(201,900),(201,879),(195,875)]
        d.polygon(outline,fill=255)
        # Preserve the painted finger highlights; reconstruct only the part hidden by the cup.
        d.rectangle((180,879,205,910),fill=0)
        d.polygon([(191,809),(196,804),(204,808),(210,816),(220,818),(239,845),
                   (260,874),(267,885),(273,902),(264,897),(252,881),(247,881),
                   (211,832),(201,827)],fill=255)
        texture=original.convert('RGBA');texture.putalpha(hand.filter(ImageFilter.GaussianBlur(.25)))
        self.handpen=Image.new('RGBA',SOURCE)
        d=ImageDraw.Draw(self.handpen)
        d.polygon([(193,870),(212,852),(243,854),(257,868),(246,889),
                   (227,902),(207,901),(196,889)],fill=NAVY)
        self.handpen.alpha_composite(texture)
        # Keep the original cup in front of the moving forearm.
        mask=Image.new('L',SOURCE);d=ImageDraw.Draw(mask)
        d.polygon([(108,883),(133,876),(182,879),(207,886),(213,898),(210,921),
                   (201,946),(215,954),(220,963),(198,976),(139,980),(104,973),
                   (99,963),(113,950),(101,926),(93,917),(96,897)],fill=255)
        self.foreground=clean.convert('RGBA');self.foreground.putalpha(mask)

    def frame(self,t):
        tip,lift,angle,strokes=pose(t)
        im=self.base.convert('RGBA');d=ImageDraw.Draw(im)
        for line in strokes:
            if len(line)>1:d.line(line,fill=INK,width=2,joint='curve')
        wrist=attached(WRIST,tip,lift,angle)
        # Two fixed-length bones: the elbow follows the hand without stretching.
        elbow=arm_elbow(wrist)
        d.line([SHOULDER,elbow],fill=NAVY,width=27)
        d.line([elbow,wrist],fill=NAVY,width=19)
        for point,radius in [(SHOULDER,13),(elbow,12),(wrist,9)]:
            d.ellipse((point[0]-radius,point[1]-radius,point[0]+radius,point[1]+radius),fill=NAVY)
        a=math.radians(angle);c,s=math.cos(a),math.sin(a);tx,ty=tip[0],tip[1]-lift
        coeff=(c,s,PIVOT[0]-c*tx-s*ty,-s,c,PIVOT[1]+s*tx-c*ty)
        im.alpha_composite(self.handpen.transform(SOURCE,Image.Transform.AFFINE,coeff,Image.Resampling.BICUBIC))
        im.alpha_composite(self.foreground)
        return im.convert('RGB').resize(SIZE,Image.Resampling.LANCZOS)
