from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
import math,json,subprocess,wave,struct
R=Path(__file__).resolve().parent;A=R/'audio';W,H=1080,1920;FPS=30
(R/'exports').mkdir(exist_ok=True)
(R/'previews').mkdir(exist_ok=True)
INK='#171717';WHITE='white';FONT='/System/Library/Fonts/Supplemental/Arial Bold.ttf';REG='/System/Library/Fonts/Supplemental/Arial.ttf'
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
if not Path(FONT).exists():
 FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
 REG='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
sc=[];cursor=0
for j,(words,cap,mode) in enumerate(json.loads((R/'scenes.json').read_text())):
 subprocess.run(['ffmpeg','-v','error','-y','-i',str(A/f'{j}.mp3'),'-ar','44100','-ac','1',str(A/f'{j}.wav')],check=True)
 with wave.open(str(A/f'{j}.wav')) as q:dur=q.getnframes()/q.getframerate()
 assert dur>1
 n=round((dur+.55)*FPS);sc.append(dict(j=j,start=cursor,end=cursor+n,mode=mode,cap=cap,words=words));cursor+=n
print([(s['mode'],round((s['end']-s['start'])/FPS,2)) for s in sc],flush=True)
def pulse(t,start,length):
 q=(t-start)/length
 return math.sin(math.pi*q)**2 if 0<q<1 else 0
def bubble(d,lines,kind='message',typing=False):
 x0,y0,x1,y1=140,280,940,540
 d.rounded_rectangle((x0,y0,x1,y1),35,fill=WHITE,outline=INK,width=5)
 d.polygon([(765,540),(810,590),(810,540)],fill=WHITE)
 line(d,[(765,540),(810,590),(810,540)],4)
 if typing:
  for xx in [470,540,610]:d.ellipse((xx-9,400-9,xx+9,400+9),fill=INK)
 else:
  lines=lines.split('\n')
  for k,text in enumerate(lines):txt(d,(540,410+(k-(len(lines)-1)/2)*75),text,53)
def phone(d,x,y):
 d.rounded_rectangle((x-45,y-85,x+45,y+85),15,fill=INK)
 d.rounded_rectangle((x-36,y-72,x+36,y+64),9,fill=WHITE)
 line(d,[(x-12,y-65),(x+12,y-65)],3)
 d.rounded_rectangle((x-25,y-40,x+18,y-7),5,outline=INK,width=3)
 d.rounded_rectangle((x-17,y+4,x+25,y+36),5,outline=INK,width=3)
 line(d,[(x-17,y-25),(x+10,y-25)],2)
 line(d,[(x-9,y+20),(x+17,y+20)],2)

def home(im,t,mode):
 d=ImageDraw.Draw(im)
 # A single front-facing set, with shared contact points for seat and feet.
 line(d,[(120,1450),(960,1450)],3,fill='#BBBBBB')
 d.rounded_rectangle((190,1110,890,1365),35,fill='#FAFAFA',outline=INK,width=6)
 line(d,[(210,1365),(210,1450)],6);line(d,[(870,1365),(870,1450)],6)
 line(d,[(190,1200),(890,1200)],5)
 # room picture kept away from head and text
 d.rectangle((790,650,925,795),outline=INK,width=4)
 line(d,[(805,772),(843,715),(875,748),(910,695)],3)
 breath=math.sin(t*1.9)*2
 # hip is fixed on seat. Head moves only a few pixels before/after gestures.
 lean=10*ease(t/.75) if mode in ('send','reply') else 0
 hx,hy=500+lean,820+breath
 curve(d,[(490,930+breath),(460,1005),(452,1120),(470,1200)],9)
 curve(d,[(470,1200),(521,1226),(560,1260),(585,1275)],9)
 curve(d,[(585,1275),(589,1335),(583,1400),(583,1440)],9)
 curve(d,[(470,1200),(475,1247),(481,1290),(489,1320)],9)
 curve(d,[(489,1320),(488,1375),(483,1420),(481,1440)],9)
 for xx in [583,481]:curve(d,[(xx,1440),(xx+15,1447),(xx+30,1447),(xx+45,1446)],8)
 lift=18*(1-ease((t-.15)/.8))
 phone_x,phone_y=667,1025+lift
 curve(d,[(485,975),(531,1112+lift*.5),(587,1138+lift),(628,1082+lift)],9)
 curve(d,[(488,975),(561,1003+lift*.5),(600,1015+lift),(632,1035+lift)],9)
 # Phone and thumbs share an anchor; hands never detach.
 phone(d,phone_x,phone_y)
 swipe=18*pulse(t,.8,.5)+14*pulse(t,1.8,.5)
 line(d,[(629,1080+lift),(648,1067+lift-swipe)],6)
 exp='neutral' if mode in ('intro','hesitate','receive') else 'happy'
 head(im,hx,hy,exp,t,tilt=-5+3*pulse(t,1.0,.8),scale=.83)
 if mode=='intro':
  txt(d,(540,200),'THE MESSAGE',46)
  # photo shows the two friends; a clear visual for time apart.
  bubble(d,'“We should catch up.”\nThree months ago.')
 elif mode=='hesitate':
  if t<3.0:bubble(d,'Want to grab a coffee?')
  elif t<3.7:bubble(d,'Want to grab a coffee?'[:max(0,int(22*(1-(t-3)/.7)))])
  else:bubble(d,'They’re probably busy.')
 elif mode=='receive':
  txt(d,(540,195),'A WEEK LATER',40)
  if t<1.25:bubble(d,'',typing=True)
  else:bubble(d,'It’s been a rough week.')
 elif mode=='send':
  if t<1.1:bubble(d,'',typing=True)
  else:bubble(d,'Coffee tomorrow?\nMy treat.')
  if t>2.5:
   txt(d,(870,600),'Sent',25)
 elif mode=='reply':
  bubble(d,'I’d really like that.')
  # relaxed shoulder, small nod in response.

def cup(d,x,y,lift=0):
 y-=lift
 d.rounded_rectangle((x-28,y-52,x+28,y),8,fill=WHITE,outline=INK,width=4)
 d.arc((x+18,y-44,x+50,y-9),-90,90,fill=INK,width=4)
 line(d,[(x-27,y-48),(x+27,y-48)],3)
 # restrained steam (not a moving overlay across the characters)
 for off in [-12,12]:curve(d,[(x+off,y-66),(x+off-10,y-78),(x+off+10,y-90),(x+off,y-104)],2,fill='#777777')
def cafe(im,t,mode):
 d=ImageDraw.Draw(im);line(d,[(110,1450),(975,1450)],3,fill='#BBBBBB')
 txt(d,(540,195),'THE NEXT MORNING',41)
 # Chairs behind people, with grounded feet.
 for x in [250,770]:
  d.rounded_rectangle((x-65,990,x+80,1250),22,fill=WHITE,outline=INK,width=5)
  for xx in [x-45,x+60]:line(d,[(xx,1250),(xx,1440)],5)
 # characters face inward; heads use original style and scale.
 for right in [False,True]:
  x=790 if right else 290;sign=-1 if right else 1
  nod=9*pulse(t,.7 if right else 1.7,1.1)
  curve(d,[(x,930),(x-15*sign,1030),(x-12*sign,1120),(x,1230)],8)
  curve(d,[(x,1230),(x+42*sign,1255),(x+75*sign,1270),(x+80*sign,1290)],8)
  line(d,[(x+80*sign,1290),(x+80*sign,1440),(x+115*sign,1445)],8)
  line(d,[(x,1230),(x-17*sign,1320),(x-15*sign,1440),(x+16*sign,1445)],8)
  curve(d,[(x,980),(x+25*sign,1040),(x+78*sign,1095),(x+150*sign,1100)],8)
  curve(d,[(x-8*sign,980),(x-45*sign,1080),(x-38*sign,1180),(x-12*sign,1210)],8)
  head(im,x,817+nod,'happy',t+(1 if right else 0),tilt=(5 if right else -5),scale=.81)
 # table is in front of forearms and between the two chairs
 d.ellipse((404,1094,688,1158),fill=WHITE,outline=INK,width=5)
 line(d,[(546,1158),(546,1423)],6);line(d,[(460,1440),(546,1423),(632,1440)],6)
 cup(d,465,1119);cup(d,620,1120)
 # clear listening gesture: fingers rest on table, not through it
 for x in [435,650]:d.ellipse((x-11,1104,x+11,1121),fill=WHITE,outline=INK,width=3)
 if mode=='end':
  # final shared glance / nod, with a quiet hold for the last line.
  pass

def captions(im,text):
 d=ImageDraw.Draw(im);words=text.split();rows=[];row=''
 for w in words:
  test=(row+' '+w).strip()
  if d.textbbox((0,0),test,font=font(58))[2]>875:rows.append(row);row=w
  else:row=test
 if row:rows.append(row)
 for i,row in enumerate(rows):txt(d,(540,1625+i*74),row,58)
def frame(i):
 s=next(s for s in sc if s['start']<=i<s['end']);t=(i-s['start'])/FPS
 im=Image.new('RGB',(W,H),WHITE)
 if s['mode'] in ('coffee','end'):cafe(im,t,s['mode'])
 else:home(im,t,s['mode'])
 captions(im,s['cap']);return im
SR=44100;buf=[0.0]*round(cursor/FPS*SR)
for s in sc:
 with wave.open(str(A/f"{s['j']}.wav")) as q:data=q.readframes(q.getnframes())
 v=struct.unpack('<'+'h'*(len(data)//2),data);at=round((s['start']/FPS+.15)*SR)
 for k,x in enumerate(v):
  if at+k<len(buf):buf[at+k]+=x/32768*.92
 # Original phone notification, kept below the voice.
 if s['mode'] in ('receive','reply'):
  for k in range(round(.25*SR)):
   t=k/SR;idx=at+k
   if idx<len(buf):buf[idx]+=.055*math.sin(2*math.pi*1046*t)*math.exp(-t*18)
with wave.open(str(A/'mix.wav'),'wb') as q:
 q.setnchannels(1);q.setsampwidth(2);q.setframerate(SR);q.writeframes(b''.join(struct.pack('<h',round(max(-.95,min(.95,v))*32767)) for v in buf))
for j,s in enumerate(sc):frame(s['start']+round((s['end']-s['start'])*.7)).resize((360,640)).save(R/f'previews/shot-{j}.jpg')
(R/'exports/the-message-script.txt').write_text('\n\n'.join(s['words'] for s in sc))
p=subprocess.Popen(['ffmpeg','-v','warning','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1080x1920','-r','30','-i','-','-i',str(A/'mix.wav'),'-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-af','loudnorm=I=-16:TP=-1.5:LRA=9','-c:a','aac','-b:a','192k','-movflags','+faststart','-shortest',str(R/'exports/the-message.mp4')],stdin=subprocess.PIPE,stderr=open(R/'render.log','w'))
for i in range(cursor):p.stdin.write(frame(i).tobytes())
p.stdin.close();assert p.wait()==0
print('Done',cursor/FPS,flush=True)
