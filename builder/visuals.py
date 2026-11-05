"""Constrained layouts keep props, hands, seats and text aligned across stories."""
from PIL import Image, ImageDraw
from .art import font, txt, curve, line, head, phone, cup, ease, pulse, loop, INK
import math

W, H = 1080, 1920
TYPE_RATE, DELETE_RATE = 18, 40  # characters per second
MESSAGE_WIDTH = {'note': 720, 'thought': 680, 'incoming': 700, 'outgoing': 700}
PANEL_Y = 200  # message layer sits between the overlay label and the heads


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
            fit(s['caption'], 875, 2, 58, 42)
            fit(s['message'], MESSAGE_WIDTH[s['message_style']], 3, 53, 35)
            fit(s['overlay'], 880, 1, 42, 28)
        except ValueError as exc:
            raise ValueError(f'Scene {i + 1}: {exc}') from exc


def effect(s):
    if s['message_effect'] != 'auto':
        return s['message_effect']
    if s['message_style'] == 'outgoing':
        return 'none' if s['action'] == 'send' else 'type'
    return 'dots' if s['message_style'] == 'incoming' else 'pop'


def message_done(s):
    """Seconds from scene start until the message has fully appeared."""
    if not s['message']:
        return 0
    kind, n, d = effect(s), len(s['message']), s['event_delay']
    typed = n/TYPE_RATE
    return {'none': 0, 'pop': d+.2, 'dots': d+1.1, 'type': d+typed,
            'retype': d+2*typed+.8+n/DELETE_RATE}[kind]


def arrival(s):
    """When an incoming bubble lands, for its notification sound; None if it never arrives."""
    if s['message_style'] != 'incoming' or not s['message'] or effect(s) == 'none':
        return None
    return s['event_delay'] + (.9 if effect(s) == 'dots' else 0)


def message_phase(s, t):
    """(characters shown, caret, typing dots, pop progress), or None before the message starts."""
    kind, n, q = effect(s), len(s['message']), t-s['event_delay']
    typed = n/TYPE_RATE
    if kind == 'none':
        return n, False, False, 1
    if q < 0:
        return None
    if kind == 'pop':
        return n, False, False, ease(q/.2)
    if kind == 'dots':
        return (0, False, True, ease(q/.2)) if q < .9 else (n, False, False, ease((q-.9)/.2))
    if kind == 'type':
        return min(n, int(q*TYPE_RATE)), q < typed+.4, False, ease(q/.15)
    # retype: type it, hesitate, delete it, pause on the empty field, type it again
    held = typed+.45; cleared = held+n/DELETE_RATE; again = cleared+.35
    if q < typed: k = int(q*TYPE_RATE)
    elif q < held: k = n
    elif q < cleared: k = n-int((q-held)*DELETE_RATE)
    elif q < again: k = 0
    else: k = min(n, int((q-again)*TYPE_RATE))
    return max(0, k), q < again+typed+.4, False, ease(q/.15)


def reveal(rows, k):
    out = []
    for row in rows:
        out.append(row[:max(0, k)])
        k -= len(row)+1
    return out


def message(im, s, t):
    state = message_phase(s, t) if s['message'] else None
    if state is None:
        return
    k, caret, dots, grow = state
    style = s['message_style']
    rows, size = fit(s['message'], MESSAGE_WIDTH[style], 3, 53, 35)
    gap = size+17
    layer = Image.new('RGBA', (W, 640), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ink, paper = ('white', INK) if style == 'outgoing' else (INK, 'white')
    if style == 'note':
        x0, y0, x1, y1 = 140, 80, 940, 350
    else:
        width = max(d.textlength(r, font=font(size)) for r in rows)
        bw, bh = (230, 120) if dots else (max(width+100, 220), len(rows)*gap+60)
        x0 = {'incoming': 110, 'outgoing': 970-bw, 'thought': 540-bw/2}[style]
        y0 = 80-24*ease((t-s['event_delay'])/.25) if s['action'] == 'send' and style == 'outgoing' else 80
        x1, y1 = x0+bw, y0+bh
    bw = x1-x0
    radius = (y1-y0)/2 if style == 'thought' else 35
    d.rounded_rectangle((x0, y0, x1, y1), radius, fill=paper, outline=INK, width=5)
    if style == 'incoming':
        d.polygon([(x0+45, y1-3), (x0+20, y1+40), (x0+95, y1-3)], fill=paper)
        line(d, [(x0+45, y1), (x0+20, y1+40), (x0+95, y1)], 5)
    elif style == 'outgoing':
        d.polygon([(x1-95, y1-3), (x1-20, y1+40), (x1-45, y1-3)], fill=INK)
    elif style == 'thought':
        for cx, cy, r in [(530, y1+40, 18), (515, y1+85, 11)]:
            d.ellipse((cx-r, cy-r, cx+r, cy+r), fill='white', outline=INK, width=4)
    if dots:
        for i in range(3):
            hop = 12*max(0, math.sin(t*8-i*.9))
            cx = x0+bw/2+(i-1)*48
            d.ellipse((cx-11, (y0+y1)/2-11-hop, cx+11, (y0+y1)/2+11-hop), fill=INK)
    elif style == 'note':
        for i, row in enumerate(reveal(rows, k)):
            txt(d, (540, 215+(i-(len(rows)-1)/2)*gap), row, size, fill=ink)
    else:
        shown = reveal(rows, k)
        for i, row in enumerate(shown):
            txt(d, (x0+50, y0+30+gap*(i+.5)), row, size, fill=ink, anchor='lm', b=style != 'thought')
        if caret and int(t*2.5) % 2 == 0:
            i = max([j for j, r in enumerate(shown) if r] or [0])
            cx = x0+52+d.textlength(shown[i], font=font(size, style != 'thought'))
            line(d, [(cx, y0+30+gap*i+8), (cx, y0+30+gap*(i+1)-8)], 4, ink)
    if s['action'] == 'send' and style == 'outgoing' and t > s['event_delay']+.25:
        txt(d, (x1-10, y1+62), 'Delivered', 34, fill='#666666', anchor='rm', b=False)
    if grow < 1:
        # pop in around the panel's centre without moving the scene behind it
        z = .88+.12*grow
        cx, cy = (x0+x1)/2, (y0+y1)/2
        layer = layer.transform(layer.size, Image.AFFINE, (1/z, 0, cx-cx/z, 0, 1/z, cy-cy/z),
                                resample=Image.Resampling.BICUBIC)
        layer.putalpha(layer.getchannel('A').point(lambda v: int(v*grow)))
    im.paste(layer, (0, PANEL_Y), layer)


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
    nod = 10*loop(t, .65, 2.6, 1.0) if action == 'nod' else 0
    breath = 2*math.sin(t*1.9)
    tap = 6*loop(t, .8, 1.1, .4) if action == 'think' else 0
    lean = 10*ease((t-s['event_delay'])/.4) if action == 'send' else 0
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
            curve(d, [(485,975),(580,1050),(601,970+tap),(558,905+tap)], 9)
        else:
            curve(d, [(485,975),(530,1020),(575,1070),(665,1080)], 9)
        motion = 8*loop(t,.5,.7,.35) if action in ('type','send') else 3*loop(t,1.2,2.4,.6)
        if action != 'think':line(d, [(665,1080),(700+motion,1067)], 5)
        curve(d, [(488,975),(440,1035),(440,1120),(468,1170)], 8)
    elif action in ('read','type','send','nod'):
        # on the couch the phone stays in hand while reading or nodding at a reply
        lift = 20*(1-ease((t-.15)/.8))
        curve(d, [(485,975),(531,1112+lift*.5),(587,1138+lift),(628,1082+lift)], 9)
        curve(d, [(488,975),(561,1003+lift*.5),(600,1015+lift),(632,1035+lift)], 9)
        phone(d, 667, 1025+lift)
        if action == 'type':swipe = 16*loop(t,.8,.6,.3)
        elif action == 'send':swipe = 22*pulse(t,s['event_delay']-.15,.35)
        else:swipe = 12*loop(t,1.2,2.2,.4)
        line(d, [(629,1080+lift),(648,1067+lift-swipe)], 6)
    elif action == 'celebrate':
        up = 70*ease(t/.6)+12*math.sin(t*7)*ease((t-.6)/.3)
        curve(d, [(485,975),(380,970-up*.5),(350,920-up),(350,860-up)], 9)
        curve(d, [(488,975),(600,970-up*.5),(660,920-up),(690,860-up)], 9)
    elif action == 'think':
        curve(d, [(485,975),(580,1050),(601,970+tap),(558,905+tap)], 9)
        curve(d, [(488,975),(440,1035),(440,1120),(468,1170)], 8)
    else:
        for sign in (-1,1):
            curve(d, [(488,975),(488+80*sign,1050),(488+80*sign,1130),(488+55*sign,1190)], 8)
    head(im,500+lean,820+breath+nod,s['expression'],t,tilt=-5+2*math.sin(t*1.3),scale=.83)


def cafe(im,t,s):
    d=ImageDraw.Draw(im);floor(d)
    for x in [250,770]:
        d.rounded_rectangle((x-65,990,x+80,1250),22,fill='white',outline='#171717',width=5)
        for xx in [x-45,x+60]:line(d,[(xx,1250),(xx,1440)],5)
    for right in [False,True]:
        x=790 if right else 290;sign=-1 if right else 1
        # the two listeners nod out of step, like a conversation
        nod=9*loop(t,.7 if right else 2.2,3.0,1.1) if s['action']=='nod' else 0
        curve(d,[(x,930),(x-15*sign,1030),(x-12*sign,1120),(x,1230)],8)
        curve(d,[(x,1230),(x+42*sign,1255),(x+75*sign,1270),(x+80*sign,1290)],8)
        line(d,[(x+80*sign,1290),(x+80*sign,1440),(x+115*sign,1445)],8)
        line(d,[(x,1230),(x-17*sign,1320),(x-15*sign,1440),(x+16*sign,1445)],8)
        curve(d,[(x,980),(x+25*sign,1040),(x+78*sign,1095),(x+150*sign,1100)],8)
        curve(d,[(x-8*sign,980),(x-45*sign,1080),(x-38*sign,1180),(x-12*sign,1210)],8)
        tilt=(5 if right else -5)+1.5*math.sin(t*1.1+(2 if right else 0))
        head(im,x,817+nod,s['expression'],t+(1 if right else 0),tilt=tilt,scale=.81)
    d.ellipse((404,1094,688,1158),fill='white',outline='#171717',width=5)
    line(d,[(546,1158),(546,1423)],6);line(d,[(460,1440),(546,1423),(632,1440)],6)
    cup(d,465,1119);cup(d,620,1120)
    for x in [435,650]:d.ellipse((x-11,1104,x+11,1121),fill='white',outline='#171717',width=3)


def standing(im,t,s):
    d=ImageDraw.Draw(im);floor(d)
    curve(d,[(540,930),(523,1030),(524,1120),(540,1200)],9)
    tap=6*loop(t,.8,1.1,.4) if s['action']=='think' else 0
    for sign in (-1,1):
        line(d,[(540,1200),(540+65*sign,1320),(540+95*sign,1440),(540+125*sign,1446)],9)
        rise=75*ease(t/.65)+10*math.sin(t*7)*ease((t-.65)/.3) if s['action']=='celebrate' else 0
        sway=6*math.sin(t*1.5)*sign if s['action']=='idle' else 0
        end=(540+160*sign+sway,1140-rise*3)
        if s['action']=='think' and sign==1:end=(600,910+tap)
        curve(d,[(540,970),(540+110*sign,1030-rise),(540+130*sign,1100-rise*2),end],9)
    nod=12*loop(t,.65,2.6,1.0) if s['action']=='nod' else 0
    head(im,540,820+nod,s['expression'],t,tilt=3*pulse(t,.6,1.1)+1.5*math.sin(t*1.2),scale=.83)


def draw(scene,t,zoom=1.0):
    """One frame. zoom slowly pushes the camera in on the set; text stays fixed."""
    im=Image.new('RGB',(W,H),'white')
    if scene['background']=='cafe':cafe(im,t,scene)
    elif scene['background']=='standing':standing(im,t,scene)
    else:single(im,t,scene)
    if zoom>1:
        cx,cy=540,1150
        im=im.transform(im.size,Image.AFFINE,(1/zoom,0,cx-cx/zoom,0,1/zoom,cy-cy/zoom),
                        resample=Image.Resampling.BICUBIC,fillcolor='white')
    d=ImageDraw.Draw(im)
    rows,size=fit(scene['overlay'],880,1,42,28)
    for row in rows:txt(d,(540,195),row,size)
    message(im,scene,t)
    rows,size=fit(scene['caption'],875,2,58,42)
    for i,row in enumerate(rows):txt(d,(540,1625+i*(size+16)),row,size)
    return im
