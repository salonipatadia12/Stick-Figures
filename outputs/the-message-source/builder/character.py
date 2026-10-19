"""Original articulated character: constant-length limbs and anchored contacts."""
import math
from PIL import ImageDraw
from .art import ease, pulse

INK = '#20292D'
ACCENT = '#3D8B87'
PAPER = '#FFFEFA'


class Pen:
    """Draw in logical pixels on a supersampled canvas, with round line ends."""
    def __init__(self, image, scale=2):
        self.d = ImageDraw.Draw(image)
        self.scale = scale

    def points(self, pts):
        return [(x*self.scale, y*self.scale) for x,y in pts]

    def line(self, pts, width=12, fill=INK):
        pts=self.points(pts); w=max(1,round(width*self.scale))
        self.d.line(pts, fill=fill, width=w, joint='curve')
        r=w/2
        for x,y in pts:
            self.d.ellipse((x-r,y-r,x+r,y+r),fill=fill)

    def curve(self, pts, width=12, fill=INK):
        a,b,c,d=pts
        out=[]
        for i in range(33):
            t=i/32;u=1-t
            out.append(tuple(u**3*a[j]+3*u*u*t*b[j]+3*u*t*t*c[j]+t**3*d[j] for j in (0,1)))
        self.line(out,width,fill)

    def ellipse(self, box, fill=PAPER, outline=None, width=1):
        self.d.ellipse(tuple(v*self.scale for v in box), fill=fill, outline=outline,width=round(width*self.scale))

    def polygon(self, pts, fill=PAPER, outline=INK, width=3):
        self.d.polygon(self.points(pts),fill=fill)
        if outline:self.line([*pts,pts[0]],width,outline)

    def rect(self, box, radius=0, fill=PAPER, outline=INK, width=3):
        self.d.rounded_rectangle(tuple(v*self.scale for v in box),radius*self.scale,fill=fill,outline=outline,width=round(width*self.scale))


def joint(start, end, upper, lower, bend=1):
    """Two-bone inverse kinematics; reachable hands/feet never stretch limbs."""
    dx,dy=end[0]-start[0],end[1]-start[1]
    distance=math.hypot(dx,dy)
    if not abs(upper-lower)+.001 <= distance <= upper+lower-.001:
        raise ValueError(f'Unreachable limb contact: {distance:.2f} for {upper}+{lower}')
    along=(upper*upper-lower*lower+distance*distance)/(2*distance)
    side=math.sqrt(max(0,upper*upper-along*along))*bend
    return (start[0]+along*dx/distance-side*dy/distance,
            start[1]+along*dy/distance+side*dx/distance)


def limb(p, start, end, upper, lower, bend=1, width=13):
    elbow=joint(start,end,upper,lower,bend)
    p.line([start,elbow,end],width)
    return elbow


def hand(p,xy,gesture='rest'):
    x,y=xy
    p.ellipse((x-13,y-10,x+14,y+12),PAPER,INK,4)
    if gesture=='grip':p.curve([(x-8,y-6),(x+3,y-9),(x+9,y-1),(x+4,y+5)],3)
    elif gesture=='open':
        p.line([(x-7,y-7),(x-16,y-18)],4)
        p.line([(x+1,y-9),(x-1,y-24)],4)
        p.line([(x+8,y-6),(x+12,y-18)],4)


def shoe(p,xy,direction=1):
    x,y=xy
    p.ellipse((x-16,y-7,x+35,y+9) if direction==1 else (x-35,y-7,x+16,y+9),INK)


def face(p, center, expression, t, look=0, tilt=0, radius=94):
    x,y=center
    p.ellipse((x-radius,y-radius,x+radius,y+radius),PAPER,INK,7)
    a=math.radians(tilt)
    def pt(q):
        u,v=q
        return x+u*math.cos(a)-v*math.sin(a),y+u*math.sin(a)+v*math.cos(a)
    def stroke(q,w=5):p.line([pt(v) for v in q],w)
    def curve(q,w=5):p.curve([pt(v) for v in q],w)
    blink=pulse(t,2.35,.15)>.35 or pulse(t,6.9,.16)>.35
    for eye in (-31,31):
        ex,ey=pt((eye+look,-7))
        if blink or expression=='tired':stroke([(eye-9+look,-9),(eye+9+look,-6)],4)
        else:p.ellipse((ex-6,ey-12,ex+6,ey+10),INK)
    if expression=='happy':
        curve([(-33,30),(-13,55),(18,55),(39,25)],5)
        curve([(-47,-33),(-33,-45),(-19,-43),(-12,-36)],4)
        curve([(17,-37),(30,-45),(44,-43),(49,-33)],4)
    elif expression=='shock':
        cx,cy=pt((look,36));p.ellipse((cx-13,cy-16,cx+13,cy+16),INK)
        stroke([(-45,-43),(-17,-48)],5);stroke([(18,-48),(47,-43)],5)
    elif expression=='tired':
        curve([(-24,39),(-9,28),(13,29),(27,34)],4)
        stroke([(-46,-24),(-18,-32)],4);stroke([(18,-32),(46,-24)],4)
    else:
        curve([(-23,35),(-9,40),(13,40),(29,30)],4)
        stroke([(-45,-32),(-18,-36)],4);stroke([(18,-39),(46,-31)],4)


def torso(p, shoulder, hip):
    sx,sy=shoulder;hx,hy=hip
    p.curve([(sx,sy-10),(sx-12,sy+70),(hx-10,hy-60),(hx,hy)],15)
    # Small identifying scarf; leaves the stick silhouette easy to read.
    p.polygon([(sx-16,sy-12),(sx+17,sy-12),(sx+6,sy+12),(sx-8,sy+6)],ACCENT,None)
    p.polygon([(sx+7,sy),(sx+27,sy+32),(sx+9,sy+24)],ACCENT,None)


def standing_pose(t,action):
    settle=ease((t-.15)/.8)
    lean=7*settle
    rise=36*ease((t-.35)/.75) if action=='celebrate' else 0
    shoulder=(530+lean,950-rise*.18)
    hip=(540,1150-rise*.1)
    hands=[(420,1190),(650,1185)]
    if action=='think':hands=[(420,1190),(601,913)]
    if action=='celebrate':hands=[(370,1015-200*settle),(710,1010-190*settle)]
    nod=9*pulse(t,.7,.8) if action=='nod' else 0
    return shoulder,hip,hands,(shoulder[0]+8,826-rise*.18+nod)


def standing(p,t,s):
    shoulder,hip,hands,center=standing_pose(t,s['action'])
    for ankle,bend in [((475,1424),1),((605,1424),-1)]:
        limb(p,hip,ankle,144,144,bend);shoe(p,ankle)
    p.line([shoulder,center],13)
    torso(p,shoulder,hip)
    for i,h in enumerate(hands):
        limb(p,shoulder,h,135,139,-1 if i==0 else 1)
        hand(p,h,'open' if s['action']=='celebrate' else 'rest')
    face(p,center,s['expression'],t,look=5,tilt=-3)
    if s['action']=='think':hand(p,hands[1])


def seated_pose(t,s,desk=False):
    action=s['action']
    move=ease((t-.2)/.85)
    lean=(14 if desk else 5)*move
    tired=10 if s['expression']=='tired' else 0
    hip=(415,1190)
    shoulder=(450+lean,965+tired)
    center=(shoulder[0]+8,840+tired+4*pulse(t,.8,.9)*(action=='nod'))
    if desk:
        write=7*math.sin((t-.8)*10)*pulse(t,.8,1.5) if action=='type' else 0
        hands=[(550,1113),(675+write,1110)]
        if action=='think':hands[1]=(center[0]+61,center[1]+64)
    elif action in ('read','type','send'):
        lift=24*move
        hands=[(605,1108-lift),(644,1048-lift)]
    elif action=='think':hands=[(410,1170),(center[0]+61,center[1]+64)]
    elif action=='celebrate':hands=[(310,1020-150*move),(625,1000-150*move)]
    else:hands=[(405,1160),(555,1155)]
    return shoulder,hip,hands,center


def seated(p,t,s,desk=False,draw_hands=True):
    shoulder,hip,hands,center=seated_pose(t,s,desk)
    for knee,ankle in [((500,1294),(493,1424)),((585,1278),(603,1424))]:
        p.line([hip,knee,ankle],13);shoe(p,ankle)
    p.line([shoulder,center],13)
    torso(p,shoulder,hip)
    for i,h in enumerate(hands):
        limb(p,shoulder,h,146,151,1)
        if draw_hands:hand(p,h)
    face(p,center,s['expression'],t,look=10,tilt=6 if s['expression']=='tired' else -3)
    if s['action']=='think' and draw_hands:hand(p,hands[1])
    return hands
