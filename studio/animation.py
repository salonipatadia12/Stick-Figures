"""Reusable articulated actors, expressions, props and independent backgrounds.

Coordinates are shared by hands and held props. Heads are rigid circles; no
image warping, inferred masks or café-specific cutouts are used.
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from .animation_models import Animation, Character

W,H=720,1280
NAVY='#182038'
S=2

def ease(t):
    t=max(0,min(1,t));return t*t*(3-2*t)
def lerp(a,b,t):return tuple(x+(y-x)*t for x,y in zip(a,b))
def pulse(q,a=.15,b=.65):
    return math.sin(math.pi*(q-a)/(b-a))**2 if a<q<b else 0
def rgb(h):return tuple(int(h[i:i+2],16) for i in (1,3,5))

class Pen:
    def __init__(self,image,scale=1):self.d=ImageDraw.Draw(image);self.s=scale
    def box(self,b):return tuple(round(v*self.s) for v in b)
    def pts(self,p):return [(round(x*self.s),round(y*self.s)) for x,y in p]
    def line(self,p,fill=NAVY,width=5):
        self.d.line(self.pts(p),fill=fill,width=max(1,round(width*self.s)),joint='curve')
    def ellipse(self,b,fill,outline=None,width=1):self.d.ellipse(self.box(b),fill=fill,outline=outline,width=round(width*self.s))
    def rect(self,b,fill,outline=None,width=1,radius=0):
        self.d.rounded_rectangle(self.box(b),radius=round(radius*self.s),fill=fill,outline=outline,width=round(width*self.s))
    def poly(self,p,fill):self.d.polygon(self.pts(p),fill=fill)
    def arc(self,b,start,end,fill=NAVY,width=3):self.d.arc(self.box(b),start,end,fill=fill,width=round(width*self.s))

def background(setting,light):
    palettes={'day':('#F7EAD2','#EBCB99','#AD774F'),'sunset':('#F5C7A3','#E9A878','#9F6354'),'night':('#334359','#617083','#344253')}
    top,bottom,floor=palettes[light];im=Image.new('RGB',(W,H));d=ImageDraw.Draw(im)
    a,b=rgb(top),rgb(bottom)
    for y in range(H):d.line((0,y,W,y),fill=tuple(round(c+(v-c)*min(1,y/1000)) for c,v in zip(a,b)))
    p=Pen(im);p.rect((0,1000,720,1280),floor)
    if setting in {'cafe','home','office'}:
        p.rect((65,110,390,610),'#826F62',radius=12)
        p.rect((78,122,377,594),'#A6C4C5' if light!='night' else '#23344D',radius=6)
        for x,y,r in [(120,390,100),(265,330,108),(360,420,100)]:
            p.ellipse((x-r,y-r,x+r,y+r),'#799D78' if light!='night' else '#465D5A')
        p.line([(228,123),(228,594)],'#D8C9AE',12);p.line([(78,375),(377,375)],'#D8C9AE',12)
        p.rect((56,600,400,619),'#B08A60',radius=5)
        if setting=='office':
            p.rect((460,200,658,575),'#967955',radius=8)
            for row in range(3):
                p.line([(470,300+row*90),(650,300+row*90)],'#D5B48A',8)
                for j,color in enumerate(['#4D7C82','#CA925D','#E6CC9B','#7D786C','#A35F4D']):
                    p.rect((480+j*30,230+row*90,502+j*30,292+row*90),color,radius=2)
        elif setting=='home':
            p.rect((460,470,690,630),'#6E9290',radius=25);p.rect((455,570,700,640),'#527878',radius=20)
        else:
            p.line([(545,0),(545,155)],NAVY,5)
            p.poly([(510,154),(580,154),(610,213),(480,213)],'#526E65')
            p.ellipse((481,200,610,229),'#F8DCA2')
        p.rect((600,841,676,1002),'#B57955',radius=10)
        for x,y in [(595,790),(646,758),(675,824),(610,858)]:
            p.line([(638,900),(x,y)],'#526A50',5);p.ellipse((x-25,y-42,x+25,y+14),'#71896A')
    elif setting=='park':
        p.rect((0,795,720,1000),'#B3C197')
        p.poly([(315,800),(405,800),(620,1280),(125,1280)],'#D1BB91')
        for x,y in [(75,500),(640,430),(500,280)]:
            p.line([(x,y+65),(x,y+340)],'#8D7252',25)
            p.ellipse((x-120,y-140,x+120,y+100),'#7C9D77')
            p.ellipse((x-80,y-170,x+80,y+20),'#91AE82')
    elif setting=='street':
        for x,h,c in [(0,550,'#C6967D'),(170,730,'#A7B3AF'),(440,600,'#D6B78C'),(600,780,'#B6A1A0')]:
            p.rect((x,800-h,x+165,1000),c,radius=3)
            for xx in [x+30,x+100]:
                for yy in range(850-h,760,100):p.rect((xx,yy,xx+35,yy+60),'#EBD6AE',radius=2)
        p.rect((0,930,720,1000),'#C7B8A3')
    else:
        p.ellipse((100,200,620,720),'#E8D6BE' if light!='night' else '#536278')
        p.line([(80,1000),(640,1000)],'#A38D75',3)
    # Fixed floor lines and light; camera never zooms or drifts.
    for y in [1090,1210]:p.line([(0,y),(720,y)],'#8B715D' if light!='night' else '#46515F',2)
    return im

def joint(shoulder,wrist,length=90,side=1,outward=True):
    dx,dy=wrist[0]-shoulder[0],wrist[1]-shoulder[1];dist=max(.001,math.hypot(dx,dy))
    # Validation keeps interaction targets in reach; clamp protects editor previews.
    if dist>2*length:
        wrist=(shoulder[0]+dx*2*length/dist,shoulder[1]+dy*2*length/dist)
        dx,dy=wrist[0]-shoulder[0],wrist[1]-shoulder[1];dist=2*length
    h=math.sqrt(max(0,length*length-dist*dist/4))
    bend=(-side if dy>=0 else side) if outward else side
    return (shoulder[0]+dx/2-bend*dy*h/dist,shoulder[1]+dy/2+bend*dx*h/dist),wrist

def actor_pose(actor,q):
    direction=1 if actor.x<.5 else -1
    x=W*(actor.x+(actor.end_x-actor.x)*ease(q) if actor.action=='walk' else actor.x)
    seated=1 if actor.pose=='seated' else 0
    if actor.action=='sit':seated=ease(q)
    elif actor.action=='stand':seated=1-ease(q)
    bob=2*math.sin(q*math.pi*8) if actor.action=='walk' else -2*pulse(q) if actor.action=='laugh' else 0
    hip=800+65*seated+bob;shoulder=hip-160
    return dict(x=x,hip=hip,cy=hip-270,shoulder=shoulder,seated=seated,direction=direction,
                hand=(x+direction*77,hip-15),backhand=(x-direction*70,hip-12))

class AnimatedScene:
    def __init__(self,scene,characters,background_path=None):
        self.scene=scene;self.plan=Animation.model_validate(scene['animation'])
        self.characters={c['id']:Character.model_validate(c) for c in characters}
        if background_path:
            with Image.open(background_path) as im:self.bg=ImageOps.fit(im.convert('RGB'),(W,H),Image.Resampling.LANCZOS)
        else:self.bg=background(self.plan.setting,self.plan.light)

    def poses(self,q):
        poses={a.character:actor_pose(a,q) for a in self.plan.actors}
        props={p.id:dict(kind=p.kind,position=(p.x*W,{'bag':744,'flower':787,'ball':792,'note':795}.get(p.kind,802)),holder=p.holder) for p in self.plan.props}
        for a in self.plan.actors:
            z=poses[a.character];x=z['x'];sign=z['direction'];base=z['hand']
            if a.action=='wave':
                beat=math.sin(q*math.pi*4)*12*math.sin(math.pi*q)
                raised=(x+sign*(120+beat),z['cy']+35+10*math.sin(q*math.pi*2))
                z['hand']=lerp(base,raised,min(ease(q/.18),ease((1-q)/.18)))
            elif a.action in {'point','talk'}:
                z['interacting']=True
                z['hand']=lerp(base,(x+sign*140,z['shoulder']+55),pulse(q,.05,.95))
            elif a.action in {'pick_up','put_down','write','give'}:
                z['interacting']=True
                item=props[a.prop];target=item['position']
                if a.action=='pick_up':
                    contact=.38
                    z['hand']=lerp(base,target,ease(q/contact)) if q<contact else lerp(target,base,ease((q-contact)/(1-contact)))
                    item['holder']='none' if q<contact else a.character
                elif a.action=='put_down':
                    contact=.65
                    z['hand']=lerp(base,target,ease(q/contact)) if q<contact else lerp(target,base,ease((q-contact)/(1-contact)))
                    item['holder']=a.character if q<contact else 'none'
                elif a.action=='write':
                    # Hand, pen and ink share this exact page-space trajectory.
                    path=[(target[0]-25+50*i/80,target[1]-9+6*math.sin(i/80*math.pi*4)) for i in range(81)]
                    f=min(80,round(ease(q)*80));tip=path[f];z['hand']=(tip[0]-10,tip[1]-26)
                    z['pen']=tip;item['ink']=path[:f+1]
                else:
                    other=next(b for b in self.plan.actors if b.character!=a.character)
                    receiver=poses[other.character];receiver['interacting']=True;mid=((x+receiver['x'])/2,min(z['shoulder'],receiver['shoulder'])+100)
                    if q<.65:
                        z['hand']=lerp(base,mid,ease(q/.55))
                        receiver['hand']=lerp(receiver['hand'],mid,ease((q-.2)/.45))
                        item['holder']=a.character
                    else:
                        z['hand']=lerp(mid,base,ease((q-.65)/.35))
                        receiver['hand']=lerp(mid,receiver['hand'],ease((q-.65)/.35))
                        item['holder']=other.character
        for item in props.values():
            if item['holder']!='none':item['position']=poses[item['holder']]['hand']
        return poses,props

    def frame(self,t,length=6,energy=0):
        q=max(0,min(1,t/max(.1,length)));poses,props=self.poses(q)
        im=self.bg.resize((W*S,H*S),Image.Resampling.LANCZOS);p=Pen(im,S)
        # Seating and contact shadows are behind the actors.
        for a in self.plan.actors:
            z=poses[a.character];x=z['x']
            p.ellipse((x-85,983,x+85,1011),'#897B68')
            if a.pose=='seated' or a.action in {'sit','stand'}:
                p.rect((x-68,742,x+70,785),'#AD8055',NAVY,3,9)
                for dx in [-50,-20,20,50]:p.line([(x+dx,780),(x+dx,883)],'#A2774C',9)
                p.rect((x-78,875,x+80,896),'#BE9566',NAVY,3,8)
                for dx in [-58,58]:p.line([(x+dx,893),(x+dx,1000)],'#A2774C',12)
        for a in self.plan.actors:self.body(p,a,poses[a.character],q)
        free=[dict(position=(v.x*W,802)) for v in self.plan.props ]
        if free:
            lo=max(30,min(v['position'][0] for v in free)-85);hi=min(690,max(v['position'][0] for v in free)+85)
            p.line([(lo+25,831),(lo+25,1000)],'#997249',12);p.line([(hi-25,831),(hi-25,1000)],'#997249',12)
            p.rect((lo,815,hi,839),'#C19A6C',NAVY,3,9)
        for v in props.values():self.prop(p,v)
        for a in self.plan.actors:
            z=poses[a.character]
            self.arms(p,z)
            if 'pen' in z:
                tip=z['pen'];p.line([(tip[0]-23,tip[1]-59),tip],NAVY,8);p.line([(tip[0]-22,tip[1]-56),(tip[0]-5,tip[1]-12)],'#478887',5)
                p.ellipse((z['hand'][0]-10,z['hand'][1]-10,z['hand'][0]+10,z['hand'][1]+10),NAVY)
            speaking=self.plan.speaker==a.character
            self.face(p,a,z,q,energy if speaking else 0)
        return im.resize((W,H),Image.Resampling.LANCZOS)

    def body(self,p,a,z,q):
        c=self.characters[a.character];x,hip,seat=z['x'],z['hip'],z['seated'];sign=z['direction']
        p.line([(x,z['cy']+80),(x,z['shoulder']+5)],NAVY,18)
        gait=math.sin(q*math.pi*8)*35 if a.action=='walk' else 0
        for side in [-1,1]:
            h=(x+side*23,hip);foot=(x+side*38+side*gait+sign*35*seat,996)
            knee=((h[0]+foot[0])/2+sign*40*seat,hip+(996-hip)*.52)
            p.line([h,knee,foot],NAVY,17);p.ellipse((foot[0]-17,984,foot[0]+24,1004),NAVY)
        p.poly([(x-43,z['shoulder']-5),(x+43,z['shoulder']-5),(x+51,hip+12),(x-51,hip+12)],NAVY)
        p.poly([(x-36,z['shoulder']+2),(x+36,z['shoulder']+2),(x+43,hip+5),(x-43,hip+5)],c.color)
        p.poly([(x-36,z['shoulder']+2),(x-17,z['shoulder']+2),(x-26,hip+5),(x-43,hip+5)],'#D8D5D3')
        if c.accessory=='scarf':
            p.rect((x-47,z['shoulder']-22,x+47,z['shoulder']+7),c.accent,NAVY,3,12)
            p.poly([(x+20,z['shoulder']),(x+40,z['shoulder']),(x+44,z['shoulder']+90),(x+22,z['shoulder']+87)],c.accent)

    def arms(self,p,z):
        for side,wrist in [(z['direction'],z['hand']),(-z['direction'],z['backhand'])]:
            shoulder=(z['x']+side*43,z['shoulder']+5)
            elbow,wrist=joint(shoulder,wrist,90,side,outward=not(z.get('interacting') and side==z['direction']))
            p.line([shoulder,elbow,wrist],NAVY,16)
            for xx,yy in [shoulder,elbow,wrist]:p.ellipse((xx-8,yy-8,xx+8,yy+8),NAVY)
            p.ellipse((wrist[0]-12,wrist[1]-10,wrist[0]+12,wrist[1]+12),NAVY)

    def face(self,p,a,z,q,energy):
        c=self.characters[a.character];x,y=z['x'],z['cy'];r=88
        p.ellipse((x-r,y-r,x+r,y+r),'#D5D2D2',NAVY,5)
        p.ellipse((x-r+13,y-r+7,x+r-6,y+r-6),c.color)
        emotion='happy' if a.action=='laugh' else (a.end_emotion if a.end_emotion and q>=.65 else a.emotion)
        for side in [-1,1]:
            ex=x+side*30
            if a.action=='laugh' and .2<q<.6:p.arc((ex-12,y+8,ex+12,y+22),185,355,NAVY,4)
            else:
                p.ellipse((ex-8,y+4,ex+8,y+26),NAVY)
                p.ellipse((ex+1,y+7,ex+5,y+12),'#FFFFFF')
            if emotion=='sad' or emotion=='worried':p.line([(ex-14,y-10-side*6),(ex+14,y-10+side*6)],NAVY,3)
            elif emotion=='angry':p.line([(ex-14,y-9+side*7),(ex+14,y-9-side*7)],NAVY,3)
            elif emotion=='surprised':p.arc((ex-13,y-26,ex+13,y-8),185,350,NAVY,3)
            else:p.arc((ex-14,y-19,ex+14,y-8),190,340,NAVY,3)
        if c.accessory=='glasses':
            for side in [-1,1]:p.ellipse((x+side*30-20,y-1,x+side*30+20,y+33),None,NAVY,3)
            p.line([(x-10,y+10),(x+10,y+10)],NAVY,3)
        opening=min(1,energy*16)
        if a.action=='laugh':opening=max(opening,.35*pulse(q,.15,.65))
        if opening>.08:
            h=5+19*opening;p.ellipse((x-15,y+40,x+15,y+40+h),NAVY)
            p.ellipse((x-8,y+40+h*.6,x+8,y+40+h-1),'#D8917A')
        elif emotion in {'happy','neutral'}:p.arc((x-20,y+30,x+20,y+53),0,180,NAVY,3)
        elif emotion in {'sad','worried','angry'}:p.arc((x-17,y+43,x+17,y+62),185,355,NAVY,3)
        else:p.ellipse((x-7,y+40,x+7,y+54),NAVY)

    def prop(self,p,item):
        x,y=item['position'];kind=item['kind']
        if kind=='book':
            p.poly([(x-48,y-13),(x-3,y-20),(x+48,y-12),(x+45,y+12),(x,y+8),(x-46,y+13)],'#FFF2D1')
            p.line([(x-48,y-13),(x-46,y+13),(x,y+8),(x+45,y+12),(x+48,y-12)],NAVY,2)
            p.line([(x-3,y-20),(x,y+8)],'#BB9B70',2)
            if len(item.get('ink',[]))>1:p.line(item['ink'],'#996B44',2)
        elif kind=='bag':
            p.arc((x-18,y,x+18,y+40),180,360,'#AA663C',7)
            p.rect((x-31,y+25,x+31,y+71),'#CA8650',NAVY,3,9);p.line([(x-27,y+44),(x+27,y+44)],'#E5B57F',2)
        elif kind=='cup':
            p.ellipse((x+11,y-16,x+34,y+7),None,'#557F75',5)
            p.rect((x-22,y-24,x+22,y+14),'#729A85',NAVY,2,6);p.ellipse((x-22,y-28,x+22,y-15),'#D6B277',NAVY,2)
        elif kind=='flower':
            p.line([(x,y+28),(x,y-22)],'#58775A',4)
            for i in range(5):
                a=i*math.pi*2/5;cx=x+12*math.cos(a);cy=y-27+12*math.sin(a);p.ellipse((cx-10,cy-10,cx+10,cy+10),'#D69384')
            p.ellipse((x-7,y-34,x+7,y-20),'#E7BE62')
        elif kind=='note':
            p.rect((x-27,y-25,x+27,y+20),'#FFF4D4',NAVY,2,2)
            for dy in [-10,0,10]:p.line([(x-17,y+dy),(x+15,y+dy)],'#B49465',2)
        else:
            p.ellipse((x-23,y-23,x+23,y+23),'#D49A62',NAVY,3)
            p.arc((x-19,y-23,x+8,y+23),270,90,NAVY,2)
