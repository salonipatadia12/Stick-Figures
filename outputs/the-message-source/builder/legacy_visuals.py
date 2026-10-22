"""Constrained layouts keep props, hands, seats and text aligned across stories."""
from PIL import Image, ImageDraw
from .art import font, txt, curve, line, head, phone, cup, ease, pulse
import math

W, H = 1080, 1920


def wrapped(text, size, width):
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    rows = []
    for paragraph in text.split('\n'):
        row = ''
        for word in paragraph.split():
            if d.textlength(word, font=font(size)) > width:
                raise ValueError('Text contains a word too wide for the frame; shorten it')
            candidate = f'{row} {word}'.strip()
            if d.textlength(candidate, font=font(size)) > width:
                rows.append(row)
                row = word
            else:
                row = candidate
        if row:
            rows.append(row)
    return rows


def fit(text, width, max_rows, max_size, min_size):
    if not text:
        return [], max_size
    for size in range(max_size, min_size - 1, -2):
        try:
            rows = wrapped(text, size, width)
        except ValueError:
            continue
        if len(rows) <= max_rows:
            return rows, size
    raise ValueError(f'Text will not fit safely: {text!r}. Shorten it.')


def preflight(story):
    for i, s in enumerate(story['scenes']):
        try:
            fit(s['caption'], 820 if story.get('character_style')=='cozy' else 875, 2, 58, 42)
            fit(s['message'], 720, 3, 53, 35)
            fit(s['overlay'], 880, 1, 42, 28)
            if story.get('character_style')=='cozy':
                fit(s.get('notebook_title','NOTES'),315,1,23,13)
        except ValueError as exc:
            raise ValueError(f'Scene {i + 1}: {exc}') from exc


def message(d, text, t, delay):
    if not text or t < delay:
        return
    d.rounded_rectangle((140, 280, 940, 550), 35, fill='white', outline='#171717', width=5)
    rows, size = fit(text, 720, 3, 53, 35)
    for i, row in enumerate(rows):
        txt(d, (540, 415 + (i-(len(rows)-1)/2)*(size+17)), row, size)


def floor(d):
    line(d, [(120, 1450), (960, 1450)], 3, '#BBBBBB')


def single(im, t, s):
    d = ImageDraw.Draw(im)
    floor(d)
    desk = s['background'] == 'desk'
    if desk:
        d.rounded_rectangle((365, 1050, 605, 1250), 18, fill='white', outline='#171717', width=5)
        line(d, [(390, 1250), (390, 1450)], 5)
        line(d, [(580, 1250), (580, 1450)], 5)
    else:
        d.rounded_rectangle((190, 1110, 890, 1365), 35, fill='#FAFAFA', outline='#171717', width=6)
        line(d, [(210, 1365), (210, 1450)], 6)
        line(d, [(870, 1365), (870, 1450)], 6)
        line(d, [(190, 1200), (890, 1200)], 5)
    action = s['action']
    nod = 10*pulse(t, .65, 1.0) if action == 'nod' else 0
    breath = 2*math.sin(t*1.9)
    curve(d, [(490,930+breath),(460,1005),(452,1120),(470,1200)], 9)
    curve(d, [(470,1200),(521,1226),(560,1260),(585,1275)], 9)
    curve(d, [(585,1275),(589,1335),(583,1400),(583,1440)], 9)
    curve(d, [(470,1200),(475,1247),(481,1290),(489,1320)], 9)
    curve(d, [(489,1320),(488,1375),(483,1420),(481,1440)], 9)
    for x in [583,481]:
        curve(d, [(x,1440),(x+15,1447),(x+30,1447),(x+45,1446)], 8)
    if desk:
        # Notebook and wrist rest share the desktop surface at y=1100.
        d.rectangle((540,1100,940,1160), fill='white', outline='#171717', width=5)
        for x in [560,920]: line(d, [(x,1160),(x,1450)], 5)
        d.polygon([(630,1098),(795,1098),(826,1060),(661,1060)], fill='white', outline='#171717', width=3)
        if action == 'think':
            curve(d, [(485,975),(580,1050),(601,970),(558,905)], 9)
        else:
            curve(d, [(485,975),(530,1020),(575,1070),(665,1080)], 9)
        motion = 8*pulse(t,.75,.7)+8*pulse(t,1.8,.7) if action in ('type','send') else 0
        if action != 'think':line(d, [(665,1080),(700+motion,1067)], 5)
        curve(d, [(488,975),(440,1035),(440,1120),(468,1170)], 8)
    elif action in ('read','type','send'):
        lift = 20*(1-ease((t-.15)/.8))
        curve(d, [(485,975),(531,1112+lift*.5),(587,1138+lift),(628,1082+lift)], 9)
        curve(d, [(488,975),(561,1003+lift*.5),(600,1015+lift),(632,1035+lift)], 9)
        phone(d, 667, 1025+lift)
        swipe = 18*pulse(t,.8,.5)+14*pulse(t,1.8,.5) if action in ('type','send') else 0
        line(d, [(629,1080+lift),(648,1067+lift-swipe)], 6)
    elif action == 'celebrate':
        up = 70*ease(t/.6)
        curve(d, [(485,975),(380,970-up*.5),(350,920-up),(350,860-up)], 9)
        curve(d, [(488,975),(600,970-up*.5),(660,920-up),(690,860-up)], 9)
    elif action == 'think':
        curve(d, [(485,975),(580,1050),(601,970),(558,905)], 9)
        curve(d, [(488,975),(440,1035),(440,1120),(468,1170)], 8)
    else:
        for sign in (-1,1):
            curve(d, [(488,975),(488+80*sign,1050),(488+80*sign,1130),(488+55*sign,1190)], 8)
    head(im,500,820+breath+nod,s['expression'],t,tilt=-5+3*pulse(t,1.0,.8),scale=.83)


def cafe(im,t,s):
    d=ImageDraw.Draw(im);floor(d)
    for x in [250,770]:
        d.rounded_rectangle((x-65,990,x+80,1250),22,fill='white',outline='#171717',width=5)
        for xx in [x-45,x+60]:line(d,[(xx,1250),(xx,1440)],5)
    for right in [False,True]:
        x=790 if right else 290;sign=-1 if right else 1
        nod=9*pulse(t,.7 if right else 1.7,1.1) if s['action']=='nod' else 0
        curve(d,[(x,930),(x-15*sign,1030),(x-12*sign,1120),(x,1230)],8)
        curve(d,[(x,1230),(x+42*sign,1255),(x+75*sign,1270),(x+80*sign,1290)],8)
        line(d,[(x+80*sign,1290),(x+80*sign,1440),(x+115*sign,1445)],8)
        line(d,[(x,1230),(x-17*sign,1320),(x-15*sign,1440),(x+16*sign,1445)],8)
        curve(d,[(x,980),(x+25*sign,1040),(x+78*sign,1095),(x+150*sign,1100)],8)
        curve(d,[(x-8*sign,980),(x-45*sign,1080),(x-38*sign,1180),(x-12*sign,1210)],8)
        head(im,x,817+nod,s['expression'],t+(1 if right else 0),tilt=5 if right else -5,scale=.81)
    d.ellipse((404,1094,688,1158),fill='white',outline='#171717',width=5)
    line(d,[(546,1158),(546,1423)],6);line(d,[(460,1440),(546,1423),(632,1440)],6)
    cup(d,465,1119);cup(d,620,1120)
    for x in [435,650]:d.ellipse((x-11,1104,x+11,1121),fill='white',outline='#171717',width=3)


def standing(im,t,s):
    d=ImageDraw.Draw(im);floor(d)
    curve(d,[(540,930),(523,1030),(524,1120),(540,1200)],9)
    for sign in (-1,1):
        line(d,[(540,1200),(540+65*sign,1320),(540+95*sign,1440),(540+125*sign,1446)],9)
        rise=75*ease(t/.65) if s['action']=='celebrate' else 0
        end=(540+160*sign,1140-rise*3)
        if s['action']=='think' and sign==1:end=(600,910)
        curve(d,[(540,970),(540+110*sign,1030-rise),(540+130*sign,1100-rise*2),end],9)
    nod=12*pulse(t,.65,1.0) if s['action']=='nod' else 0
    head(im,540,820+nod,s['expression'],t,tilt=3*pulse(t,.6,1.1),scale=.83)


def draw(scene,t):
    im=Image.new('RGB',(W,H),'white');d=ImageDraw.Draw(im)
    if scene['background']=='cafe':cafe(im,t,scene)
    elif scene['background']=='standing':standing(im,t,scene)
    else:single(im,t,scene)
    rows,size=fit(scene['overlay'],880,1,42,28)
    for row in rows:txt(d,(540,195),row,size)
    message(d,scene['message'],t,scene['event_delay'])
    rows,size=fit(scene['caption'],875,2,58,42)
    for i,row in enumerate(rows):txt(d,(540,1625+i*(size+16)),row,size)
    return im
