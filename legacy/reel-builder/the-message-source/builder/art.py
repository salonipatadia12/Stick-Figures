"""Pure drawing primitives; importing this module never renders or makes network calls."""
from pathlib import Path
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont
import math
INK = '#171717'
WHITE = 'white'
FONT = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
REG = '/System/Library/Fonts/Supplemental/Arial.ttf'
if not Path(FONT).exists():
    FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    REG = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
@lru_cache(maxsize=96)
def font(n,b=True):return ImageFont.truetype(FONT if b else REG,n)

def txt(d,xy,s,n=55,fill=INK,anchor='mm',b=True):d.text(xy,s,font=font(n,b),fill=fill,anchor=anchor)

def ease(x):x=max(0,min(1,x));return x*x*(3-2*x)

def lerp(a,b,x):return a+(b-a)*x

def curve(d,pts,w=8,fill=INK):
 # cubic Bezier segments; author control points, no random frame jitter
 p0,p1,p2,p3=pts;out=[]
 for k in range(31):
  t=k/30;u=1-t;out.append((u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0],u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]))
 d.line(out,fill=fill,width=w,joint='curve')

def line(d,pts,w=8,fill=INK):d.line(pts,fill=fill,width=w,joint='curve')

def head(im,x,y,expression,t,tilt=0,scale=1):
 face=Image.new('RGBA',(350,350));d=ImageDraw.Draw(face)
 d.ellipse((35,45,305,310),fill=WHITE,outline=INK,width=7)
 # three wisps; keeps same identity throughout
 curve(d,[(145,47),(145,25),(163,17),(177,12)],5)
 curve(d,[(165,47),(178,23),(195,21),(207,23)],5)
 sleepy=expression in ('sleep','tired');surprise=expression in ('shock','alarm')
 blink=(int(t*30)%117 in range(0,4))
 if sleepy:
  for ex in (115,225):
   curve(d,[(ex-23,150),(ex-5,166),(ex+12,167),(ex+27,146)],6)
   if expression=='tired':curve(d,[(ex-27,176),(ex-12,189),(ex+16,190),(ex+30,176)],3)
 else:
  for ex in (115,225):
   if blink:line(d,[(ex-23,151),(ex+25,151)],6)
   else:
    d.ellipse((ex-28,112,ex+31,183),fill=WHITE,outline=INK,width=5)
    look=10 if expression in ('tempt','scroll','resolve') else 0
    d.ellipse((ex-7+look,133,ex+9+look,160),fill=INK)
 if expression in ('happy','resolve','tempt'):
  curve(d,[(114,217),(147,248),(200,248),(228,211)],7)
  curve(d,[(79,100),(98,85),(118,85),(140,96)],5);curve(d,[(203,94),(228,84),(246,89),(255,103)],5)
 elif surprise:
  d.ellipse((145,218,197,274),fill=INK)
  curve(d,[(79,95),(105,71),(126,71),(143,89)],6);curve(d,[(201,90),(222,68),(246,74),(264,95)],6)
 else:
  curve(d,[(134,237),(153,222),(181,222),(202,234)],6)
  curve(d,[(78,100),(101,114),(123,111),(141,100)],5);curve(d,[(201,101),(226,112),(245,111),(265,98)],5)
 if expression=='tired':
  curve(d,[(62,197),(68,209),(74,209),(81,196)],3)
 face=face.resize((int(350*scale),int(350*scale)),Image.Resampling.LANCZOS).rotate(tilt,resample=Image.Resampling.BICUBIC,expand=True)
 im.paste(face,(int(x-face.width/2),int(y-face.height/2)),face)

def pulse(t,start,length):
 q=(t-start)/length
 return math.sin(math.pi*q)**2 if 0<q<1 else 0

def phone(d,x,y):
 d.rounded_rectangle((x-45,y-85,x+45,y+85),15,fill=INK)
 d.rounded_rectangle((x-36,y-72,x+36,y+64),9,fill=WHITE)
 line(d,[(x-12,y-65),(x+12,y-65)],3)
 d.rounded_rectangle((x-25,y-40,x+18,y-7),5,outline=INK,width=3)
 d.rounded_rectangle((x-17,y+4,x+25,y+36),5,outline=INK,width=3)
 line(d,[(x-17,y-25),(x+10,y-25)],2)
 line(d,[(x-9,y+20),(x+17,y+20)],2)

def cup(d,x,y,lift=0):
 y-=lift
 d.rounded_rectangle((x-28,y-52,x+28,y),8,fill=WHITE,outline=INK,width=4)
 d.arc((x+18,y-44,x+50,y-9),-90,90,fill=INK,width=4)
 line(d,[(x-27,y-48),(x+27,y-48)],3)
 # restrained steam (not a moving overlay across the characters)
 for off in [-12,12]:curve(d,[(x+off,y-66),(x+off-10,y-78),(x+off+10,y-90),(x+off,y-104)],2,fill='#777777')
