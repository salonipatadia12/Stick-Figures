"""Local adaptation of Stickman Video Director's Style 4 directing principles.

Original procedural artwork; upstream references and MIT notice are in
references/stickman-video-director/. No generated upstream footage is reused.
"""
import math
from functools import lru_cache
from PIL import Image, ImageDraw
from .art import font, ease, pulse
from .character import Pen, face, limb, shoe, INK, PAPER
from .legacy_visuals import fit

W, H, S = 1080, 1920, 2
TEAL = '#36766E'
WOOD = '#B98256'


def text(im, xy, value, size=40, color=INK, bold=False):
    ImageDraw.Draw(im).text(tuple(v*S for v in xy), value,
                           font=font(size*S, bold), anchor='mm', fill=color)


def foliage(p, x, y, scale=1, color='#708B68'):
    p.line([(x,y+120*scale),(x,y-50*scale)],6*scale,'#76684E')
    for dx,dy,r in [(-60,-20,58),(30,-50,74),(-15,-105,62),(78,4,51)]:
        cx=x+dx*scale;cy=y+dy*scale;r*=scale
        p.ellipse((cx-r,cy-r,cx+r,cy+r),color)


@lru_cache(maxsize=6)
def environment(setting):
    """Static scenery is cached; the actor and props remain frame-driven."""
    im=Image.new('RGB',(W*S,H*S),'#F4E6CD');p=Pen(im,S)
    if setting=='garden':
        p.rect((0,0,W,1300),0,'#DDE9DE',None)
        p.ellipse((760,260,925,425),'#FFF2C5')
        for x,y,k,c in [(40,520,2.6,'#B8CBB0'),(1000,600,2.8,'#A2B99D'),
                         (160,800,1.8,'#7C9E82'),(980,930,2.0,'#6F927A')]:
            foliage(p,x,y,k,c)
        p.ellipse((-400,1030,1500,1780),'#B6C7A2')
        p.polygon([(0,1490),(1080,1260),(1080,1920),(0,1920)],'#E4D3B3',None)
        for x,y in [(80,1340),(960,1500),(130,1550)]:
            for dx in [-12,0,12]:p.line([(x,y),(x+dx,y-27)],3,'#79936D')
    else:
        night=setting=='cafe'
        p.rect((95,295,985,975),25,'#A88F70',None)
        p.rect((113,313,967,955),15,'#789CA0' if night else '#D6E7E4',None)
        p.ellipse((775,385,860,470),'#FFF2C7')
        for x,h,c in [(120,660,'#A0B4A6'),(360,740,'#8EA79B'),(620,615,'#91ADA4'),(830,700,'#A8BBA7')]:
            p.rect((x,h,x+170,955),0,c,None)
            for yy in range(h+30,930,70):
                for xx in range(x+25,x+150,55):p.rect((xx,yy,xx+24,yy+32),3,'#E6DDBC',None)
        for x in [395,685]:p.line([(x,310),(x,962)],14,'#F2E4CF')
        p.line([(108,680),(968,680)],12,'#F2E4CF')
        p.rect((75,967,1005,990),5,'#C7A37C',None)
        p.rect((0,1300,1080,1920),0,'#D6B896',None)
        for y in [1400,1545,1710,1890]:p.line([(0,y),(1080,y)],2,'#C2A17E')
        for x in [80,350,680,1010]:p.line([(540+(x-540)*.7,1300),(x,1920)],2,'#C2A17E')
        if night:
            for x in [220,840]:
                p.line([(x,0),(x,290)],4,'#6F5D48')
                p.polygon([(x-78,350),(x+78,350),(x+45,285),(x-45,285)],'#C89B54',None)
                p.ellipse((x-40,341,x+40,359),'#FFF0BA')
        else:
            # A curtain and a floor plant make the study distinct from the café.
            p.polygon([(20,260),(135,260),(115,1050),(10,1050)],'#C89573',None)
            p.line([(65,300),(58,1020)],3,'#B78463')
        foliage(p,950,1160,.65,'#6D8F74')
        p.polygon([(895,1245),(1005,1245),(987,1375),(915,1375)],'#C68C6D',None)
    # Chair is fixed to the floor; no translation or bobbing of its feet.
    p.rect((325,1080,530,1275),20,'#74908A',INK,4)
    p.line([(325,1250),(300,1498)],9,'#665D50')
    p.line([(455,1250),(480,1498)],9,'#665D50')
    return im


def page_state(scene,t):
    base=scene.get('page_lines',0)
    progress=ease((t-1.15)/2.0) if scene['action']=='type' else 0
    return base,progress


def pen_tip(scene,t):
    base,progress=page_state(scene,t)
    return (min(base,7),progress)


def notebook(p,scene,t,detail=False):
    """Same book content in the establishing shot and overhead insert."""
    if detail:
        corners=[(130,640),(950,640),(970,1300),(110,1300)]
    else:
        corners=[(510,1082),(812,1082),(881,1160),(489,1160)]
    a,b,c,d=corners
    def point(u,v):
        return ((1-v)*((1-u)*a[0]+u*b[0])+v*((1-u)*d[0]+u*c[0]),
                (1-v)*a[1]+v*d[1])
    p.polygon([(x+5,y+12) for x,y in corners],TEAL,INK,3)
    p.polygon(corners,'#FFFAE8',INK,3)
    p.line([point(.48,.02),point(.48,.98)],2,'#CABCA0')
    base,progress=page_state(scene,t)
    for i in range(min(base,8)+(1 if progress else 0)):
        part=1 if i<base else progress
        v=.19+i*.082
        start=point(.55,v);end=point(.55+.34*part,v)
        phrases=scene.get('page_text','').splitlines()
        if detail and i<len(phrases):
            # Reveal letters at the pencil; retain completed lines across cuts.
            phrase=phrases[i]
            size=30
            target_width=point(.89,v)[0]-start[0]
            while p.d.textlength(phrase,font=font(size*S,False))>target_width*S:
                size-=1
            p.d.text((start[0]*S,(start[1]-4)*S),phrase[:math.ceil(len(phrase)*part)],
                     font=font(size*S,False),fill=INK,anchor='ls')
        else:
            p.line([start,end],1.3,'#657A74')
        if i in [2,5]:
            p.line([point(.57,v-.01),point(.85*part+.04*(1-part),v+.008)],2,'#B3846B')
    if detail:
        title=scene.get('notebook_title','NOTES')
        _,title_size=fit(title,315,1,23,13)
        text(p.d._image,(334,745),title,title_size,TEAL,True)
        # Left page remains the first imperfect sentence across later scenes.
        if base>3:
            for i in range(8):
                y=850+i*34
                p.line([(210,y),(440-(i%3)*26,y)],2,'#87988C')
            p.line([(210,918),(440,924)],2,'#B3846B')
    row,progress=pen_tip(scene,t)
    tip=point(.55+.34*progress,.19+row*.082)
    return tip


def actor_pose(scene,t):
    writing=scene['action']=='type'
    settle=ease(t/.8)
    breath=1.4*math.sin(t*1.8)
    lean=7*settle if writing else 0
    shoulder=(510+lean,973+breath)
    hip=(480,1225)
    head=(512+lean,854+breath+3*pulse(t,.3,1.1))
    base,progress=page_state(scene,t)
    # Project the exact notebook writing point to the hand gripping the pencil.
    v=.19+min(base,7)*.082;u=.55+.34*progress
    tip=((1-v)*(510+u*302)+v*(489+u*392),1082+78*v)
    right=(tip[0]+13,tip[1]-35)
    left=(535,1142)
    if scene['action']=='think':
        q=ease(t/.7)
        right=(745+(550-745)*q,1140+(902+breath-1140)*q)
    if scene['action'] in ('read','idle','nod','celebrate'):
        right=(745,1140)
    return shoulder,hip,head,left,right,tip


def world(scene,t):
    im=environment(scene.get('setting','study')).copy();p=Pen(im,S)
    shoulder,hip,head,left,right,tip=actor_pose(scene,t)
    p.ellipse((280,1480,920,1535),'#B3A68B')
    # Seated legs keep their feet planted.
    p.line([hip,(550,1300),(515,1490)],10)
    p.line([hip,(643,1285),(668,1490)],10)
    shoe(p,(515,1490));shoe(p,(668,1490))
    sx,sy=shoulder;hx,hy=hip
    p.line([(head[0],head[1]+92),(sx,sy+5)],8)
    p.polygon([(sx-39,sy),(sx+38,sy+2),(hx+28,hy),(hx-27,hy)],PAPER,INK,6)
    face(p,head,scene['expression'],t,look=8,
         tilt=4 if scene['action'] in ('type','read') else -3,radius=96)
    p.polygon([(535,1070),(941,1070),(1030,1175),(520,1175)],'#DAB185','#7D624C',4)
    p.rect((520,1175,1030,1207),3,WOOD,'#7D624C',3)
    for x in [550,982]:p.line([(x,1205),(x-14,1510)],12,'#7D624C')
    notebook(p,scene,t)
    # Cup has a physical base on the tabletop and never changes ownership.
    p.ellipse((887,1067,950,1084),'#C5976D')
    p.ellipse((925,1020,956,1055),PAPER,'#7D624C',3)
    p.rect((884,1015,933,1073),8,'#F4EEE0','#7D624C',3)
    p.ellipse((887,1010,931,1023),'#785644')
    # Both forearms belong above the desktop; no detached hand at the edge.
    limb(p,(sx-20,sy+8),left,110,120,1,9)
    lengths=(160,180)
    limb(p,(sx+20,sy+8),right,*lengths,1,9)
    for x,y in [left,right]:p.ellipse((x-11,y-9,x+11,y+10),INK)
    if scene['action']=='type':
        p.line([tip,(tip[0]+27,tip[1]-70)],6,TEAL)
        p.line([tip,(tip[0]+3,tip[1]-8)],3,INK)
        p.ellipse((right[0]-8,right[1]-7,right[0]+8,right[1]+7),INK)
    else:
        p.line([(775,1147),(848,1160)],5,TEAL)
    return im


def insert(scene,t):
    im=Image.new('RGB',(W*S,H*S),'#DAB185');p=Pen(im,S)
    for y in [180,470,790,1130,1490,1820]:p.line([(0,y),(1080,y-30)],2,'#C69C73')
    p.ellipse((770,300,975,505),'#C39870')
    p.ellipse((771,282,959,470),'#F5EBDD')
    p.ellipse((795,306,935,446),'#785644')
    tip=notebook(p,scene,t,True)
    if scene['action']=='type':
        # Wrist, hand and pencil share one anchor, including while writing.
        hx,hy=tip[0]+34,tip[1]-90
        p.line([(1100,1420),(hx+130,hy+175),(hx,hy)],17)
        p.line([tip,(tip[0]+68,tip[1]-180)],11,TEAL)
        p.ellipse((hx-25,hy-21,hx+25,hy+21),INK)
    else:
        p.line([(775,1237),(930,1290)],10,TEAL)
    return im


def camera_for(scene,t):
    mode=scene.get('camera','auto')
    if mode!='auto':return mode
    if scene['action']=='type':
        return 'wide' if t<1.35 else ('detail' if t<4.0 else 'close')
    if scene['action']=='read':
        return 'wide' if t<2.0 else 'detail'
    return 'wide' if t<2.35 else 'close'


def draw(scene,t):
    mode=camera_for(scene,t)
    im=insert(scene,t) if mode=='detail' else world(scene,t)
    if mode=='close':
        # A deliberate cut to the actor, with a tiny ease-in after the cut.
        zoom=1.24+.018*ease((t-2.35)/1.0)
        cw=W/zoom;ch=H/zoom;cx=555;cy=1000
        box=((cx-cw/2)*S,(cy-ch/2)*S,(cx+cw/2)*S,(cy+ch/2)*S)
        im=im.crop(tuple(round(x) for x in box)).resize((W*S,H*S),Image.Resampling.LANCZOS)
    p=Pen(im,S)
    # Titles and captions are postproduction overlays, independent of the camera.
    label=scene['overlay']
    if label:
        width=max(230,ImageDraw.Draw(im).textlength(label,font=font(29*S,True))/S+60)
        p.rect((540-width/2,170,540+width/2,235),32,'#FFF9ED',None)
        text(im,(540,202),label,29,TEAL,True)
    rows,size=fit(scene['caption'],820,2,53,42)
    if rows:
        top=1620
        p.rect((75,top-36,1005,top+len(rows)*(size+14)+16),24,'#FFF9ED',None)
        for i,row in enumerate(rows):text(im,(540,top+size/2+i*(size+14)),row,size,INK,True)
    return im.resize((W,H),Image.Resampling.LANCZOS)
