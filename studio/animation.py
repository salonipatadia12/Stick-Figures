"""Reusable articulated actors, expressions, props and independent backgrounds.

Coordinates are shared by hands and held props. Heads are rigid circles; no
image warping, inferred masks or café-specific cutouts are used.
"""
import math
from PIL import Image, ImageDraw, ImageOps, ImageFilter
from .animation_models import Animation, Character
from .illustrated_assets import backdrop, cel, place

W,H=720,1280
NAVY='#182038'
S=2

def ease(t):
    t=max(0,min(1,t));return t*t*(3-2*t)
def lerp(a,b,t):return tuple(x+(y-x)*t for x,y in zip(a,b))
def pulse(q,a=.15,b=.65):
    return math.sin(math.pi*(q-a)/(b-a))**2 if a<q<b else 0

class Pen:
    def __init__(self,image,scale=1):self.image=image;self.d=ImageDraw.Draw(image);self.s=scale
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
    return backdrop(setting,light).copy()


def joint(shoulder,wrist,length=90,side=1):
    dx,dy=wrist[0]-shoulder[0],wrist[1]-shoulder[1];dist=max(.001,math.hypot(dx,dy))
    # Validation keeps interaction targets in reach; clamp protects editor previews.
    if dist>2*length:
        wrist=(shoulder[0]+dx*2*length/dist,shoulder[1]+dy*2*length/dist)
        dx,dy=wrist[0]-shoulder[0],wrist[1]-shoulder[1];dist=2*length
    h=math.sqrt(max(0,length*length-dist*dist/4))
    # A fixed bend sign is crucial: changing it as dy crosses zero flips the
    # elbow to the other IK solution. Neutral wrists now hang nearly straight.
    bend=side
    return (shoulder[0]+dx/2-bend*dy*h/dist,shoulder[1]+dy/2+bend*dx*h/dist),wrist

def arm_target(z,side,upper_degrees=87,forearm_degrees=78):
    upper,lower=math.radians(upper_degrees),math.radians(forearm_degrees)
    return (z['x']+side*(43+90*math.cos(upper)+90*math.cos(lower)),
            z['shoulder']+5+90*math.sin(upper)+90*math.sin(lower))

def action_phase(actor,q):
    return max(0,min(1,(q-actor.action_start)/(actor.action_end-actor.action_start)))

def actor_pose(actor,q):
    direction=1 if actor.x<.5 else -1
    x=W*(actor.x+(actor.end_x-actor.x)*ease(q) if actor.action=='walk' else actor.x)
    seated=1 if actor.pose=='seated' else 0
    if actor.action=='sit':seated=ease(q)
    elif actor.action=='stand':seated=1-ease(q)
    laugh_q=q-(.10 if actor.character=='hero' else 0)
    bob=2*math.sin(q*math.pi*8) if actor.action=='walk' else -1.5*pulse(laugh_q) if actor.action=='laugh' else 0
    hip=800+65*seated+bob;shoulder=hip-160
    z=dict(x=x,hip=hip,cy=hip-270,shoulder=shoulder,seated=seated,direction=direction)
    z['hand']=arm_target(z,direction);z['backhand']=arm_target(z,-direction)
    return z

class AnimatedScene:
    def __init__(self,scene,characters,background_path=None):
        self.scene=scene;self.plan=Animation.model_validate(scene['animation'])
        self.characters={c['id']:Character.model_validate(c) for c in characters}
        positions=[a.x*W for a in self.plan.actors]+[a.end_x*W for a in self.plan.actors if a.action=='walk']
        width=min(W,max(580,max(positions)-min(positions)+264))
        left=max(0,min(W-width,(min(positions)+max(positions)-width)/2))
        self.camera_box=(left,H-width*H/W,left+width,H)
        self.shadow=Image.new('RGBA',(190*S,45*S));sd=ImageDraw.Draw(self.shadow)
        sd.ellipse((15*S,15*S,175*S,32*S),fill=(35,26,23,95))
        self.shadow=self.shadow.filter(ImageFilter.GaussianBlur(7*S))
        if background_path:
            with Image.open(background_path) as im:self.bg=ImageOps.fit(im.convert('RGB'),(W,H),Image.Resampling.LANCZOS)
        else:self.bg=background(self.plan.setting,self.plan.light)

    def poses(self,q):
        global_q=q
        poses={a.character:actor_pose(a,action_phase(a,q)) for a in self.plan.actors}
        props={p.id:dict(kind=p.kind,position=(p.x*W,{'bag':744,'flower':787,'ball':792,'note':795}.get(p.kind,802)),holder=p.holder) for p in self.plan.props}
        for a in self.plan.actors:
            q=action_phase(a,global_q)
            z=poses[a.character];x=z['x'];sign=z['direction'];base=z['hand']
            if a.action=='wave':
                # Pause, raise, one small greeting, then return and hold.
                amount=min(ease((q-.18)/.18),1-ease((q-.58)/.20))
                greeting=math.sin((q-.36)/.22*math.pi*2)*3 if .36<q<.58 else 0
                z['hand']=arm_target(z,sign,87+(49-87)*amount,78+(-84-78)*amount+greeting)
                z['open_hand']=amount>.80
                z['greeting']=amount
            elif a.action in {'point','talk'}:
                z['interacting']=True
                # A small palm-up conversational gesture; upper arm stays low.
                amount=pulse(q,.22,.68)
                z['hand']=arm_target(z,sign,87-4*amount,78-(45 if a.action=='point' else 22)*amount)
                z['gesture']=amount
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
            im.paste(self.shadow,(round((x-95)*S),978*S),self.shadow)
            if a.pose=='seated' or a.action in {'sit','stand'}:
                p.rect((x-68,742,x+70,785),'#AD8055',NAVY,3,9)
                for dx in [-50,-20,20,50]:p.line([(x+dx,780),(x+dx,883)],'#A2774C',9)
                p.rect((x-78,875,x+80,896),'#BE9566',NAVY,3,8)
                for dx in [-58,58]:p.line([(x+dx,893),(x+dx,1000)],'#A2774C',12)
        for a in self.plan.actors:self.body(p,a,poses[a.character],action_phase(a,q))
        free=[dict(position=(v.x*W,802)) for v in self.plan.props ]
        if free:
            lo=max(30,min(v['position'][0] for v in free)-85);hi=min(690,max(v['position'][0] for v in free)+85)
            p.line([(lo+25,831),(lo+25,1000)],'#997249',12);p.line([(hi-25,831),(hi-25,1000)],'#997249',12)
            p.rect((lo,815,hi,839),'#C19A6C',NAVY,3,9)
        for v in props.values():self.prop(p,v)
        for a in self.plan.actors:
            z=poses[a.character]
            self.arms(p,z,a)
            if 'pen' in z:
                tip=z['pen'];p.line([(tip[0]-23,tip[1]-59),tip],NAVY,8);p.line([(tip[0]-22,tip[1]-56),(tip[0]-5,tip[1]-12)],'#478887',5)
                p.ellipse((z['hand'][0]-10,z['hand'][1]-10,z['hand'][0]+10,z['hand'][1]+10),NAVY)
            speaking=self.plan.speaker==a.character
            self.face(p,a,z,action_phase(a,q),energy if speaking else 0)
        return im.crop(tuple(round(v*S) for v in self.camera_box)).resize((W,H),Image.Resampling.LANCZOS)

    def body(self,p,a,z,q):
        c=self.characters[a.character];x,hip,seat=z['x'],z['hip'],z['seated'];sign=z['direction']
        p.line([(x,z['cy']+80),(x,z['shoulder']+5)],NAVY,18)
        gait=math.sin(q*math.pi*8)*35 if a.action=='walk' else 0
        for side in [-1,1]:
            h=(x+side*23,hip);foot=(x+side*38+side*gait+sign*35*seat,996)
            knee=((h[0]+foot[0])/2+sign*40*seat,hip+(996-hip)*.52)
            p.line([h,knee,foot],NAVY,17);p.ellipse((foot[0]-17,984,foot[0]+24,1004),NAVY)
        torso=cel('torso',round(116*p.s),round(196*p.s),color=c.color)
        place(p.image,torso,(torso.width/2,0),(x*p.s,(z['shoulder']-14)*p.s))
        if c.accessory=='scarf':
            p.rect((x-47,z['shoulder']-22,x+47,z['shoulder']+7),c.accent,NAVY,3,12)
            p.poly([(x+20,z['shoulder']),(x+40,z['shoulder']),(x+44,z['shoulder']+90),(x+22,z['shoulder']+87)],c.accent)

    def arms(self,p,z,a):
        for side,wrist in [(z['direction'],z['hand']),(-z['direction'],z['backhand'])]:
            shoulder=(z['x']+side*43,z['shoulder']+5)
            elbow,wrist=joint(shoulder,wrist,90,side)
            # Rounded navy limbs with a fine reflected edge; no silhouette warp.
            p.line([shoulder,elbow,wrist],NAVY,14)
            for xx,yy in [shoulder,elbow,wrist]:p.ellipse((xx-7,yy-7,xx+7,yy+7),NAVY)
            active=side==z['direction']
            if active and a.action in {'write','give','pick_up','put_down'}:
                p.ellipse((wrist[0]-12,wrist[1]-10,wrist[0]+12,wrist[1]+12),NAVY)
                for dy in [-3,3,8]:p.arc((wrist[0]-9,wrist[1]+dy-5,wrist[0]+10,wrist[1]+dy+4),200,335,'#71665F',.8)
                continue
            opened=active and z.get('open_hand',False)
            width,height=(35,39) if opened else (27,31)
            hand=cel('hand-open' if opened else 'hand-rest',round(width*p.s),round(height*p.s),mirror=side<0)
            if opened:
                anchor=(hand.width*.5,hand.height*.94)
                angle=-side*8
            else:
                anchor=(hand.width*.5,hand.height*.06)
                angle=90-math.degrees(math.atan2(wrist[1]-elbow[1],wrist[0]-elbow[0]))
                # Avoid an abrupt spin when a forearm angle wraps around ±180°.
                angle=(angle+180)%360-180
            place(p.image,hand,anchor,(wrist[0]*p.s,wrist[1]*p.s),angle)

    def face(self,p,a,z,q,energy):
        # Face, eyes and mouth share one rigid layer. This is a rotation, never
        # a local zoom/displacement mask over the underlying artwork.
        size=224
        layer=Image.new('RGBA',(size*p.s,size*p.s))
        local=dict(z,x=size/2,cy=size/2)
        self.face_features(Pen(layer,p.s),a,local,q,energy)
        angle=-z['direction']*1.2*pulse(q,.18,.78) if a.action=='wave' else 0
        place(p.image,layer,(layer.width/2,layer.height/2),(z['x']*p.s,z['cy']*p.s),angle)

    def face_features(self,p,a,z,q,energy):
        c=self.characters[a.character];x,y=z['x'],z['cy'];r=102
        head=cel('head',round(2*r*p.s),round(2*r*p.s))
        place(p.image,head,(head.width/2,head.height/2),(x*p.s,y*p.s))
        x+=z['direction']*5
        laugh_q=q-(.10 if a.character=='hero' else 0)
        emotion='happy' if a.action=='laugh' else (a.end_emotion if a.end_emotion and q>=.65 else a.emotion)
        for side in [-1,1]:
            ex=x+side*34
            if a.action=='laugh' and .2<laugh_q<.6:p.arc((ex-12,y+8,ex+12,y+22),185,355,NAVY,4)
            else:
                p.ellipse((ex-9,y+1,ex+9,y+30),NAVY)
                p.ellipse((ex+2,y+4,ex+6,y+12),'#FFFFFF')
            if emotion=='sad' or emotion=='worried':p.line([(ex-14,y-10-side*6),(ex+14,y-10+side*6)],NAVY,3)
            elif emotion=='angry':p.line([(ex-14,y-9+side*7),(ex+14,y-9-side*7)],NAVY,3)
            elif emotion=='surprised':p.arc((ex-13,y-26,ex+13,y-8),185,350,NAVY,3)
            else:p.arc((ex-14,y-19,ex+14,y-8),190,340,NAVY,3)
        if c.accessory=='glasses':
            for side in [-1,1]:p.ellipse((x+side*30-20,y-1,x+side*30+20,y+33),None,NAVY,3)
            p.line([(x-10,y+10),(x+10,y+10)],NAVY,3)
        opening=min(1,energy*10)
        if a.action=='laugh':opening=max(opening,.35*pulse(laugh_q,.15,.65))
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
