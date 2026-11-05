"""Supersampled scenes with an original expressive, articulated stick character."""
import math
from PIL import Image, ImageDraw
from .art import font, ease, pulse
from .character import Pen, INK, ACCENT, PAPER, face, torso, limb, hand, shoe, seated, seated_pose, standing
from .legacy_visuals import fit, wrapped, preflight, draw as classic_draw

W,H=1080,1920
SCALE=2


def text(im,xy,value,size=48,color=INK,bold=True):
    ImageDraw.Draw(im).text((xy[0]*SCALE,xy[1]*SCALE),value,font=font(size*SCALE,bold),anchor='mm',fill=color)


def ground(p):
    p.ellipse((235,1401,835,1460),'#E9EFEC')
    p.line([(110,1440),(970,1440)],2,'#D4DDDA')


def sofa(p):
    p.rect((205,1060,845,1345),35,'#EDF3EF','#A8BBB4',4)
    p.line([(240,1198),(810,1198)],3,'#A8BBB4')
    p.line([(265,1345),(255,1434)],7,'#A8BBB4')
    p.line([(795,1345),(805,1434)],7,'#A8BBB4')
    p.rect((170,1175,235,1348),23,'#E3ECE6','#A8BBB4',4)
    p.rect((815,1175,880,1348),23,'#E3ECE6','#A8BBB4',4)


def chair(p):
    p.rect((325,1060,455,1230),18,'#EDF3EF','#A8BBB4',4)
    p.line([(345,1230),(320,1430)],5,'#A8BBB4')
    p.line([(435,1230),(460,1430)],5,'#A8BBB4')


def notebook(p,t,s):
    p.polygon([(602,1127),(782,1127),(819,1084),(639,1084)],PAPER,INK,3)
    p.line([(690,1123),(725,1089)],2,'#BAC8C3')
    if s['action']=='type':
        progress=ease((t-.8)/1.5)
        for i in range(3):
            if progress>i/3:
                x=653+i*2;y=1094+i*8
                p.line([(x,y),(x+49*min(1,(progress-i/3)*3),y)],2,ACCENT)


def phone(p,x,y):
    p.rect((x-33,y-64,x+33,y+64),10,INK,INK,3)
    p.rect((x-26,y-52,x+26,y+46),5,'#E8F1ED',None)
    p.line([(x-9,y-54),(x+9,y-54)],3,INK)
    p.rect((x-18,y-24,x+17,y-3),4,ACCENT,None)
    p.rect((x-11,y+6,x+19,y+24),4,'#B4CDC6',None)


def cup(p,x,y):
    p.ellipse((x+19,y-44,x+49,y-8),PAPER,INK,3)
    p.rect((x-25,y-53,x+25,y),6,PAPER,INK,4)
    p.line([(x-21,y-47),(x+21,y-47)],3,ACCENT)
    for dx in [-8,9]:p.curve([(x+dx,y-64),(x+dx-7,y-76),(x+dx+6,y-84),(x+dx,y-96)],2,'#99AEA7')


def desk_scene(p,t,s):
    chair(p)
    hands=seated(p,t,s,True,False)
    # Desktop occludes the lower arm; hands are then drawn above their contacts.
    p.polygon([(535,1130),(880,1130),(920,1165),(555,1165)],'#F0DDBE','#826F59',4)
    p.rect((555,1165,920,1194),0,'#E4CCA8','#826F59',3)
    for x in [578,889]:p.line([(x,1194),(x-8,1430)],7,'#826F59')
    notebook(p,t,s)
    if s['action']!='think':
        hx,hy=hands[1]
        p.line([(hx-10,hy+10),(hx+20,hy-37)],5,ACCENT)
        p.line([(hx-10,hy+10),(hx-14,hy+17)],3,INK)
    for h in hands:hand(p,h,'grip' if h==hands[1] else 'rest')
    # Quiet scene anchor, far from the character and captions.
    p.line([(840,1126),(840,1055)],3,'#829C89')
    p.ellipse((818,1061,841,1098),'#A9BBA4')
    p.ellipse((840,1045,866,1083),'#8EA88E')
    p.polygon([(820,1096),(864,1096),(857,1126),(827,1126)],'#DBC6A8',None)


def home_scene(p,t,s):
    sofa(p)
    hands=seated(p,t,s,False,False)
    if s['action'] in ('read','type','send'):
        lift=24*ease((t-.2)/.85)
        phone(p,646,1070-lift)
        # Thumb motion stays on the phone, with the wrist anchored.
        for h in hands:hand(p,h,'grip')
        tap=5*pulse(t,.8,.45)+5*pulse(t,1.55,.45) if s['action'] in ('type','send') else 0
        p.line([(642,1045-lift),(632-tap,1057-lift)],4)
    else:
        for h in hands:hand(p,h,'open' if s['action']=='celebrate' else 'rest')


def cafe_scene(p,t,s):
    for x,sign in [(270,1),(810,-1)]:
        p.rect((x-60,1055,x+60,1240),16,'#EDF3EF','#A8BBB4',4)
        for delta in [-40,40]:p.line([(x+delta,1240),(x+delta,1430)],5,'#A8BBB4')
        hip=(x,1210);shoulder=(x+10*sign,961)
        for knee,ankle in [((x+75*sign,1280),(x+80*sign,1424)),((x+8*sign,1295),(x+12*sign,1424))]:
            p.line([hip,knee,ankle],13);shoe(p,ankle,sign)
        p.line([shoulder,(x+16*sign,837)],13)
        torso(p,shoulder,hip)
        for h,bend in [((x-25*sign,1200),sign),((x+153*sign,1116),sign)]:
            limb(p,shoulder,h,132,136,bend)
        nod=8*pulse(t,.5 if sign==1 else 1.6,.9) if s['action']=='nod' else 0
        face(p,(x+16*sign,837+nod),s['expression'],t+(0 if sign==1 else 1),look=9*sign,tilt=sign*-3)
    p.ellipse((396,1120,684,1170),'#F0DDBE','#826F59',4)
    p.line([(540,1170),(540,1416)],7,'#826F59')
    p.line([(465,1432),(540,1416),(615,1432)],6,'#826F59')
    cup(p,458,1128);cup(p,620,1128)
    for h in [(423,1116),(657,1116),(245,1200),(835,1200)]:hand(p,h)


def draw(scene,t,style='expressive'):
    if style=='classic':return classic_draw(scene,t)
    if style=='cozy':
        from .cozy import draw as cozy_draw
        return cozy_draw(scene,t)
    if style!='expressive':raise ValueError(f'Unknown character style: {style}')
    im=Image.new('RGB',(W*SCALE,H*SCALE),PAPER);p=Pen(im,SCALE)
    ground(p)
    if scene['background']=='desk':desk_scene(p,t,scene)
    elif scene['background']=='home':home_scene(p,t,scene)
    elif scene['background']=='cafe':cafe_scene(p,t,scene)
    else:standing(p,t,scene)
    if scene['overlay']:
        rows,size=fit(scene['overlay'],880,1,42,28)
        for row in rows:text(im,(540,200),row,size,ACCENT)
        p.line([(500,247),(580,247)],4,ACCENT)
    if scene['message'] and t>=scene['event_delay']:
        # Instant readable text, with a short settle of the container only.
        dy=8*(1-ease((t-scene['event_delay'])/.2))
        p.rect((140,310+dy,940,550+dy),30,'#EFF5F0',None)
        p.polygon([(402,548+dy),(458,582+dy),(468,548+dy)],'#EFF5F0',None)
        rows,size=fit(scene['message'],720,3,53,35)
        for i,row in enumerate(rows):text(im,(540,430+dy+(i-(len(rows)-1)/2)*(size+17)),row,size)
    rows,size=fit(scene['caption'],875,2,58,42)
    for i,row in enumerate(rows):text(im,(540,1625+i*(size+16)),row,size)
    return im.resize((W,H),Image.Resampling.LANCZOS)
