"""Dialogue-only visemes; speech envelope closes the mouth between syllables."""
import math
import wave
from array import array
from PIL import Image, ImageDraw
from render import HERE

# Source-audio seconds. The narrator's linking phrases do not move a character.
WORDS=[
 (7.30,7.60,'Mind','MBP AI'),(7.60,7.79,'if','EE FV'),(7.79,7.94,'I','AI'),
 (7.94,8.55,'sit','EE MBP'),
 (12.55,13.06,'Wait','W AI'),(13.20,13.42,'you','W OO'),
 (13.42,13.77,'drew','AI OO'),(13.77,14.20,'this','EE'),
 (24.00,24.16,'I','AI'),(24.16,24.49,'tried','AI EE'),
 (24.49,24.96,'drawing','W AI OO'),(24.96,25.23,'our','AI OO'),
 (25.23,25.83,'table','EE MBP AI'),
]
MOUTHS={'question':(607,558,24),'bag':(607,558,24),
        'together':(627,652,23),'return':(602,526,25), 'laugh':(321,627,23)}
SHAPES={'MBP':(1,.02),'AI':(.9,.65),'EE':(1.12,.25),'OO':(.48,.62),'W':(.55,.30),'FV':(.9,.12)}

class LipSync:
    def __init__(self):
        path=HERE/'recut-voice.wav'
        if not path.exists():
            from recut import voice
            path=voice()
        with wave.open(str(path),'rb') as f:
            self.rate=f.getframerate();self.audio=array('h',f.readframes(f.getnframes()))
    def shape(self,t,source_t):
        word=next((w for w in WORDS if w[0]<=source_t<w[1]),None)
        if not word:return (1,0)
        a,b,_,phones=word;phones=phones.split()
        p=(source_t-a)/(b-a)*len(phones);i=min(int(p),len(phones)-1)
        # A brief closed contact between words avoids a continuous flapping loop.
        envelope=min(1,(source_t-a)/.035)*min(1,(b-source_t)/.04)
        center=round(t*self.rate);lo=max(0,center-240);hi=min(len(self.audio),center+240)
        level=math.sqrt(sum(v*v for v in self.audio[lo:hi])/max(1,hi-lo))/32768
        width,height=SHAPES[phones[i]]
        return width,height*envelope*min(1,level/.045)

def paint_mouth(im,name,width,height):
    if name not in MOUTHS:return im
    x,y,r=MOUTHS[name];scale=720/941;x*=scale;y*=scale;r*=scale
    out=im.copy()
    # Restore the small mouth area using a row-wise skin gradient, safely below eyes.
    top,bottom=(-24,26) if name=='laugh' else (-11,13)
    l,t,rr,b=map(round,(x-r-3,y+top*scale,x+r+3,y+bottom*scale))
    pix=out.load()
    for yy in range(t,b+1):
        left,right=pix[l,yy],pix[rr,yy]
        for xx in range(l+1,rr):
            q=(xx-l)/(rr-l)
            pix[xx,yy]=tuple(round(a*(1-q)+v*q) for a,v in zip(left,right))
    # Supersample just the mouth for clean outlines at small output sizes.
    mouth=Image.new('RGBA',(round(r*4+20),round(r*3+20)))
    d=ImageDraw.Draw(mouth);cx=mouth.width/2;cy=12
    rw=r*width*2;rh=max(1,r*height*2)
    if height<.035:
        d.arc((cx-rw,cy-8,cx+rw,cy+7),0,175,fill='#171A32',width=4)
    else:
        d.ellipse((cx-rw,cy-rh*.24,cx+rw,cy+rh),fill='#171A32')
        if height>.20:
            d.ellipse((cx-rw*.58,cy+rh*.50,cx+rw*.58,cy+rh*.89),fill='#ECA080')
        if width>1.05:
            d.rounded_rectangle((cx-rw*.70,cy+1,cx+rw*.70,cy+rh*.23+2),radius=2,fill='#FFF9EC')
    mouth=mouth.resize((mouth.width//2,mouth.height//2),Image.Resampling.LANCZOS)
    if name=='laugh':
        mouth=mouth.rotate(24,resample=Image.Resampling.BICUBIC,expand=True)
    out.paste(mouth,(round(x-mouth.width/2),round(y-mouth.height/2+3) if name=='laugh' else round(y-6)),mouth)
    return out


def paint_laugh(im,openness):
    """A sustained smiling expression: cheeks/closed eyes, then two chuckles."""
    out=paint_mouth(im,'laugh',1.04,openness)
    scale=720/941
    for ex,ey in [(263,598),(348,545)]:
        x,y=ex*scale,ey*scale;rx,ry=25*scale,38*scale
        original=out.copy();pixels=out.load();source=original.load()
        for yy in range(round(y-ry),round(y+ry)+1):
            half=rx*math.sqrt(max(0,1-((yy-y)/ry)**2))
            l,r=round(x-half),round(x+half)
            if r<=l:continue
            # Skin boundary comes from the same row, beyond the original eye.
            left,right=source[round(x-rx-2),yy],source[round(x+rx+2),yy]
            for xx in range(l,r+1):
                q=(xx-(x-rx))/(2*rx)
                pixels[xx,yy]=tuple(round(a*(1-q)+b*q) for a,b in zip(left,right))
        d=ImageDraw.Draw(out)
        points=[(x-rx*.67+rx*1.34*i/24,y-5*scale-7*scale*math.sin(math.pi*i/24)) for i in range(25)]
        d.line(points,fill='#171A32',width=3)
    return out
