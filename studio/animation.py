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


def performance(actor, q, speaker, has_partner):
    """One intentional turn/reaction, not a repeating idle oscillation."""
    blend=ease((q-.35)/.40)
    worry=(1 if actor.emotion in {'worried','sad'} else 0)*(1-blend)
    worry+=(1 if (actor.end_emotion or actor.emotion) in {'worried','sad'} else 0)*blend
    sign=1 if actor.x<.5 else -1
    engaged=has_partner and speaker!='narrator'
    listening=engaged and speaker!=actor.character
    # A listener gives one small acknowledging nod; both settle at the end.
    nod=2*pulse(q,.38,.78) if listening else 0
    look_up=ease((q-.12)/.30) if engaged else ease((q-.55)/.30)
    return dict(lean=sign*(1.3 if engaged else 0),
                head_angle=-sign*(4*worry+nod),
                gaze_x=sign*(10 if listening else 3+7*look_up),
                gaze_y=7*worry*(1-look_up),
                tension=worry)


def tilt_point(point,pivot,degrees):
    a=math.radians(degrees);dx,dy=point[0]-pivot[0],point[1]-pivot[1]
    return (pivot[0]+math.cos(a)*dx-math.sin(a)*dy,
            pivot[1]+math.sin(a)*dx+math.cos(a)*dy)

def walking_feet(actor,q):
    """Alternating swing and planted stance; feet advance in world coordinates."""
    distance=(actor.end_x-actor.x)*W
    steps=max(1,math.ceil(abs(distance)/48))
    progress=ease(q)*steps
    cycle=min(steps,int(progress));phase=progress-cycle
    stride=distance/steps
    feet=[]
    for side,offset in [(-1,0),(1,.5)]:
        swing=max(0,min(1,(phase-offset)*2))
        x=actor.x*W+side*38+stride*(cycle+ease(swing))
        lift=14*math.sin(math.pi*swing) if abs(distance)>1 else 0
        feet.append((x,996-lift))
    return feet

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
    if actor.action=='walk':
        steps=max(1,math.ceil(abs(actor.end_x-actor.x)*W/48))
        swing=6*math.sin(ease(q)*steps*math.pi*2)
        z['hand']=arm_target(z,direction,87+swing,78)
        z['backhand']=arm_target(z,-direction,87-swing,78)
    return z

class AnimatedScene:
    def __init__(self,scene,characters,background_path=None):
        self.scene=scene;self.plan=Animation.model_validate(scene['animation'])
        self.characters={c['id']:Character.model_validate(c) for c in characters}
        positions=[a.x*W for a in self.plan.actors]+[a.end_x*W for a in self.plan.actors if a.action=='walk']
        width=min(W,max(580,max(positions)-min(positions)+264))
        left=max(0,min(W-width,(min(positions)+max(positions)-width)/2))
        self.camera_box=(left,H-width*H/W,left+width,H)
        if self.plan.framing=='speaker' and self.plan.speaker!='narrator':
            subject=next(a for a in self.plan.actors if a.character==self.plan.speaker)
            # A fixed shot per speaking turn. No animated zoom or face scaling.
            width=470;left=max(0,min(W-width,subject.x*W-width/2))
            self.camera_box=(left,350,left+width,350+width*H/W)
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
                z['hand']=arm_target(z,sign,87+(-4 if a.action=='point' else 10)*amount,78-(45 if a.action=='point' else 58)*amount)
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
        for a in self.plan.actors:
            z=poses[a.character];phase=action_phase(a,global_q)
            acting=a.action in {'idle','talk','laugh'}
            z.update(performance(a,phase,self.plan.speaker,len(self.plan.actors)>1) if acting else
                     dict(lean=0,head_angle=0,gaze_x=0,gaze_y=0,tension=0))
            # Hold a page at reading height. The wrist and paper share an anchor.
            held=next((v for v in props.values() if v['holder']==a.character),None)
            if acting and held and held['kind']=='note':
                z['hand']=(z['x']+z['direction']*10,z['shoulder']+126-5*z['tension'])
                z['grip']=True
                if a.action=='talk':z['backhand']=arm_target(z,-z['direction'],87-5*pulse(phase,.22,.68),78-24*pulse(phase,.22,.68))
            pivot=(z['x'],z['hip']);angle=z['lean']
            z['shoulder_point']=tilt_point((z['x'],z['shoulder']),pivot,angle)
            z['head_point']=tilt_point((z['x'],z['cy']),pivot,angle)
            for key in ['hand','backhand']:z[key]=tilt_point(z[key],pivot,angle)
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
        free=[dict(position=(v.x*W,802)) for v in self.plan.props if v.holder=='none' or any(a.action=='put_down' and a.prop==v.id for a in self.plan.actors)]
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
            # One brief coordinated blink, never a painted patch over the face.
            blink_at=2.8+(.45 if a.character=='friend' else 0)
            z['blink']=math.sin(math.pi*(t-blink_at)/.16)**2 if blink_at<t<blink_at+.16 else 0
            self.face(p,a,z,action_phase(a,q),energy if speaking else 0)
        return im.crop(tuple(round(v*S) for v in self.camera_box)).resize((W,H),Image.Resampling.LANCZOS)

    def body(self,p,a,z,q):
        c=self.characters[a.character];x,hip,seat=z['x'],z['hip'],z['seated'];sign=z['direction']
        hx,hy=z['head_point'];sx,sy=z['shoulder_point']
        p.line([(hx,hy+80),(sx,sy+5)],NAVY,18)
        feet=walking_feet(a,q) if a.action=='walk' else None
        for i,side in enumerate([-1,1]):
            h=(x+side*23,hip)
            foot=feet[i] if feet else (x+side*38+sign*35*seat,996)
            lift=996-foot[1]
            knee=((h[0]+foot[0])/2+sign*(40*seat+lift*.55),hip+(foot[1]-hip)*.52)
            p.line([h,knee,foot],NAVY,17)
            p.ellipse((foot[0]-17,foot[1]-12,foot[0]+24,foot[1]+8),NAVY)
        torso=cel('torso',round(116*p.s),round(196*p.s),color=c.color)
        place(p.image,torso,(torso.width/2,174*p.s),(x*p.s,hip*p.s),-z['lean'])
        if c.accessory=='scarf':
            p.rect((sx-47,sy-22,sx+47,sy+7),c.accent,NAVY,3,12)
            p.arc((sx-41,sy-25,sx+42,sy+1),8,166,'#76A5A0',2)
            p.poly([(sx+20,sy),(sx+40,sy),(sx+44,sy+90),(sx+22,sy+87)],c.accent)
            for dx in [24,30,36,42]:p.line([(sx+dx,sy+82),(sx+dx,sy+96)],c.accent,2)

    def arms(self,p,z,a):
        for side,wrist in [(z['direction'],z['hand']),(-z['direction'],z['backhand'])]:
            shoulder=tilt_point((z['x']+side*43,z['shoulder']+5),(z['x'],z['hip']),z['lean'])
            elbow,wrist=joint(shoulder,wrist,90,-side if z.get('grip') and side==z['direction'] else side)
            # Rounded navy limbs with a fine reflected edge; no silhouette warp.
            p.line([shoulder,elbow,wrist],NAVY,14)
            for xx,yy in [shoulder,elbow,wrist]:p.ellipse((xx-7,yy-7,xx+7,yy+7),NAVY)
            active=side==z['direction']
            if active and (a.action in {'write','give','pick_up','put_down'} or z.get('grip')):
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
        angle=-z['direction']*1.2*pulse(q,.18,.78) if a.action=='wave' else z['head_angle']
        hx,hy=z['head_point']
        place(p.image,layer,(layer.width/2,layer.height/2),(hx*p.s,hy*p.s),angle)

    def face_features(self,p,a,z,q,energy):
        c=self.characters[a.character];x,y=z['x'],z['cy'];r=102
        head=cel('head',round(2*r*p.s),round(2*r*p.s))
        place(p.image,head,(head.width/2,head.height/2),(x*p.s,y*p.s))
        x+=z['direction']*5+z.get('gaze_x',0)
        y+=z.get('gaze_y',0)
        laugh_q=q-(.10 if a.character=='hero' else 0)
        emotion='happy' if a.action=='laugh' else a.emotion
        end='happy' if a.action=='laugh' else (a.end_emotion or emotion)
        blend=ease((q-.45)/.30) if end!=emotion else 0
        def mix(values):return values.get(emotion,0)*(1-blend)+values.get(end,0)*blend
        surprise=mix({'surprised':1})
        brow_slope=mix({'sad':6,'worried':6,'angry':-7})
        brow_arch=mix({'happy':5,'neutral':5,'surprised':9})
        brow_y=-10-9*surprise
        eye_open=(1-.90*pulse(laugh_q,.15,.65) if a.action=='laugh' else 1)*(1-.94*z.get('blink',0))
        smile=mix({'happy':9,'neutral':7,'sad':-7,'worried':-7,'angry':-7})
        for side in [-1,1]:
            ex=x+side*34
            half=14.5*eye_open*(1+.08*surprise);ey=y+15.5
            p.ellipse((ex-9,ey-half,ex+9,ey+half),NAVY)
            if eye_open>.35:p.ellipse((ex+2,ey-half+3,ex+6,ey-half+min(11,half)),'#FFFFFF')
            points=[]
            for i in range(9):
                u=i/8
                points.append((ex-14+28*u,y+brow_y+side*brow_slope*(2*u-1)-brow_arch*4*u*(1-u)))
            p.line(points,NAVY,3)
        if c.accessory=='glasses':
            for side in [-1,1]:p.ellipse((x+side*30-20,y-1,x+side*30+20,y+33),None,NAVY,3)
            p.line([(x-10,y+10),(x+10,y+10)],NAVY,3)
        opening=max(min(1,energy*10),.25*surprise)
        if a.action=='laugh':opening=max(opening,.35*pulse(laugh_q,.15,.65))
        if opening>.08:
            h=5+19*opening;p.ellipse((x-15,y+40,x+15,y+40+h),NAVY)
            p.ellipse((x-8,y+40+h*.6,x+8,y+40+h-1),'#D8917A')
        else:
            points=[(x-18+36*i/12,y+44+smile*4*(i/12)*(1-i/12)) for i in range(13)]
            p.line(points,NAVY,3)

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
            # Wrist anchors the left edge, rather than the middle of the page.
            p.poly([(x-5,y-63),(x+67,y-56),(x+62,y+28),(x-9,y+21)],'#FFF4D4')
            p.line([(x-5,y-63),(x+67,y-56),(x+62,y+28),(x-9,y+21),(x-5,y-63)],NAVY,2)
            for dy in [-40,-25,-10,5]:p.line([(x+7,y+dy),(x+49,y+dy+4)],'#B49465',1.5)
        else:
            p.ellipse((x-23,y-23,x+23,y+23),'#D49A62',NAVY,3)
            p.arc((x-19,y-23,x+8,y+23),270,90,NAVY,2)
